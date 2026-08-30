"""``parse_stdout`` / ``resolve`` — the precedence function of §4.2.

**Precedence is a function, not a field ordering.** ``continue`` is not simply
"highest": the reference says it takes precedence over any *event-specific
decision field*, and exit 2 takes precedence over it — "exit 2 blocks whether or
not you print JSON: even a JSON ``permissionDecision`` of ``allow`` can't
override it". So the order is **exit 2 → continue → event decision**, which
cannot be expressed as field order inside a dataclass.

**Five stdout states, not three.** The reference decides whether to attempt JSON
at all from **both ends** of the string, and treats a parse failure as an error
rather than as text:

===============  =========================================================
``empty``        nothing on stdout
``plain``        does not both start with ``{`` and end with ``}`` — a JSON
                 array and a quoted JSON string are named as plain text, so
                 a ``[``-leading string is never parsed
``parse_error``  starts ``{``, ends ``}``, does not parse
``schema_invalid``  parses to an object, and a **known** field's value fails
                 validation — an unrecognized key never lands here
``valid``        parses and validates
===============  =========================================================

Two traps in that table, each one a clause. **The gate is both ends**: a
``{"decision":`` truncated by a dying pipe is *plain text*, never reaching the
parser, so it is not a parse error either. And **a parse failure is not text**,
which is version-gated — the sentence that said otherwise is no longer in the
reference, and the pre-v2.1.248 reading is the one to avoid re-deriving.

**An unrecognized key is not a schema failure.** The profile is narrower than the
reference by nine fields, so a hook that is perfectly legal upstream routinely
emits keys agentao does not implement. Validating with a closed schema would turn
every one of them into a user-visible `hook error` — telling the author their
correct hook is broken, and dropping the fields agentao *does* implement in the
same object.
"""

from __future__ import annotations

import json
from typing import Any

from ._parsed_output import Block, ParsedHookOutput, ResolvedHookOutput, Stop, UniversalFields
from ._profile import (
    PLAIN_TEXT_CONTEXT_EVENTS,
    exit2_outcome,
    field_disposition,
    honors_continue,
    honors_stop_reason,
    honors_system_message,
)

from ._profile import PROFILE_ID as PROFILE_ID_NAME

StdoutState = str  # "empty" | "plain" | "parse_error" | "schema_invalid" | "valid"

#: Fields whose *value* the profile validates. Everything else is either a
#: declared field with a free-form value or an unrecognized key — and the second
#: is ignored, never a failure.
_VALIDATED = ("continue", "suppressOutput")


def parse_stdout(stdout: str, event: str) -> tuple[dict[str, Any] | None, StdoutState, str | None]:
    """Classify one hook's stdout. Returns ``(data, state, failure_message)``.

    ``data`` is ``None`` unless the state is ``valid``; ``failure`` carries the
    parse or validation message for the two failure states and is ``None``
    otherwise — a failed parse has no object to hang a message on.
    """
    text = (stdout or "").strip()
    if not text:
        return None, "empty", None
    if not (text.startswith("{") and text.endswith("}")):
        # A JSON array and a quoted JSON string are plain text by name. Writing
        # this as "does not start with { or [" is the natural mistake and it
        # implies an array is parsed.
        return None, "plain", None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, "parse_error", str(exc)
    if not isinstance(data, dict):
        return None, "plain", None

    for key in _VALIDATED:
        if key in data and not isinstance(data[key], bool):
            return None, "schema_invalid", f"{key!r} must be a boolean"

    nested = data.get("hookSpecificOutput")
    if isinstance(nested, dict):
        # The discriminator of the whole nested object. Absent or mismatched
        # invalidates it **including the top-level fields beside it** — "the top
        # level still applies" is the reading that makes a mismatch harmless and
        # it is not what the reference says.
        name = nested.get("hookEventName")
        if name != event:
            return None, "schema_invalid", (
                f"hookSpecificOutput.hookEventName is {name!r}, expected {event!r}"
            )
    return data, "valid", None


def to_parsed(data: dict[str, Any], event: str) -> ParsedHookOutput:
    """Lift validated JSON into :class:`ParsedHookOutput` — no routing."""
    universal = UniversalFields(
        continue_processing=data.get("continue") if isinstance(data.get("continue"), bool) else None,
        stop_reason=data.get("stopReason") if isinstance(data.get("stopReason"), str) else None,
        system_message=data.get("systemMessage") if isinstance(data.get("systemMessage"), str) else None,
        terminal_sequence=data.get("terminalSequence") if isinstance(data.get("terminalSequence"), str) else None,
        suppress_output=bool(data.get("suppressOutput", False)),
    )
    parsed = ParsedHookOutput(universal=universal)
    nested = data.get("hookSpecificOutput")
    ctx = nested.get("additionalContext") if isinstance(nested, dict) else None
    if isinstance(ctx, str):
        parsed.additional_context.append(ctx)
    elif isinstance(ctx, list):
        parsed.additional_context.extend(str(c) for c in ctx)

    known = {"continue", "stopReason", "systemMessage", "terminalSequence",
             "suppressOutput", "hookSpecificOutput", "decision", "reason"}
    parsed.unknown_fields = [k for k in data if k not in known]
    return parsed


def resolve(
    event: str,
    returncode: int,
    stdout: str,
    stderr: str,
) -> ResolvedHookOutput:
    """The precedence function: exit 2 → ``continue`` → the event's decision.

    Five things this ordering buys, four of which a first implementation gets
    wrong: valid JSON takes effect on **every** exit code; plain text reaches the
    model on **exit 0 only**; a parse failure reaches it **never**;
    ``continue`` passes through the capability table rather than firing
    everywhere; and **four separate failure shapes reach the user** rather than a
    log.
    """
    out = ResolvedHookOutput()
    data, state, failure = parse_stdout(stdout, event)
    stderr = (stderr or "").strip()

    # 1. Channels. Valid JSON applies on every exit code; a parse or schema
    #    failure is a user notice on every code EXCEPT 2, where the block below
    #    owns the outcome and stderr supplies the reason.
    parsed: ParsedHookOutput | None = None
    if state == "valid" and data is not None:
        parsed = to_parsed(data, event)
        out.absorb_channels(parsed, event)
    elif state in ("parse_error", "schema_invalid") and returncode != 2:
        out.user_notices.append(f"{event} hook error: {failure}")
    elif state == "plain" and returncode == 0 and event in PLAIN_TEXT_CONTEXT_EVENTS:
        out.model_contexts.append((stdout or "").strip())

    # 2. Exit 2 — three outcomes, and all three are live across the eight events.
    if returncode == 2:
        kind = exit2_outcome(event)
        reason = parsed.blocking_reason if parsed is not None else None
        if reason is None:
            # Spelled out rather than folded into a conditional expression: the
            # compact form binds as ``(x) if parsed else (None or stderr)``, so a
            # hook that exits 2 with JSON but no blocking reason would block with
            # ``reason=None`` and never reach stderr.
            reason = stderr
        if kind == "block":
            out.control = Block(reason)
            return out                      # JSON cannot override an exit-2 block
        if kind == "model_feedback" and reason:
            out.model_contexts.append(reason)
        elif kind == "user_notice" and reason:
            out.user_notices.append(reason)

    # 3. Any other non-zero exit with no usable JSON is a user-visible error.
    elif returncode != 0 and state in ("plain", "empty"):
        first_line = stderr.splitlines()[0] if stderr else ""
        out.user_notices.append(
            f"{event} hook error: Failed with non-blocking status code: "
            f"{returncode} {first_line}".rstrip()
        )

    # 4. The control verdict, only if exit 2 did not already settle it.
    if parsed is not None:
        if parsed.universal.continue_processing is False and honors_continue(event):
            reason = parsed.universal.stop_reason if honors_stop_reason(event) else None
            out.control = Stop(reason)
    return out


def diagnose_fields(
    data: dict[str, Any],
    event: str,
    rule: Any,
    session_id: str | None = None,
) -> list[str]:
    """One diagnostic per (rule, field), for fields the profile does not honor.

    Two kinds reach here and they are **not** the same thing:

    * a field the profile declares and **ignores** — that reports an *agentao*
      limitation, so the author is told once, with the reason from the table;
    * a key the profile has never heard of — ignored for control purposes and
      named once, so the author learns it had no effect.

    Neither is ever a `hook error` shown to the user: neither is the author's
    mistake. And a **discard** never reaches here at all — an accepted field a
    given event drops is upstream-conformant behavior, and a diagnostic for it
    would flag correct code, which is the fastest way to train someone to ignore
    the channel.
    """
    from ._diagnostics import get_registry, rule_key
    from ._profile import ignore_reason

    registry = get_registry(session_id)
    key = rule_key(rule)
    notes: list[str] = []

    def _announce(field: str, message: str) -> None:
        if registry.announce(key, field):
            notes.append(message)

    known_top = {"continue", "stopReason", "systemMessage", "terminalSequence",
                 "suppressOutput", "hookSpecificOutput", "decision", "reason"}

    def _classify(name: str, qualified: str, always_name: bool) -> None:
        """The disposition is read **per event**, not per field.

        ``OUTPUT_FIELDS[x]`` alone answers "does the profile know this name
        anywhere", which is a different question: ``reloadSkills`` emitted on
        ``PostToolUse`` would be announced with ``SessionStart``'s reason, as
        though agentao declined it there. It is an unrecognized key on that
        event, and that is what the author needs to be told.
        """
        disposition = field_disposition(qualified, event)
        if disposition == "ignore":
            _announce(qualified,
                      f"{event}: '{qualified}' is parsed and has no effect — "
                      f"{ignore_reason(qualified)}")
        elif disposition is None and (always_name or name not in known_top):
            _announce(qualified,
                      f"{event}: '{qualified}' is not a field "
                      f"{PROFILE_ID_NAME} implements — ignored")

    for name in data:
        _classify(name, name, always_name=False)

    nested = data.get("hookSpecificOutput")
    if isinstance(nested, dict):
        for name in nested:
            if name == "hookEventName":
                continue
            _classify(name, f"hookSpecificOutput.{name}", always_name=True)
    return notes
