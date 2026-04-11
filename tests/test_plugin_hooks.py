"""Tests for Phase 5: hooks parser, payload adapters, dispatch, and prepare_user_turn."""

import json
import os
import stat
import textwrap
from pathlib import Path
from typing import Any

import pytest

from agentao.plugins.hooks import (
    ClaudeHookPayloadAdapter,
    ClaudeHooksParser,
    PluginHookDispatcher,
    ToolAliasResolver,
    prepare_user_turn,
    resolve_all_hook_rules,
)
from agentao.plugins.models import (
    HookAttachmentRecord,
    LoadedPlugin,
    ParsedHookRule,
    PluginManifest,
    PreparedTurnMessage,
    PreparedUserTurn,
    UserPromptSubmitResult,
)


# ======================================================================
# ClaudeHooksParser
# ======================================================================


class TestClaudeHooksParser:
    def test_parse_valid_hooks_json(self, tmp_path):
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {"type": "command", "command": "echo hello"},
                ],
            }
        }), encoding="utf-8")

        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_file(hooks_file, plugin_name="test")
        assert warnings == []
        assert len(rules) == 1
        assert rules[0].event == "UserPromptSubmit"
        assert rules[0].hook_type == "command"
        assert rules[0].command == "echo hello"
        assert rules[0].plugin_name == "test"

    def test_parse_prompt_hook(self, tmp_path):
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {"type": "prompt", "prompt": "Always be helpful."},
                ],
            }
        }), encoding="utf-8")

        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_file(hooks_file, plugin_name="test")
        assert len(rules) == 1
        assert rules[0].hook_type == "prompt"
        assert rules[0].prompt == "Always be helpful."

    def test_unsupported_hook_type_warns(self):
        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_dict({
            "hooks": {
                "UserPromptSubmit": [
                    {"type": "http", "url": "https://example.com"},
                ],
            }
        }, plugin_name="test")
        assert rules == []
        assert len(warnings) == 1
        assert "http" in warnings[0].message
        assert "not supported" in warnings[0].message.lower()

    def test_unsupported_event_warns(self):
        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_dict({
            "hooks": {
                "UnknownEvent": [
                    {"type": "command", "command": "echo"},
                ],
            }
        }, plugin_name="test")
        assert rules == []
        assert len(warnings) == 1
        assert "UnknownEvent" in warnings[0].message

    def test_unknown_hook_type_warns(self):
        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_dict({
            "hooks": {
                "UserPromptSubmit": [
                    {"type": "alien_type", "data": 42},
                ],
            }
        }, plugin_name="test")
        assert rules == []
        assert len(warnings) == 1
        assert "alien_type" in warnings[0].message

    def test_multiple_events_and_rules(self):
        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_dict({
            "hooks": {
                "UserPromptSubmit": [
                    {"type": "command", "command": "cmd1"},
                    {"type": "prompt", "prompt": "p1"},
                ],
                "PreToolUse": [
                    {"type": "command", "command": "cmd2"},
                ],
            }
        }, plugin_name="test")
        assert warnings == []
        assert len(rules) == 3

    def test_inner_dict_without_wrapper(self):
        """Accept hooks dict directly (no outer 'hooks' key)."""
        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_dict({
            "UserPromptSubmit": [
                {"type": "command", "command": "echo hi"},
            ],
        }, plugin_name="test")
        assert len(rules) == 1

    def test_malformed_hooks_file(self, tmp_path):
        hooks_file = tmp_path / "bad.json"
        hooks_file.write_text("{bad", encoding="utf-8")
        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_file(hooks_file, plugin_name="test")
        assert rules == []
        assert len(warnings) == 1

    def test_single_hook_not_in_array(self):
        """Accept a single hook object instead of a list."""
        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_dict({
            "hooks": {
                "UserPromptSubmit": {"type": "command", "command": "echo"},
            }
        }, plugin_name="test")
        assert len(rules) == 1

    def test_timeout_parsed(self):
        parser = ClaudeHooksParser()
        rules, _ = parser.parse_dict({
            "hooks": {
                "UserPromptSubmit": [
                    {"type": "command", "command": "slow", "timeout": 120},
                ],
            }
        }, plugin_name="test")
        assert rules[0].timeout == 120

    def test_is_supported_property(self):
        rule = ParsedHookRule(event="UserPromptSubmit", hook_type="command", command="echo")
        assert rule.is_supported

        rule2 = ParsedHookRule(event="UserPromptSubmit", hook_type="http")
        assert not rule2.is_supported

        rule3 = ParsedHookRule(event="UnknownEvent", hook_type="command")
        assert not rule3.is_supported


# ======================================================================
# ToolAliasResolver
# ======================================================================


class TestToolAliasResolver:
    def test_known_aliases(self):
        resolver = ToolAliasResolver()
        assert resolver.to_claude_name("read_file") == "Read"
        assert resolver.to_claude_name("write_file") == "Write"
        assert resolver.to_claude_name("replace") == "Edit"
        assert resolver.to_claude_name("run_shell_command") == "Bash"
        assert resolver.to_claude_name("glob") == "Glob"
        assert resolver.to_claude_name("search_file_content") == "Grep"

    def test_unknown_passthrough(self):
        resolver = ToolAliasResolver()
        assert resolver.to_claude_name("custom_tool") == "custom_tool"

    def test_reverse_lookup(self):
        resolver = ToolAliasResolver()
        assert resolver.to_agentao_name("Read") == "read_file"
        assert resolver.to_agentao_name("Bash") == "run_shell_command"

    def test_extra_aliases(self):
        resolver = ToolAliasResolver(extra={"my_tool": "MyTool"})
        assert resolver.to_claude_name("my_tool") == "MyTool"
        assert resolver.to_agentao_name("MyTool") == "my_tool"


# ======================================================================
# ClaudeHookPayloadAdapter
# ======================================================================


class TestPayloadAdapter:
    def test_user_prompt_submit_payload(self):
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_user_prompt_submit(
            user_message="fix the bug",
            session_id="sess-123",
            cwd=Path("/project"),
        )
        assert payload["event"] == "UserPromptSubmit"
        assert payload["data"]["userMessage"] == "fix the bug"
        assert payload["data"]["sessionId"] == "sess-123"
        assert payload["data"]["cwd"] == "/project"

    def test_pre_tool_use_payload(self):
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_pre_tool_use(
            tool_name="read_file",
            tool_input={"path": "/tmp/x"},
            session_id="s1",
        )
        assert payload["event"] == "PreToolUse"
        assert payload["data"]["toolName"] == "Read"  # aliased
        assert payload["data"]["toolInput"]["path"] == "/tmp/x"

    def test_post_tool_use_payload(self):
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_post_tool_use(
            tool_name="run_shell_command",
            tool_output="ok",
        )
        assert payload["data"]["toolName"] == "Bash"
        assert payload["data"]["toolOutput"] == "ok"

    def test_post_tool_use_failure_payload(self):
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_post_tool_use_failure(
            tool_name="write_file",
            error="Permission denied",
        )
        assert payload["data"]["toolName"] == "Write"
        assert payload["data"]["error"] == "Permission denied"

    def test_session_payloads(self):
        adapter = ClaudeHookPayloadAdapter()
        start = adapter.build_session_start(session_id="s")
        assert start["event"] == "SessionStart"
        end = adapter.build_session_end(session_id="s")
        assert end["event"] == "SessionEnd"


# ======================================================================
# PluginHookDispatcher — command hooks
# ======================================================================


class TestCommandHookDispatch:
    def test_command_hook_executes(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="command",
            command='echo \'{"additionalContext": "extra info"}\'',
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "hi"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])

        assert not result.blocking_error
        assert "extra info" in result.additional_contexts
        assert any(a.attachment_type == "hook_additional_context" for a in result.messages)

    def test_blocking_error_suppresses_query(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="command",
            command='echo \'{"blockingError": "not allowed"}\'',
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "bad"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])

        assert result.blocking_error == "not allowed"
        assert any(a.attachment_type == "hook_blocking_error" for a in result.messages)

    def test_prevent_continuation(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="command",
            command='echo \'{"preventContinuation": true, "stopReason": "paused"}\'',
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "x"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])

        assert result.prevent_continuation
        assert result.stop_reason == "paused"
        assert any(a.attachment_type == "hook_stopped_continuation" for a in result.messages)

    def test_non_json_output_becomes_context(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="command",
            command="echo 'plain text output'",
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "hi"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])

        assert "plain text output" in result.additional_contexts

    def test_empty_output_is_success(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="command",
            command="true",  # exits 0, no output
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "hi"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])

        assert not result.blocking_error
        assert any(a.attachment_type == "hook_success" for a in result.messages)

    def test_nonzero_exit_without_output_warns(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="command",
            command="exit 1",
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "hi"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])

        # Should produce a warning attachment, not crash.
        assert any("warning" in str(a.payload).lower() for a in result.messages)

    def test_timeout_produces_warning(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="command",
            command="sleep 60",
            timeout=1,
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "hi"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])

        assert any("timeout" in str(a.payload).lower() or "timed out" in str(a.payload).lower()
                    for a in result.messages)

    def test_payload_contains_user_message(self, tmp_path):
        """Verify the payload piped to the command includes the user message."""
        script = tmp_path / "check.sh"
        script.write_text(
            '#!/bin/sh\ncat | python3 -c "import sys,json; d=json.load(sys.stdin); '
            'print(json.dumps({\'additionalContext\': d[\'data\'][\'userMessage\']}))"',
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="command",
            command=f"sh {script}",
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "secret-msg"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])
        assert "secret-msg" in result.additional_contexts

    def test_only_ups_rules_executed(self, tmp_path):
        """Non-UserPromptSubmit rules are filtered out."""
        ups_rule = ParsedHookRule(
            event="UserPromptSubmit", hook_type="command",
            command='echo \'{"additionalContext": "yes"}\'', plugin_name="t",
        )
        other_rule = ParsedHookRule(
            event="PreToolUse", hook_type="command",
            command='echo \'{"additionalContext": "no"}\'', plugin_name="t",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": ""}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[ups_rule, other_rule])
        assert "yes" in result.additional_contexts
        assert "no" not in result.additional_contexts


# ======================================================================
# PluginHookDispatcher — prompt hooks
# ======================================================================


class TestPromptHookDispatch:
    def test_prompt_hook_injects_context(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="prompt",
            prompt="Always respond in JSON.",
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "hi"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])

        assert "Always respond in JSON." in result.additional_contexts
        assert any(a.attachment_type == "hook_additional_context" for a in result.messages)

    def test_prompt_hook_template_expansion(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="prompt",
            prompt="User said: {userMessage}",
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "hello world"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])

        assert "User said: hello world" in result.additional_contexts

    def test_prompt_hook_empty_prompt_skipped(self, tmp_path):
        rule = ParsedHookRule(
            event="UserPromptSubmit",
            hook_type="prompt",
            prompt=None,
            plugin_name="test",
        )
        payload = {"event": "UserPromptSubmit", "data": {"userMessage": "hi"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        result = dispatcher.dispatch_user_prompt_submit(payload=payload, rules=[rule])
        assert result.additional_contexts == []


# ======================================================================
# prepare_user_turn
# ======================================================================


class TestPrepareUserTurn:
    def test_no_hooks_fast_path(self, tmp_path):
        plugin = LoadedPlugin(
            name="bare", version=None, root_path=tmp_path,
            source="project", manifest=PluginManifest(name="bare"),
        )
        turn = prepare_user_turn(user_message="hello", plugins=[plugin], cwd=tmp_path)

        assert turn.original_user_message == "hello"
        assert turn.should_query is True
        assert turn.hook_attachments == []
        assert turn.normalized_messages == []

    def test_command_hook_additional_context(self, tmp_path):
        # Create a plugin with inline hooks that produce additional context.
        plugin = LoadedPlugin(
            name="ctx-plug", version=None, root_path=tmp_path,
            source="project",
            manifest=PluginManifest(name="ctx-plug"),
            hook_specs=[{
                "hooks": {
                    "UserPromptSubmit": [
                        {"type": "command", "command": "echo '{\"additionalContext\": \"injected\"}'"},
                    ],
                },
            }],
        )
        turn = prepare_user_turn(user_message="test", plugins=[plugin], cwd=tmp_path)

        assert turn.should_query is True
        assert any("injected" in m.content for m in turn.normalized_messages)

    def test_blocking_error_prevents_query(self, tmp_path):
        plugin = LoadedPlugin(
            name="block-plug", version=None, root_path=tmp_path,
            source="project",
            manifest=PluginManifest(name="block-plug"),
            hook_specs=[{
                "hooks": {
                    "UserPromptSubmit": [
                        {"type": "command", "command": "echo '{\"blockingError\": \"denied\"}'"},
                    ],
                },
            }],
        )
        turn = prepare_user_turn(user_message="bad", plugins=[plugin], cwd=tmp_path)

        assert turn.should_query is False
        assert "denied" in (turn.stop_reason or "")

    def test_prevent_continuation_prevents_query(self, tmp_path):
        plugin = LoadedPlugin(
            name="stop-plug", version=None, root_path=tmp_path,
            source="project",
            manifest=PluginManifest(name="stop-plug"),
            hook_specs=[{
                "hooks": {
                    "UserPromptSubmit": [
                        {"type": "command", "command": "echo '{\"preventContinuation\": true}'"},
                    ],
                },
            }],
        )
        turn = prepare_user_turn(user_message="x", plugins=[plugin], cwd=tmp_path)

        assert turn.should_query is False

    def test_prompt_hook_via_prepare(self, tmp_path):
        plugin = LoadedPlugin(
            name="prompt-plug", version=None, root_path=tmp_path,
            source="project",
            manifest=PluginManifest(name="prompt-plug"),
            hook_specs=[{
                "hooks": {
                    "UserPromptSubmit": [
                        {"type": "prompt", "prompt": "Be concise."},
                    ],
                },
            }],
        )
        turn = prepare_user_turn(user_message="hi", plugins=[plugin], cwd=tmp_path)

        assert turn.should_query is True
        assert any("Be concise" in m.content for m in turn.normalized_messages)

    def test_file_based_hooks(self, tmp_path):
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "hooks.json").write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {"type": "command", "command": "echo '{\"additionalContext\": \"from-file\"}'"},
                ],
            }
        }), encoding="utf-8")

        plugin = LoadedPlugin(
            name="file-plug", version=None, root_path=tmp_path,
            source="project",
            manifest=PluginManifest(name="file-plug"),
            hook_specs=["./hooks/hooks.json"],
        )
        turn = prepare_user_turn(user_message="hi", plugins=[plugin], cwd=tmp_path)

        assert turn.should_query is True
        assert any("from-file" in m.content for m in turn.normalized_messages)

    def test_original_message_preserved(self, tmp_path):
        """Original user message is included as the final non-meta message."""
        plugin = LoadedPlugin(
            name="plug", version=None, root_path=tmp_path,
            source="project",
            manifest=PluginManifest(name="plug"),
            hook_specs=[{
                "hooks": {
                    "UserPromptSubmit": [
                        {"type": "prompt", "prompt": "extra context"},
                    ],
                },
            }],
        )
        turn = prepare_user_turn(user_message="original text", plugins=[plugin], cwd=tmp_path)

        assert turn.original_user_message == "original text"
        assert turn.should_query is True
        # The last message should be the original user message (non-meta).
        non_meta = [m for m in turn.normalized_messages if not m.is_meta]
        assert len(non_meta) == 1
        assert non_meta[0].content == "original text"
        assert non_meta[0].role == "user"

    def test_blocked_turn_excludes_user_message(self, tmp_path):
        """When hooks block, the original user message is NOT in normalized_messages."""
        plugin = LoadedPlugin(
            name="block-plug", version=None, root_path=tmp_path,
            source="project",
            manifest=PluginManifest(name="block-plug"),
            hook_specs=[{
                "hooks": {
                    "UserPromptSubmit": [
                        {"type": "command", "command": "echo '{\"blockingError\": \"nope\"}'"},
                    ],
                },
            }],
        )
        turn = prepare_user_turn(user_message="bad input", plugins=[plugin], cwd=tmp_path)

        assert turn.should_query is False
        non_meta = [m for m in turn.normalized_messages if not m.is_meta]
        assert non_meta == []


# ======================================================================
# resolve_all_hook_rules
# ======================================================================


class TestResolveAllHookRules:
    def test_collects_from_multiple_plugins(self, tmp_path):
        p1 = LoadedPlugin(
            name="p1", version=None, root_path=tmp_path,
            source="project", manifest=PluginManifest(name="p1"),
            hook_specs=[{"hooks": {"UserPromptSubmit": [{"type": "command", "command": "c1"}]}}],
        )
        p2 = LoadedPlugin(
            name="p2", version=None, root_path=tmp_path,
            source="project", manifest=PluginManifest(name="p2"),
            hook_specs=[{"hooks": {"PreToolUse": [{"type": "command", "command": "c2"}]}}],
        )
        rules, warnings = resolve_all_hook_rules([p1, p2])
        assert len(rules) == 2
        assert {r.plugin_name for r in rules} == {"p1", "p2"}

    def test_missing_hook_file_warns(self, tmp_path):
        plugin = LoadedPlugin(
            name="p", version=None, root_path=tmp_path,
            source="project", manifest=PluginManifest(name="p"),
            hook_specs=["./missing/hooks.json"],
        )
        rules, warnings = resolve_all_hook_rules([plugin])
        assert rules == []
        assert len(warnings) == 1
        assert "not found" in warnings[0].message.lower()
