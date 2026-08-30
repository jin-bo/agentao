"""``claude-code@profile-1`` — the capability table of the conformance plan §5.1.

**This module is the authority.** Where any other part of the hook package
decides what a field means, it reads this table; §5.1's rule is that a
disposition changes here first. Encoding it as data rather than as branches is
what makes §12's table-driven tests possible: a new row is a data change, and a
missing row is a test failure rather than silence.

Two axes, not four values — the distinction §5.1 spent a revision getting right:

* **Profile disposition** — ``accept`` or ``ignore``, decided **once per field**,
  never per event. An ``ignore`` reports an *agentao* limitation and earns one
  diagnostic per (rule, field) (``_diagnostics.py``).
* **Delivery** — ``honored`` or ``discarded``, and it applies **only to an
  accepted field**, per event. A discard is **silent**: the hook is
  upstream-conformant, the same output does nothing on Claude Code either, and a
  diagnostic here would flag correct code.

That is why :data:`UNIVERSAL_DELIVERY` carries the three *accepted* universal
fields and not the ignored two. ``suppressOutput`` and ``terminalSequence`` are
``ignore``, so the delivery axis never runs for them and they have no column —
writing them in as "n/a" is what made an earlier revision read as a four-valued
model. ``stopReason`` has a column because it is *accepted*, and it mirrors
``continue`` by construction: it is the message a stop carries, so on an event
that discards the stop there is nothing for it to qualify.

Rows marked *measured* come from ``docs/reference/hooks-probe-2.1.251.md`` rather
than from the reference's prose; both were contested rows the document could not
settle, and one of them reversed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PROFILE_ID = "claude-code@profile-1"
LEGACY_CONTRACT_ID = "agentao-v1"

#: The eight events in the profile. The reference documents 56; the other 48 are
#: absent by declaration (§1), not by oversight.
PROFILE_EVENTS: frozenset[str] = frozenset({
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "PreCompact",
})

Disposition = Literal["accept", "ignore"]
Delivery = Literal["honored", "discarded"]
Exit2Outcome = Literal["block", "model_feedback", "user_notice"]


@dataclass(frozen=True)
class FieldSpec:
    """One row of §5.1's field table."""

    disposition: Disposition
    events: frozenset[str]
    #: Why, in the author's terms. Surfaced in the diagnostic for an ``ignore``,
    #: because §1's third rule is that nothing is excluded silently.
    reason: str = ""


_ALL = PROFILE_EVENTS
_UNIVERSAL = _ALL


#: §5.1's field table. The key is the wire name; nested fields are spelled with
#: the ``hookSpecificOutput.`` prefix they arrive under.
OUTPUT_FIELDS: dict[str, FieldSpec] = {
    # --- universal, accepted; delivery is per event (see UNIVERSAL_DELIVERY) --
    "continue": FieldSpec("accept", _UNIVERSAL),
    "stopReason": FieldSpec("accept", _UNIVERSAL),
    "systemMessage": FieldSpec("accept", _UNIVERSAL),
    # --- universal, ignored ------------------------------------------------
    "suppressOutput": FieldSpec(
        "ignore", _UNIVERSAL,
        "documented inert upstream; live in agentao-v1, where it gates the "
        "<stop-hook> echo",
    ),
    "terminalSequence": FieldSpec(
        "ignore", _UNIVERSAL,
        "agentao's CLI has no hook-owned terminal-write path, and the OSC "
        "allowlist is a security boundary this profile will not implement blind",
    ),
    # --- hookSpecificOutput -------------------------------------------------
    "hookSpecificOutput.hookEventName": FieldSpec(
        "accept", _UNIVERSAL,
        "the discriminator of the nested object; absent or mismatched makes the "
        "whole object schema-invalid, top-level fields included",
    ),
    "hookSpecificOutput.additionalContext": FieldSpec(
        "accept",
        frozenset({"SessionStart", "UserPromptSubmit", "PreToolUse",
                   "PostToolUse", "PostToolUseFailure", "Stop"}),
    ),
    # --- event decisions ----------------------------------------------------
    # PostToolUseFailure is here on a MEASUREMENT, not on the reference: the
    # global table names it, its own section does not, and the probe found it
    # honored — as feedback, with the turn continuing.
    "decision": FieldSpec(
        "accept",
        frozenset({"UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
                   "Stop", "PreCompact"}),
    ),
    "reason": FieldSpec(
        "accept",
        frozenset({"UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
                   "Stop", "PreCompact"}),
    ),
    "hookSpecificOutput.permissionDecision": FieldSpec(
        "accept", frozenset({"PreToolUse"}),
    ),
    "hookSpecificOutput.permissionDecisionReason": FieldSpec(
        "accept", frozenset({"PreToolUse"}),
    ),
    "hookSpecificOutput.updatedInput": FieldSpec(
        "accept", frozenset({"PreToolUse"}),
    ),
    "hookSpecificOutput.updatedToolOutput": FieldSpec(
        "accept", frozenset({"PostToolUse"}),
    ),
    # --- ignored, each with the reason §1 requires ---------------------------
    "hookSpecificOutput.updatedMCPToolOutput": FieldSpec(
        "ignore", frozenset({"PostToolUse"}),
        "a second spelling of updatedToolOutput, which the reference itself says "
        "to prefer",
    ),
    "hookSpecificOutput.classifierContext": FieldSpec(
        "ignore", frozenset({"PostToolUse"}),
        "agentao has no auto-mode classifier, so there is no consumer to route "
        "it to",
    ),
    "hookSpecificOutput.sessionTitle": FieldSpec(
        "ignore", frozenset({"SessionStart", "UserPromptSubmit"}),
        "agentao sessions have ids, not titles; a title field is a product "
        "decision, not a conformance fix",
    ),
    "hookSpecificOutput.reloadSkills": FieldSpec(
        "ignore", frozenset({"SessionStart"}),
        "agentao's SkillManager scans a different tree (~/.agentao/skills) than "
        "the one a Claude-authored hook means, and its reload path holds no lock",
    ),
    "hookSpecificOutput.initialUserMessage": FieldSpec(
        "ignore", frozenset({"SessionStart"}),
        "would have to inject a turn into `agentao run` before the spec's own "
        "prompt — a pipeline change with its own ordering questions",
    ),
    "hookSpecificOutput.watchPaths": FieldSpec(
        "ignore", frozenset({"SessionStart"}),
        "FileChanged is not one of the profile's events, so accepting the field "
        "would arm nothing",
    ),
    "hookSpecificOutput.suppressOriginalPrompt": FieldSpec(
        "ignore", frozenset({"UserPromptSubmit"}),
        "agentao's block message never contains the prompt, so there is nothing "
        "for the flag to suppress",
    ),
}

#: §5.1's universal-field matrix. Only accepted universal fields appear.
#: "Universal" is not universal: the reference says so in the same breath as
#: introducing the fields, and two of the eight events are named exceptions —
#: three, once SessionStart's Decision-control row is read (hooks.md:1009,
#: confirmed by probe).
UNIVERSAL_DELIVERY: dict[str, dict[str, Delivery]] = {
    "SessionStart":       {"continue": "discarded", "stopReason": "discarded", "systemMessage": "honored"},
    "UserPromptSubmit":   {"continue": "honored",   "stopReason": "honored",   "systemMessage": "honored"},
    "PreToolUse":         {"continue": "honored",   "stopReason": "honored",   "systemMessage": "honored"},
    "PostToolUse":        {"continue": "honored",   "stopReason": "honored",   "systemMessage": "honored"},
    "PostToolUseFailure": {"continue": "honored",   "stopReason": "honored",   "systemMessage": "honored"},
    "Stop":               {"continue": "honored",   "stopReason": "honored",   "systemMessage": "honored"},
    "PreCompact":         {"continue": "discarded", "stopReason": "discarded", "systemMessage": "discarded"},
    "SessionEnd":         {"continue": "discarded", "stopReason": "discarded", "systemMessage": "discarded"},
}

#: §4.2's exit-2 table. Exit 2 is **not** a boolean: the reference gives it three
#: outcomes and all three are live across the profile's eight events. A single
#: ``blocks_on_exit_2`` predicate silently discards the stderr on exactly the two
#: events where §5.2 promises it reaches the model.
EXIT2_OUTCOME: dict[str, Exit2Outcome] = {
    "PreToolUse":         "block",
    "UserPromptSubmit":   "block",
    "Stop":               "block",
    "PreCompact":         "block",
    "PostToolUse":        "model_feedback",
    "PostToolUseFailure": "model_feedback",
    "SessionStart":       "user_notice",
    "SessionEnd":         "user_notice",
}

#: Events where plain-text stdout becomes model context — and **only on exit 0**
#: (§"Exit code 0"). Gating this on the exit code is what stops a failing hook's
#: diagnostic from being injected into the model's context.
PLAIN_TEXT_CONTEXT_EVENTS: frozenset[str] = frozenset({
    "UserPromptSubmit", "SessionStart",
})

#: Values of ``permissionDecision`` that agentao does not implement, and what it
#: does instead. A value agentao cannot honor is in exactly the position of a
#: field it cannot honor (§1), so it is accept / ignore / **degrade to a named
#: alternative** — never "reject", which would discard every sibling field.
PERMISSION_DECISION_DEGRADES: dict[str, str] = {
    "defer": "deny",
}


def honors_continue(event: str) -> bool:
    """Does ``event`` honor ``continue: false``?

    Five of the eight do. Applying it unconditionally — which an earlier design
    did — fires a stop on events where the reference discards the field.
    """
    return UNIVERSAL_DELIVERY.get(event, {}).get("continue") == "honored"


def honors_stop_reason(event: str) -> bool:
    """Does ``event`` deliver ``stopReason``?

    Always the same answer as :func:`honors_continue` — a stop's message has
    nothing to qualify where the stop is discarded — but it is its own predicate
    so a resolver never has to reach for a literal or borrow the wrong one.
    """
    return UNIVERSAL_DELIVERY.get(event, {}).get("stopReason") == "honored"


def honors_system_message(event: str) -> bool:
    """Does ``event`` deliver ``systemMessage`` to the user?

    Its own predicate rather than a reuse of :func:`honors_continue`, because
    the two differ on ``SessionStart``: the Decision-control table excludes that
    event from decision control and says nothing about user notices.
    """
    return UNIVERSAL_DELIVERY.get(event, {}).get("systemMessage") == "honored"


def exit2_outcome(event: str) -> Exit2Outcome | None:
    return EXIT2_OUTCOME.get(event)


def field_disposition(field: str, event: str) -> Disposition | None:
    """``accept`` / ``ignore`` for a field on an event, or ``None`` when the
    profile does not define the field for it at all.

    ``None`` is **not** an error: an unrecognized key is ignored and diagnosed,
    never a schema failure (§1). Returning it distinctly lets the caller tell
    "the profile declines this" from "the profile has never heard of it".
    """
    spec = OUTPUT_FIELDS.get(field)
    if spec is None or event not in spec.events:
        return None
    return spec.disposition


def ignore_reason(field: str) -> str:
    spec = OUTPUT_FIELDS.get(field)
    return spec.reason if spec else ""
