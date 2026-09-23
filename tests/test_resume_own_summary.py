"""A resumed conversation does not read its own summaries back as an earlier one's.

Session summaries are written under ``MemoryManager._session_id``, and the
cross-session tail (``get_cross_session_tail``) injects every summary whose id
is *not* that one into ``<memory-stable>`` as "previous sessions". That id was a
fresh random one per manager and per ``archive_session()``, unrelated to the
conversation's id — so after ``--resume``, ``/sessions resume`` or ACP
``session/load``, the resumed conversation's own summaries came back through
the tail while the same text was already in history as
``[Conversation Summary]``.

The memory session is now keyed by the conversation id on every path that has
one. These tests use a real ``Agentao`` on the CLI's own layout —
``<working_directory>/.agentao/memory.db`` — and a second ``Agentao`` on the
same directory stands in for the restarted process.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentao import Agentao
from agentao.embedding.sessions import save_session

from .support.acp_server import make_initialized_server


def _agent(wd: Path) -> Agentao:
    return Agentao(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        working_directory=wd,
        logger=logging.getLogger("test-resume-own-summary"),
    )


@pytest.fixture
def agents():
    made: list = []

    def make(wd: Path) -> Agentao:
        agent = _agent(wd)
        made.append(agent)
        return agent

    yield make
    for agent in made:
        agent.close()


def _write_summary(agent: Agentao, conversation_id: str, text: str) -> None:
    agent.memory_manager.archive_session(conversation_id)
    agent.memory_manager.save_session_summary(text, tokens_before=100, messages_summarized=4)


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------


def test_a_manager_keyed_by_the_conversation_id_excludes_its_own_summaries(
    tmp_path, agents
):
    _write_summary(agents(tmp_path), "conv-A", "SUMMARY-OF-A")

    resumed = agents(tmp_path).memory_manager  # the restarted process
    resumed.archive_session("conv-A")
    assert resumed.get_cross_session_tail() == ""

    other = agents(tmp_path).memory_manager
    other.archive_session("conv-B")
    assert "SUMMARY-OF-A" in other.get_cross_session_tail()


def test_a_random_key_is_what_read_them_back(tmp_path, agents):
    """The defect, pinned so the test above cannot pass for another reason."""
    _write_summary(agents(tmp_path), "conv-A", "SUMMARY-OF-A")

    fresh = agents(tmp_path).memory_manager
    fresh.archive_session()
    assert "SUMMARY-OF-A" in fresh.get_cross_session_tail()


@pytest.mark.parametrize("bad", [None, "", 123, MagicMock()])
def test_anything_but_a_nonempty_string_falls_back_to_a_random_id(tmp_path, agents, bad):
    manager = agents(tmp_path).memory_manager
    before = manager._session_id
    manager.archive_session(bad)
    assert isinstance(manager._session_id, str)
    assert len(manager._session_id) == 12
    assert manager._session_id != before


# ---------------------------------------------------------------------------
# CLI: launch-time --resume goes through on_session_start
# ---------------------------------------------------------------------------


def test_on_session_start_keys_memory_by_the_conversation_id(tmp_path, agents, monkeypatch):
    from agentao.cli import session as cli_session

    monkeypatch.setattr(cli_session, "_dispatch_session_start_hooks", lambda cli, *, source: None)
    _write_summary(agents(tmp_path), "conv-A", "SUMMARY-OF-A")

    agent = agents(tmp_path)
    # What ``resume_session(at_launch=True)`` leaves before run_loop's
    # ``on_session_start``: the loaded session's id.
    cli = SimpleNamespace(current_session_id="conv-A", agent=agent)
    cli_session.on_session_start(cli, source="resume")

    assert agent.memory_manager._session_id == "conv-A"
    assert agent.memory_manager.get_cross_session_tail() == ""


def test_a_fresh_cli_session_is_keyed_by_its_new_id(tmp_path, agents, monkeypatch):
    from agentao.cli import session as cli_session

    monkeypatch.setattr(cli_session, "_dispatch_session_start_hooks", lambda cli, *, source: None)
    agent = agents(tmp_path)
    cli = SimpleNamespace(current_session_id=None, agent=agent)
    cli_session.on_session_start(cli, source="startup")

    assert cli.current_session_id
    assert agent.memory_manager._session_id == cli.current_session_id


# ---------------------------------------------------------------------------
# ACP: session/new writes under the ACP id, session/load adopts it
# ---------------------------------------------------------------------------


def test_acp_new_then_load_keeps_its_own_summaries_out_of_the_tail(tmp_path, agents):
    from agentao.acp import session_load as acp_session_load
    from agentao.acp import session_new as acp_session_new

    def factory(**kwargs):
        return agents(tmp_path)

    server = make_initialized_server()
    result = acp_session_new.handle_session_new(
        server, {"cwd": str(tmp_path), "mcpServers": []}, agent_factory=factory
    )
    sid = result["sessionId"]
    first = server.sessions.require(sid).agent
    assert first.memory_manager._session_id == sid

    # A compaction in that session writes under the ACP id.
    first.memory_manager.save_session_summary("SUMMARY-OF-ACP", tokens_before=1, messages_summarized=1)
    save_session(
        messages=[{"role": "user", "content": "hi"}],
        model="test-model",
        active_skills=[],
        session_id=sid,
        project_root=tmp_path,
    )

    other_server = make_initialized_server()  # the restarted process
    acp_session_load.handle_session_load(
        other_server,
        {"sessionId": sid, "cwd": str(tmp_path), "mcpServers": []},
        agent_factory=factory,
    )
    loaded = other_server.sessions.require(sid).agent
    assert loaded is not first
    assert loaded.memory_manager._session_id == sid
    assert loaded.memory_manager.get_cross_session_tail() == ""
