"""A foreground sub-agent that raises still sends ``__agent_end__``.

``execute()`` sent ``__agent_start__`` and then, on a raising run, only the
public terminal event: the CLI and ACP never heard that the child ended, and
the start had no matching end.

These tests drive the real wrapper against a real parent. They record the
progress callback and the public event stream into one list, so the order of
the two is asserted along with the counts.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from agentao.agent import Agentao
from agentao.cancellation import AgentCancelledError, CancellationToken
from agentao.host.projection import HostSubagentEmitter
from agentao.permissions import PermissionEngine


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


class _Stream:
    def __init__(self, log: List[Tuple[str, Any]]) -> None:
        self._log = log

    def publish(self, event: Any) -> None:
        self._log.append(("public", event))


@pytest.fixture
def parent(tmp_path):
    (tmp_path / "user").mkdir(exist_ok=True)
    agent = Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m",
        enable_builtin_agents=True,
        permission_engine=PermissionEngine(
            project_root=tmp_path, user_root=tmp_path / "user",
        ),
    )
    try:
        yield agent
    finally:
        agent.close()


def _watch(wrapper, *, end_raises: bool = False) -> List[Tuple[str, Any]]:
    log: List[Tuple[str, Any]] = []
    wrapper._subagent_emitter = HostSubagentEmitter(
        _Stream(log), parent_session_id_provider=lambda: "parent-s",
    )

    def step(name, payload):
        log.append((name, payload))
        if end_raises and name == "__agent_end__":
            raise RuntimeError("host callback broke")

    wrapper._step_callback = step
    return log


def _shape(log):
    """``[(kind, state-or-phase), ...]`` in arrival order."""
    return [
        (kind, payload.phase if kind == "public" else payload.state)
        for kind, payload in log
    ]


def _end(log):
    ends = [payload for kind, payload in log if kind == "__agent_end__"]
    assert len(ends) == 1, f"expected one __agent_end__, got {len(ends)}"
    return ends[0]


def _chat_raising(monkeypatch, exc: BaseException) -> None:
    def chat(self, *args, **kwargs):
        raise exc

    monkeypatch.setattr(Agentao, "chat", chat)


def test_an_ordinary_exception_ends_the_run_as_failed(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _chat_raising(monkeypatch, RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        wrapper.execute(task="x")

    assert _shape(log) == [
        ("__agent_start__", "running"),
        ("public", "spawned"),
        ("__agent_end__", "failed"),
        ("public", "failed"),
    ]
    end = _end(log)
    # The type name, as on the public event — never the message, which can
    # carry anything the child was handed.
    assert end.error == "RuntimeError"
    assert log[-1][1].error_type == "RuntimeError"
    # No stats came back, so the counters keep their defaults.
    assert (end.turns, end.tool_calls, end.tokens, end.duration_ms) == (0, 0, 0, 0)
    assert end.agent_name == "generalist"


def test_a_construction_failure_ends_the_run_as_failed(parent):
    """The constructor raises before ``_run_sync`` has a sub-agent, so there
    is no ``stats`` and no ``finally`` usage either."""
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    wrapper._llm_config_getter = lambda: {}

    with pytest.raises(KeyError, match="api_key"):
        wrapper.execute(task="x")

    assert _shape(log) == [
        ("__agent_start__", "running"),
        ("public", "spawned"),
        ("__agent_end__", "failed"),
        ("public", "failed"),
    ]
    end = _end(log)
    assert end.error == "KeyError"
    assert (end.turns, end.tool_calls, end.tokens, end.duration_ms) == (0, 0, 0, 0)


def test_a_raised_cancel_ends_the_run_as_cancelled(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _chat_raising(monkeypatch, AgentCancelledError("user-cancel"))

    with pytest.raises(AgentCancelledError):
        wrapper.execute(task="x")

    assert _shape(log) == [
        ("__agent_start__", "running"),
        ("public", "spawned"),
        ("__agent_end__", "cancelled"),
        ("public", "cancelled"),
    ]
    # A cancel is not an error; the state carries it.
    assert _end(log).error is None


def test_a_base_exception_sends_no_end_and_no_public_terminal(parent, monkeypatch):
    """``KeyboardInterrupt`` is outside both ``except`` branches. The end
    event follows the public event there, so neither is sent."""
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper)
    _chat_raising(monkeypatch, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        wrapper.execute(task="x")

    assert _shape(log) == [
        ("__agent_start__", "running"),
        ("public", "spawned"),
    ]


def test_a_raising_end_callback_does_not_mask_the_run_exception(parent, monkeypatch):
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper, end_raises=True)
    _chat_raising(monkeypatch, ValueError("the real one"))

    with pytest.raises(ValueError, match="the real one"):
        wrapper.execute(task="x")

    # The public terminal event still went out after the failed send.
    assert _shape(log)[-2:] == [("__agent_end__", "failed"), ("public", "failed")]


def test_a_raising_end_callback_does_not_lose_a_finished_run(parent):
    """On the ordinary path too: the run's result comes back and the public
    terminal event is sent. The cancelled token stops the real ``chat()``
    before its first request, so nothing is sent to a model."""
    wrapper = parent.tools.tools["agent_generalist"]
    log = _watch(wrapper, end_raises=True)
    token = CancellationToken()
    token.cancel("user-cancel")
    wrapper._cancellation_token = token

    out = wrapper.execute(task="x")

    assert "did not finish: it was cancelled" in out
    assert _shape(log)[-2:] == [
        ("__agent_end__", "cancelled"), ("public", "cancelled"),
    ]
