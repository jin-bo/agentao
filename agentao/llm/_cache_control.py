"""Explicit prompt-cache breakpoints over the ordinary Chat Completions wire.

Stage 0b of ``docs/design/llm-api-adapters.md`` §2.3. No adapter, no second
protocol: the marker is an extra key on a message content part and on a tool
definition, and the OpenAI SDK forwards unknown keys verbatim (verified locally
against openai 2.24.0 — ``maybe_transform`` leaves ``cache_control`` on message
dicts, content parts and tool dicts alone).

**Opt-in, off by default, and that is not timidity.** SDK pass-through is
verified; *endpoint acceptance is not*. Whether a given OpenAI-compatible
gateway forwards the key to Anthropic, ignores it, or 400s on it is a property
of that deployment. pi-mono ships the same three breakpoints but gates them on
``provider === "openrouter" && model.id.startsWith("anthropic/")``
(``packages/ai/src/api/openai-completions.ts:1632``) — which is evidence that
*OpenRouter* accepts them, not an acceptance test for anyone else's endpoint.
So agentao asks the operator to name the format for their endpoint rather than
inferring it from a base-URL or model-name pattern.

Three rules this module exists to hold:

1. **Copy-on-mark.** The caller's ``messages`` is not the caller's to mutate:
   in the runtime it shares its dicts with ``agent.messages``, so an in-place
   marker would enter history, the session file, the replay record, ACP
   ``session/load`` and compaction — and breakpoints would pile up one per
   turn. Every function here copies along the path it touches (message dict →
   content list → target part) and shares everything else, read-only.
2. **At most three explicit breakpoints.** Anthropic's caching documentation
   allows four and states that with automatic caching enabled, "If 4 explicit
   block-level breakpoints already exist, the API returns a 400 error (no slots
   left for automatic caching)" (checked 2026-09-18). Four explicit markers
   alone are legal; agentao spends three and leaves the fourth slot free, so
   turning automatic caching on at the endpoint cannot start failing requests.
   Markers the caller already placed count against the three.
3. **The breakpoint goes at the end of *stable* history.** Not the end of the
   request: since stage 0a the last message is a request-only volatile tail
   that changes every request, and a breakpoint there would never be read back
   — it would burn a slot and a cache *write* to cache something already known
   to be different next time.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

#: Wire dialects for the marker. One entry, deliberately: a second belongs to
#: the protocol adapters of stage 1+, not to a key bolted onto this one.
CACHE_CONTROL_FORMATS = frozenset({"anthropic"})

#: Retention values Anthropic documents for ``cache_control``. ``None`` means
#: "send no ttl", which is the 5-minute default.
CACHE_CONTROL_TTLS = frozenset({"5m", "1h"})

#: Three, with the fourth slot left for the endpoint's automatic caching —
#: see rule 2 in the module docstring.
MAX_EXPLICIT_BREAKPOINTS = 3

_INSTRUCTION_ROLES = ("system", "developer")
_CONVERSATION_ROLES = ("user", "assistant", "tool")


def resolve_cache_control(
    fmt: Optional[str], ttl: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """The marker value for one configured format, or ``None`` when off.

    Raises ``ValueError`` on an unknown format or ttl rather than silently
    sending nothing: this is a deployment knob whose whole purpose is to be
    explicit, and a typo that quietly disables caching is indistinguishable
    from caching that does not work.
    """
    if fmt is None:
        return None
    normalized = str(fmt).strip().lower()
    if normalized in ("", "off", "none", "false"):
        return None
    if normalized not in CACHE_CONTROL_FORMATS:
        raise ValueError(
            f"Unknown prompt-cache format {fmt!r}; supported: "
            f"{sorted(CACHE_CONTROL_FORMATS)} (or 'off')."
        )
    marker: Dict[str, str] = {"type": "ephemeral"}
    if ttl is not None:
        normalized_ttl = str(ttl).strip().lower()
        if normalized_ttl:
            if normalized_ttl not in CACHE_CONTROL_TTLS:
                raise ValueError(
                    f"Unknown prompt-cache ttl {ttl!r}; supported: "
                    f"{sorted(CACHE_CONTROL_TTLS)}."
                )
            marker["ttl"] = normalized_ttl
    return marker


def apply_cache_control(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    cache_control: Dict[str, str],
    *,
    request_only_tail: int = 0,
) -> Tuple[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """Return ``(messages, tools)`` copies carrying the explicit breakpoints.

    ``request_only_tail`` is how many messages at the end of ``messages`` are
    request-only (stage 0a's volatile tail: 0 or 1). The conversation
    breakpoint is placed *before* them.

    Both returned lists are new; inside them, only the dicts that were marked
    are copies. Nothing in the input is mutated.
    """
    out_messages: List[Dict[str, Any]] = list(messages)
    out_tools: Optional[List[Dict[str, Any]]] = list(tools) if tools else tools

    budget = MAX_EXPLICIT_BREAKPOINTS - _count_existing(out_messages, out_tools)
    if budget <= 0:
        return out_messages, out_tools

    # Placed most-covering first, so a budget short of three keeps the
    # breakpoints that cover the most tokens. Anthropic orders a prompt
    # tools → system → messages, so a breakpoint at the end of history covers
    # everything, one on system covers tools + system, and one on the last tool
    # covers the tools alone.
    if _mark_last_stable_message(
        out_messages, cache_control, request_only_tail=request_only_tail,
    ):
        budget -= 1
    if budget > 0 and _mark_instruction_message(out_messages, cache_control):
        budget -= 1
    if budget > 0 and out_tools and _mark_last_tool(out_tools, cache_control):
        budget -= 1
    return out_messages, out_tools


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def _mark_instruction_message(
    messages: List[Dict[str, Any]], cache_control: Dict[str, str],
) -> bool:
    """Mark the first ``system`` / ``developer`` message, in place in the list."""
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") in _INSTRUCTION_ROLES:
            marked = _marked_message(message, cache_control)
            if marked is None:
                return False
            messages[i] = marked
            return True
    return False


def _mark_last_stable_message(
    messages: List[Dict[str, Any]],
    cache_control: Dict[str, str],
    *,
    request_only_tail: int = 0,
) -> bool:
    """Mark the last markable conversation message before the volatile tail.

    Scans backwards, because the last message is not always markable: an
    assistant message that carries only ``tool_calls`` has ``content: None``
    and no text part to hang the marker on. Marking the message *before* it is
    right — a prefix is a prefix — and is what pi-mono's own backward scan
    does.
    """
    end = len(messages) - max(0, request_only_tail)
    for i in range(end - 1, -1, -1):
        message = messages[i]
        if not isinstance(message, dict):
            continue
        if message.get("role") not in _CONVERSATION_ROLES:
            continue
        marked = _marked_message(message, cache_control)
        if marked is not None:
            messages[i] = marked
            return True
    return False


def _mark_last_tool(
    tools: List[Dict[str, Any]], cache_control: Dict[str, str],
) -> bool:
    """Mark the last tool definition, in place in the list.

    The marker sits at the top level of the tool dict, next to ``type`` and
    ``function`` — not inside ``function``. A copy, because ``tools`` holds the
    registry's canonical serialized schemas.
    """
    last = tools[-1]
    if not isinstance(last, dict):
        return False
    marked = dict(last)
    marked["cache_control"] = cache_control
    tools[-1] = marked
    return True


# ---------------------------------------------------------------------------
# Copy-on-mark
# ---------------------------------------------------------------------------


def _marked_message(
    message: Dict[str, Any], cache_control: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """A copy of ``message`` with the marker on its last text part.

    ``None`` when there is nothing to mark: no content, empty string content,
    or a content list with no text part. The caller then keeps looking or
    spends the slot elsewhere — an unplaceable marker must not be counted as
    placed.

    A string ``content`` is promoted to a one-element text-part list, which is
    the only way to carry a block-level marker on this wire.
    """
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return None
        marked = dict(message)
        marked["content"] = [
            {"type": "text", "text": content, "cache_control": cache_control},
        ]
        return marked
    if isinstance(content, list):
        for i in range(len(content) - 1, -1, -1):
            part = content[i]
            if isinstance(part, dict) and part.get("type") == "text":
                marked = dict(message)
                new_content = list(content)
                new_part = dict(part)
                new_part["cache_control"] = cache_control
                new_content[i] = new_part
                marked["content"] = new_content
                return marked
        return None
    return None


def _count_existing(
    messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]],
) -> int:
    """Count ``cache_control`` markers the caller already placed.

    Counted so a host that marks its own messages cannot push the request past
    four breakpoints by sitting underneath a feature that assumes it owns all
    of them.
    """
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        if "cache_control" in message:
            total += 1
        content = message.get("content")
        if isinstance(content, list):
            total += sum(
                1 for part in content
                if isinstance(part, dict) and "cache_control" in part
            )
    for tool in tools or ():
        if isinstance(tool, dict) and "cache_control" in tool:
            total += 1
    return total
