"""Tests for ACP startup-resume (``agentao --acp --resume [SESSION_ID]``).

ACP is client-driven, so the server cannot proactively create a session.
The ``--resume`` directive is therefore consumed by the *first*
``session/new``, which hydrates and replays a persisted session instead of
starting blank. These tests cover:

1. :class:`ResumeDirective.consume` — one-shot, thread-safe claim.
2. ``handle_session_new`` with a pending directive — resumes the latest /
   a specific session, hydrates ``messages``, returns the persisted
   ``sessionId``, and is one-shot (the second ``session/new`` is fresh).
3. Graceful fallback when there is nothing to resume.
4. ``acp.__main__.main`` builds the directive from the ``resume`` selector
   and threads it into :class:`AcpServer`.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List

import pytest

from agentao.acp import session_new as acp_session_new
from agentao.acp.models import AcpSessionState, ResumeDirective
from agentao.embedding.sessions import save_session

from .support.acp_agents import FakeAgent, make_factory
from .support.acp_server import make_initialized_server


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def initialized_server():
    return make_initialized_server()


def _minimal_params(cwd: Path) -> Dict[str, Any]:
    return {"cwd": str(cwd), "mcpServers": []}


def _persist(cwd: Path, session_id: str, messages: List[Dict[str, Any]]) -> None:
    save_session(
        messages=messages,
        model="test-model",
        active_skills=[],
        session_id=session_id,
        project_root=cwd,
    )


# ---------------------------------------------------------------------------
# ResumeDirective.consume
# ---------------------------------------------------------------------------

def test_consume_is_one_shot():
    directive = ResumeDirective(session_id=None)
    assert directive.consume() is True
    assert directive.consume() is False
    assert directive.consume() is False


def test_consume_is_thread_safe():
    """Exactly one of many racing consumers may claim the directive."""
    directive = ResumeDirective(session_id="abc")
    start = threading.Barrier(8)
    winners: List[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        start.wait()
        won = directive.consume()
        with lock:
            winners.append(won)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert winners.count(True) == 1
    assert winners.count(False) == 7


# ---------------------------------------------------------------------------
# Resume on first session/new
# ---------------------------------------------------------------------------

def test_first_session_new_resumes_latest(initialized_server, tmp_path):
    history = [
        {"role": "user", "content": "hello there"},
        {"role": "assistant", "content": "hi, how can I help?"},
    ]
    _persist(tmp_path, "sess_persisted_one", history)

    initialized_server.resume_directive = ResumeDirective(session_id=None)
    agent = FakeAgent()

    result = acp_session_new.handle_session_new(
        initialized_server,
        _minimal_params(tmp_path),
        agent_factory=make_factory(agent),
    )

    # The persisted id is returned, not a freshly-generated sess_<uuid>.
    assert result["sessionId"] == "sess_persisted_one"
    # History was hydrated onto the runtime so a follow-up prompt continues it.
    assert agent.messages == history
    # The session is registered under the persisted id.
    assert "sess_persisted_one" in initialized_server.sessions
    # The directive was consumed.
    assert initialized_server.resume_directive.consume() is False


def test_resume_specific_session_by_id(initialized_server, tmp_path):
    _persist(tmp_path, "sess_alpha", [{"role": "user", "content": "alpha"}])
    _persist(tmp_path, "sess_beta", [{"role": "user", "content": "beta"}])

    initialized_server.resume_directive = ResumeDirective(session_id="sess_alpha")
    agent = FakeAgent()

    result = acp_session_new.handle_session_new(
        initialized_server,
        _minimal_params(tmp_path),
        agent_factory=make_factory(agent),
    )

    assert result["sessionId"] == "sess_alpha"
    assert agent.messages == [{"role": "user", "content": "alpha"}]


def test_resume_is_one_shot_second_session_is_fresh(initialized_server, tmp_path):
    _persist(tmp_path, "sess_persisted", [{"role": "user", "content": "hi"}])

    initialized_server.resume_directive = ResumeDirective(session_id=None)

    first = acp_session_new.handle_session_new(
        initialized_server,
        _minimal_params(tmp_path),
        agent_factory=make_factory(FakeAgent()),
    )
    assert first["sessionId"] == "sess_persisted"

    second_agent = FakeAgent()
    second = acp_session_new.handle_session_new(
        initialized_server,
        _minimal_params(tmp_path),
        agent_factory=make_factory(second_agent),
    )
    # Fresh server-generated id, no hydrated history.
    assert second["sessionId"].startswith("sess_")
    assert second["sessionId"] != "sess_persisted"
    assert second_agent.messages == []


def test_resume_with_no_sessions_falls_back_to_fresh(initialized_server, tmp_path):
    """A pending directive against an empty store starts a normal session."""
    initialized_server.resume_directive = ResumeDirective(session_id=None)
    agent = FakeAgent()

    result = acp_session_new.handle_session_new(
        initialized_server,
        _minimal_params(tmp_path),
        agent_factory=make_factory(agent),
    )

    assert result["sessionId"].startswith("sess_")
    assert agent.messages == []
    # The directive is still consumed — fallback does not re-arm it.
    assert initialized_server.resume_directive.consume() is False


def test_resume_missing_specific_id_falls_back_to_fresh(initialized_server, tmp_path):
    _persist(tmp_path, "sess_real", [{"role": "user", "content": "hi"}])
    initialized_server.resume_directive = ResumeDirective(session_id="sess_does_not_exist")

    result = acp_session_new.handle_session_new(
        initialized_server,
        _minimal_params(tmp_path),
        agent_factory=make_factory(FakeAgent()),
    )

    assert result["sessionId"].startswith("sess_")
    assert result["sessionId"] != "sess_real"


def test_resume_corrupt_session_falls_back_to_fresh(initialized_server, tmp_path):
    """A corrupt persisted file degrades to a fresh session instead of
    failing the client's first session/new with a JSONDecodeError."""
    sessions_dir = tmp_path / ".agentao" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "20990101_000000_000000.json").write_text(
        "{ this is not valid json", encoding="utf-8"
    )

    initialized_server.resume_directive = ResumeDirective(session_id=None)
    agent = FakeAgent()

    result = acp_session_new.handle_session_new(
        initialized_server,
        _minimal_params(tmp_path),
        agent_factory=make_factory(agent),
    )

    assert result["sessionId"].startswith("sess_")
    assert agent.messages == []
    assert initialized_server.resume_directive.consume() is False


def test_resume_already_active_session_falls_back_to_fresh(initialized_server, tmp_path):
    """If the resume target is already live in the registry, resume yields a
    fresh session rather than raising INVALID_REQUEST on the registry collision."""
    _persist(tmp_path, "sess_dup", [{"role": "user", "content": "hi"}])
    # Pre-register a live session under the same id the resume would resolve to.
    existing = AcpSessionState(session_id="sess_dup", agent=FakeAgent(), cwd=tmp_path)
    initialized_server.sessions.create(existing)

    initialized_server.resume_directive = ResumeDirective(session_id="sess_dup")
    agent = FakeAgent()

    result = acp_session_new.handle_session_new(
        initialized_server,
        _minimal_params(tmp_path),
        agent_factory=make_factory(agent),
    )

    # Fresh id, not the colliding one; the pre-existing session is untouched.
    assert result["sessionId"].startswith("sess_")
    assert result["sessionId"] != "sess_dup"
    assert agent.messages == []
    assert initialized_server.sessions.get("sess_dup") is existing


def test_no_directive_starts_fresh(initialized_server, tmp_path):
    """Without a directive (the default), session/new never resumes even
    when a persisted session exists."""
    _persist(tmp_path, "sess_persisted", [{"role": "user", "content": "hi"}])
    assert initialized_server.resume_directive is None

    agent = FakeAgent()
    result = acp_session_new.handle_session_new(
        initialized_server,
        _minimal_params(tmp_path),
        agent_factory=make_factory(agent),
    )

    assert result["sessionId"].startswith("sess_")
    assert result["sessionId"] != "sess_persisted"
    assert agent.messages == []


# ---------------------------------------------------------------------------
# acp.__main__.main directive construction
# ---------------------------------------------------------------------------

class TestMainBuildsDirective:
    def _capture_directive(self, monkeypatch, resume_arg):
        from agentao.acp import __main__ as acp_main_module

        captured: Dict[str, Any] = {}
        original = acp_main_module.AcpServer

        class _CapturingServer(original):  # type: ignore[misc, valid-type]
            def __init__(self, *a, **kw):
                import io as _io
                kw.setdefault("stdin", _io.StringIO(""))
                kw.setdefault("stdout", _io.StringIO())
                super().__init__(*a, **kw)
                captured["directive"] = self.resume_directive

        monkeypatch.setattr(acp_main_module, "AcpServer", _CapturingServer)
        acp_main_module.main(resume=resume_arg)
        return captured["directive"]

    def test_none_resume_yields_no_directive(self, monkeypatch):
        assert self._capture_directive(monkeypatch, None) is None

    def test_empty_resume_yields_latest_directive(self, monkeypatch):
        directive = self._capture_directive(monkeypatch, "")
        assert isinstance(directive, ResumeDirective)
        assert directive.session_id is None

    def test_specific_resume_yields_targeted_directive(self, monkeypatch):
        directive = self._capture_directive(monkeypatch, "sess_xyz")
        assert isinstance(directive, ResumeDirective)
        assert directive.session_id == "sess_xyz"
