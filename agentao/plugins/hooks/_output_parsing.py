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
import logging
from typing import Any

from ..models import ParsedHookRule, StopHookResult, UserPromptSubmitResult
from ._attachments import _make_attachment
from ._budget import cap_channel
from ._profile import LEGACY_CONTRACT_ID

logger = logging.getLogger(__name__)


def _cap(text: str, rule: ParsedHookRule) -> str:
    """Tier-2 budget (§6) for one hook-authored string.

    Applied at every point a hook's text becomes a model context, a user
    surface or the next turn's input — which is why it sits on the
    assignments rather than on three named fields: a ``stopReason`` carried
    into the next turn is as much a hook-authored string as
    ``additionalContext`` is.

    The diagnostic is logged rather than delivered. ``diagnostics[]`` does not
    exist until ``ResolvedHookOutput`` lands (step 2), and the alternative
    available today — a ``HookAttachmentRecord`` — becomes a *model-visible*
    message (``_attachments.py::_attachment_to_message``), which would put a
    budget notice into the context the budget exists to protect.
    """
    capped, diagnostic = cap_channel(text, hook_event=rule.event)
    if diagnostic:
        logger.warning("%s: %s", rule.command or rule.event, diagnostic)
    return capped


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
            # Non-JSON output treated as additional context. The attachment
            # carries the *capped* text too: ``_attachment_to_message`` renders
            # every payload value into a model-visible user message, so handing
            # it the raw string would put the whole flood back into the context
            # the budget one line above exists to protect.
            capped = _cap(stdout, rule)
            result.additional_contexts.append(capped)
            result.messages.append(
                _make_attachment(
                    "hook_additional_context",
                    {"context": capped},
                    hook_name=rule.command or "",
                    hook_event=rule.event,
                )
            )
            return

        if not isinstance(data, dict):
            result.additional_contexts.append(_cap(str(data), rule))
            return

        # --- profile spellings, for a `claude-code` rule only -------------
        # `agentao-v1` is frozen, so its four keys keep their meanings and its
        # hooks never see these. Read before the v1 keys because the profile is
        # what a copied Claude hook writes.
        if rule.contract != LEGACY_CONTRACT_ID:
            self._parse_profile_ups_output(data, rule, result)
            if result.blocking_error or result.prevent_continuation or result.user_notices \
                    or result.additional_contexts:
                result.messages.append(
                    _make_attachment(
                        "hook_success", {"parsed": "claude-code"},
                        hook_name=rule.command or "", hook_event=rule.event,
                    )
                )
                return

        # Process structured fields.
        if "blockingError" in data:
            result.blocking_error = _cap(str(data["blockingError"]), rule)
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
            result.stop_reason = _cap(
                str(data.get("stopReason", "Hook prevented continuation")), rule,
            )
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
                capped_ctx: Any = _cap(ctx, rule)
                result.additional_contexts.append(capped_ctx)
            elif isinstance(ctx, list):
                capped_ctx = [_cap(str(c), rule) for c in ctx]
                result.additional_contexts.extend(capped_ctx)
            else:
                capped_ctx = ctx
            result.messages.append(
                _make_attachment(
                    "hook_additional_context",
                    # Capped, for the same reason as the non-JSON branch: this
                    # payload becomes a model-visible message.
                    {"context": capped_ctx},
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

    def _parse_profile_ups_output(
        self,
        data: dict,
        rule: ParsedHookRule,
        result: UserPromptSubmitResult,
    ) -> None:
        """The four channels ``UserPromptSubmit`` carries under the profile.

        Four, not one: a blocking decision, context for the model, a turn-level
        stop, and a notice for the **user**. agentao honored one of them, which
        is deviation 2.
        """
        decision = data.get("decision")
        reason = data.get("reason")
        if decision == "block":
            result.blocking_error = _cap(str(reason), rule) if isinstance(reason, str) else "blocked by hook"

        if data.get("continue") is False:
            result.prevent_continuation = True
            stop_reason = data.get("stopReason")
            result.stop_reason = (
                _cap(stop_reason, rule) if isinstance(stop_reason, str)
                else "Hook prevented continuation"
            )

        system_message = data.get("systemMessage")
        if isinstance(system_message, str):
            # → the user. Appending it to ``additional_contexts`` as well is the
            # double-write §4.3 names: the same string would reach the model,
            # which is the one reader the reference says it is not for.
            result.user_notices.append(_cap(system_message, rule))

        nested = data.get("hookSpecificOutput")
        ctx = nested.get("additionalContext") if isinstance(nested, dict) else None
        if isinstance(ctx, str):
            result.additional_contexts.append(_cap(ctx, rule))
        elif isinstance(ctx, list):
            result.additional_contexts.extend(_cap(str(c), rule) for c in ctx)

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
            result.additional_contexts.append(_cap(stdout, rule))
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
            result.additional_contexts.append(_cap(str(data), rule))
            return

        # ``continue: false`` overrides any force_continue-producing field.
        continue_false = data.get("continue") is False

        decision = data.get("decision")
        reason = data.get("reason")
        if decision == "block" and isinstance(reason, str):
            reason = _cap(reason, rule)
            if continue_false:
                result.stop_reason = reason
            else:
                result.force_continue = True
                result.follow_up_message = reason
                result.stop_reason = reason
                result.continuation_contract = rule.contract

        # Capped **once** and reused below. ``cap_channel`` writes a spill file
        # and logs a diagnostic every time it fires, so re-capping the same
        # string for the ``preventContinuation`` branch wrote three copies of
        # one hook's output to disk and logged the budget three times.
        stop_reason = data.get("stopReason")
        capped_stop_reason = _cap(str(stop_reason), rule) if stop_reason else None
        if isinstance(stop_reason, str):
            result.stop_reason = capped_stop_reason

        if data.get("suppressOutput") is True:
            result.suppress_output = True

        system_message = data.get("systemMessage")
        if isinstance(system_message, str):
            system_message = _cap(system_message, rule)
            result.system_message = system_message
            if rule.contract == LEGACY_CONTRACT_ID:
                # The double-write §4.3 names. It stays under `agentao-v1`,
                # which is frozen and whose hooks were written against it; under
                # the profile the field goes to the user and **only** the user,
                # which is what the reference says it is for.
                result.additional_contexts.append(system_message)

        hook_specific = data.get("hookSpecificOutput")
        if isinstance(hook_specific, dict):
            ctx = hook_specific.get("additionalContext")
            collected: list[str] = []
            if isinstance(ctx, str):
                collected = [_cap(ctx, rule)]
            elif isinstance(ctx, list):
                collected = [_cap(str(c), rule) for c in ctx]
            result.additional_contexts.extend(collected)
            # Deviation 4: on `Stop` this context is a **continuation**, not a
            # note. Upstream feeds it back and the conversation goes on, through
            # the same loop protections — the `stop_hook_active` input and the
            # reentry cap — so it lands inside the cap rather than beside it.
            if collected and rule.contract != LEGACY_CONTRACT_ID and not continue_false:
                result.force_continue = True
                result.follow_up_message = "\n".join(collected)
                result.continuation_contract = rule.contract

        # Tolerated for hook scripts that use the top-level field.
        legacy_ctx = data.get("additionalContext")
        if isinstance(legacy_ctx, str):
            result.additional_contexts.append(_cap(legacy_ctx, rule))
        elif isinstance(legacy_ctx, list):
            result.additional_contexts.extend(_cap(str(c), rule) for c in legacy_ctx)

        # ``blockingError`` is independent of ``continue: false``.
        blocking_error = data.get("blockingError")
        if isinstance(blocking_error, str):
            blocking_error = _cap(blocking_error, rule)
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
            reason = capped_stop_reason or "Hook prevented continuation"
            follow_up = capped_stop_reason or "Stop hook requested continuation"
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
