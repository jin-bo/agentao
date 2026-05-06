"""No-emit gate — ``PLUGIN_HOOK_FIRED`` must not be emitted for ``Stop``
when there are zero matching Stop rules.

Three sub-cases:
  (a) No plugin rules at all.
  (b) Rules exist but target a different event (e.g. UserPromptSubmit).
  (c) Positive control — exactly one Stop rule selected → exactly one
      ``PLUGIN_HOOK_FIRED`` with ``matched_rule_count == 1``.

The ``matched_rule_count == 1`` assertion in (c) is what blocks a
future refactor from swapping in ``len(attachments)`` (which equals 1
for a clean exit-0 hook but diverges in every other case).

Phase B note: emission moved from ``_dispatch_stop`` to
``_emit_stop_hook_fired`` (called by chat-loop branches with the
branch-specific outcome label). The gate now lives on the emit helper
and reads ``stop_result.matched_rule_count``; both sources of the count
agree by construction (B1 dispatcher field set from the same filter
expression as ``select_matching_rules``).
"""

from __future__ import annotations

from agentao.plugins.models import ParsedHookRule

from tests.support.stop_precompact import make_runner_with_rules


def _dispatch_and_emit(runner, *, turn_end_reason, last_assistant_message):
    """Dispatch Stop hooks then emit the allow event — mirrors the
    chat-loop wiring's "allow" branch without an end-to-end runner.run()
    setup."""
    stop_result = runner._dispatch_stop(
        turn_end_reason=turn_end_reason,
        last_assistant_message=last_assistant_message,
    )
    runner._emit_stop_hook_fired(
        outcome="allow",
        turn_end_reason=turn_end_reason,
        stop_result=stop_result,
    )
    return stop_result


def test_no_plugin_rules_emits_nothing(tmp_path):
    runner, transport = make_runner_with_rules(tmp_path, rules=[])
    _dispatch_and_emit(
        runner, turn_end_reason="final_response", last_assistant_message="bye",
    )
    assert transport.hook_fired_events("Stop") == []


def test_rules_exist_but_none_target_stop(tmp_path):
    """A UserPromptSubmit-only rule must not produce a Stop emit. The
    early-return at _dispatch_user_prompt_submit cannot catch this on
    its own — the Stop helper must filter independently."""
    ups_rule = ParsedHookRule(
        event="UserPromptSubmit",
        hook_type="command",
        command="echo ok",
        plugin_name="t",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[ups_rule])
    _dispatch_and_emit(
        runner, turn_end_reason="final_response", last_assistant_message="bye",
    )
    assert transport.hook_fired_events("Stop") == []


def test_positive_control_emits_exactly_one_event(tmp_path):
    stop_rule = ParsedHookRule(
        event="Stop",
        hook_type="command",
        command="echo bye",
        plugin_name="t",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[stop_rule])
    _dispatch_and_emit(
        runner,
        turn_end_reason="final_response",
        last_assistant_message="here is the answer",
    )

    stop_events = transport.hook_fired_events("Stop")
    assert len(stop_events) == 1

    data = stop_events[0].data
    assert data["outcome"] == "allow"
    assert data["turn_end_reason"] == "final_response"
    assert data["at_max_iter"] is False
    # Selection count, not execution count.
    assert data["matched_rule_count"] == 1


def test_max_iter_emits_at_max_iter_true(tmp_path):
    stop_rule = ParsedHookRule(
        event="Stop", hook_type="command", command="echo", plugin_name="t",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[stop_rule])
    _dispatch_and_emit(
        runner,
        turn_end_reason="max_iterations",
        last_assistant_message="hit the cap",
    )
    stop_events = transport.hook_fired_events("Stop")
    assert len(stop_events) == 1
    assert stop_events[0].data["turn_end_reason"] == "max_iterations"
    assert stop_events[0].data["at_max_iter"] is True


def test_doom_loop_turn_end_reason_round_trips(tmp_path):
    stop_rule = ParsedHookRule(
        event="Stop", hook_type="command", command="echo", plugin_name="t",
    )
    runner, transport = make_runner_with_rules(tmp_path, rules=[stop_rule])
    _dispatch_and_emit(
        runner,
        turn_end_reason="doom_loop",
        last_assistant_message="halted by doom-loop",
    )
    stop_events = transport.hook_fired_events("Stop")
    assert len(stop_events) == 1
    assert stop_events[0].data["turn_end_reason"] == "doom_loop"
    assert stop_events[0].data["at_max_iter"] is False
