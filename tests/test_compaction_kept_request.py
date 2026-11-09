"""Compaction keeps the request of the turn in progress.

Every history here opens its turns the way ``ChatLoopRunner.run`` does, and
``test_a_real_turn_writes_what_the_recogniser_reads`` ties that shape to the
runner. The ``[PIN]`` prefix this replaced had a test that inserted its message
directly, so it stayed green for months after a real turn stopped matching.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentao.compaction.request import (
    KEPT_REQUEST_NOTE,
    is_kept_request,
    is_user_request,
    turn_reminder,
)
from agentao.context_manager import ContextManager, PrepareRejected
from agentao.memory.crystallizer import MemoryCrystallizer

REMINDER = turn_reminder(datetime(2026, 11, 9, 2, 40, 4))
CONSTRAINT = "HARD CONSTRAINT: never touch prod"
TASK = (
    "".join(f"target-{i:02d} " + "x" * 280 + "\n" for i in range(1, 11)) + CONSTRAINT
)


def _make_cm(max_tokens=200_000, memory_manager=None):
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    return ContextManager(
        llm, Mock(), max_tokens=max_tokens, memory_manager=memory_manager,
    )


def _request(text=TASK):
    return {"role": "user", "content": REMINDER + text}


def _exchange(i):
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"c{i}",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"file_path": "a"}'},
            }],
        },
        {"role": "tool", "tool_call_id": f"c{i}", "name": "read_file", "content": "data"},
    ]


def _one_turn(exchanges=20, request=None):
    """A batch run: one request, then only tool traffic."""
    out = [request or _request()]
    for i in range(exchanges):
        out += _exchange(i)
    return out


def _prep(cm, messages):
    prep = cm.prepare_compaction(
        messages, trigger="auto", kind="full", reason="compression_threshold",
    )
    assert not isinstance(prep, PrepareRejected)
    return prep


def _kept(messages):
    return [m for m in messages if is_kept_request(m)]


def _is_valid(messages):
    calls = {c["id"] for m in messages for c in (m.get("tool_calls") or [])}
    answers = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
    return calls == answers


# ---------------------------------------------------------------------------
# The recogniser against the real producer
# ---------------------------------------------------------------------------


def test_a_real_turn_writes_what_the_recogniser_reads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class Stop(Exception):
        pass

    with patch("agentao.agent.LLMClient") as llm_class:
        llm_class.return_value = Mock(logger=Mock(), model="gpt-4")
        from agentao.agent import Agentao

        agent = Agentao(working_directory=Path.cwd())

    seen = []

    def capture(role, content):
        seen.append({"role": role, "content": content})
        raise Stop

    agent.add_message = capture
    with pytest.raises(Stop):
        agent.chat("do the task")
    assert is_user_request(seen[0])


@pytest.mark.parametrize(
    "content",
    [
        "<system-reminder>\nBackground agent update:\nresult\n</system-reminder>",
        "<system-reminder>Stop hook injected this</system-reminder>\nfollow up",
        '<system-reminder>\nSkill loaded: "s"\nbody\n</system-reminder>',
        "[hook_additional_context] context: x",
        "plain text",
        # Server text that closes a reminder and writes a date of its own.
        "<system-reminder>\nBackground agent update:\n</system-reminder>\n" + REMINDER,
    ],
)
def test_other_user_messages_are_not_requests(content):
    assert not is_user_request({"role": "user", "content": content})


@pytest.mark.parametrize("role", ["assistant", "tool", "system"])
def test_only_a_user_message_is_a_request(role):
    assert not is_user_request({"role": role, "content": REMINDER + "x"})


def test_a_multimodal_turn_is_a_request():
    assert is_user_request({
        "role": "user",
        "content": [
            {"type": "text", "text": REMINDER + "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    })


# ---------------------------------------------------------------------------
# A real turn, end to end
# ---------------------------------------------------------------------------


def _response(content="", tool_calls=None, prompt_tokens=10):
    message = SimpleNamespace(
        content=content, tool_calls=tool_calls, reasoning_content=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=1,
            total_tokens=prompt_tokens + 1,
        ),
        model="test-model",
    )


def _read_call(i):
    return SimpleNamespace(
        id=f"call{i}",
        type="function",
        function=SimpleNamespace(
            name="read_file", arguments=json.dumps({"file_path": f"f{i}.txt"}),
        ),
    )


def test_requests_after_a_compaction_carry_the_whole_task(isolated_cwd):
    """Long batch turn: one prompt, then a compaction in the middle of its tools."""
    from agentao import Agentao

    agent = Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
    )
    window = agent.context_manager.effective_max_tokens
    calls = 30
    for i in range(calls):
        Path(f"f{i}.txt").write_text("data")

    # Usage crosses the threshold at call 25, so the cut lands inside the turn.
    responses = iter(
        [
            _response(
                tool_calls=[_read_call(i)],
                prompt_tokens=int(window * 0.95) if i == 25 else 10,
            )
            for i in range(calls)
        ]
        + [_response("done")]
    )
    sent: list = []

    def llm_call(messages, tools, token):
        sent.append(list(messages))
        return next(responses)

    summarizer_input: list = []

    def summarize(formatted, **_kwargs):
        summarizer_input.append(formatted)
        return "SUMMARY"

    agent._llm_call = llm_call
    agent.context_manager._summarize_formatted = summarize

    assert agent.chat(TASK) == "done"

    assert summarizer_input, "the turn never compacted, so this test proves nothing"
    assert "target-01" in summarizer_input[0]
    after = [
        request for request in sent
        if any("[Conversation Summary]" in str(m.get("content")) for m in request)
    ]
    assert after
    for request in after:
        (kept,) = _kept(request)
        assert kept["content"].endswith(TASK)
        assert CONSTRAINT in kept["content"]
        assert kept["content"].count(KEPT_REQUEST_NOTE) == 1
    # The history the next turn starts from has it too, once.
    (kept,) = _kept(agent.messages)
    assert kept["content"].endswith(TASK)


# ---------------------------------------------------------------------------
# Full compaction
# ---------------------------------------------------------------------------


def test_a_cut_inside_a_turn_keeps_the_whole_request():
    cm = _make_cm()
    prep = _prep(cm, _one_turn())
    result = cm.commit_compaction(prep, "a summary")

    kept = _kept(result)
    assert len(kept) == 1
    assert kept[0]["content"] == KEPT_REQUEST_NOTE + REMINDER + TASK
    assert CONSTRAINT in kept[0]["content"]
    assert _is_valid(result)


def test_the_kept_request_follows_the_summary():
    cm = _make_cm()
    result = cm.commit_compaction(_prep(cm, _one_turn()), "a summary")
    summary_at = next(
        i for i, m in enumerate(result)
        if str(m.get("content")).startswith("[Conversation Summary]")
    )
    assert result.index(_kept(result)[0]) > summary_at


def test_the_summarizer_still_sees_the_request():
    prep = _prep(_make_cm(), _one_turn())
    assert "target-01" in prep.summary_input
    assert "[USER]" in prep.summary_input


def test_a_cut_between_turns_keeps_nothing():
    cm = _make_cm()
    messages = []
    for i in range(14):
        messages += [_request(f"request {i}"), {"role": "assistant", "content": f"answer {i}"}]
    prep = _prep(cm, messages)
    assert prep.kept_request is None
    assert _kept(cm.commit_compaction(prep, "a summary")) == []


def test_a_finished_turn_keeps_nothing():
    """``/compact`` between turns: a note to continue would restart the work."""
    cm = _make_cm()
    messages = _one_turn() + [{"role": "assistant", "content": "all done"}]
    prep = _prep(cm, messages)
    assert prep.kept_request is None
    assert "target-01" in prep.summary_input


def test_the_note_promises_no_summary():
    """The last rung keeps none, so the note cannot state that one exists."""
    assert "The summary above" not in KEPT_REQUEST_NOTE


def test_a_new_instruction_at_the_cap_is_the_request_to_keep(isolated_cwd):
    from agentao import Agentao
    from agentao.transport.sdk import SdkTransport

    transport = SdkTransport(
        on_max_iterations=lambda _n, _pending: {
            "action": "new_instruction", "message": "change of plan",
        },
    )
    agent = Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
        transport=transport,
    )
    Path("f0.txt").write_text("data")
    responses = iter([_response(tool_calls=[_read_call(0)]), _response("done")])
    agent._llm_call = lambda messages, tools, token: next(responses)
    agent.chat("original task", max_iterations=1)

    (instruction,) = [
        m for m in agent.messages if "change of plan" in str(m.get("content"))
    ]
    assert is_user_request(instruction)


def test_later_compactions_keep_one_copy_with_one_note():
    cm = _make_cm()
    history = cm.commit_compaction(_prep(cm, _one_turn()), "s1")
    for round_ in (2, 3):
        for i in range(20):
            history += _exchange(round_ * 100 + i)
        history = cm.commit_compaction(_prep(cm, history), f"s{round_}")
        kept = _kept(history)
        assert len(kept) == 1
        assert kept[0]["content"] == KEPT_REQUEST_NOTE + REMINDER + TASK


def test_a_new_turn_ends_the_keeping():
    cm = _make_cm()
    history = cm.commit_compaction(_prep(cm, _one_turn()), "s1")
    history.append(_request("the next task"))
    for i in range(20):
        history += _exchange(500 + i)
    history.append(_request("the last task"))
    history.append({"role": "assistant", "content": "done"})
    result = cm.commit_compaction(_prep(cm, history), "s2")
    assert _kept(result) == []
    assert "the last task" in str(result)


def test_the_note_stays_out_of_the_summary_input():
    cm = _make_cm()
    history = cm.commit_compaction(_prep(cm, _one_turn()), "s1")
    for i in range(20):
        history += _exchange(100 + i)
    assert KEPT_REQUEST_NOTE not in _prep(cm, history).summary_input


def test_a_background_note_is_not_kept_in_place_of_the_request():
    messages = _one_turn(exchanges=6)
    messages.append({
        "role": "user",
        "content": "<system-reminder>\nBackground agent update:\nx\n</system-reminder>",
    })
    for i in range(20):
        messages += _exchange(100 + i)
    prep = _prep(_make_cm(), messages)
    assert TASK in prep.kept_request["content"]


def test_an_oversized_request_keeps_both_ends_and_states_the_gap():
    cm = _make_cm(max_tokens=50_000)
    big = "START " + "word " * 100_000 + CONSTRAINT
    prep = _prep(cm, _one_turn(request=_request(big)))
    content = prep.kept_request["content"]
    assert "START" in content
    assert CONSTRAINT in content
    assert "omitted" in content
    assert cm.count_tokens_in_text(content) <= cm._summary_input_budget() * 1.1
    # The compaction shrinks the history, the point of running it.
    result = cm.commit_compaction(prep, "s")
    assert cm.estimate_tokens(result) < prep.pre_tokens / 2


def test_an_oversized_request_with_dense_ends_meets_the_budget():
    cm = _make_cm(max_tokens=50_000)
    big = "\u6587" * 8_000 + "word " * 100_000 + "\u6587" * 8_000
    content = _prep(cm, _one_turn(request=_request(big))).kept_request["content"]
    assert (
        cm.count_tokens_in_text(content)
        <= cm._summary_input_budget() + cm.count_tokens_in_text(KEPT_REQUEST_NOTE)
    )


def test_a_malformed_text_part_does_not_end_the_turn():
    request = {
        "role": "user",
        "content": [
            {"type": "text", "text": REMINDER + "look"},
            {"type": "text", "text": None},
        ],
    }
    content = _make_cm().apply_minimal_history(
        _one_turn(request=request), keep_tail=2,
    )[0]["content"]
    assert content.endswith("look")


def test_images_are_not_carried():
    cm = _make_cm()
    request = {
        "role": "user",
        "content": [
            {"type": "text", "text": REMINDER + "look at this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 5000}},
        ],
    }
    content = _prep(cm, _one_turn(request=request)).kept_request["content"]
    assert isinstance(content, str)
    assert "look at this" in content
    assert "base64" not in content
    assert "1 attachment(s)" in content


def test_a_history_with_no_real_turn_is_unchanged():
    """Hosts and old tests can write history directly: nothing is invented."""
    messages = [{"role": "user", "content": "plain"}]
    for i in range(20):
        messages += _exchange(i)
    assert _prep(_make_cm(), messages).kept_request is None


# ---------------------------------------------------------------------------
# [PIN] is gone
# ---------------------------------------------------------------------------


def test_the_pin_prefix_has_no_special_meaning():
    cm = _make_cm()
    messages = [{"role": "user", "content": "[PIN] old syntax"}]
    for i in range(20):
        messages += _exchange(i)
    prep = _prep(cm, messages)
    result = cm.commit_compaction(prep, "s")
    assert "[PIN] old syntax" in prep.summary_input
    assert not any("[PIN]" in str(m.get("content")) for m in result)


@pytest.mark.parametrize("role", ["tool", "assistant"])
def test_a_pin_prefix_no_longer_orphans_a_tool_exchange(role):
    """The old predicate read no role, and moved these out of their exchange."""
    cm = _make_cm()
    messages = _one_turn(exchanges=2)
    pair = _exchange(99)
    pair[0 if role == "assistant" else 1]["content"] = "[PIN] text"
    messages[1:1] = pair
    for i in range(20):
        messages += _exchange(200 + i)
    assert _is_valid(cm.commit_compaction(_prep(cm, messages), "s"))


# ---------------------------------------------------------------------------
# The last rung
# ---------------------------------------------------------------------------


def test_the_last_rung_keeps_the_request():
    cm = _make_cm()
    messages = _one_turn()
    result = cm.apply_minimal_history(messages, keep_tail=2)
    assert is_kept_request(result[0])
    assert CONSTRAINT in result[0]["content"]
    assert result[1:] == messages[-2:]
    assert _is_valid(result)


def test_the_last_rung_reports_the_count_it_applies():
    cm = _make_cm()
    messages = _one_turn()
    prepared = cm.prepare_minimal_history(messages, keep_tail=2)
    assert prepared.keep_tail == len(cm.apply_minimal_history(messages, keep_tail=2))


def test_an_empty_window_stays_empty():
    assert _make_cm().apply_minimal_history(_one_turn(), keep_tail=0) == []


def test_the_last_rung_stands_down_when_it_would_shed_only_the_request():
    cm = _make_cm()
    messages = [_request("short")] + _exchange(0)
    assert cm.minimal_history_would_help(messages, keep_tail=2) is False


def test_the_last_rung_still_helps_when_it_sheds_more():
    assert _make_cm().minimal_history_would_help(_one_turn(), keep_tail=2) is True


def test_the_last_rung_helps_when_only_an_oversized_request_is_cut():
    cm = _make_cm(max_tokens=50_000)
    messages = [_request("word " * 100_000)] + _exchange(0)
    assert cm.minimal_history_would_help(messages, keep_tail=2) is True


# ---------------------------------------------------------------------------
# Readers of the user's own words
# ---------------------------------------------------------------------------


def test_crystallize_evidence_quotes_the_goal_not_the_reminders():
    from types import SimpleNamespace

    from agentao.cli.commands_ext.crystallize._evidence import (
        collect_crystallize_evidence,
    )

    messages = [
        _request("Fix the login bug."),
        {"role": "user", "content": KEPT_REQUEST_NOTE + REMINDER + "Fix the login bug."},
    ]
    cli = SimpleNamespace(agent=SimpleNamespace(messages=messages))
    assert collect_crystallize_evidence(cli).user_goals == ["Fix the login bug."]


def test_a_kept_request_is_not_crystallized_twice():
    memory = Mock()
    cm = _make_cm(memory_manager=memory)
    request = _request("I prefer fish shell")
    history = cm.commit_compaction(_prep(cm, _one_turn(request=request)), "s1")
    for i in range(20):
        history += _exchange(100 + i)
    cm.commit_compaction(_prep(cm, history), "s2")

    first, second = [c.args[0] for c in memory.crystallize_user_messages.call_args_list]
    assert request in first
    assert not any(is_kept_request(m) for m in second)
    assert not any(
        "fish shell" in str(p.evidence)
        for p in MemoryCrystallizer().extract_from_user_messages(second)
    )
