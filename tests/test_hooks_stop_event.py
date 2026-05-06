"""End-to-end Stop helper test — runs a real subprocess and asserts the
emit dict matches the post-Phase-B schema.

The ``turn_end_reason`` assertion is the load-bearing one: it guards
the B7 disambiguation contract, so a refactor that drops the field
from ``_emit_stop_hook_fired``'s emit dict would silently break
dashboard consumers (the field's only purpose on the transport channel
is to disambiguate ``outcome="continue"`` across emit sites).
"""

from __future__ import annotations

from agentao.plugins.models import ParsedHookRule

from tests.support.stop_precompact import (
    make_runner_with_rules,
    write_capture_script,
)


def test_stop_hook_subprocess_invoked_and_emits_allow(tmp_path):
    script, capture = write_capture_script(tmp_path)
    rule = ParsedHookRule(
        event="Stop",
        hook_type="command",
        command=f"sh '{script}'",
        plugin_name="t",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[rule])
    agent = runner._agent
    pre_messages = list(agent.messages)
    stop_result = runner._dispatch_stop(
        turn_end_reason="final_response",
        last_assistant_message="here is the final answer",
    )
    runner._emit_stop_hook_fired(
        outcome="allow",
        turn_end_reason="final_response",
        stop_result=stop_result,
    )

    assert capture.exists()
    assert stop_result.matched_rule_count == 1
    assert stop_result.force_continue is False

    fired = transport.hook_fired_events("Stop")
    assert len(fired) == 1
    data = fired[0].data
    assert data["outcome"] == "allow"
    assert data["turn_end_reason"] == "final_response"
    assert data["at_max_iter"] is False
    assert data["matched_rule_count"] == 1

    # Helper-side: dispatch must not mutate agent.messages.
    assert agent.messages == pre_messages
