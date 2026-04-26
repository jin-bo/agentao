"""Outbound-message sanitization for the chat loop.

Two concerns, both about *what we ship back to the API on the next turn*:

1. **Lone UTF-16 surrogates** (U+D800–U+DFFF). Byte-level reasoning models
   (Kimi K2.5, GLM-5, Qwen via Ollama) occasionally emit these in
   ``content``, ``reasoning_content``, or ``tool_calls.arguments``. The
   OpenAI / httpx JSON encoder crashes on them, killing the session.
   Replace each with U+FFFD (Unicode replacement char).

2. **Non-canonical / invalid JSON in tool_call arguments.** The planner
   uses repaired args to *execute*, but the original raw string was being
   serialised verbatim into conversation history — meaning strict API
   proxies receive malformed JSON next turn, and the model sees its own
   bad output reflected back unchanged. We re-parse via the repair
   pipeline and re-emit canonical compact JSON only when repair was
   needed; clean strict-JSON input is returned verbatim so the
   conversation-history bytes match what the model emitted (preserves
   prompt-cache hits). Unparseable args become ``"{}"`` so the next API
   request still succeeds.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from .arg_repair import parse_tool_arguments


SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def sanitize_surrogates(text: str) -> str:
    """Replace lone UTF-16 surrogates with U+FFFD. Fast no-op when clean."""
    if not isinstance(text, str):
        return text
    if SURROGATE_RE.search(text) is None:
        return text
    return SURROGATE_RE.sub("�", text)


def canonicalize_tool_arguments(
    raw: Any,
    *,
    tool_name: str = "?",
    logger: Optional[Any] = None,
) -> str:
    """Round-trip tool-call ``arguments`` through the repair pipeline.

    Returns a wire-valid, surrogate-free, canonical-compact JSON string.
    On unparseable input returns ``"{}"`` and logs a warning — at this
    boundary we cannot ship malformed JSON, so degradation beats failure.
    """
    # Fast path: surrogate-free strict-JSON object input is returned
    # verbatim (skips the full repair pipeline). Keeping the wire bytes
    # identical to what the model emitted preserves prompt-cache hits.
    # Non-object top-level (``[]``, scalars) falls through to the slow
    # path so the same ``"{}"`` fallback applies.
    if isinstance(raw, str) and SURROGATE_RE.search(raw) is None:
        try:
            if isinstance(json.loads(raw), dict):
                return raw
        except json.JSONDecodeError:
            pass
    cleaned_raw = sanitize_surrogates(raw) if isinstance(raw, str) else raw
    try:
        # Outbound boundary: enable the bracket-balance tier so a
        # mid-stream truncation still yields wire-valid JSON instead of
        # crashing the next API request.
        parsed, _tags = parse_tool_arguments(
            cleaned_raw, allow_bracket_balance=True,
        )
    except ValueError as exc:
        if logger is not None:
            logger.warning(
                "Outbound tool_call arguments for '%s' unparseable (%s); "
                "replaced with '{}' to keep session alive",
                tool_name,
                exc,
            )
        return "{}"
    # ``ensure_ascii=False`` keeps non-ASCII content as-is (cheaper bytes,
    # readable logs); surrogates were already stripped so this is safe.
    try:
        return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        # ``ast.literal_eval`` (the python-literal repair tier) accepts
        # values like ``bytes`` / ``set`` that JSON cannot encode. Treat
        # this the same as a parse failure at this boundary: we cannot
        # ship a TypeError to the next API call.
        if logger is not None:
            logger.warning(
                "Outbound tool_call arguments for '%s' produced non-JSON "
                "values (%s); replaced with '{}' to keep session alive",
                tool_name,
                exc,
            )
        return "{}"


def _clone_tool_call_with_overrides(
    tc: Any,
    *,
    new_id: Any,
    new_name: Any,
    new_args: Any,
) -> Any:
    """Build a SimpleNamespace mirror of ``tc`` with overridden fields.

    Used as a fallback for frozen / read-only SDK objects (Pydantic
    ``model_config={'frozen': True}``) where ``setattr`` is rejected.
    The proxy preserves common extras (``thought_signature`` at either
    level) so the manual-construction branch of ``_serialize_tool_call``
    still emits a faithful history entry.
    """
    fn_orig = getattr(tc, "function", None)
    proxy_fn = SimpleNamespace(name=new_name, arguments=new_args)
    if fn_orig is not None:
        for attr in ("thought_signature",):
            if hasattr(fn_orig, attr):
                setattr(proxy_fn, attr, getattr(fn_orig, attr))
    proxy = SimpleNamespace(id=new_id, function=proxy_fn)
    for attr in ("type", "thought_signature"):
        if hasattr(tc, attr):
            setattr(proxy, attr, getattr(tc, attr))
    return proxy


def normalize_tool_calls(
    tool_calls: Any,
    *,
    repair_name_fn: Optional[Callable[[str], Optional[str]]] = None,
    logger: Optional[Any] = None,
) -> Tuple[List[Any], bool]:
    """Surrogate-sanitize and (optionally) name-repair every tool_call.

    Returns ``(cleaned_list, any_changed)``. The returned list contains:
    - the original SDK object when it was already clean OR when in-place
      ``setattr`` succeeded (preserves identity for downstream code);
    - a ``SimpleNamespace`` proxy with the cleaned fields when the SDK
      object was frozen and ``setattr`` failed.

    Both the conversation-history serializer and ``ToolRunner.execute()``
    must iterate the returned list, never ``assistant_message.tool_calls``
    directly — otherwise frozen tool_calls would yield divergent history
    vs. tool-result IDs/names, which strict OpenAI-compatible APIs reject.
    """
    if not tool_calls:
        return [], False
    out: List[Any] = []
    any_changed = False
    for tc in tool_calls:
        cleaned, changed = _normalize_one(
            tc, repair_name_fn=repair_name_fn, logger=logger,
        )
        out.append(cleaned)
        any_changed |= changed
    return out, any_changed


def _normalize_one(
    tc: Any,
    *,
    repair_name_fn: Optional[Callable[[str], Optional[str]]],
    logger: Optional[Any],
) -> Tuple[Any, bool]:
    """Normalize one tool_call. See ``normalize_tool_calls`` for contract."""
    raw_id = getattr(tc, "id", None)
    fn = getattr(tc, "function", None)
    raw_name = getattr(fn, "name", None) if fn is not None else None
    raw_args = getattr(fn, "arguments", None) if fn is not None else None

    new_id = sanitize_surrogates(raw_id) if isinstance(raw_id, str) else raw_id
    new_name = sanitize_surrogates(raw_name) if isinstance(raw_name, str) else raw_name
    new_args = sanitize_surrogates(raw_args) if isinstance(raw_args, str) else raw_args

    if repair_name_fn is not None and isinstance(new_name, str):
        repaired = repair_name_fn(new_name)
        if repaired is not None and repaired != new_name:
            if logger is not None:
                logger.warning(
                    "Tool name '%s' repaired to '%s'", new_name, repaired,
                )
            new_name = repaired

    id_changed = isinstance(raw_id, str) and new_id is not raw_id
    name_changed = isinstance(raw_name, str) and new_name != raw_name
    args_changed = isinstance(raw_args, str) and new_args is not raw_args
    if not (id_changed or name_changed or args_changed):
        return tc, False

    # Try in-place mutation first to preserve object identity. If any
    # setattr fails (frozen Pydantic, dataclass(frozen=True), slots-only,
    # validation rejection, …), fall back to a proxy. Catch broadly:
    # Pydantic v2 raises ``ValidationError`` (a ``ValueError``) on frozen
    # assignment; dataclasses raise ``FrozenInstanceError`` (an
    # ``AttributeError``); other SDKs may raise something custom. Any
    # setattr failure here is recoverable via the proxy path.
    in_place_ok = True
    try:
        if id_changed:
            tc.id = new_id
        if fn is not None and name_changed:
            fn.name = new_name
        if fn is not None and args_changed:
            fn.arguments = new_args
    except Exception:
        in_place_ok = False

    if in_place_ok:
        return tc, True
    return _clone_tool_call_with_overrides(
        tc, new_id=new_id, new_name=new_name, new_args=new_args,
    ), True


def _sanitize_str_field(d: Dict[str, Any], key: str) -> bool:
    """If ``d[key]`` is a string with surrogates, replace in place. Returns True
    iff a substitution happened. Cheap fast-no-op via ``sanitize_surrogates``."""
    val = d.get(key)
    if not isinstance(val, str):
        return False
    cleaned = sanitize_surrogates(val)
    if cleaned is val:
        return False
    d[key] = cleaned
    return True


def sanitize_assistant_message(msg: Dict[str, Any]) -> bool:
    """In-place surrogate sanitization of an assistant message dict.

    Walks ``content``, ``reasoning_content``, and each
    ``tool_calls[*].function.{name,arguments}`` / ``tool_calls[*].id``.
    Returns True if any field was changed — useful for telemetry.
    """
    found = _sanitize_str_field(msg, "content")
    found |= _sanitize_str_field(msg, "reasoning_content")

    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            found |= _sanitize_str_field(tc, "id")
            fn = tc.get("function")
            if isinstance(fn, dict):
                found |= _sanitize_str_field(fn, "name")
                found |= _sanitize_str_field(fn, "arguments")

    return found
