"""Sibling no-emit gate test for ``PreCompact`` — A5's no-emit clause
applies symmetrically.

Three sub-cases:
  (a) No plugin rules at all.
  (b) Rules exist but none target PreCompact.
  (c) Positive control — exactly one PreCompact rule selected.
"""

from __future__ import annotations

from agentao.plugins.models import ParsedHookRule

from tests.support.stop_precompact import make_runner_with_rules


def test_no_plugin_rules_emits_nothing(tmp_path):
    runner, transport = make_runner_with_rules(tmp_path, rules=[])
    runner._dispatch_pre_compact(
        compaction_type="microcompact",
        reason="microcompact_threshold",
    )
    assert transport.hook_fired_events("PreCompact") == []


def test_rules_exist_but_none_target_pre_compact(tmp_path):
    stop_rule = ParsedHookRule(
        event="Stop", hook_type="command", command="echo", plugin_name="t",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[stop_rule])
    runner._dispatch_pre_compact(
        compaction_type="microcompact",
        reason="microcompact_threshold",
    )
    assert transport.hook_fired_events("PreCompact") == []


def test_positive_control_emits_with_correct_payload(tmp_path):
    rule = ParsedHookRule(
        event="PreCompact",
        hook_type="command",
        command="echo compacting",
        plugin_name="t",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[rule])
    runner._dispatch_pre_compact(
        compaction_type="microcompact",
        reason="microcompact_threshold",
    )

    fired = transport.hook_fired_events("PreCompact")
    assert len(fired) == 1
    data = fired[0].data
    assert data["outcome"] == "allow"
    assert data["compaction_type"] == "microcompact"
    assert data["trigger"] == "auto"
    assert data["matched_rule_count"] == 1


def test_compaction_type_round_trips_for_every_emit_site(tmp_path):
    """Each PreCompact emit site (microcompact / full / minimal_history)
    must round-trip its compaction_type label through the emit dict."""
    rule = ParsedHookRule(
        event="PreCompact", hook_type="command", command="echo",
        plugin_name="t",
    )
    sites = [
        ("microcompact", "microcompact_threshold"),
        ("full", "compression_threshold"),
        ("full", "api_overflow"),
        ("minimal_history", "api_overflow_after_compression"),
    ]
    for compaction_type, reason in sites:
        runner, transport = make_runner_with_rules(tmp_path, rules=[rule])
        runner._dispatch_pre_compact(
            compaction_type=compaction_type, reason=reason,
        )
        fired = transport.hook_fired_events("PreCompact")
        assert len(fired) == 1, (compaction_type, reason)
        assert fired[0].data["compaction_type"] == compaction_type
        assert fired[0].data["trigger"] == "auto"
