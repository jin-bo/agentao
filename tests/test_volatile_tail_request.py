"""Stage 0a: the volatile tail rides the request, and the anchor knows it.

``docs/design/llm-api-adapters.md`` §2.3 / §10 / §11. Two halves, and the
second is the one that bites:

1. Skills, todos, dynamic recall and the plan prompt left ``messages[0]`` for a
   trailing ``user`` message built per request and never persisted. While they
   sat in the system message, one ``todo_write`` invalidated the provider's
   cached prefix over the whole history.
2. The Tier-1 token anchor has to be recorded against the **persistent** prefix.
   Anchoring the request instead makes the next slice skip the first new history
   message *and* charge a new tail on top of the old one — an error at
   whole-tail scale, every turn, in the direction that triggers compaction
   early.

``test_the_local_estimate_does_not_drift_with_tail_size`` is the §11 gate: N
consecutive turns, fixed history growth, tail size varied deliberately. Merely
checking that new messages are not skipped does not catch the drift, which is
why that check is not what this file does.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao import Agentao

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

TODO_MARKER = "=== Current Task List ==="


def _make_agent(**kwargs) -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
        **kwargs,
    )


def _fake_tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _fake_response(content, *, tool_calls=None, prompt_tokens=None):
    message = SimpleNamespace(
        content=content, tool_calls=tool_calls, reasoning_content=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = (
        None if prompt_tokens is None
        else SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=1,
            total_tokens=prompt_tokens + 1,
        )
    )
    return SimpleNamespace(choices=[choice], usage=usage, model="test-model")


def _capture(agent, responses):
    """Stub ``_llm_call`` to record every request list it is handed."""
    sent: list = []
    it = iter(responses)

    def _call(messages, tools, token):
        sent.append(list(messages))
        return next(it)

    agent._llm_call = _call
    return sent


def _tails(request):
    return [
        m for m in request
        if m.get("role") == "user" and TODO_MARKER in str(m.get("content", ""))
    ]


# ---------------------------------------------------------------------------
# The tail is on the request and nowhere else
# ---------------------------------------------------------------------------


def test_the_tail_is_the_last_request_message_and_never_enters_history():
    agent = _make_agent()
    agent.todo_tool.execute(todos=[{"content": "step one", "status": "pending"}])
    sent = _capture(agent, [_fake_response("done")])

    agent.chat("go")

    (request,) = sent
    tail = request[-1]
    assert tail["role"] == "user"
    assert tail["content"].startswith("<system-reminder>")
    assert TODO_MARKER in tail["content"]
    assert "step one" in tail["content"]

    # Not in history, not in the system message, and the history the request
    # was built from is untouched: `agent.messages` is shared with the request
    # list by a shallow concat, so an in-place append would show up here.
    # ``messages[:-1]`` because the answer is appended after the call returns.
    assert not any(TODO_MARKER in str(m.get("content", "")) for m in agent.messages)
    assert TODO_MARKER not in request[0]["content"]
    assert request[1:-1] == agent.messages[:-1]
    print("✅ Tail is request-only and sits last")


def test_no_tail_message_when_nothing_volatile_renders():
    """A bare agent with no todos / skills / plan sends the pre-0a request."""
    agent = _make_agent()
    agent.skill_manager.available_skills = {}
    agent.skill_manager.active_skills = {}
    sent = _capture(agent, [_fake_response("done")])

    agent.chat("go")

    (request,) = sent
    assert request[1:] == agent.messages[:-1]
    assert request[0]["role"] == "system"
    assert request[-1]["role"] == "user"
    print("✅ No volatile content, no tail message")


def test_every_request_in_a_tool_loop_carries_exactly_one_tail():
    """Three requests across a two-iteration turn; the tail neither goes
    missing on a rebuild nor accumulates."""
    agent = _make_agent()
    agent.todo_tool.execute(todos=[{"content": "step one", "status": "pending"}])
    sent = _capture(agent, [
        _fake_response("reading", tool_calls=[
            _fake_tool_call("c1", "list_directory", '{"path": "."}'),
        ]),
        _fake_response("writing", tool_calls=[
            _fake_tool_call("c2", "list_directory", '{"path": "."}'),
        ]),
        _fake_response("done"),
    ])

    agent.chat("go")

    assert len(sent) == 3
    for i, request in enumerate(sent):
        assert len(_tails(request)) == 1, f"request {i} carries {len(_tails(request))} tails"
        assert request[-1] is not agent.messages[-1]
        assert TODO_MARKER in request[-1]["content"]
    assert not any(TODO_MARKER in str(m.get("content", "")) for m in agent.messages)
    print("✅ One tail per request across a tool loop, none persisted")


def test_a_tail_is_rebuilt_per_request_so_a_mid_turn_todo_write_is_visible():
    """The tail is built per request, not per turn: the model's own
    ``todo_write`` in iteration 1 reaches iteration 2's prompt. Before 0a this
    waited for a system-prompt rebuild."""
    agent = _make_agent()
    sent = _capture(agent, [
        _fake_response("planning", tool_calls=[
            _fake_tool_call(
                "c1", "todo_write",
                '{"todos": [{"content": "MARKER-FRESH-TODO", "status": "pending"}]}',
            ),
        ]),
        _fake_response("done"),
    ])

    agent.chat("go")

    assert len(sent) == 2
    assert "MARKER-FRESH-TODO" not in str(sent[0])
    assert "MARKER-FRESH-TODO" in sent[1][-1]["content"]
    print("✅ A mid-turn todo_write is visible to the next request")


# ---------------------------------------------------------------------------
# The Tier-1 anchor
# ---------------------------------------------------------------------------


def test_the_anchor_is_recorded_against_the_persistent_prefix():
    agent = _make_agent()
    agent.todo_tool.execute(todos=[{"content": "step one", "status": "pending"}])
    sent = _capture(agent, [_fake_response("done", prompt_tokens=5000)])

    agent.chat("go")

    cm = agent.context_manager
    (request,) = sent
    tail_est = cm.estimate_request_tail_tokens(request[-1])
    assert tail_est > 0
    # Prefix anchor: the provider's count minus this request's tail.
    assert cm._last_api_prompt_tokens == 5000 - tail_est
    # Anchored on the persistent length, which is the request minus the tail.
    assert cm._api_anchor_msg_count == len(request) - 1
    # system + the history as it stood at send time (the answer is appended
    # after the call returns).
    assert cm._api_anchor_msg_count == len(agent.messages)
    # And the unmodified total is still reported, for "what did that cost".
    assert cm._last_api_request_tokens == 5000
    assert cm.get_usage_stats(agent.messages)["estimated_tokens"] == 5000
    print("✅ Anchor = prefix, reporting = request total")


def test_a_tail_estimate_larger_than_the_providers_count_clamps_at_zero():
    cm = _make_agent().context_manager
    cm.record_api_usage(10, message_count=3, tail_tokens=99)
    assert cm._last_api_prompt_tokens == 0
    assert cm._last_api_request_tokens == 10
    print("✅ Anchor clamped, never negative")


def test_the_threshold_estimate_adds_the_tail_on_top_of_the_anchor():
    cm = _make_agent().context_manager
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    cm.record_api_usage(1000, message_count=2)
    assert cm._threshold_token_estimate(msgs) == 1000
    assert cm._threshold_token_estimate(msgs, tail_tokens=42) == 1042
    # And on the no-anchor fallback path too.
    cm.invalidate_token_anchor()
    assert (
        cm._threshold_token_estimate(msgs, tail_tokens=42)
        == cm.estimate_tokens(msgs) + 42
    )
    print("✅ Tail rides on top of both estimate paths")


def test_the_local_estimate_does_not_drift_with_tail_size():
    """The §11 gate: N ≥ 10 consecutive turns, fixed history growth, tail size
    varied on purpose.

    The fake provider bills exactly what the local estimator would, plus the
    per-message envelope, so the residual is attributable: it must be the
    envelope of the messages added since the anchor and nothing else. If the
    anchor were recorded against the request, the residual would instead swing
    with the size of the *previous* turn's tail — which is the whole defect.
    """
    agent = _make_agent()
    cm = agent.context_manager
    from agentao.context_manager import _MESSAGE_ENVELOPE_TOKENS as ENV

    def _bill(request):
        return sum(cm._count_message_tokens(m) for m in request) + ENV * len(request)

    estimates: list = []
    seen_tail_tokens: list = []
    real = cm._threshold_token_estimate

    def _record_estimate(messages, tail_tokens=0):
        value = real(messages, tail_tokens=tail_tokens)
        estimates.append(value)
        seen_tail_tokens.append(tail_tokens)
        return value

    cm._threshold_token_estimate = _record_estimate

    billed: list = []

    def _call(messages, tools, token):
        billed.append(_bill(list(messages)))
        return _fake_response("ok", prompt_tokens=billed[-1])

    agent._llm_call = _call

    # Tail size swings by three orders of magnitude across the run; history
    # grows by exactly two messages (user + assistant) per turn.
    todo_counts = [0, 1, 40, 2, 0, 60, 3, 0, 25, 1, 80, 0]
    for n in todo_counts:
        if n:
            agent.todo_tool.execute(todos=[
                {"content": f"task {i} " + "x" * 40, "status": "pending"}
                for i in range(n)
            ])
        else:
            agent.todo_tool.execute(todos=[])
        agent.chat("go")

    assert len(estimates) == len(todo_counts) == len(billed)
    assert len(set(seen_tail_tokens)) > 3, "tail size did not actually vary"

    # Turn 1 has no anchor yet (full local estimate, envelope-free), so the
    # invariant starts at turn 2.
    residuals = [b - e for b, e in zip(billed[1:], estimates[1:])]
    # Two new persistent messages per turn since the anchor: the user message
    # and the assistant answer.
    assert residuals == [2 * ENV] * len(residuals), residuals

    # Stated as the gate asks it: the error is uncorrelated with tail size.
    by_tail = {t: r for t, r in zip(seen_tail_tokens[1:], residuals)}
    assert len(set(by_tail.values())) == 1, by_tail
    print(f"✅ Residual constant at {2 * ENV} tokens over {len(residuals)} turns, "
          f"tail estimates {min(seen_tail_tokens)}–{max(seen_tail_tokens)}")


# ---------------------------------------------------------------------------
# Replay delta baseline
# ---------------------------------------------------------------------------


def test_the_replay_delta_baseline_counts_history_not_the_tail():
    """``run_llm_call`` records the baseline for the next call's delta. Counting
    the request-only tail there starts the next delta one message late, which
    silently drops a real history message from the audit record."""
    from agentao.runtime.llm_call import run_llm_call

    agent = _make_agent()
    agent.llm.chat_stream = lambda **kwargs: _fake_response("ok")
    agent._llm_call_seq = 0
    agent._llm_call_last_msg_count = 0

    persistent = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
    ]
    tail = {"role": "user", "content": "<system-reminder>t</system-reminder>"}

    agent._llm_request_tail_count = 1
    run_llm_call(agent, persistent + [tail], tools=[])
    assert agent._llm_call_last_msg_count == len(persistent)

    agent._llm_request_tail_count = 0
    run_llm_call(agent, persistent, tools=[])
    assert agent._llm_call_last_msg_count == len(persistent)
    print("✅ Delta baseline stays in history units")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
