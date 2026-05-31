"""Command-hook stdout parsers for :class:`PluginHookDispatcher`.

The two methods here translate a hook subprocess's stdout into the
corresponding result object (``UserPromptSubmitResult`` / ``StopHookResult``),
implementing Claude Code's JSON output contracts. They are pure parsing logic
— they read ``stdout`` and mutate the passed-in ``result`` — and carry no
dispatcher state, so they live in their own mixin to keep ``_dispatcher.py``
focused on hook discovery / matching / subprocess execution.

Mixed into ``PluginHookDispatcher``; the tests call
``dispatcher._parse_stop_command_output(...)`` as an instance method, which the
mixin preserves.
"""

from __future__ import annotations

import json

from ..models import ParsedHookRule, StopHookResult, UserPromptSubmitResult
from ._attachments import _make_attachment


class _OutputParsingMixin:
    """Structured-output parsers for command / Stop hooks."""

    def _parse_command_output(
        self,
        stdout: str,
        rule: ParsedHookRule,
        result: UserPromptSubmitResult,
    ) -> None:
        """Parse structured JSON output from a command hook."""
        stdout = stdout.strip()
        if not stdout:
            result.messages.append(
                _make_attachment(
                    "hook_success",
                    {},
                    hook_name=rule.command or "",
                    hook_event=rule.event,
                )
            )
            return

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Non-JSON output treated as additional context.
            result.additional_contexts.append(stdout)
            result.messages.append(
                _make_attachment(
                    "hook_additional_context",
                    {"context": stdout},
                    hook_name=rule.command or "",
                    hook_event=rule.event,
                )
            )
            return

        if not isinstance(data, dict):
            result.additional_contexts.append(str(data))
            return

        # Process structured fields.
        if "blockingError" in data:
            result.blocking_error = str(data["blockingError"])
            result.messages.append(
                _make_attachment(
                    "hook_blocking_error",
                    {"error": result.blocking_error},
                    hook_name=rule.command or "",
                    hook_event=rule.event,
                )
            )
            return

        if data.get("preventContinuation"):
            result.prevent_continuation = True
            result.stop_reason = data.get("stopReason", "Hook prevented continuation")
            result.messages.append(
                _make_attachment(
                    "hook_stopped_continuation",
                    {"reason": result.stop_reason},
                    hook_name=rule.command or "",
                    hook_event=rule.event,
                )
            )
            return

        if "additionalContext" in data:
            ctx = data["additionalContext"]
            if isinstance(ctx, str):
                result.additional_contexts.append(ctx)
            elif isinstance(ctx, list):
                result.additional_contexts.extend(str(c) for c in ctx)
            result.messages.append(
                _make_attachment(
                    "hook_additional_context",
                    {"context": ctx},
                    hook_name=rule.command or "",
                    hook_event=rule.event,
                )
            )
            return

        # Generic success.
        result.messages.append(
            _make_attachment(
                "hook_success",
                data,
                hook_name=rule.command or "",
                hook_event=rule.event,
            )
        )

    def _parse_stop_command_output(
        self,
        stdout: str,
        rule: ParsedHookRule,
        result: StopHookResult,
    ) -> None:
        """Parse structured JSON output from a Stop command hook.

        Implements Claude Code's Stop JSON contract. ``continue: false``
        overrides any ``force_continue``-producing field on the same
        output. ``blocking_error`` is independent of ``continue: false``
        because both intents agree on "stop the turn."
        """
        stdout = stdout.strip()
        if not stdout:
            result.messages.append(
                _make_attachment(
                    "hook_success",
                    {},
                    hook_name=rule.command or "",
                    hook_event="Stop",
                )
            )
            return

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Non-JSON output is treated as additional context.
            result.additional_contexts.append(stdout)
            result.messages.append(
                _make_attachment(
                    "hook_additional_context",
                    {"context": stdout},
                    hook_name=rule.command or "",
                    hook_event="Stop",
                )
            )
            return

        if not isinstance(data, dict):
            result.additional_contexts.append(str(data))
            return

        # ``continue: false`` overrides any force_continue-producing field.
        continue_false = data.get("continue") is False

        decision = data.get("decision")
        reason = data.get("reason")
        if decision == "block" and isinstance(reason, str):
            if continue_false:
                result.stop_reason = reason
            else:
                result.force_continue = True
                result.follow_up_message = reason
                result.stop_reason = reason

        stop_reason = data.get("stopReason")
        if isinstance(stop_reason, str):
            result.stop_reason = stop_reason

        if data.get("suppressOutput") is True:
            result.suppress_output = True

        system_message = data.get("systemMessage")
        if isinstance(system_message, str):
            result.system_message = system_message
            result.additional_contexts.append(system_message)

        hook_specific = data.get("hookSpecificOutput")
        if isinstance(hook_specific, dict):
            ctx = hook_specific.get("additionalContext")
            if isinstance(ctx, str):
                result.additional_contexts.append(ctx)
            elif isinstance(ctx, list):
                result.additional_contexts.extend(str(c) for c in ctx)

        # Tolerated for hook scripts that use the top-level field.
        legacy_ctx = data.get("additionalContext")
        if isinstance(legacy_ctx, str):
            result.additional_contexts.append(legacy_ctx)
        elif isinstance(legacy_ctx, list):
            result.additional_contexts.extend(str(c) for c in legacy_ctx)

        # ``blockingError`` is independent of ``continue: false``.
        blocking_error = data.get("blockingError")
        if isinstance(blocking_error, str):
            result.blocking_error = blocking_error
            result.messages.append(
                _make_attachment(
                    "hook_blocking_error",
                    {"error": blocking_error},
                    hook_name=rule.command or "",
                    hook_event="Stop",
                )
            )
            return

        # ``preventContinuation: true`` — Agentao internal legacy field
        # tolerated for hook scripts authored against UserPromptSubmit.
        # Honors ``continue: false`` precedence.
        if data.get("preventContinuation") is True and not continue_false:
            reason = data.get("stopReason") or "Hook prevented continuation"
            follow_up = data.get("stopReason") or "Stop hook requested continuation"
            result.force_continue = True
            result.stop_reason = reason
            result.follow_up_message = follow_up
            result.messages.append(
                _make_attachment(
                    "hook_stopped_continuation",
                    {"reason": reason},
                    hook_name=rule.command or "",
                    hook_event="Stop",
                )
            )
            return

        # Generic success path — record the parse so the dispatcher
        # boundary always observes a non-empty attachment list.
        result.messages.append(
            _make_attachment(
                "hook_success",
                data,
                hook_name=rule.command or "",
                hook_event="Stop",
            )
        )
