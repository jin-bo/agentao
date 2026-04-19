"""Hook parsing, payload adapters, dispatch, and user-turn preparation.

This module implements the Claude-compatible subset of hooks:
  - ``ClaudeHooksParser``  — parse hooks.json / inline hook defs
  - ``ToolAliasResolver``  — stable tool-name mapping for payloads
  - ``ClaudeHookPayloadAdapter`` — build event payloads
  - ``PluginHookDispatcher`` — execute hooks (command / prompt)
  - ``prepare_user_turn()`` — top-level entry for UserPromptSubmit
"""

from __future__ import annotations

import json
import logging
import subprocess
import uuid as _uuid
from dataclasses import field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    KNOWN_UNSUPPORTED_HOOK_TYPES,
    SUPPORTED_HOOK_EVENTS,
    SUPPORTED_HOOK_TYPES,
    HookAttachmentRecord,
    LoadedPlugin,
    ParsedHookRule,
    PluginLoadError,
    PluginWarning,
    PluginWarningSeverity,
    PreparedTurnMessage,
    PreparedUserTurn,
    UserPromptSubmitResult,
)

logger = logging.getLogger(__name__)


# =========================================================================
# ClaudeHooksParser
# =========================================================================

class ClaudeHooksParser:
    """Parse Claude-compatible ``hooks.json`` files."""

    def parse_file(
        self, path: Path, *, plugin_name: str = ""
    ) -> tuple[list[ParsedHookRule], list[PluginWarning]]:
        """Parse a hooks JSON file and return ``(rules, warnings)``."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return [], [
                PluginWarning(
                    plugin_name=plugin_name,
                    message=f"Could not parse hooks file {path}: {exc}",
                    field="hooks",
                )
            ]
        return self.parse_dict(raw, plugin_name=plugin_name)

    def parse_dict(
        self, raw: dict[str, Any], *, plugin_name: str = ""
    ) -> tuple[list[ParsedHookRule], list[PluginWarning]]:
        """Parse an already-deserialised hooks dict.

        Expected shape::

            {
              "hooks": {
                "EventName": [
                  { "type": "command", "command": "..." },
                  ...
                ],
                ...
              }
            }

        Also accepts the inner ``hooks`` dict directly (no wrapper).
        """
        warnings: list[PluginWarning] = []
        rules: list[ParsedHookRule] = []

        hooks_dict = raw.get("hooks", raw)
        if not isinstance(hooks_dict, dict):
            warnings.append(
                PluginWarning(
                    plugin_name=plugin_name,
                    message="hooks must be a JSON object",
                    field="hooks",
                )
            )
            return rules, warnings

        for event_name, hook_list in hooks_dict.items():
            if event_name not in SUPPORTED_HOOK_EVENTS:
                warnings.append(
                    PluginWarning(
                        plugin_name=plugin_name,
                        message=f"Unsupported hook event '{event_name}' — skipped",
                        field="hooks",
                    )
                )
                continue

            if not isinstance(hook_list, list):
                hook_list = [hook_list]

            for entry in hook_list:
                if not isinstance(entry, dict):
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=f"Hook entry under '{event_name}' is not an object — skipped",
                            field="hooks",
                        )
                    )
                    continue

                hook_type = entry.get("type", "")
                if hook_type in KNOWN_UNSUPPORTED_HOOK_TYPES:
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=f"Hook type '{hook_type}' under '{event_name}' is not supported — skipped",
                            field="hooks",
                        )
                    )
                    continue

                if hook_type not in SUPPORTED_HOOK_TYPES:
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=f"Unknown hook type '{hook_type}' under '{event_name}' — skipped",
                            field="hooks",
                        )
                    )
                    continue

                try:
                    timeout = int(entry.get("timeout", 60))
                except (ValueError, TypeError):
                    warnings.append(
                        PluginWarning(
                            plugin_name=plugin_name,
                            message=f"Invalid timeout value '{entry.get('timeout')}' under '{event_name}' — using default 60s",
                            field="hooks",
                        )
                    )
                    timeout = 60

                rules.append(
                    ParsedHookRule(
                        event=event_name,
                        hook_type=hook_type,
                        command=entry.get("command"),
                        prompt=entry.get("prompt"),
                        timeout=timeout,
                        matcher=entry.get("matcher"),
                        plugin_name=plugin_name,
                    )
                )

        return rules, warnings


# =========================================================================
# ToolAliasResolver
# =========================================================================

# Agentao tool name -> Claude-compatible alias
_TOOL_ALIASES: dict[str, str] = {
    "read_file": "Read",
    "write_file": "Write",
    "replace": "Edit",
    "run_shell_command": "Bash",
    "glob": "Glob",
    "search_file_content": "Grep",
    "web_fetch": "WebFetch",
    "web_search": "WebSearch",
    "list_directory": "LS",
    "save_memory": "SaveMemory",
    "ask_user": "AskUser",
    "todo_write": "TodoWrite",
    "plan_save": "PlanSave",
    "plan_finalize": "PlanFinalize",
    "activate_skill": "ActivateSkill",
}


class ToolAliasResolver:
    """Bidirectional mapping between Agentao tool names and Claude aliases."""

    def __init__(self, extra: dict[str, str] | None = None) -> None:
        self._to_claude = dict(_TOOL_ALIASES)
        if extra:
            self._to_claude.update(extra)
        self._to_agentao = {v: k for k, v in self._to_claude.items()}

    def to_claude_name(self, agentao_name: str) -> str:
        return self._to_claude.get(agentao_name, agentao_name)

    def to_agentao_name(self, claude_name: str) -> str:
        return self._to_agentao.get(claude_name, claude_name)


# =========================================================================
# ClaudeHookPayloadAdapter
# =========================================================================

class ClaudeHookPayloadAdapter:
    """Build hook payloads in Claude-compatible format."""

    def build_user_prompt_submit(
        self,
        *,
        user_message: str,
        session_id: str | None = None,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        return {
            "event": "UserPromptSubmit",
            "data": {
                "userMessage": user_message,
                "sessionId": session_id or "",
                "cwd": str(cwd or Path.cwd()),
            },
        }

    def build_session_start(
        self, *, session_id: str | None = None, cwd: Path | None = None
    ) -> dict[str, Any]:
        return {
            "event": "SessionStart",
            "data": {
                "sessionId": session_id or "",
                "cwd": str(cwd or Path.cwd()),
            },
        }

    def build_session_end(
        self, *, session_id: str | None = None, cwd: Path | None = None
    ) -> dict[str, Any]:
        return {
            "event": "SessionEnd",
            "data": {
                "sessionId": session_id or "",
                "cwd": str(cwd or Path.cwd()),
            },
        }

    def build_pre_tool_use(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolver = ToolAliasResolver()
        return {
            "event": "PreToolUse",
            "data": {
                "toolName": resolver.to_claude_name(tool_name),
                "toolInput": tool_input or {},
                "sessionId": session_id or "",
            },
        }

    def build_post_tool_use(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        tool_output: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolver = ToolAliasResolver()
        return {
            "event": "PostToolUse",
            "data": {
                "toolName": resolver.to_claude_name(tool_name),
                "toolInput": tool_input or {},
                "toolOutput": tool_output or "",
                "sessionId": session_id or "",
            },
        }

    def build_post_tool_use_failure(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        error: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolver = ToolAliasResolver()
        return {
            "event": "PostToolUseFailure",
            "data": {
                "toolName": resolver.to_claude_name(tool_name),
                "toolInput": tool_input or {},
                "error": error or "",
                "sessionId": session_id or "",
            },
        }


# =========================================================================
# PluginHookDispatcher
# =========================================================================

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

        data = payload.get("data", {})

        # Tool-name matcher (PreToolUse / PostToolUse / PostToolUseFailure).
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


# =========================================================================
# prepare_user_turn — top-level entry point
# =========================================================================

def resolve_all_hook_rules(
    plugins: list[LoadedPlugin],
) -> tuple[list[ParsedHookRule], list[PluginWarning]]:
    """Collect and parse hook rules from all loaded plugins.

    Returns ``(rules, warnings)``.
    """
    parser = ClaudeHooksParser()
    all_rules: list[ParsedHookRule] = []
    all_warnings: list[PluginWarning] = []

    for plugin in plugins:
        for spec in plugin.hook_specs:
            if isinstance(spec, str):
                # File path reference.
                hook_path = (plugin.root_path / spec).resolve()
                if hook_path.is_file():
                    rules, warns = parser.parse_file(hook_path, plugin_name=plugin.name)
                    all_rules.extend(rules)
                    all_warnings.extend(warns)
                else:
                    all_warnings.append(
                        PluginWarning(
                            plugin_name=plugin.name,
                            message=f"Hooks file not found: {hook_path}",
                            field="hooks",
                        )
                    )
            elif isinstance(spec, dict):
                # Inline hooks dict.
                rules, warns = parser.parse_dict(spec, plugin_name=plugin.name)
                all_rules.extend(rules)
                all_warnings.extend(warns)

    return all_rules, all_warnings


def prepare_user_turn(
    *,
    user_message: str,
    plugins: list[LoadedPlugin],
    session_id: str | None = None,
    cwd: Path | None = None,
) -> PreparedUserTurn:
    """Run UserPromptSubmit hooks and build a PreparedUserTurn.

    This is the single entry point that ``agent.chat()`` should call
    before processing the user's message.
    """
    rules, _warnings = resolve_all_hook_rules(plugins)
    ups_rules = [r for r in rules if r.event == "UserPromptSubmit" and r.is_supported]

    if not ups_rules:
        # No hooks — fast path.
        return PreparedUserTurn(
            original_user_message=user_message,
            should_query=True,
        )

    adapter = ClaudeHookPayloadAdapter()
    payload = adapter.build_user_prompt_submit(
        user_message=user_message,
        session_id=session_id,
        cwd=cwd,
    )

    dispatcher = PluginHookDispatcher(cwd=cwd)
    hook_result = dispatcher.dispatch_user_prompt_submit(
        payload=payload,
        rules=ups_rules,
    )

    # Build normalized messages.
    messages: list[PreparedTurnMessage] = []
    for attachment in hook_result.messages:
        messages.append(
            _attachment_to_message(attachment)
        )

    # Determine whether to proceed with the query.
    should_query = True
    stop_reason: str | None = None

    if hook_result.blocking_error:
        should_query = False
        stop_reason = f"Blocked by hook: {hook_result.blocking_error}"
        messages.append(
            PreparedTurnMessage(
                role="user",
                content=f"[Hook blocking error] {hook_result.blocking_error}",
                is_meta=True,
                source="hook",
            )
        )

    if hook_result.prevent_continuation:
        should_query = False
        stop_reason = stop_reason or hook_result.stop_reason or "Hook prevented continuation"

    # Inject additional contexts as meta user messages.
    for ctx in hook_result.additional_contexts:
        messages.append(
            PreparedTurnMessage(
                role="user",
                content=f"[Hook context] {ctx}",
                is_meta=True,
                source="hook",
            )
        )

    # Always include the original user message so the model sees the
    # actual prompt — unless hooks explicitly blocked continuation.
    if should_query:
        messages.append(
            PreparedTurnMessage(
                role="user",
                content=user_message,
                is_meta=False,
                source=None,
            )
        )

    return PreparedUserTurn(
        original_user_message=user_message,
        hook_attachments=hook_result.messages,
        normalized_messages=messages,
        should_query=should_query,
        stop_reason=stop_reason,
    )


# =========================================================================
# Internal helpers
# =========================================================================

def _make_attachment(
    attachment_type: str,
    payload: dict[str, Any],
    *,
    hook_name: str,
    hook_event: str,
) -> HookAttachmentRecord:
    return HookAttachmentRecord(
        attachment_type=attachment_type,
        payload=payload,
        hook_name=hook_name,
        hook_event=hook_event,
        tool_use_id="",
        uuid=str(_uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _attachment_to_message(attachment: HookAttachmentRecord) -> PreparedTurnMessage:
    """Convert a HookAttachmentRecord to a PreparedTurnMessage."""
    content_parts: list[str] = [f"[{attachment.attachment_type}]"]
    if attachment.payload:
        for k, v in attachment.payload.items():
            content_parts.append(f"{k}: {v}")
    return PreparedTurnMessage(
        role="user",
        content=" ".join(content_parts),
        is_meta=True,
        source=f"hook:{attachment.hook_name}",
    )


def _glob_match(pattern: str, value: str) -> bool:
    """Simple glob match: ``*`` matches any substring, otherwise exact."""
    if pattern == "*":
        return True
    if "*" not in pattern:
        return pattern == value
    # Convert simple glob to a prefix/suffix check.
    parts = pattern.split("*")
    if len(parts) == 2:
        return value.startswith(parts[0]) and value.endswith(parts[1])
    # Fallback: use fnmatch.
    import fnmatch
    return fnmatch.fnmatch(value, pattern)
