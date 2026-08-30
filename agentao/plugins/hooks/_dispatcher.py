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

from ...capabilities.process import OutputLimitExceeded, build_child_env, run_captured
from ..models import (
    LifecycleHookResult,
    CLAUDE_FLAT_EVENTS,
    HookAttachmentRecord,
    ParsedHookRule,
    PreCompactHookResult,
    PreToolUseHookResult,
    StopHookResult,
    UserPromptSubmitResult,
)
from ._alias import ToolAliasResolver
from ._attachments import _make_attachment
from ._budget import HOOK_RAW_OUTPUT_LIMIT_BYTES
from ._paths import _placeholder_values, _substitute
from ._profile import (
    LEGACY_CONTRACT_ID,
    PERMISSION_DECISION_DEGRADES,
    PROFILE_ID,
    exit2_outcome,
    field_disposition,
    honors_continue,
    honors_system_message,
)
from ._profile_payload import to_profile_payload
from ._matchers import _claude_matcher_match, _glob_match, _regex_match_full
from ._output_parsing import _cap, _OutputParsingMixin

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
    ) -> LifecycleHookResult:
        return self._dispatch_lifecycle("SessionStart", payload, rules)

    def dispatch_session_end(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> LifecycleHookResult:
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
    ) -> LifecycleHookResult:
        return self._dispatch_lifecycle("PostToolUse", payload, rules)

    def dispatch_post_tool_use_failure(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> LifecycleHookResult:
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
    ) -> LifecycleHookResult:
        return self._dispatch_lifecycle("PreCompact", payload, rules)

    def dispatch_pre_compact_decision(
        self,
        *,
        payload: dict[str, Any],
        rules: list[ParsedHookRule],
    ) -> PreCompactHookResult:
        """Run matching PreCompact hooks; aggregate a cancel/allow decision.

        Sibling of :meth:`dispatch_pre_compact`, which is side-effect only
        and does not read stdout at all. This one parses each hook's stdout
        for ``hookSpecificOutput.compactionDecision`` and merges: the first
        ``cancel`` wins and stops the remaining forks; ``allow`` is a no-op.

        Everything that is not an explicit ``cancel`` means allow — a missing
        key, a missing ``hookSpecificOutput``, non-JSON stdout, a script that
        prints nothing, a non-zero exit, an unknown value. A control plane
        that fails must not be able to pause compaction indefinitely.
        """
        result = PreCompactHookResult()
        matched = self.select_matching_rules("PreCompact", payload, rules)
        result.matched_rule_count = len(matched)
        for rule in matched:
            if rule.hook_type != "command":
                continue
            self._run_pre_compact_command(rule, payload, result)
            if result.decision == "cancel":
                break
        return result

    def _run_pre_compact_command(
        self,
        rule: ParsedHookRule,
        payload: dict[str, Any],
        result: PreCompactHookResult,
    ) -> None:
        """Run one PreCompact command hook and fold its verdict into ``result``."""
        proc, _timed_out = self._run_subprocess(rule, payload)
        if proc is None:  # empty / timed out / failed to start — already logged
            return

        if proc.returncode != 0:
            # Exit-code 2 is not honoured here either — only the JSON shape,
            # matching ``dispatch_pre_tool_use_decision``. Any JSON on stdout
            # is still parsed below.
            logger.warning(
                "PreCompact hook exited %d: %s (stderr: %s)",
                proc.returncode, rule.command, (proc.stderr or "")[:200],
            )

        stdout = (proc.stdout or "").strip()
        if not stdout:
            return
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return

        hook_specific = data.get("hookSpecificOutput")
        if not isinstance(hook_specific, dict):
            return
        raw = hook_specific.get("compactionDecision")
        if raw is None:
            return
        if raw != "cancel":
            if raw != "allow":
                logger.warning(
                    "PreCompact hook returned an unknown compactionDecision %r: %s "
                    "— treating as allow",
                    raw, rule.command,
                )
            return

        reason: str | None = None
        for key in ("compactionDecisionReason", "reason"):
            rv = hook_specific.get(key)
            if isinstance(rv, str):
                reason = rv
                break
        result.decision = "cancel"
        result.reason = reason

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
    ) -> LifecycleHookResult:
        """Run all matching command hooks for a lifecycle event.

        Returns a result rather than a bare attachment list: these four events
        have channels the reference defines — exit-2 stderr, `additionalContext`,
        `systemMessage` — and a caller that receives only attachments cannot
        route any of them.
        """
        result = LifecycleHookResult()
        matched = [r for r in rules if r.event == event and r.hook_type == "command"]

        for rule in matched:
            if not self._matches(rule, payload):
                continue
            result.matched_rule_count += 1
            proc, failure = self._run_subprocess(rule, payload)
            if failure:
                result.attachments.append(self._timeout_attachment(rule, failure))
                continue
            if proc is None:
                continue
            self._route_lifecycle_output(event, rule, proc, result)
            result.attachments.append(
                _make_attachment(
                    "hook_success",
                    {"stdout": _cap(proc.stdout.strip(), rule),
                     "returncode": proc.returncode},
                    hook_name=rule.command,
                    hook_event=rule.event,
                )
            )

        return result

    def _route_lifecycle_output(
        self,
        event: str,
        rule: ParsedHookRule,
        proc: subprocess.CompletedProcess[str],
        result: LifecycleHookResult,
    ) -> None:
        """Route one lifecycle hook's output per the profile's tables.

        A **narrow** interpreter, deliberately: step 6 replaces it with the full
        ``resolve()`` over five stdout states and three exit-code branches. What
        it must not do meanwhile is contradict the tables, so every branch here
        asks ``_profile`` rather than deciding for itself.
        """
        if rule.contract == LEGACY_CONTRACT_ID:
            # `agentao-v1` is frozen: these events were side-effect only and stay
            # that way. Only a profile rule gets the new channels.
            if proc.returncode != 0:
                logger.warning(
                    "Lifecycle hook exited %s: %s (stderr: %s)",
                    proc.returncode, rule.command, proc.stderr[:200],
                )
            return

        stderr = (proc.stderr or "").strip()
        if proc.returncode == 2:
            # Exit 2 is not a boolean: three outcomes, and all three are live
            # across the eight events.
            outcome = exit2_outcome(event)
            if outcome == "user_notice" and stderr:
                result.user_notices.append(_cap(stderr, rule))
            elif outcome == "model_feedback" and stderr:
                result.model_contexts.append(_cap(stderr, rule))
            elif outcome == "block" and stderr:
                result.stop_reason = result.stop_reason or _cap(stderr, rule)

        stdout = (proc.stdout or "").strip()
        data: dict[str, Any] | None = None
        if stdout.startswith("{") and stdout.endswith("}"):
            try:
                loaded = json.loads(stdout)
                data = loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                data = None

        if data is None:
            if proc.returncode not in (0, 2) and stderr:
                first_line = stderr.splitlines()[0]
                result.user_notices.append(
                    f"{event} hook error: Failed with non-blocking status code: "
                    f"{proc.returncode} {first_line}"
                )
            return

        system_message = data.get("systemMessage")
        if isinstance(system_message, str) and honors_system_message(event):
            result.user_notices.append(_cap(system_message, rule))

        if field_disposition("hookSpecificOutput.additionalContext", event) == "accept":
            nested = data.get("hookSpecificOutput")
            ctx = nested.get("additionalContext") if isinstance(nested, dict) else None
            if isinstance(ctx, str):
                result.model_contexts.append(_cap(ctx, rule))
            elif isinstance(ctx, list):
                result.model_contexts.extend(_cap(str(c), rule) for c in ctx)

        if data.get("continue") is False and honors_continue(event):
            reason = data.get("stopReason")
            result.stop_reason = _cap(str(reason), rule) if isinstance(reason, str) else ""

    def _matches(self, rule: ParsedHookRule, payload: dict[str, Any]) -> bool:
        """Check if a rule's matcher applies to this payload."""
        # A profile rule carries a *string* matcher, evaluated by Claude's own
        # semantics — an anchored full match with ``*`` as a wildcard. It is
        # deliberately not mapped onto the dict matcher below: that one globs, so
        # ``Edit|Write`` would be compared literally and fire for nothing.
        if rule.contract != LEGACY_CONTRACT_ID:
            if rule.matcher_pattern is None:
                return True
            return _claude_matcher_match(rule.matcher_pattern, self._matched_name(rule, payload))

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

    @staticmethod
    def _matched_name(rule: ParsedHookRule, payload: dict[str, Any]) -> str:
        """What a profile matcher is matched against, per event.

        Tool events match on the tool name; ``PreCompact`` matches on its
        trigger, the only other thing the reference gives a matcher. Events with
        no documented matcher fall through to the empty string, where any
        non-``*`` pattern simply fails — the conservative direction, since an
        author who wrote a filter meant to narrow.
        """
        event = payload.get("hook_event_name") or rule.event
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if event == "PreCompact":
            return str(payload.get("trigger", ""))
        # Measured, not assumed (probe §G6): a `SessionStart` matcher is
        # compared against `source` — "startup" fires, "resume" does not — and a
        # `SessionEnd` matcher against `reason`. Returning "" for these events
        # made every non-`*` matcher on them silently dead, which is the exact
        # failure §2.3 gives as the reason not to translate matchers.
        if event == "SessionStart":
            return str(payload.get("source") or data.get("source") or "")
        if event == "SessionEnd":
            return str(payload.get("reason") or data.get("reason") or "")
        return str(payload.get("tool_name") or data.get("toolName") or "")

    def _run_subprocess(
        self, rule: ParsedHookRule, payload: dict[str, Any],
    ) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
        """Run ``rule.command`` with the JSON payload on stdin.

        Returns ``(proc, failure)``: ``proc`` is the completed process, or
        ``None`` when the hook produced no usable result. ``failure`` names
        why — ``"timeout"``, ``"output_budget"``, or ``None`` for an empty
        command / spawn failure, which stay silent as they always did.

        ``failure`` is a string where it used to be a bool. Every call site
        tests it for truthiness, so the three-state value is compatible by
        construction — and the two failures that *are* the hook's own doing
        now reach the user distinguishably instead of both reading "timed
        out".
        """
        if not rule.command and not rule.args:
            return None, None
        try:
            # Shared hardened runner: feeds the JSON payload on a stdin pipe
            # (so the user command can't read the host's real stdin) and, on
            # timeout, kills the whole process tree rather than just the
            # shell — a hook that backgrounds a child would otherwise keep
            # the captured pipe open and hang dispatch past ``rule.timeout``.
            # One rule, one contract, one wire shape. A profile rule gets the
            # flat snake_case payload of §5.3; a v1 rule gets today's envelope,
            # frozen. Never both in one payload — that would be a third contract.
            wire_payload = (
                to_profile_payload(payload)
                if rule.contract != LEGACY_CONTRACT_ID
                else payload
            )
            placeholders = _placeholder_values(rule, self._cwd)
            # Substitution is a *profile* feature (§2.4), and ``agentao-v1`` is
            # frozen: a v1 command must still reach the shell byte-for-byte.
            # Nothing is lost by it — the same three names are exported on the
            # child's environment below, so ``${CLAUDE_PROJECT_DIR}`` in a v1
            # command still resolves, expanded by the shell exactly as before.
            subs = placeholders if rule.contract != LEGACY_CONTRACT_ID else {}
            if rule.args:
                # Exec form: no shell, each element one argument. The reference
                # tells authors to use it whenever a hook takes a path — which is
                # exactly when a shell is the thing that breaks it.
                cmd: Any = [_substitute(rule.command or "", subs)] + [
                    _substitute(a, subs) for a in rule.args
                ]
                use_shell = False
            else:
                cmd = _substitute(rule.command or "", subs)
                use_shell = True
            proc = run_captured(
                cmd,
                input=json.dumps(wire_payload),
                timeout=rule.timeout,
                shell=use_shell,
                cwd=str(self._cwd),
                # The only correct spelling. ``env={...}`` or
                # ``env=os.environ | {...}`` silently deletes the provider-key
                # scrub ``run_captured`` applies when ``env`` is omitted — one of
                # the five places agentao leads both peers. ``build_child_env``
                # applies overrides *after* the scrub, by construction.
                env=build_child_env(placeholders),
                # Tier 1 (§6). ``communicate()`` reads to EOF, so without a
                # ceiling here a hook that prints without stopping is bounded
                # only by the host's memory — and it exhausts it long before
                # any parser exists to apply the semantic cap.
                max_output_bytes=HOOK_RAW_OUTPUT_LIMIT_BYTES,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "%s hook timed out after %ds: %s", rule.event, rule.timeout, rule.command,
            )
            return None, "timeout"
        except OutputLimitExceeded as exc:
            # Not a truncation. A hook cut off mid-JSON has no decision left to
            # contribute, and pretending otherwise turns a resource failure
            # into a silent semantic one.
            logger.warning(
                "%s hook exceeded the raw output budget: %s (%s)",
                rule.event, rule.command, exc,
            )
            return None, "output_budget"
        except OSError as exc:
            logger.warning("%s hook failed to run: %s (%s)", rule.event, rule.command, exc)
            return None, None
        return proc, None

    @staticmethod
    def _timeout_attachment(
        rule: ParsedHookRule, failure: str = "timeout",
    ) -> HookAttachmentRecord:
        """Attachment for a hook that produced nothing usable.

        Kept under its original name because every call site and the tests
        reach it that way; ``failure`` distinguishes the two causes so the
        user is not told a hook "timed out" when it was killed for flooding
        its pipe.
        """
        warning = (
            f"Hook timed out after {rule.timeout}s"
            if failure == "timeout"
            else f"Hook killed: output exceeded {HOOK_RAW_OUTPUT_LIMIT_BYTES:,} bytes"
        )
        return _make_attachment(
            "hook_success",
            {"warning": warning},
            hook_name=rule.command,
            hook_event=rule.event,
        )

    def _run_lifecycle_command(
        self, rule: ParsedHookRule, payload: dict[str, Any]
    ) -> HookAttachmentRecord | None:
        """Execute a single lifecycle command hook.  Returns attachment or None."""
        proc, failure = self._run_subprocess(rule, payload)
        if failure:
            return self._timeout_attachment(rule, failure)
        if proc is None:
            return None

        if proc.returncode != 0:
            logger.warning(
                "Lifecycle hook exited %d: %s (stderr: %s)",
                proc.returncode, rule.command, proc.stderr[:200],
            )

        return _make_attachment(
            "hook_success",
            # Capped: an attachment payload is rendered verbatim into a
            # model-visible message by ``_attachment_to_message``, and tier 1
            # lets a well-behaved hook print megabytes.
            {"stdout": _cap(proc.stdout.strip(), rule), "returncode": proc.returncode},
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
        proc, _failure = self._run_subprocess(rule, payload)
        if proc is None:  # empty / timed out / failed to start — warning already logged
            return

        profile = rule.contract != LEGACY_CONTRACT_ID
        if proc.returncode == 2 and profile:
            # Exit 2 blocks on this event, and the JSON is still read: its
            # blocking reason wins when it has one, stderr otherwise. `agentao-v1`
            # keeps the old warning-only behavior, which is what its hooks expect.
            result.decision = "deny"
            result.reason = _cap((proc.stderr or "").strip() or "blocked by hook (exit 2)", rule)
        elif proc.returncode != 0:
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
            elif raw_decision in PERMISSION_DECISION_DEGRADES and profile:
                # A value agentao cannot honor sits in exactly the position of a
                # field it cannot honor: accept, ignore, or **degrade to a named
                # alternative** — never "reject", which would discard every
                # sibling field in the same object. The reason says which value
                # was degraded, because silently substituting one permission
                # verdict for another is the worst of the three outcomes.
                decision = PERMISSION_DECISION_DEGRADES[raw_decision]
                reason = (
                    f"hook returned permissionDecision {raw_decision!r}, which "
                    f"{PROFILE_ID} does not implement — degraded to "
                    f"{decision!r}"
                )
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

        if profile:
            updated = hook_specific.get("updatedInput") if isinstance(hook_specific, dict) else None
            if isinstance(updated, dict) and result.updated_tool_input is None:
                result.updated_tool_input = dict(updated)
            if data.get("continue") is False:
                stop_reason = data.get("stopReason")
                result.stop_reason = (
                    _cap(stop_reason, rule) if isinstance(stop_reason, str) else ""
                )

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
        proc, failure = self._run_subprocess(rule, payload)
        if failure:
            result.messages.append(self._timeout_attachment(rule, failure))
            return
        if proc is None:
            return

        if rule.contract != LEGACY_CONTRACT_ID and proc.returncode == 2:
            # Exit 2 blocks on this event, and it blocks **whether or not** JSON
            # was printed: "even a JSON permissionDecision of allow can't
            # override it". The JSON's own reason wins when it has one; stderr
            # is the fallback.
            self._parse_command_output(proc.stdout, rule, result)
            if not result.blocking_error:
                result.blocking_error = _cap(
                    (proc.stderr or "").strip() or "blocked by hook (exit 2)", rule,
                )
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
        proc, failure = self._run_subprocess(rule, payload)
        if failure:
            result.messages.append(self._timeout_attachment(rule, failure))
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
