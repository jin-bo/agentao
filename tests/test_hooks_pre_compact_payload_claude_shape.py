"""PreCompact payload wire-shape contract — sibling of the Stop test.

Asserts top-level keys exactly match Claude Code's flat snake_case
schema and that ``trigger`` is always ``"auto"`` under this plan.
"""

from __future__ import annotations

import json

from agentao.plugins.hooks import (
    ClaudeHookPayloadAdapter,
    PluginHookDispatcher,
)
from agentao.plugins.models import ParsedHookRule

from tests.support.stop_precompact import write_capture_script


PRECOMPACT_TOP_LEVEL_KEYS = {
    "hook_event_name",
    "session_id",
    "transcript_path",
    "cwd",
    "permission_mode",
    "trigger",
    "custom_instructions",
    "compaction_type",
    "reason",
}


def _capture(tmp_path, *, compaction_type: str, reason: str) -> dict:
    script, capture = write_capture_script(tmp_path, name=f"capture_{compaction_type}.sh")
    rule = ParsedHookRule(
        event="PreCompact",
        hook_type="command",
        command=f"sh '{script}'",
        plugin_name="t",
    )
    payload = ClaudeHookPayloadAdapter().build_pre_compact(
        session_id="s",
        cwd=tmp_path,
        compaction_type=compaction_type,
        reason=reason,
        permission_mode="workspace-write",
    )
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    dispatcher.dispatch_pre_compact(payload=payload, rules=[rule])
    return json.loads(capture.read_text(encoding="utf-8"))


def test_pre_compact_payload_top_level_keys_match_claude(tmp_path):
    received = _capture(tmp_path, compaction_type="microcompact", reason="microcompact_threshold")
    assert set(received.keys()) == PRECOMPACT_TOP_LEVEL_KEYS
    assert "data" not in received
    assert received["hook_event_name"] == "PreCompact"


def test_pre_compact_trigger_always_auto_for_every_emit_site(tmp_path):
    """Every PreCompact emit site keeps trigger=='auto' (no manual surface)."""
    sites = [
        ("microcompact", "microcompact_threshold"),
        ("full", "compression_threshold"),
        ("full", "api_overflow"),
        ("minimal_history", "api_overflow_after_compression"),
    ]
    for compaction_type, reason in sites:
        received = _capture(tmp_path, compaction_type=compaction_type, reason=reason)
        assert received["trigger"] == "auto", (compaction_type, reason)
        assert received["custom_instructions"] == ""
        assert received["compaction_type"] == compaction_type
        assert received["reason"] == reason
