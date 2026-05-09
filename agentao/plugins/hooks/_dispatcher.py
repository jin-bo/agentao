"""``PluginHookDispatcher`` — execute hook commands and prompt hooks.

Owns the hook → subprocess boundary: spawns ``shell=True`` for command
hooks, parses Claude Code's exit-2 + JSON output contract for ``Stop``,
inflates ``UserPromptSubmit`` JSON output into structured
``UserPromptSubmitResult`` fields, and emits attachment records the
``prepare_user_turn`` entry point converts into prompt messages.

Lifecycle dispatchers (``Session*``, ``*ToolUse*``, ``PreCompact``) are
side-effect only — failures produce warnings, never errors. ``Stop``
and ``UserPromptSubmit`` honor full control surfaces (``continue:
false``, ``decision: "block"``, ``preventContinuation``,
``blockingError``) and short-circuit on the first signal.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ..models import (
    CLAUDE_FLAT_EVENTS,
    HookAttachmentRecord,
    ParsedHookRule,
    StopHookResult,
    UserPromptSubmitResult,
)
from ._alias import ToolAliasResolver
from ._attachments import _make_attachment
from ._matchers import _glob_match, _regex_match_full

logger = logging.getLogger(__name__)


class PluginHookDispatcher:
    """Execute hooks for plugin-defined events.

    Supports all lifecycle events with ``command`` hook type.  ``prompt``
    hooks are only supported for ``UserPromptSubmit``.
    """

    def __init__(self, *, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()
        self._alias_resolver = ToolAliasResolver()

    # ------------------------------------------------------------------
    # Lifecycle hook dispatch (side-effect only, Phase 6)
    # ------------------------------------------------------------------

    def dispatch_session_start(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> list[HookAttachmentRecord]:
        return self._dispatch_lifecycle("SessionStart", payload, rules)

    def dispatch_session_end(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> list[HookAttachmentRecord]:
        return self._dispatch_lifecycle("SessionEnd", payload, rules)

    def dispatch_pre_tool_use(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> list[HookAttachmentRecord]:
        return self._dispatch_lifecycle("PreToolUse", payload, rules)

    def dispatch_post_tool_use(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> list[HookAttachmentRecord]:
        return self._dispatch_lifecycle("PostToolUse", payload, rules)

    def dispatch_post_tool_use_failure(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> list[HookAttachmentRecord]:
        return self._dispatch_lifecycle("PostToolUseFailure", payload, rules)

    def dispatch_stop(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> StopHookResult:
        """Run matching Stop hooks; return aggregated control signal.

        Honors Claude Code's full control surface (exit code 2,
        ``decision: "block"``, ``continue: false``, etc.).
        ``result.messages`` carries the per-rule attachment list.
        Idempotent on a pre-filtered ``rules`` list.
        """
        result = StopHookResult()
        stop_rules = self.select_matching_rules("Stop", payload, rules)
        result.matched_rule_count = len(stop_rules)
        for rule in stop_rules:
            if rule.hook_type == "command":
                self._run_stop_command_hook(rule, payload, result)
            if result.blocking_error or result.force_continue:
                break
        return result

    def dispatch_pre_compact(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> list[HookAttachmentRecord]:
        return self._dispatch_lifecycle("PreCompact", payload, rules)

    def select_matching_rules(
        self,
        event: str,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> list[ParsedHookRule]:
        """Canonical Stop / PreCompact selection filter.

        Applies event + is_supported + _matches. Callers use this both to
        count matched rules for the A5 emit gate and to feed an
        already-filtered list to the corresponding dispatch_* method.
        """
        return [
            r for r in rules
            if r.event == event and r.is_supported and self._matches(r, payload)
        ]

    def _dispatch_lifecycle(
        self,
        event: str,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> list[HookAttachmentRecord]:
        """Run all matching command hooks for a lifecycle event.

        These hooks are side-effect only — failures produce warnings, not
        errors, and never change tool input/output.
        """
        attachments: list[HookAttachmentRecord] = []
        matched = [r for r in rules if r.event == event and r.hook_type == "command"]

        for rule in matched:
            if not self._matches(rule, payload):
                continue
            attachment = self._run_lifecycle_command(rule, payload)
            if attachment is not None:
                attachments.append(attachment)

        return attachments

    def _matches(self, rule: ParsedHookRule, payload: dict[str, Any]) -> bool:
        """Check if a rule's matcher applies to this payload."""
        if rule.matcher is None:
            return True

        # Defense-in-depth: parser drops non-dict matchers, but a future
        # caller could construct ParsedHookRule directly. None ≡ "match
        # everything" at the top of this method, so degrading a bad matcher
        # to no-match (rather than match-everything) preserves the user's
        # filter intent.
        if not isinstance(rule.matcher, dict):
            logger.warning(
                "Hook rule for event %r has non-dict matcher %r; "
                "treating as no-match. Matchers must be objects, e.g. "
                "{\"trigger\": \"manual|auto\"}.",
                rule.event, rule.matcher,
            )
            return False

        # Claude-flat events read fields from the top level of the payload.
        event = payload.get("hook_event_name") or rule.event
        if event in CLAUDE_FLAT_EVENTS:
            if event == "PreCompact":
                trigger_pattern = rule.matcher.get("trigger")
                if trigger_pattern is not None:
                    payload_trigger = payload.get("trigger", "")
                    if not _regex_match_full(trigger_pattern, payload_trigger):
                        return False
            # Stop: no documented matcher in Claude Code; always fire.
            return True

        # Agentao-envelope events use the {event, data} shape and globs.
        data = payload.get("data", {})
        tool_name_pattern = rule.matcher.get("toolName")
        if tool_name_pattern is not None:
            payload_tool = data.get("toolName", "")
            if not _glob_match(tool_name_pattern, payload_tool):
                return False

        return True

    def _run_lifecycle_command(
        self, rule: ParsedHookRule, payload: dict[str, Any]
    ) -> HookAttachmentRecord | None:
        """Execute a single lifecycle command hook.  Returns attachment or None."""
        if not rule.command:
            return None

        payload_json = json.dumps(payload)
        try:
            proc = subprocess.run(
                rule.command,
                input=payload_json,
                capture_output=True,
                text=True,
                timeout=rule.timeout,
                shell=True,
                cwd=str(self._cwd),
            )
        except subprocess.TimeoutExpired:
            logger.warning("Lifecycle hook timed out: %s (%s)", rule.event, rule.command)
            return _make_attachment(
                "hook_success",
                {"warning": f"Hook timed out after {rule.timeout}s"},
                hook_name=rule.command,
                hook_event=rule.event,
            )
        except OSError as exc:
            logger.warning("Lifecycle hook failed: %s (%s)", rule.command, exc)
            return None

        if proc.returncode != 0:
            logger.warning(
                "Lifecycle hook exited %d: %s (stderr: %s)",
                proc.returncode, rule.command, proc.stderr[:200],
            )

        return _make_attachment(
            "hook_success",
            {"stdout": proc.stdout.strip(), "returncode": proc.returncode},
            hook_name=rule.command,
            hook_event=rule.event,
        )

    # ------------------------------------------------------------------
    # UserPromptSubmit dispatch (Phase 5)
    # ------------------------------------------------------------------

    def dispatch_user_prompt_submit(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> UserPromptSubmitResult:
        """Execute all ``UserPromptSubmit`` hooks serially.

        Returns an aggregated ``UserPromptSubmitResult``.
        """
        result = UserPromptSubmitResult()

        ups_rules = [r for r in rules if r.event == "UserPromptSubmit" and r.is_supported]

        for rule in ups_rules:
            if rule.hook_type == "command":
                self._run_command_hook(rule, payload, result)
            elif rule.hook_type == "prompt":
                self._run_prompt_hook(rule, payload, result)

            # Short-circuit on blocking error or prevent continuation.
            if result.blocking_error or result.prevent_continuation:
                break

        return result

    # ------------------------------------------------------------------
    # Command hooks
    # ------------------------------------------------------------------

    def _run_command_hook(
        self,
        rule: ParsedHookRule,
        payload: dict[str, Any],
        result: UserPromptSubmitResult,
    ) -> None:
        if not rule.command:
            return

        payload_json = json.dumps(payload)
        try:
            proc = subprocess.run(
                rule.command,
                input=payload_json,
                capture_output=True,
                text=True,
                timeout=rule.timeout,
                shell=True,
                cwd=str(self._cwd),
            )
        except subprocess.TimeoutExpired:
            logger.warning("Hook command timed out after %ds: %s", rule.timeout, rule.command)
            result.messages.append(
                _make_attachment(
                    "hook_success",
                    {"warning": f"Hook timed out after {rule.timeout}s"},
                    hook_name=rule.command,
                    hook_event=rule.event,
                )
            )
            return
        except OSError as exc:
            logger.warning("Hook command failed to run: %s (%s)", rule.command, exc)
            return

        if proc.returncode != 0 and not proc.stdout.strip():
            logger.warning(
                "Hook command exited %d: %s (stderr: %s)",
                proc.returncode, rule.command, proc.stderr[:200],
            )
            result.messages.append(
                _make_attachment(
                    "hook_success",
                    {"warning": f"Hook exited with code {proc.returncode}", "stderr": proc.stderr[:500]},
                    hook_name=rule.command,
                    hook_event=rule.event,
                )
            )
            return

        self._parse_command_output(proc.stdout, rule, result)

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

    # ------------------------------------------------------------------
    # Stop-specific runner (Claude Code exit-2 + JSON contract)
    # ------------------------------------------------------------------

    def _run_stop_command_hook(
        self,
        rule: ParsedHookRule,
        payload: dict[str, Any],
        result: StopHookResult,
    ) -> None:
        """Stop-specific runner.

        Honors Claude Code's exit-code-2 contract (block the stop and
        feed stderr back as the follow-up reason). ``_run_command_hook``
        cannot be reused because it demotes nonzero+empty-stdout to a
        benign warning, which would silently drop the most common Claude
        Stop control signal.
        """
        if not rule.command:
            return

        payload_json = json.dumps(payload)
        try:
            proc = subprocess.run(
                rule.command,
                input=payload_json,
                capture_output=True,
                text=True,
                timeout=rule.timeout,
                shell=True,
                cwd=str(self._cwd),
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Stop hook timed out after %ds: %s", rule.timeout, rule.command,
            )
            result.messages.append(
                _make_attachment(
                    "hook_success",
                    {"warning": f"Hook timed out after {rule.timeout}s"},
                    hook_name=rule.command,
                    hook_event="Stop",
                )
            )
            return
        except OSError as exc:
            logger.warning("Stop hook failed to run: %s (%s)", rule.command, exc)
            return

        # Exit code 2 is checked BEFORE the JSON parser so ``continue:
        # false`` in stdout cannot countermand it (Claude Code precedence).
        if proc.returncode == 2:
            stderr = (proc.stderr or "").strip() or "Stop hook blocked via exit 2"
            result.force_continue = True
            result.follow_up_message = stderr
            result.stop_reason = stderr
            result.messages.append(
                _make_attachment(
                    "hook_stop_blocked_via_exit2",
                    {"stderr": stderr[:500]},
                    hook_name=rule.command,
                    hook_event="Stop",
                )
            )
            return

        # Nonzero exit with no JSON output — not a control signal.
        if proc.returncode != 0 and not (proc.stdout or "").strip():
            logger.warning(
                "Stop hook exited %d: %s (stderr: %s)",
                proc.returncode, rule.command, (proc.stderr or "")[:200],
            )
            result.messages.append(
                _make_attachment(
                    "hook_success",
                    {
                        "warning": f"Hook exited with code {proc.returncode}",
                        "stderr": (proc.stderr or "")[:500],
                    },
                    hook_name=rule.command,
                    hook_event="Stop",
                )
            )
            return

        # JSON path — Claude Code Stop output schema.
        self._parse_stop_command_output(proc.stdout, rule, result)

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

    # ------------------------------------------------------------------
    # Prompt hooks
    # ------------------------------------------------------------------

    def _run_prompt_hook(
        self,
        rule: ParsedHookRule,
        payload: dict[str, Any],
        result: UserPromptSubmitResult,
    ) -> None:
        """Execute a prompt hook.

        Prompt hooks provide their prompt text as additional context —
        they don't run an external command but inject structured content.
        """
        if not rule.prompt:
            return

        # Prompt hooks produce additional context from their prompt text.
        # The prompt may reference ``{userMessage}`` for template expansion.
        user_message = payload.get("data", {}).get("userMessage", "")
        expanded = rule.prompt.replace("{userMessage}", user_message)

        result.additional_contexts.append(expanded)
        result.messages.append(
            _make_attachment(
                "hook_additional_context",
                {"context": expanded, "source": "prompt_hook"},
                hook_name="prompt_hook",
                hook_event=rule.event,
            )
        )
