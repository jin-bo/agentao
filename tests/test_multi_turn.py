"""Tool calls across rounds and across turns.

The offline test scripts the provider below the real ``openai`` SDK
(``tests/support/openai_responses_wire.py``), so it runs everywhere and makes
no network request. The live test is opt-in with ``AGENTAO_TEST_LIVE_LLM=1``
and uses the shell's provider environment as-is (#468).
"""

import json
import os
from pathlib import Path

import pytest

from agentao import Agentao
from tests.support.openai_responses_wire import (
    Wire, attach, completed, created, function_call_events, function_call_item,
    message_item, stream_of, text_events,
)

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _live_llm_opted_in() -> bool:
    return os.getenv("AGENTAO_TEST_LIVE_LLM", "").strip().lower() in {"1", "true", "yes", "on"}


def _tool_call(call_id: str, name: str, arguments: dict) -> bytes:
    raw = json.dumps(arguments)
    return stream_of(created(), function_call_events(0, call_id, name, raw),
                     completed([function_call_item(call_id, name, raw)]))


def _answer(text: str) -> bytes:
    return stream_of(created(), text_events(0, text), completed([message_item(text)]))


def _outputs(request: dict) -> dict:
    """``call_id`` -> output for every tool result the request sends back."""
    return {item["call_id"]: item["output"] for item in request["input"]
            if item.get("type") == "function_call_output"}


def test_tool_results_carry_across_rounds_and_turns(tmp_path: Path):
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\n---\nDemo skill body.\n", encoding="utf-8")

    agent = Agentao(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                    api_format="openai-responses", working_directory=tmp_path)
    try:
        wire = attach(agent.llm, Wire(
            _tool_call("call_ls", "list_directory", {"directory_path": "skills"}),
            _tool_call("call_read", "read_file", {"file_path": "skills/demo/SKILL.md"}),
            _answer("demo is a skill"),
            _answer("still demo"),
        ))

        assert agent.chat("List the skills directory and describe one skill") == "demo is a skill"
        assert agent.chat("Which skill was that?") == "still demo"

        assert len(wire.requests) == 4
        # Round 2 sees round 1's result; round 3 sees both.
        assert "demo" in _outputs(wire.requests[1])["call_ls"]
        assert "Demo skill body." in _outputs(wire.requests[2])["call_read"]
        assert set(_outputs(wire.requests[2])) == {"call_ls", "call_read"}
        # The next turn still sends the earlier turn's results back.
        assert set(_outputs(wire.requests[3])) == {"call_ls", "call_read"}

        # History keeps ``call_id|fc_…`` on this wire (``llm/_tool_ids.py``).
        tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
        assert [m["tool_call_id"].split("|")[0] for m in tool_messages] == ["call_ls", "call_read"]
    finally:
        agent.close()


@pytest.mark.skipif(not _live_llm_opted_in(), reason="live LLM test; set AGENTAO_TEST_LIVE_LLM=1")
def test_multi_turn_tool_calls_live(tmp_path: Path):
    """The model drives real tool calls against the configured provider."""
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\n---\nDemo skill body.\n", encoding="utf-8")

    agent = Agentao(working_directory=tmp_path)
    try:
        response = agent.chat("List the contents of the skills directory and tell me about one of the skills")
        assert "[LLM API error:" not in response
        assert response.strip()
        assert any(m.get("role") == "tool" for m in agent.messages)
    finally:
        agent.close()
