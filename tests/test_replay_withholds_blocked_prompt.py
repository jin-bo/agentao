"""A prompt a ``UserPromptSubmit`` hook blocks or stops never reaches the replay file.

``TURN_BEGIN`` still carries the raw input (hosts pair it with ``TURN_END``);
only the replay adapter holds the ``user_message`` row back until the turn has
passed its hooks (codex#56234). Every check reads the whole replay file, not
just the ``user_message`` rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from agentao import Agentao
from agentao.replay.config import ReplayConfig
from agentao.transport import AgentEvent, EventType

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

SECRET = "customer card 4111-0000-SECRET-PROMPT"


def _agent() -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=Path.cwd(),
        replay_config=ReplayConfig(enabled=True),
    )


def _prompt_hook(agent: Agentao, **hook: Any) -> None:
    from agentao.plugins.hooks._profile import PROFILE_ID
    from agentao.plugins.models import ParsedHookRule

    from ._hook_commands import as_kwargs, emitting

    agent._plugin_hook_rules = [ParsedHookRule(
        event="UserPromptSubmit", hook_type="command", **as_kwargs(emitting(**hook)),
        contract=PROFILE_ID, plugin_name="p", timeout=30,
    )]


def _answer(messages, tools, token):
    message = SimpleNamespace(content="done", tool_calls=None, reasoning_content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _never(messages, tools, token):
    raise AssertionError("the model must not be called")


def _run(agent: Agentao, *prompts: str, **chat_kwargs: Any) -> str:
    path = agent.start_replay(session_id="s")
    assert path is not None
    try:
        for prompt in prompts:
            try:
                agent.chat(prompt, **chat_kwargs)
            except ValueError:
                pass
    finally:
        agent.end_replay()
    return path.read_text(encoding="utf-8")


def _rows(text: str, kind: str) -> List[Dict[str, Any]]:
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [r for r in rows if r.get("kind") == kind]


@pytest.mark.parametrize("hook, withheld", [
    ({"stderr": "refused by policy\n", "exit_code": 2}, "hook_blocked"),
    ({"stdout": '{"continue": false, "stopReason": "paused"}\n'}, "hook_stopped"),
], ids=["block", "stop"])
def test_a_blocked_or_stopped_prompt_is_nowhere_in_the_replay(hook, withheld):
    agent = _agent()
    _prompt_hook(agent, **hook)
    agent._llm_call = _never
    try:
        text = _run(agent, SECRET)
    finally:
        agent.close()

    assert SECRET not in text
    [row] = _rows(text, "user_message")
    assert row["payload"] == {"content": "", "withheld": withheld}
    assert _rows(text, "plugin_hook_fired")
    assert _rows(text, "turn_started") and _rows(text, "turn_completed")


def test_a_prompt_every_hook_allows_is_recorded():
    agent = _agent()
    _prompt_hook(agent)  # exit 0, no output: allow
    agent._llm_call = _answer
    try:
        text = _run(agent, SECRET)
    finally:
        agent.close()

    [row] = _rows(text, "user_message")
    assert row["payload"] == {"content": SECRET}


def test_a_prompt_with_no_hooks_configured_is_recorded():
    agent = _agent()
    agent._llm_call = _answer
    try:
        text = _run(agent, SECRET)
    finally:
        agent.close()

    [row] = _rows(text, "user_message")
    assert row["payload"] == {"content": SECRET}


def test_a_turn_that_fails_before_its_first_request_writes_no_text_and_carries_none_over():
    """A malformed image ends the turn after the hooks and before any model
    request: the verdict never reached ``TURN_START``. The text is dropped,
    not written, and not carried into the next turn."""
    agent = _agent()
    agent._llm_call = _answer
    path = agent.start_replay(session_id="s")
    try:
        with pytest.raises(ValueError):
            agent.chat(SECRET, images=[{"bogus": 1}])
        agent.chat("next prompt")
    finally:
        agent.end_replay()
        agent.close()
    text = path.read_text(encoding="utf-8")

    assert SECRET not in text
    rows = _rows(text, "user_message")
    assert [r["payload"]["content"] for r in rows] == ["next prompt"]


def test_turn_begin_still_carries_the_raw_input_to_hosts():
    agent = _agent()
    _prompt_hook(agent, stderr="refused\n", exit_code=2)
    agent._llm_call = _never
    seen: List[AgentEvent] = []
    inner_emit = agent.transport.emit

    def recording(event):
        seen.append(event)
        return inner_emit(event)

    agent.transport.emit = recording
    try:
        agent.chat(SECRET)
    finally:
        agent.close()

    begins = [e for e in seen if e.type == EventType.TURN_BEGIN]
    ends = [e for e in seen if e.type == EventType.TURN_END]
    assert [e.data["user_message"] for e in begins] == [SECRET]
    assert len(ends) == 1


def test_a_direct_begin_turn_records_the_message_at_once(tmp_path):
    from agentao.replay.adapter import ReplayAdapter

    recorded: List[tuple] = []
    recorder = SimpleNamespace(record=lambda kind, **kw: recorded.append((kind, kw)))
    adapter = ReplayAdapter(SimpleNamespace(emit=lambda e: None), recorder)
    adapter.begin_turn("hello")
    kinds = [str(getattr(k, "value", k)) for k, _ in recorded]
    assert kinds == ["turn_started", "user_message"]
    assert recorded[1][1]["payload"] == {"content": "hello"}
