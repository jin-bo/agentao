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
    PreToolUseHookResult,
    StopHookResult,
    UserPromptSubmitResult,
)
from ._alias import ToolAliasResolver
from ._attachments import _make_attachment
from ._matchers import _glob_match, _regex_match_full
from ._output_parsing import _OutputParsingMixin

logger = logging.getLogger(__name__)


class PluginHookDispatcher(_OutputParsingMixin):
    """Execute hooks for plugin-defined events.

    Supports all lifecycle events with ``command`` hook type.  ``prompt``
    hooks are only supported for ``UserPromptSubmit``.

    The structured-stdout parsers (``_parse_command_output`` /
    ``_parse_stop_command_output``) are provided by :class:`_OutputParsingMixin`.
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
        """Side-effect-only PreToolUse dispatch — returns attachments only.

        .. deprecated::
           Production code uses :meth:`dispatch_pre_tool_use_decision`,
           which also parses the ``permissionDecision`` control surface.
           This wrapper is kept for the lifecycle-dispatch tests.
        """
        return self._dispatch_lifecycle("PreToolUse", payload, rules)

    def dispatch_pre_tool_use_decision(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> PreToolUseHookResult:
        """Run matching PreToolUse hooks; aggregate a permission decision.

        Unlike :meth:`dispatch_pre_tool_use` (side-effect only, returns
        attachments), this parses each hook's stdout for the Claude
        Code-compatible ``hookSpecificOutput.permissionDecision`` shape
        (``allow`` / ``deny`` / ``ask``) and merges the verdicts: the
        first ``deny`` wins; otherwise the first ``ask`` wins; ``allow``
        is a no-op. Stops forking subprocesses once a ``deny`` is seen.
        Exit-code-2 "block" is intentionally NOT honored here — only the
        JSON shape — matching the documented MVP scope. ``additionalContext``
        is parsed and recorded on the result but not injected.
        """
        result = PreToolUseHookResult()
        matched = self.select_matching_rules("PreToolUse", payload, rules)
        result.matched_rule_count = len(matched)
        for rule in matched:
            if rule.hook_type != "command":
                continue
            self._run_pre_tool_use_command(rule, payload, result)
            if result.decision == "deny":
                break
        return result

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

    def _run_subprocess(
        self, rule: ParsedHookRule, payload: dict[str, Any],
    ) -> tuple[subprocess.CompletedProcess[str] | None, bool]:
        """Run ``rule.command`` with the JSON payload on stdin.

        Returns ``(proc, timed_out)``: ``proc`` is the completed process,
        or ``None`` when the command timed out (``timed_out=True``), or
        when it is empty / failed to start (``timed_out=False``). A
        warning is logged on timeout and spawn failure.
        """
        if not rule.command:
            return None, False
        try:
            proc = subprocess.run(
                rule.command,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=rule.timeout,
                shell=True,
                cwd=str(self._cwd),
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "%s hook timed out after %ds: %s", rule.event, rule.timeout, rule.command,
            )
            return None, True
        except OSError as exc:
            logger.warning("%s hook failed to run: %s (%s)", rule.event, rule.command, exc)
            return None, False
        return proc, False

    @staticmethod
    def _timeout_attachment(rule: ParsedHookRule) -> HookAttachmentRecord:
        return _make_attachment(
            "hook_success",
            {"warning": f"Hook timed out after {rule.timeout}s"},
            hook_name=rule.command,
            hook_event=rule.event,
        )

    def _run_lifecycle_command(
        self, rule: ParsedHookRule, payload: dict[str, Any]
    ) -> HookAttachmentRecord | None:
        """Execute a single lifecycle command hook.  Returns attachment or None."""
        proc, timed_out = self._run_subprocess(rule, payload)
        if timed_out:
            return self._timeout_attachment(rule)
        if proc is None:
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
    # PreToolUse decision parsing (Phase 6, decision-capable)
    # ------------------------------------------------------------------

    def _run_pre_tool_use_command(
        self,
        rule: ParsedHookRule,
        payload: dict[str, Any],
        result: PreToolUseHookResult,
    ) -> None:
        """Run one PreToolUse command hook and fold its verdict into ``result``."""
        proc, _timed_out = self._run_subprocess(rule, payload)
        if proc is None:  # empty / timed out / failed to start — warning already logged
            return

        if proc.returncode != 0:
            # MVP scope: exit-code-2 "block" is not honored — surface a
            # warning like other lifecycle hooks. Any JSON on stdout is
            # still parsed below.
            logger.warning(
                "PreToolUse hook exited %d: %s (stderr: %s)",
                proc.returncode, rule.command, (proc.stderr or "")[:200],
            )

        stdout = (proc.stdout or "").strip()
        if not stdout:
            return

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            result.additional_contexts.append(stdout)
            return
        if not isinstance(data, dict):
            result.additional_contexts.append(str(data))
            return

        decision: str | None = None
        reason: str | None = None
        hook_specific = data.get("hookSpecificOutput")
        if isinstance(hook_specific, dict):
            raw_decision = hook_specific.get("permissionDecision")
            if isinstance(raw_decision, str) and raw_decision in ("allow", "deny", "ask"):
                decision = raw_decision
            for key in ("permissionDecisionReason", "reason"):
                rv = hook_specific.get(key)
                if isinstance(rv, str):
                    reason = rv
                    break
            self._harvest_additional_context(hook_specific.get("additionalContext"), result)

        # Tolerate top-level ``reason`` / ``additionalContext`` for hook
        # scripts that don't nest under ``hookSpecificOutput``.
        if reason is None and isinstance(data.get("reason"), str):
            reason = data["reason"]
        self._harvest_additional_context(data.get("additionalContext"), result)

        # ``deny`` always wins (and the caller stops forking further hooks);
        # ``ask`` only takes hold if nothing stronger has been seen.
        if decision == "deny":
            result.decision = "deny"
            result.reason = reason
        elif decision == "ask" and result.decision is None:
            result.decision = "ask"
            result.reason = reason

    @staticmethod
    def _harvest_additional_context(ctx: Any, result: PreToolUseHookResult) -> None:
        if isinstance(ctx, str):
            result.additional_contexts.append(ctx)
        elif isinstance(ctx, list):
            result.additional_contexts.extend(str(c) for c in ctx)

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
        proc, timed_out = self._run_subprocess(rule, payload)
        if timed_out:
            result.messages.append(self._timeout_attachment(rule))
            return
        if proc is None:
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
        proc, timed_out = self._run_subprocess(rule, payload)
        if timed_out:
            result.messages.append(self._timeout_attachment(rule))
            return
        if proc is None:
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
