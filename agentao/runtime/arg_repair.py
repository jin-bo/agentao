"""Conservative repair layer for LLM-emitted tool-call argument strings.

Aggregators and local models (GLM, DeepSeek, Kimi, llama.cpp, …) do not
always emit strict JSON for ``tool_calls.arguments``. We try a small set
of conservative recoveries before failing.

Two design rules:

- Never guess punctuation (no comma/brace insertion, no key-quoting
  heuristics) and never apply blanket ``'`` → ``"`` substitution: shell
  args, paths, and natural-language apostrophes would silently corrupt.
- Repair, when it happens, is invisible to the model — only logged.
  Surfacing "[repaired]" in the tool result trains it to keep emitting
  bad JSON.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, List, Tuple


TAG_EMPTY = "empty"
TAG_FENCE = "fence"
TAG_DOUBLE_ENCODED = "double-encoded"
TAG_LENIENT_JSON = "lenient-json"
TAG_PYTHON_LITERAL = "python-literal"
TAG_TRAILING_COMMA = "trailing-comma"
TAG_BRACKET_BALANCE = "bracket-balance"


_SENTINEL: Any = object()

_FENCE_RE = re.compile(
    r"\A\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*\Z",
    re.DOTALL | re.IGNORECASE,
)

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

# Bound the bracket-balancing fixup so a pathological input cannot loop.
# 50 is far more than any real LLM emits.
_BRACKET_FIXUP_MAX_ITERS = 50

# Defense against pathological / hostile payloads: ``_balance_brackets``
# is O(n²) in the worst case (50 trim-and-reparse iterations on
# progressively shorter strings, each ``json.loads`` walking the whole
# thing). Real tool-call args are <2KB; 64KB is well above any plausible
# legitimate payload.
_BRACKET_FIXUP_MAX_INPUT = 64 * 1024


def _strip_fence(text: str) -> Tuple[str, bool]:
    """Peel a single outer ```/```json fence. Returns (text, stripped?)."""
    if not text.startswith("```"):
        return text, False
    m = _FENCE_RE.match(text)
    if m is None:
        return text, False
    return m.group(1), True


def _coerce_to_json_data_model(value: Any) -> Any:
    """Round-trip ``value`` through JSON to coerce it into the JSON data model.

    Used to gate the python-literal repair tier. ``ast.literal_eval`` accepts
    several shapes that ``json.dumps`` either rejects outright or silently
    re-shapes (and that downstream code does NOT expect):

    - ``bytes`` / ``set`` / ``complex`` — ``json.dumps`` raises TypeError.
    - non-string dict keys (e.g. ``{1: "x"}``) — ``json.dumps`` coerces to
      ``"1"`` in the serialized form, but the original dict still has
      ``int`` keys, which makes ``tool.execute(**args)`` raise
      ``TypeError: keywords must be strings``.
    - ``tuple`` values — ``json.dumps`` emits ``[...]`` but the in-memory
      value is still a tuple, surprising any tool / hook that checks
      ``isinstance(v, list)``.

    Round-tripping through ``json.dumps`` + ``json.loads`` produces exactly
    the value JSON parsing would have produced, so callers see the same
    shape as every other repair tier. Returns ``_SENTINEL`` when the value
    cannot be JSON-encoded at all.
    """
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return _SENTINEL


def _balance_brackets(text: str) -> str:
    """Append missing ``}`` / ``]`` to balance opening counts; then strip
    excess trailing closers in a bounded loop until ``json.loads`` accepts."""
    if len(text) > _BRACKET_FIXUP_MAX_INPUT:
        return text
    fixed = text
    open_curly = fixed.count("{") - fixed.count("}")
    open_bracket = fixed.count("[") - fixed.count("]")
    if open_curly > 0:
        fixed += "}" * open_curly
    if open_bracket > 0:
        fixed += "]" * open_bracket

    for _ in range(_BRACKET_FIXUP_MAX_ITERS):
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            if fixed.endswith("}") and fixed.count("}") > fixed.count("{"):
                fixed = fixed[:-1]
            elif fixed.endswith("]") and fixed.count("]") > fixed.count("["):
                fixed = fixed[:-1]
            else:
                return fixed
    return fixed


def parse_tool_arguments(
    raw: Any, *, allow_bracket_balance: bool = False,
) -> Tuple[dict, List[str]]:
    """Parse a tool-call ``arguments`` payload into a ``dict``.

    Returns ``(parsed_dict, repair_tags)``. ``repair_tags`` is empty when
    strict JSON succeeded on the first try — callers can treat a non-empty
    list as a signal worth logging.

    ``allow_bracket_balance``: when ``True``, enables a final repair tier
    that counts ``{``/``}`` / ``[``/``]`` and appends or strips closers to
    rescue truncated args (e.g. GLM-5.1 mid-stream cutoffs). Off by default
    because it guesses where the truncation happened — fine for the
    *outbound* canonicaliser (it must produce something) but risky as a
    silent inbound default.

    Raises ``ValueError`` if every layer fails or the result is not a dict.
    The error message intentionally does not echo ``raw`` (could be large
    or contain secrets); callers attach tool name + truncated context.
    """
    tags: List[str] = []

    if raw is None:
        return {}, [TAG_EMPTY]
    if isinstance(raw, dict):
        return raw, []
    if not isinstance(raw, str):
        raise ValueError(
            f"tool arguments must be a string or dict, got {type(raw).__name__}"
        )
    stripped = raw.strip()
    if stripped == "" or stripped.lower() in {"none", "null"}:
        return {}, [TAG_EMPTY]

    after_fence, fence_stripped = _strip_fence(stripped)
    if fence_stripped:
        tags.append(TAG_FENCE)
        stripped = after_fence.strip()
        if stripped == "":
            tags.append(TAG_EMPTY)
            return {}, tags

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = _SENTINEL
    else:
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
                tags.append(TAG_DOUBLE_ENCODED)
            except json.JSONDecodeError:
                pass

    if parsed is _SENTINEL:
        try:
            parsed = json.loads(stripped, strict=False)
            tags.append(TAG_LENIENT_JSON)
        except json.JSONDecodeError:
            parsed = _SENTINEL

    if parsed is _SENTINEL:
        de_comma = _TRAILING_COMMA_RE.sub(r"\1", stripped)
        if de_comma != stripped:
            try:
                parsed = json.loads(de_comma, strict=False)
                tags.append(TAG_TRAILING_COMMA)
            except json.JSONDecodeError:
                parsed = _SENTINEL

    if parsed is _SENTINEL:
        try:
            candidate = ast.literal_eval(stripped)
        except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
            candidate = _SENTINEL
        if candidate is not _SENTINEL:
            normalized = _coerce_to_json_data_model(candidate)
            if normalized is not _SENTINEL:
                parsed = normalized
                tags.append(TAG_PYTHON_LITERAL)

    if parsed is _SENTINEL and allow_bracket_balance:
        balanced = _balance_brackets(stripped)
        if balanced != stripped:
            try:
                parsed = json.loads(balanced, strict=False)
                tags.append(TAG_BRACKET_BALANCE)
            except json.JSONDecodeError:
                parsed = _SENTINEL

    if parsed is _SENTINEL:
        raise ValueError("could not parse tool arguments as JSON or Python literal")

    # Downstream is ``tool.execute(**args)`` and ``args.get(...)`` —
    # a list / scalar / None must not propagate.
    if not isinstance(parsed, dict):
        raise ValueError(
            f"tool arguments must parse to an object, got {type(parsed).__name__}"
        )

    return parsed, tags
