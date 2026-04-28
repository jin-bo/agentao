"""Issue #12 — ``Agentao.arun()`` bridges sync chat into async hosts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch


def _make_agent(tmp_path):
    with patch("agentao.agent.LLMClient") as mock_llm_cls, \
         patch("agentao.tooling.mcp_tools.load_mcp_config", return_value={}), \
         patch("agentao.tooling.mcp_tools.McpClientManager"):
        mock_llm = Mock()
        mock_llm.logger = Mock()
        mock_llm.model = "gpt-test"
        mock_llm_cls.return_value = mock_llm

        from agentao.agent import Agentao
        return Agentao(working_directory=tmp_path)


def test_arun_dispatches_to_chat(tmp_path):
    agent = _make_agent(tmp_path)
    fake_response = MagicMock()
    fake_response.choices[0].message.tool_calls = None
    fake_response.choices[0].message.content = "ok"
    fake_response.choices[0].message.reasoning_content = None
    agent._llm_call = Mock(return_value=fake_response)

    result = asyncio.run(agent.arun("hi"))
    assert result == "ok"
    assert any(m["role"] == "user" for m in agent.messages)


def test_arun_does_not_block_event_loop(tmp_path):
    """`arun` must hand the sync work to a thread so the loop stays alive."""
    agent = _make_agent(tmp_path)

    side_thread_seen = []

    def _fake_chat(*args, **kwargs):
        import threading
        side_thread_seen.append(threading.current_thread().name)
        return "done"

    agent.chat = _fake_chat  # type: ignore[assignment]

    async def _run():
        # Schedule another task — if arun runs sync on the loop thread,
        # this co-task wouldn't get a chance to record before arun returns.
        marker = []

        async def _record():
            await asyncio.sleep(0)
            marker.append("co")

        result, _ = await asyncio.gather(agent.arun("hi"), _record())
        return result, marker

    result, marker = asyncio.run(_run())
    assert result == "done"
    assert marker == ["co"]
    # The chat call landed on a different thread than the loop's main thread.
    assert side_thread_seen and side_thread_seen[0] != "MainThread"
