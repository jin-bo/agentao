"""A constructor that raises releases what it built.

The caller of a failed ``Agentao(...)`` never gets the object, so cannot
call ``close()`` — and ``with Agentao(...)`` never reaches ``__exit__``. The
MCP manager and memory manager the constructor created are released before
the exception propagates; ones the caller passed in are left alone, since
the caller still holds them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentao import Agentao
from agentao.memory import MemoryManager
from agentao.tooling import mcp_tools


class _FakeMcp:
    def __init__(self) -> None:
        self.disconnects = 0

    def connect_all(self) -> None:
        pass

    def disconnect_all(self) -> None:
        self.disconnects += 1


def _build(tmp_path: Path, **kw):
    return Agentao(
        working_directory=tmp_path,
        api_key="k",
        base_url="https://test.local/v1",
        model="m",
        logger=logging.getLogger("test_agentao_init_failure_cleanup"),
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


def test_a_failure_after_mcp_connects_disconnects_the_built_manager(
    tmp_path, monkeypatch, memory_closes
):
    """``enabled_tools`` is checked after MCP has connected; a typo there
    used to leave the connections and their loop thread running."""
    built = _FakeMcp()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: built)
    with pytest.raises(ValueError):
        _build(tmp_path, enabled_tools={"no_such_tool"})
    assert built.disconnects == 1
    assert len(memory_closes) == 1  # the transient/project store it opened


def test_injected_managers_are_left_to_the_caller(
    tmp_path, monkeypatch, memory_closes
):
    injected_mcp = _FakeMcp()
    injected_memory = MemoryManager(project_store=None)
    monkeypatch.setattr("agentao.agent.register_mcp_tools", lambda *a: None)
    with pytest.raises(ValueError):
        _build(
            tmp_path,
            enabled_tools={"no_such_tool"},
            mcp_manager=injected_mcp,
            memory_manager=injected_memory,
        )
    assert injected_mcp.disconnects == 0
    assert memory_closes == []


def test_a_successful_construction_releases_nothing(
    tmp_path, monkeypatch, memory_closes
):
    built = _FakeMcp()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: built)
    agent = _build(tmp_path)
    try:
        assert built.disconnects == 0
        assert memory_closes == []
    finally:
        agent.close()
    assert built.disconnects == 1


def test_the_original_exception_survives_a_failing_release(
    tmp_path, monkeypatch
):
    class _BrokenMcp(_FakeMcp):
        def disconnect_all(self) -> None:
            raise RuntimeError("disconnect broke")

    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: _BrokenMcp())
    with pytest.raises(ValueError, match="no_such_tool"):
        _build(tmp_path, enabled_tools={"no_such_tool"})


def test_init_mcp_disconnects_when_tool_registration_fails(monkeypatch):
    """Inside ``init_mcp`` the manager is not returned yet, so nothing else
    could release it."""
    managers: list[_FakeMcp] = []

    def _make(configs):
        managers.append(_FakeMcp())
        return managers[-1]

    def _boom(agent, manager):
        raise RuntimeError("registration broke")

    mcp_tools._ensure_mcp_classes()  # bind the real name, so it can be patched
    monkeypatch.setattr(mcp_tools, "McpClientManager", _make)
    monkeypatch.setattr(mcp_tools, "register_mcp_tools", _boom)
    agent = SimpleNamespace(
        _mcp_registry=SimpleNamespace(list_servers=lambda: {"srv": {"command": "x"}}),
        _extra_mcp_servers=None,
        llm=SimpleNamespace(logger=logging.getLogger("test")),
    )
    with pytest.raises(RuntimeError, match="registration broke"):
        mcp_tools.init_mcp(agent)  # type: ignore[arg-type]
    assert [m.disconnects for m in managers] == [1]


def test_factory_closes_the_memory_manager_it_built(
    tmp_path, monkeypatch, memory_closes
):
    """To ``Agentao`` the factory's memory manager is injected, so the
    factory releases it when construction fails."""
    from agentao.embedding import build_from_environment

    monkeypatch.setattr(
        "agentao.embedding.factory.safe_load_dotenv", lambda *a, **kw: None
    )
    monkeypatch.setattr("agentao.paths.user_root", lambda: tmp_path / "home")
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://test.local/v1")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    built = _FakeMcp()
    monkeypatch.setattr(Agentao, "_init_mcp", lambda self: built)
    with pytest.raises(ValueError):
        build_from_environment(
            working_directory=tmp_path, enabled_tools={"no_such_tool"}
        )
    assert built.disconnects == 1
    assert len(memory_closes) == 1


def test_init_mcp_disconnects_when_connect_is_interrupted(monkeypatch):
    """``connect_all`` swallows ``Exception`` only; a Ctrl-C while servers
    are still connecting must not leave the manager's loop thread running."""
    managers: list[_FakeMcp] = []

    class _Interrupted(_FakeMcp):
        def connect_all(self) -> None:
            raise KeyboardInterrupt

    def _make(configs):
        managers.append(_Interrupted())
        return managers[-1]

    mcp_tools._ensure_mcp_classes()
    monkeypatch.setattr(mcp_tools, "McpClientManager", _make)
    agent = SimpleNamespace(
        _mcp_registry=SimpleNamespace(list_servers=lambda: {"srv": {"command": "x"}}),
        _extra_mcp_servers=None,
        llm=SimpleNamespace(logger=logging.getLogger("test")),
    )
    with pytest.raises(KeyboardInterrupt):
        mcp_tools.init_mcp(agent)  # type: ignore[arg-type]
    assert [m.disconnects for m in managers] == [1]
