"""PreCompact payload wire-shape contract — sibling of the Stop test.

Asserts top-level keys exactly match Claude Code's flat snake_case
schema and that ``trigger`` reports the provenance of the site that
built the payload — ``"manual"`` for ``/compact``, ``"auto"`` for the
four automatic sites.
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


def _capture(
    tmp_path, *, trigger: str = "auto", compaction_type: str, reason: str,
) -> dict:
    script, capture = write_capture_script(
        tmp_path, name=f"capture_{trigger}_{compaction_type}_{reason}.sh",
    )
    rule = ParsedHookRule(
        event="PreCompact",
        hook_type="command",
        command=f"sh '{script}'",
        plugin_name="t",
    )
    payload = ClaudeHookPayloadAdapter().build_pre_compact(
        session_id="s",
        cwd=tmp_path,
        trigger=trigger,
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


def test_pre_compact_trigger_matches_its_emit_site(tmp_path):
    """Every PreCompact emit site reports its own provenance.

    The list is **five** sites, not four: manual ``/compact``
    (``cli/commands/compact.py``) shipped after this test was written, and
    while ``trigger`` was hardcoded ``"auto"`` its omission here was
    invisible. That omission is the reason the defect survived — the test
    enumerated exactly the sites that happened to be right.
    """
    sites = [
        ("auto", "microcompact", "microcompact_threshold"),
        ("auto", "full", "compression_threshold"),
        ("auto", "full", "api_overflow"),
        ("auto", "minimal_history", "api_overflow_after_compression"),
        ("manual", "full", "manual_cli"),
    ]
    for trigger, compaction_type, reason in sites:
        received = _capture(
            tmp_path, trigger=trigger, compaction_type=compaction_type, reason=reason,
        )
        assert received["trigger"] == trigger, (compaction_type, reason)
        assert received["custom_instructions"] == ""
        assert received["compaction_type"] == compaction_type
        assert received["reason"] == reason


def test_custom_instructions_round_trips(tmp_path):
    """``custom_instructions`` is a parameter now, not a hardcoded ``""``.

    Nothing passes it yet — ``handle_compact_command`` still ignores the
    ``args`` it receives — so this pins the payload half only, so that
    wiring ``/compact <instructions>`` later needs no schema change.
    """
    received = _capture(
        tmp_path, trigger="manual", compaction_type="full", reason="manual_cli",
    )
    assert received["custom_instructions"] == ""

    payload = ClaudeHookPayloadAdapter().build_pre_compact(
        cwd=tmp_path,
        trigger="manual",
        compaction_type="full",
        reason="manual_cli",
        custom_instructions="focus on the failing test",
    )
    assert payload["custom_instructions"] == "focus on the failing test"
    assert set(payload.keys()) == PRECOMPACT_TOP_LEVEL_KEYS
