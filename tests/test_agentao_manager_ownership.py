"""``close()`` releases the managers the agent built, and only those.

An ``mcp_manager=`` / ``memory_manager=`` passed in is the caller's: it may
be shared with other agents, and the caller releases it. So is a manager
assigned over the attribute later; the one the agent built is still
released. Where a caller builds a manager for one agent alone and never
sees it again — ``build_from_environment``, the sub-agent factory — the
agent adopts it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from agentao import Agentao
from agentao.agents.tools._wrapper import AgentToolWrapper
from agentao.memory import MemoryManager


class _FakeMcp:
    def __init__(self) -> None:
        self.disconnects = 0

    def disconnect_all(self) -> None:
        self.disconnects += 1


def _build(tmp_path: Path, **kw) -> Agentao:
    return Agentao(
        working_directory=tmp_path,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        logger=logging.getLogger("test_agentao_manager_ownership"),
        **kw,
    )


@pytest.fixture
def memory_closes(monkeypatch):
    closed: list[MemoryManager] = []
    real = MemoryManager.close

    def _spy(self: MemoryManager) -> None:
        closed.append(self)
        real(self)

    monkeypatch.setattr(MemoryManager, "close", _spy)
    return closed


@pytest.fixture
def no_registration(monkeypatch):
    monkeypatch.setattr("agentao.agent.register_mcp_tools", lambda *a: None)


def test_close_releases_the_managers_the_agent_built(
    tmp_path, monkeypatch, memory_closes
):
    built = _FakeMcp()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: built)
    agent = _build(tmp_path)
    memory = agent.memory_manager
    agent.close()
    assert built.disconnects == 1
    assert agent.mcp_manager is None
    assert memory_closes == [memory]


def test_close_leaves_injected_managers_to_the_caller(
    tmp_path, memory_closes, no_registration
):
    mcp = _FakeMcp()
    memory = MemoryManager(project_store=None)
    agent = _build(tmp_path, mcp_manager=mcp, memory_manager=memory)
    agent.close()
    assert mcp.disconnects == 0
    assert agent.mcp_manager is mcp
    assert memory_closes == []


def test_a_manager_assigned_later_is_left_alone_and_the_built_one_released(
    tmp_path, monkeypatch, memory_closes
):
    built = _FakeMcp()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: built)
    agent = _build(tmp_path)
    built_memory = agent.memory_manager
    assigned_mcp = _FakeMcp()
    assigned_memory = MemoryManager(project_store=None)
    agent.mcp_manager = assigned_mcp  # type: ignore[assignment]
    agent.memory_manager = assigned_memory
    agent.close()
    assert built.disconnects == 1
    assert assigned_mcp.disconnects == 0
    assert agent.mcp_manager is assigned_mcp
    assert memory_closes == [built_memory]


def test_a_second_close_does_not_disconnect_again(tmp_path, monkeypatch):
    built = _FakeMcp()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: built)
    agent = _build(tmp_path)
    agent.close()
    agent.close()
    assert built.disconnects == 1


class _InterruptedOnce(_FakeMcp):
    def disconnect_all(self) -> None:
        super().disconnect_all()
        if self.disconnects == 1:
            raise KeyboardInterrupt


def test_a_close_interrupted_in_the_disconnect_can_be_retried(
    tmp_path, monkeypatch
):
    """``disconnect_all`` is final and bounded; a repeat call waits for the
    first one's shutdown. So an interrupted close keeps the manager owned."""
    built = _InterruptedOnce()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: built)
    agent = _build(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        agent.close()
    agent.close()
    assert built.disconnects == 2
    assert agent.mcp_manager is None
    agent.close()
    assert built.disconnects == 2


def test_an_interrupted_reinit_leaves_the_old_manager_for_close(
    tmp_path, monkeypatch
):
    built = _InterruptedOnce()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: built)
    agent = _build(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        agent._reinit_mcp()
    agent.close()
    assert built.disconnects == 2


def test_reinit_mcp_disconnects_the_built_manager_and_owns_the_new_one(
    tmp_path, monkeypatch
):
    managers = [_FakeMcp(), _FakeMcp()]
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: managers.pop(0))
    agent = _build(tmp_path)
    first = agent.mcp_manager
    agent._reinit_mcp()
    second = agent.mcp_manager
    assert first.disconnects == 1
    agent.close()
    assert second.disconnects == 1


def test_reinit_mcp_leaves_an_injected_manager_connected(
    tmp_path, monkeypatch, no_registration
):
    injected = _FakeMcp()
    agent = _build(tmp_path, mcp_manager=injected)
    rebuilt = _FakeMcp()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: rebuilt)
    agent._reinit_mcp()
    agent.close()
    assert injected.disconnects == 0
    assert rebuilt.disconnects == 1


def _factory_env(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentao.embedding.factory.safe_load_dotenv", lambda *a, **kw: None
    )
    monkeypatch.setattr("agentao.paths.user_root", lambda: tmp_path / "home")
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://test.local/v1")
    monkeypatch.setenv("OPENAI_MODEL", "m")


def test_the_factory_hands_its_memory_manager_to_the_agent(
    tmp_path, monkeypatch, memory_closes
):
    from agentao.embedding import build_from_environment

    _factory_env(tmp_path, monkeypatch)
    agent = build_from_environment(
        working_directory=tmp_path,
        logger=logging.getLogger("test_agentao_manager_ownership"),
    )
    memory = agent.memory_manager
    agent.close()
    assert memory_closes == [memory]


def test_the_factory_leaves_a_callers_memory_manager_alone(
    tmp_path, monkeypatch, memory_closes
):
    from agentao.embedding import build_from_environment

    _factory_env(tmp_path, monkeypatch)
    memory = MemoryManager(project_store=None)
    agent = build_from_environment(
        working_directory=tmp_path,
        memory_manager=memory,
        logger=logging.getLogger("test_agentao_manager_ownership"),
    )
    agent.close()
    assert memory_closes == []


def test_a_failed_permission_mode_releases_the_factorys_managers(
    tmp_path, monkeypatch, memory_closes
):
    from agentao.embedding import build_from_environment
    from agentao.runtime import permission_mode

    _factory_env(tmp_path, monkeypatch)
    built = _FakeMcp()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: built)

    def _boom(agent, mode):
        raise RuntimeError("mode broke")

    monkeypatch.setattr(permission_mode, "_set_initial_permission_mode", _boom)
    with pytest.raises(RuntimeError, match="mode broke"):
        build_from_environment(
            working_directory=tmp_path,
            permission_mode="read-only",
            logger=logging.getLogger("test_agentao_manager_ownership"),
        )
    assert built.disconnects == 1
    assert len(memory_closes) == 1  # the factory's, closed exactly once


def _agent_tool(parent: Agentao) -> AgentToolWrapper:
    return next(
        t for t in parent.tools.tools.values() if isinstance(t, AgentToolWrapper)
    )


def test_a_sub_agent_closes_its_own_memory_manager(tmp_path, memory_closes):
    parent = _build(tmp_path, enable_builtin_agents=True)
    try:
        sub_agent, _ = _agent_tool(parent)._build_sub_agent(suppress_output=True)
        child_memory = sub_agent.memory_manager
        assert child_memory is not parent.memory_manager
        sub_agent.close()
        assert memory_closes == [child_memory]
    finally:
        parent.close()


def test_a_sub_agent_that_fails_to_construct_closes_its_memory_manager(
    tmp_path, monkeypatch, memory_closes
):
    parent = _build(tmp_path, enable_builtin_agents=True)
    try:
        def _boom(self, *a, **kw):
            raise RuntimeError("replay broke")

        monkeypatch.setattr(Agentao, "_init_replay", _boom)
        with pytest.raises(RuntimeError, match="replay broke"):
            _agent_tool(parent)._build_sub_agent(suppress_output=True)
        assert len(memory_closes) == 1
        assert memory_closes[0] is not parent.memory_manager
    finally:
        monkeypatch.undo()
        parent.close()
