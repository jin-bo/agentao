"""``complete_task`` ends the sub-agent's turn with its answer.

Before, the loop went on after the call. In a real run the model saw its own
answer come back as a tool result, called ``complete_task`` three more times
with "see above", and ended on a one-line reply. The parent got that line, and
the 1,671-character answer was lost.

These tests run the sub-agent's real chat loop and ``ToolRunner``. Only the
model is fake: it plays the reply sequence from that run, so a loop that keeps
going reaches the short replies.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentao.agent import Agentao

pytestmark = pytest.mark.usefixtures("isolated_home", "isolated_skill_dirs")

ANSWER = "## Findings\n" + "Four people, each with a source and a page to write.\n" * 30


def _response(content="", calls=None):
    message = SimpleNamespace(content=content, tool_calls=calls, reasoning_content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if calls else "stop")],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1, total_tokens=11),
        model="test-model",
    )


def _call(name, call_id, **arguments):
    function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    return SimpleNamespace(id=call_id, type="function", function=function)


def _complete(text, call_id="done"):
    return _call("complete_task", call_id, result=text)


# The reply sequence from the run: the answer, then "see above" until a reply.
RECORDED = [
    [_complete(ANSWER, "c1")],
    [_complete("Results already given above.", "c2")],
    [_complete("Submitted.", "c3")],
    "Read-only check reported: create the four pages.",
]


def _script(monkeypatch, replies):
    """Make every sub-agent's model play ``replies``; return the requests it got."""
    queue = list(replies)
    requests = []

    def llm(self, messages, tools, token):
        requests.append(list(messages))
        if not queue:
            raise AssertionError("the sub-agent asked the model again after it finished")
        reply = queue.pop(0)
        if isinstance(reply, str):
            return _response(reply)
        return _response(calls=reply)

    monkeypatch.setattr(Agentao, "_llm_call", llm)
    return requests, queue


def _parent(tmp_path):
    return Agentao(
        working_directory=tmp_path, api_key="k", base_url="https://test.local/v1",
        model="m", enable_builtin_agents=True,
    )


def _run(tmp_path):
    parent = _parent(tmp_path)
    try:
        return parent.tools.tools["agent_generalist"]._run_sync("check the four people")
    finally:
        parent.close()


def test_the_parent_gets_the_answer_not_a_later_line(tmp_path, monkeypatch):
    requests, queue = _script(monkeypatch, RECORDED)

    result, stats = _run(tmp_path)

    assert result == ANSWER.strip()
    assert stats["incomplete"] is None
    assert len(requests) == 1, "the turn ends on the call; the model is not asked again"
    assert len(queue) == 3


def test_the_first_call_in_a_batch_wins(tmp_path, monkeypatch):
    _script(monkeypatch, [[_complete(ANSWER, "c1"), _complete("Done.", "c2")]])

    result, _ = _run(tmp_path)

    assert result == ANSWER.strip()


def test_other_calls_in_the_batch_still_run_and_history_stays_valid(tmp_path, monkeypatch):
    seen = []
    original = Agentao.chat

    def chat(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        finally:
            seen.append(list(self.messages))

    monkeypatch.setattr(Agentao, "chat", chat)
    (tmp_path / "notes.txt").write_text("the notes")
    _script(monkeypatch, [[
        _call("read_file", "r1", file_path=str(tmp_path / "notes.txt")),
        _complete(ANSWER, "c1"),
    ]])

    result, _ = _run(tmp_path)

    assert result == ANSWER.strip()
    (history,) = seen
    declared = {c["id"] for m in history for c in m.get("tool_calls") or ()}
    answered = {m["tool_call_id"] for m in history if m.get("role") == "tool"}
    assert declared == answered == {"r1", "c1"}
    read = next(m for m in history if m.get("tool_call_id") == "r1")
    assert "the notes" in read["content"]
    assert history[-1] == {"role": "assistant", "content": ANSWER.strip()}


def test_an_empty_answer_is_reported_as_no_output(tmp_path, monkeypatch):
    _script(monkeypatch, [[_complete("   ")]])

    result, stats = _run(tmp_path)

    # The sub-agent said it was done, so the classifier still counts it complete;
    # the parent sees the placeholder rather than an empty string.
    assert result == "[No response]"
    assert stats["incomplete"] is None


def test_an_answer_of_only_tag_characters_is_no_output(tmp_path, monkeypatch):
    # Unicode tag characters are stripped by the sanitize, leaving nothing.
    _script(monkeypatch, [[_complete("\U000E0041\U000E0042")]])

    result, _ = _run(tmp_path)

    assert result == "[No response]"


def test_a_turn_without_complete_task_is_unchanged(tmp_path, monkeypatch):
    requests, _ = _script(monkeypatch, [
        [_call("list_directory", "l1", path=str(tmp_path))],
        "plain answer",
    ])

    result, _ = _run(tmp_path)

    assert result == "plain answer"
    assert len(requests) == 2


def test_a_top_level_turn_is_unchanged(tmp_path, monkeypatch):
    """The parent has no ``complete_task``; its turn ends on its own reply."""
    requests, _ = _script(monkeypatch, [
        [_call("list_directory", "l1", path=str(tmp_path))],
        "parent answer",
    ])
    parent = _parent(tmp_path)
    try:
        assert "complete_task" not in parent.tools.tools
        assert parent.chat("hello") == "parent answer"
        assert parent.tool_runner.last_task_complete is None
    finally:
        parent.close()
    assert len(requests) == 2
