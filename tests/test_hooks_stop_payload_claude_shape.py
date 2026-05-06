"""Stop payload wire-shape contract.

Captures the JSON the Stop hook subprocess actually receives on stdin
and asserts top-level keys exactly match Claude Code's flat snake_case
schema. This pins A3's "deviate from sibling builders for Stop /
PreCompact only" decision.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentao.plugins.hooks import (
    ClaudeHookPayloadAdapter,
    PluginHookDispatcher,
)
from agentao.plugins.models import ParsedHookRule

from tests.support.stop_precompact import write_capture_script


STOP_TOP_LEVEL_KEYS = {
    "hook_event_name",
    "session_id",
    "transcript_path",
    "cwd",
    "permission_mode",
    "stop_hook_active",
    "last_assistant_message",
    "turn_end_reason",
}


def test_stop_payload_is_flat_snake_case_top_level(tmp_path):
    script, capture = write_capture_script(tmp_path)
    rule = ParsedHookRule(
        event="Stop",
        hook_type="command",
        command=f"sh '{script}'",
        plugin_name="t",
    )
    payload = ClaudeHookPayloadAdapter().build_stop(
        session_id="sess-xyz",
        cwd=tmp_path,
        last_assistant_message="here is the answer",
        stop_hook_active=False,
        turn_end_reason="final_response",
        permission_mode="workspace-write",
    )

    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    dispatcher.dispatch_stop(payload=payload, rules=[rule])

    raw = capture.read_text(encoding="utf-8")
    received = json.loads(raw)

    assert set(received.keys()) == STOP_TOP_LEVEL_KEYS
    assert "data" not in received  # no envelope
    assert received["hook_event_name"] == "Stop"
    assert received["session_id"] == "sess-xyz"
    assert received["transcript_path"] is None
    assert received["last_assistant_message"] == "here is the answer"
    assert received["turn_end_reason"] == "final_response"
    assert received["stop_hook_active"] is False
    assert received["permission_mode"] == "workspace-write"


def test_stop_payload_round_trips_assistant_content(tmp_path):
    script, capture = write_capture_script(tmp_path)
    rule = ParsedHookRule(
        event="Stop",
        hook_type="command",
        command=f"sh '{script}'",
        plugin_name="t",
    )
    fixture_text = "Multi-line\nassistant\noutput"
    payload = ClaudeHookPayloadAdapter().build_stop(
        cwd=tmp_path,
        last_assistant_message=fixture_text,
        turn_end_reason="max_iterations",
    )

    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    dispatcher.dispatch_stop(payload=payload, rules=[rule])

    received = json.loads(capture.read_text(encoding="utf-8"))
    assert received["last_assistant_message"] == fixture_text
    assert received["turn_end_reason"] == "max_iterations"
