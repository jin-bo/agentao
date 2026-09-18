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
4. **Longer retention has to come first.** Anthropic's caching documentation
   (fetched 2026-09-17 from
   ``platform.claude.com/docs/en/build-with-claude/prompt-caching``): "You can
   use both 1-hour and 5-minute cache controls in the same request, but with an
   important constraint: Cache entries with longer TTL must appear before
   shorter TTLs (that is, a 1-hour cache entry must appear before any 5-minute
   cache entries)." agentao's own three markers all carry one configured
   retention, so agentao alone can never mix — the rule only bites next to a
   marker the *caller* placed with a different ttl, and then it bites in both
   directions: a configured ``1h`` added after a caller's ``5m``, and a
   configured ``5m`` added before a caller's ``1h``, are each illegal. Such a
   site is **skipped**, never re-timed: substituting the caller's retention for
   the configured one, or the reverse, would silently change what the operator
   asked for. The documentation states the constraint without naming the
   failure, so a violation is mis-billed against its own ``A``/``B``/``C``
   position model at best and rejected at worst; either way it is not ours to
   emit.

Prompt order, which rules 2 and 4 are both counted in, is also from that page:
"Cache prefixes are created in the following order: ``tools``, ``system``, then
``messages``." In agentao's Chat Completions assembly the system message *is*
``messages[0]``, so one index over the message list already orders system ahead
of history; only the tool definitions need a block of their own.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

#: Wire dialects for the marker. One entry, deliberately: a second belongs to
#: the protocol adapters of stage 1+, not to a key bolted onto this one.
CACHE_CONTROL_FORMATS = frozenset({"anthropic"})

#: Retention values Anthropic documents for ``cache_control``, ranked by how
#: long-lived they are (higher is longer). The rank is what rule 4 is checked
#: in; the key set is the accepted-value list, derived from it rather than
#: written twice so the two cannot drift.
_TTL_RANKS = {"5m": 0, "1h": 1}

#: Retention values Anthropic documents for ``cache_control``. ``None`` means
#: "send no ttl", which is the 5-minute default.
CACHE_CONTROL_TTLS = frozenset(_TTL_RANKS)

#: Three, with the fourth slot left for the endpoint's automatic caching —
#: see rule 2 in the module docstring.
MAX_EXPLICIT_BREAKPOINTS = 3

_INSTRUCTION_ROLES = ("system", "developer")
_CONVERSATION_ROLES = ("user", "assistant", "tool")

#: Prompt-order blocks, so a marker's position is comparable across them.
#: ``tools`` precedes everything in ``messages`` (see the docstring).
_TOOLS_BLOCK = 0
_MESSAGES_BLOCK = 1

#: One marker's position in the prompt: ``(block, index within it)``.
_Position = Tuple[int, int]


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

    existing = _existing_markers(out_messages, out_tools)
    budget = MAX_EXPLICIT_BREAKPOINTS - len(existing)
    if budget <= 0:
        return out_messages, out_tools

    rank = _retention_rank(cache_control)
    if rank is None or any(other is None for _, other in existing):
        # A retention agentao does not recognise — a gateway extension on a
        # caller's marker, or a malformed one — makes rule 4 unanswerable.
        # Place nothing rather than assume a rank: guessing wrong emits exactly
        # the illegal order the rule exists to prevent, and the markers the
        # caller placed keep working untouched either way.
        return out_messages, out_tools
    ranked: List[Tuple[_Position, int]] = [
        (position, other) for position, other in existing if other is not None
    ]

    # Placed most-covering first, so a budget short of three keeps the
    # breakpoints that cover the most tokens. Anthropic orders a prompt
    # tools → system → messages, so a breakpoint at the end of history covers
    # everything, one on system covers tools + system, and one on the last tool
    # covers the tools alone.
    #
    # Each helper answers how many **new** markers it wrote — 0 or 1 — and 0
    # covers three different cases that must all leave the budget alone: there
    # was nothing markable at that site; the site already carries the caller's
    # own marker (which ``_existing_markers`` already charged, so spending a
    # second unit on it would silently ship two breakpoints instead of three);
    # and placing there would break rule 4's retention order.
    budget -= _mark_last_stable_message(
        out_messages, cache_control, rank, ranked,
        request_only_tail=request_only_tail,
    )
    if budget > 0:
        budget -= _mark_instruction_message(
            out_messages, cache_control, rank, ranked,
        )
    if budget > 0 and out_tools:
        budget -= _mark_last_tool(out_tools, cache_control, rank, ranked)
    return out_messages, out_tools


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def _mark_instruction_message(
    messages: List[Dict[str, Any]],
    cache_control: Dict[str, str],
    rank: int,
    ranked: List[Tuple[_Position, int]],
) -> int:
    """Mark the first ``system`` / ``developer`` message, in place in the list.

    Returns the number of **new** markers written: 0 or 1 (see
    :func:`apply_cache_control` for why "already marked" and "would break
    retention order" must also be 0).
    """
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") in _INSTRUCTION_ROLES:
            if not _ordering_allows((_MESSAGES_BLOCK, i), rank, ranked):
                return 0
            marked = _marked_message(message, cache_control)
            if marked is None or marked is message:
                return 0
            messages[i] = marked
            return 1
    return 0


def _mark_last_stable_message(
    messages: List[Dict[str, Any]],
    cache_control: Dict[str, str],
    rank: int,
    ranked: List[Tuple[_Position, int]],
    *,
    request_only_tail: int = 0,
) -> int:
    """Mark the last markable conversation message before the volatile tail.

    Scans backwards, because the last message is not always markable: an
    assistant message that carries only ``tool_calls`` has ``content: None``
    and no text part to hang the marker on. Marking the message *before* it is
    right — a prefix is a prefix — and is what pi-mono's own backward scan
    does.

    Returns the number of **new** markers written: 0 or 1.
    """
    end = len(messages) - max(0, request_only_tail)
    for i in range(end - 1, -1, -1):
        message = messages[i]
        if not isinstance(message, dict):
            continue
        if message.get("role") not in _CONVERSATION_ROLES:
            continue
        if not _ordering_allows((_MESSAGES_BLOCK, i), rank, ranked):
            # Keep scanning: an earlier position can be legal where this one is
            # not — a caller's ``1h`` further back forbids a configured ``5m``
            # in front of it, but not behind it.
            continue
        marked = _marked_message(message, cache_control)
        if marked is message:
            # The site the scan wanted already carries the caller's own marker.
            # Stop here rather than walking further back — the boundary is
            # covered, and it is covered at a *later* point than any earlier
            # message would give.
            return 0
        if marked is not None:
            messages[i] = marked
            return 1
    return 0


def _mark_last_tool(
    tools: List[Dict[str, Any]],
    cache_control: Dict[str, str],
    rank: int,
    ranked: List[Tuple[_Position, int]],
) -> int:
    """Mark the last tool definition, in place in the list.

    The marker sits at the top level of the tool dict, next to ``type`` and
    ``function`` — not inside ``function``. A copy, because ``tools`` holds the
    registry's canonical serialized schemas.

    Returns the number of **new** markers written: 0 or 1. A tool the caller
    already marked is left exactly as it is — overwriting it would replace the
    caller's own ttl and spend a budget unit ``_count_existing`` already
    charged.
    """
    last = tools[-1]
    if not isinstance(last, dict):
        return 0
    if "cache_control" in last:
        return 0
    if not _ordering_allows((_TOOLS_BLOCK, len(tools) - 1), rank, ranked):
        return 0
    marked = dict(last)
    marked["cache_control"] = cache_control
    tools[-1] = marked
    return 1


# ---------------------------------------------------------------------------
# Copy-on-mark
# ---------------------------------------------------------------------------


def _marked_message(
    message: Dict[str, Any], cache_control: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """A copy of ``message`` with the marker on its last text part.

    Three answers, and the caller has to tell them apart:

    - ``None`` — nothing to mark: no content, empty string content, or a
      content list with no text part. The caller keeps looking or spends the
      slot elsewhere; an unplaceable marker must not be counted as placed.
    - ``message`` **itself** — the site already carries a marker the caller
      placed. Left untouched (so the caller's own ttl survives) and *not*
      counted as a new breakpoint, because ``_count_existing`` already charged
      it. Overwriting it here would ship two breakpoints where the budget says
      three.
    - anything else — a copy carrying the new marker.

    A string ``content`` is promoted to a one-element text-part list, which is
    the only way to carry a block-level marker on this wire.
    """
    if "cache_control" in message:
        return message
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
                if "cache_control" in part:
                    return message
                marked = dict(message)
                new_content = list(content)
                new_part = dict(part)
                new_part["cache_control"] = cache_control
                new_content[i] = new_part
                marked["content"] = new_content
                return marked
        return None
    return None


# ---------------------------------------------------------------------------
# What the caller already placed
# ---------------------------------------------------------------------------


def _retention_rank(marker: Any) -> Optional[int]:
    """How long-lived a marker is — higher is longer. ``None`` = undecidable.

    An absent ``ttl`` is the provider's 5-minute default, which is why a bare
    ``{"type": "ephemeral"}`` and an explicit ``{"ttl": "5m"}`` rank the same
    although the dicts differ. A ttl agentao does not recognise (a gateway
    extension, a typo on a caller's own marker) returns ``None``: the caller
    must then place nothing rather than guess a rank, because guessing wrong
    emits exactly the illegal order rule 4 exists to prevent.
    """
    if not isinstance(marker, dict):
        return None
    ttl = marker.get("ttl")
    if ttl is None:
        return _TTL_RANKS["5m"]
    return _TTL_RANKS.get(str(ttl).strip().lower())


def _existing_markers(
    messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]],
) -> List[Tuple[_Position, Optional[int]]]:
    """Every marker the caller already placed, as ``(position, rank)``.

    Two jobs in one pass. The count bounds the budget, so a host that marks its
    own messages cannot push the request past four breakpoints by sitting
    underneath a feature that assumes it owns all of them. The positions and
    ranks are what rule 4 is checked against — which is why they are collected
    in prompt order (tools, then messages) rather than just tallied.
    """
    found: List[Tuple[_Position, Optional[int]]] = []
    for index, tool in enumerate(tools or ()):
        if isinstance(tool, dict) and "cache_control" in tool:
            found.append(
                ((_TOOLS_BLOCK, index), _retention_rank(tool["cache_control"])),
            )
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        if "cache_control" in message:
            found.append(
                ((_MESSAGES_BLOCK, index), _retention_rank(message["cache_control"])),
            )
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "cache_control" in part:
                    found.append((
                        (_MESSAGES_BLOCK, index),
                        _retention_rank(part["cache_control"]),
                    ))
    return found


def _ordering_allows(
    position: _Position, rank: int, ranked: List[Tuple[_Position, int]],
) -> bool:
    """Whether a marker of ``rank`` at ``position`` keeps retention order legal.

    Rule 4 is "longer TTL first", so the ranks along the prompt must be
    non-increasing. Every marker agentao places carries the *same* configured
    rank, which collapses the check to two questions per site: is there a
    shorter-lived marker at or before this position (placing a longer-lived one
    after it is illegal), and is there a longer-lived one at or after it
    (placing a shorter-lived one before it is illegal).

    Equal ranks are fine in either direction — non-increasing, not decreasing —
    which is why an all-``5m`` or all-``1h`` request never loses a breakpoint to
    this check, and neither does the ordinary case of no caller markers at all.
    """
    for other_position, other_rank in ranked:
        if other_rank < rank and other_position <= position:
            return False
        if other_rank > rank and other_position >= position:
            return False
    return True
