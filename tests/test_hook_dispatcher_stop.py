"""Direct dispatcher-level test for the Stop event.

Phase B upgrade — ``dispatch_stop`` now returns a ``StopHookResult``
carrying both the aggregated control verdict and the legacy attachment
list (under ``result.messages``). The dispatcher boundary remains the
authoritative observer of attachments — chat-loop call sites consume
the result for force_continue / blocking_error branching.
"""

from __future__ import annotations

from agentao.plugins.hooks import (
    ClaudeHookPayloadAdapter,
    PluginHookDispatcher,
)
from agentao.plugins.models import ParsedHookRule, StopHookResult


def test_dispatch_stop_returns_hook_success_attachment(tmp_path):
    """A clean exit-0 hook with no stdout produces a single
    ``hook_success`` attachment under ``result.messages``."""
    rule = ParsedHookRule(
        event="Stop",
        hook_type="command",
        command="true",  # exit 0, empty stdout — generic-success branch
        plugin_name="t",
    )
    payload = ClaudeHookPayloadAdapter().build_stop(
        session_id="s1",
        cwd=tmp_path,
        last_assistant_message="final answer",
        stop_hook_active=False,
        turn_end_reason="final_response",
        permission_mode="workspace-write",
    )

    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    result = dispatcher.dispatch_stop(payload=payload, rules=[rule])

    assert isinstance(result, StopHookResult)
    assert result.matched_rule_count == 1
    assert result.force_continue is False
    assert result.blocking_error is None
    assert len(result.messages) == 1
    att = result.messages[0]
    assert att.attachment_type == "hook_success"
    assert att.hook_event == "Stop"


def test_dispatch_pre_compact_returns_hook_success_attachment(tmp_path):
    rule = ParsedHookRule(
        event="PreCompact",
        hook_type="command",
        command="echo compacting",
        plugin_name="t",
    )
    payload = ClaudeHookPayloadAdapter().build_pre_compact(
        session_id="s1",
        cwd=tmp_path,
        compaction_type="microcompact",
        reason="microcompact_threshold",
        permission_mode="workspace-write",
    )

    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    attachments = dispatcher.dispatch_pre_compact(payload=payload, rules=[rule])

    assert len(attachments) == 1
    assert attachments[0].attachment_type == "hook_success"
    assert attachments[0].hook_event == "PreCompact"


def test_select_matching_rules_filters_by_event_and_supported(tmp_path):
    """select_matching_rules drops rules for other events and unsupported types."""
    stop_rule = ParsedHookRule(event="Stop", hook_type="command", command="x", plugin_name="t")
    other_rule = ParsedHookRule(event="UserPromptSubmit", hook_type="command", command="x", plugin_name="t")
    bad_rule = ParsedHookRule(event="Stop", hook_type="http", command="x", plugin_name="t")

    payload = ClaudeHookPayloadAdapter().build_stop(
        cwd=tmp_path,
        last_assistant_message="",
        turn_end_reason="final_response",
    )

    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    matched = dispatcher.select_matching_rules(
        "Stop", payload, [stop_rule, other_rule, bad_rule],
    )

    assert matched == [stop_rule]
