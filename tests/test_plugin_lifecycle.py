"""Tests for Phase 6: lifecycle hooks, matcher, CLI, and diagnostics."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentao.plugins.hooks import (
    ClaudeHookPayloadAdapter,
    PluginHookDispatcher,
    ToolAliasResolver,
    resolve_all_hook_rules,
)
from agentao.plugins.models import (
    HookAttachmentRecord,
    LoadedPlugin,
    ParsedHookRule,
    PluginManifest,
)
from agentao.plugins.diagnostics import PluginDiagnostics, build_diagnostics
from agentao.plugins.manager import PluginManager


# ======================================================================
# Lifecycle dispatch — SessionStart / SessionEnd
# ======================================================================


class TestSessionHooks:
    def test_session_start_executes(self, tmp_path):
        rule = ParsedHookRule(
            event="SessionStart",
            hook_type="command",
            command="echo ok",
            plugin_name="test",
        )
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_session_start(session_id="s1", cwd=tmp_path)

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        attachments = dispatcher.dispatch_session_start(payload=payload, rules=[rule])
        assert len(attachments) == 1
        assert attachments[0].hook_event == "SessionStart"
        assert attachments[0].attachment_type == "hook_success"

    def test_session_end_executes(self, tmp_path):
        rule = ParsedHookRule(
            event="SessionEnd",
            hook_type="command",
            command="echo done",
            plugin_name="test",
        )
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_session_end(session_id="s1", cwd=tmp_path)

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        attachments = dispatcher.dispatch_session_end(payload=payload, rules=[rule])
        assert len(attachments) == 1
        assert attachments[0].hook_event == "SessionEnd"

    def test_non_matching_event_skipped(self, tmp_path):
        rule = ParsedHookRule(
            event="PreToolUse",
            hook_type="command",
            command="echo wrong",
            plugin_name="test",
        )
        payload = {"event": "SessionStart", "data": {}}
        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        attachments = dispatcher.dispatch_session_start(payload=payload, rules=[rule])
        assert attachments == []


# ======================================================================
# Lifecycle dispatch — PreToolUse / PostToolUse / PostToolUseFailure
# ======================================================================


class TestToolHooks:
    def test_pre_tool_use_executes(self, tmp_path):
        rule = ParsedHookRule(
            event="PreToolUse",
            hook_type="command",
            command="echo pre",
            plugin_name="test",
        )
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_pre_tool_use(tool_name="read_file", tool_input={"path": "x"})

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        attachments = dispatcher.dispatch_pre_tool_use(payload=payload, rules=[rule])
        assert len(attachments) == 1
        assert attachments[0].hook_event == "PreToolUse"

    def test_pre_tool_use_payload_uses_alias(self):
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_pre_tool_use(tool_name="read_file")
        assert payload["data"]["toolName"] == "Read"

    def test_post_tool_use_executes(self, tmp_path):
        rule = ParsedHookRule(
            event="PostToolUse",
            hook_type="command",
            command="echo post",
            plugin_name="test",
        )
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_post_tool_use(tool_name="run_shell_command", tool_output="ok")

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        attachments = dispatcher.dispatch_post_tool_use(payload=payload, rules=[rule])
        assert len(attachments) == 1

    def test_post_tool_use_payload_fields(self):
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_post_tool_use(
            tool_name="write_file",
            tool_input={"path": "/x"},
            tool_output="written",
            session_id="s",
        )
        assert payload["data"]["toolName"] == "Write"
        assert payload["data"]["toolOutput"] == "written"

    def test_post_tool_use_failure_executes(self, tmp_path):
        rule = ParsedHookRule(
            event="PostToolUseFailure",
            hook_type="command",
            command="echo fail",
            plugin_name="test",
        )
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_post_tool_use_failure(tool_name="run_shell_command", error="boom")

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        attachments = dispatcher.dispatch_post_tool_use_failure(payload=payload, rules=[rule])
        assert len(attachments) == 1
        assert attachments[0].hook_event == "PostToolUseFailure"

    def test_post_tool_use_failure_payload_fields(self):
        adapter = ClaudeHookPayloadAdapter()
        payload = adapter.build_post_tool_use_failure(
            tool_name="replace",
            tool_input={"file": "x"},
            error="not found",
        )
        assert payload["data"]["toolName"] == "Edit"
        assert payload["data"]["error"] == "not found"


# ======================================================================
# Matcher / tool-name filtering
# ======================================================================


class TestMatcher:
    def test_no_matcher_matches_all(self, tmp_path):
        rule = ParsedHookRule(
            event="PreToolUse",
            hook_type="command",
            command="echo yes",
            matcher=None,
            plugin_name="test",
        )
        payload = {"event": "PreToolUse", "data": {"toolName": "Read"}}
        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        attachments = dispatcher.dispatch_pre_tool_use(payload=payload, rules=[rule])
        assert len(attachments) == 1

    def test_tool_name_matcher_exact(self, tmp_path):
        rule = ParsedHookRule(
            event="PreToolUse",
            hook_type="command",
            command="echo matched",
            matcher={"toolName": "Bash"},
            plugin_name="test",
        )
        payload_match = {"event": "PreToolUse", "data": {"toolName": "Bash"}}
        payload_miss = {"event": "PreToolUse", "data": {"toolName": "Read"}}

        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        assert len(dispatcher.dispatch_pre_tool_use(payload=payload_match, rules=[rule])) == 1
        assert len(dispatcher.dispatch_pre_tool_use(payload=payload_miss, rules=[rule])) == 0

    def test_tool_name_matcher_wildcard(self, tmp_path):
        rule = ParsedHookRule(
            event="PreToolUse",
            hook_type="command",
            command="echo glob",
            matcher={"toolName": "*"},
            plugin_name="test",
        )
        payload = {"event": "PreToolUse", "data": {"toolName": "Anything"}}
        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        assert len(dispatcher.dispatch_pre_tool_use(payload=payload, rules=[rule])) == 1

    def test_tool_name_matcher_glob_pattern(self, tmp_path):
        rule = ParsedHookRule(
            event="PreToolUse",
            hook_type="command",
            command="echo glob",
            matcher={"toolName": "Web*"},
            plugin_name="test",
        )
        dispatcher = PluginHookDispatcher(cwd=tmp_path)

        payload_hit = {"event": "PreToolUse", "data": {"toolName": "WebFetch"}}
        payload_miss = {"event": "PreToolUse", "data": {"toolName": "Read"}}
        assert len(dispatcher.dispatch_pre_tool_use(payload=payload_hit, rules=[rule])) == 1
        assert len(dispatcher.dispatch_pre_tool_use(payload=payload_miss, rules=[rule])) == 0


# ======================================================================
# Failure isolation
# ======================================================================


class TestFailureIsolation:
    def test_failing_hook_only_warns(self, tmp_path):
        """A failing lifecycle hook does not crash the dispatcher."""
        rule = ParsedHookRule(
            event="SessionStart",
            hook_type="command",
            command="exit 1",
            plugin_name="test",
        )
        payload = {"event": "SessionStart", "data": {}}
        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        # Should not raise.
        attachments = dispatcher.dispatch_session_start(payload=payload, rules=[rule])
        # Still produces an attachment (with returncode info).
        assert len(attachments) == 1

    def test_timeout_hook_warns(self, tmp_path):
        rule = ParsedHookRule(
            event="PreToolUse",
            hook_type="command",
            command="sleep 60",
            timeout=1,
            plugin_name="test",
        )
        payload = {"event": "PreToolUse", "data": {"toolName": "Bash"}}
        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        attachments = dispatcher.dispatch_pre_tool_use(payload=payload, rules=[rule])
        assert len(attachments) == 1
        assert "timeout" in str(attachments[0].payload).lower() or "timed out" in str(attachments[0].payload).lower()

    def test_prompt_hook_on_non_ups_event_skipped(self, tmp_path):
        """prompt hooks are only for UserPromptSubmit; lifecycle dispatch ignores them."""
        rule = ParsedHookRule(
            event="SessionStart",
            hook_type="prompt",
            prompt="Be nice.",
            plugin_name="test",
        )
        payload = {"event": "SessionStart", "data": {}}
        dispatcher = PluginHookDispatcher(cwd=tmp_path)
        attachments = dispatcher.dispatch_session_start(payload=payload, rules=[rule])
        assert attachments == []  # prompt hooks filtered out by lifecycle dispatch


# ======================================================================
# Unsupported hook type / event warnings
# ======================================================================


class TestUnsupportedWarnings:
    def test_unsupported_hook_type_warning(self):
        from agentao.plugins.hooks import ClaudeHooksParser

        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_dict({
            "hooks": {
                "PreToolUse": [{"type": "http", "url": "http://x"}],
            }
        }, plugin_name="test")
        assert rules == []
        assert any("http" in w.message and "not supported" in w.message.lower() for w in warnings)

    def test_unsupported_event_warning(self):
        from agentao.plugins.hooks import ClaudeHooksParser

        parser = ClaudeHooksParser()
        rules, warnings = parser.parse_dict({
            "hooks": {
                "BadEvent": [{"type": "command", "command": "echo"}],
            }
        }, plugin_name="test")
        assert rules == []
        assert any("BadEvent" in w.message for w in warnings)


# ======================================================================
# CLI — --plugin-dir (argparse)
# ======================================================================


class TestPluginDirArg:
    def test_repeatable_plugin_dir(self):
        from agentao.cli import _build_parser

        parser = _build_parser()
        args, _ = parser.parse_known_args(["--plugin-dir", "/a", "--plugin-dir", "/b"])
        assert args.plugin_dirs == ["/a", "/b"]

    def test_default_empty(self):
        from agentao.cli import _build_parser

        parser = _build_parser()
        args, _ = parser.parse_known_args([])
        assert args.plugin_dirs == []


# ======================================================================
# CLI — agentao plugin list
# ======================================================================


class TestPluginListCli:
    def test_plugin_list_no_plugins(self, tmp_path, capsys):
        """plugin list with no plugins doesn't crash."""
        from agentao.cli import _plugin_list_cli
        import argparse

        args = argparse.Namespace(plugin_dirs=[], json_output=False)
        # Patch cwd to an empty directory to avoid picking up real plugins.
        import unittest.mock
        with unittest.mock.patch.object(PluginManager, "__init__", lambda self, **kw: (
            setattr(self, '_cwd', tmp_path.resolve()),
            setattr(self, '_inline_dirs', []),
            setattr(self, '_parser', __import__('agentao.plugins.manifest', fromlist=['PluginManifestParser']).PluginManifestParser()),
            setattr(self, '_loaded', []),
            setattr(self, '_warnings', []),
            setattr(self, '_errors', []),
            setattr(self, '_loaded_done', False),
        )[-1]):
            _plugin_list_cli(args)

    def test_plugin_list_json(self, tmp_path, capsys):
        """plugin list --json produces valid JSON."""
        from agentao.cli import _plugin_list_cli
        import argparse
        import unittest.mock

        # Create a plugin directory under local/.
        plugins_dir = tmp_path / ".agentao" / "plugins" / "local" / "demo"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "plugin.json").write_text(
            json.dumps({"name": "demo", "version": "1.0"}), encoding="utf-8",
        )

        args = argparse.Namespace(plugin_dirs=[], json_output=True)
        with unittest.mock.patch.object(PluginManager, "__init__", lambda self, **kw: (
            setattr(self, '_cwd', tmp_path.resolve()),
            setattr(self, '_inline_dirs', []),
            setattr(self, '_parser', __import__('agentao.plugins.manifest', fromlist=['PluginManifestParser']).PluginManifestParser()),
            setattr(self, '_loaded', []),
            setattr(self, '_warnings', []),
            setattr(self, '_errors', []),
            setattr(self, '_loaded_done', False),
        )[-1]):
            _plugin_list_cli(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "plugins" in data
        demo = next(p for p in data["plugins"] if p["name"] == "demo")
        assert demo["name"] == "demo"
        assert demo["marketplace"] == "local"
        assert demo["qualified_name"] == "demo@local"


# ======================================================================
# Diagnostics renderer
# ======================================================================


class TestDiagnosticsRenderer:
    def test_format_report_includes_plugin_name_and_source(self, tmp_path):
        from agentao.plugins.models import LoadedPlugin, PluginManifest, PluginWarning

        plugin = LoadedPlugin(
            name="my-plugin", version="1.2.3",
            root_path=tmp_path / "my-plugin",
            source="project",
            manifest=PluginManifest(name="my-plugin"),
        )
        warning = PluginWarning(
            plugin_name="my-plugin",
            message="unsupported field 'outputStyles'",
            field="outputStyles",
        )
        diag = build_diagnostics([plugin], [warning], [])

        report = diag.format_report()
        assert "my-plugin" in report
        assert "1.2.3" in report
        assert "project" in report
        assert "outputStyles" in report

    def test_format_report_shows_errors(self):
        from agentao.plugins.models import PluginLoadError

        error = PluginLoadError(plugin_name="bad", message="JSON parse failed")
        diag = build_diagnostics([], [], [error])

        report = diag.format_report()
        assert "bad" in report
        assert "JSON parse failed" in report

    def test_summary_counts(self):
        from agentao.plugins.models import LoadedPlugin, PluginManifest, PluginWarning, PluginLoadError

        diag = PluginDiagnostics(
            loaded=[
                LoadedPlugin(name="a", version=None, root_path=Path("/a"),
                             source="global", manifest=PluginManifest(name="a")),
                LoadedPlugin(name="b", version=None, root_path=Path("/b"),
                             source="project", manifest=PluginManifest(name="b")),
            ],
            warnings=[PluginWarning(plugin_name="a", message="w1")],
            errors=[PluginLoadError(plugin_name="c", message="e1")],
        )
        summary = diag.summary()
        assert "2 plugin(s)" in summary
        assert "1 warning" in summary
        assert "1 error" in summary


# ======================================================================
# End-to-end: resolve rules from plugins with lifecycle hooks
# ======================================================================


class TestResolveLifecycleRules:
    def test_resolve_lifecycle_rules(self, tmp_path):
        plugin = LoadedPlugin(
            name="lc-plug", version=None, root_path=tmp_path,
            source="project", manifest=PluginManifest(name="lc-plug"),
            hook_specs=[{
                "hooks": {
                    "SessionStart": [{"type": "command", "command": "echo start"}],
                    "PreToolUse": [
                        {"type": "command", "command": "echo pre", "matcher": {"toolName": "Bash"}},
                    ],
                    "PostToolUse": [{"type": "command", "command": "echo post"}],
                    "PostToolUseFailure": [{"type": "command", "command": "echo fail"}],
                    "SessionEnd": [{"type": "command", "command": "echo end"}],
                }
            }],
        )
        rules, warnings = resolve_all_hook_rules([plugin])
        assert warnings == []
        events = {r.event for r in rules}
        assert events == {"SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure", "SessionEnd"}
        # Check matcher preserved
        pre_rules = [r for r in rules if r.event == "PreToolUse"]
        assert pre_rules[0].matcher == {"toolName": "Bash"}
