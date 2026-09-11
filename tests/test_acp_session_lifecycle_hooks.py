"""``SessionStart`` / ``SessionEnd`` on the ACP surface.

ACP was the one surface of three that dispatched neither event. Wiring it is
not "three more dispatch calls": ACP holds several sessions at once, a client
can supply its own session id and pipeline a prompt behind the load, and the
one method name that looks like "a new session" is sometimes a resume.

Design and the fixed behaviour table:
``docs/design/session-lifecycle-source-vs-codex.md`` §4.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from agentao.acp import session_load, session_new
from agentao.acp.models import AcpSessionState, ResumeDirective
from agentao.acp.server import JsonRpcHandlerError
from agentao.embedding.sessions import save_session

from .support.acp_agents import FakeAgent, make_factory
from .support.acp_server import make_initialized_server


class _HookedAgent(FakeAgent):
    """A ``FakeAgent`` that looks like it has hook rules configured."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._plugin_hook_rules = [object()]

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})


@pytest.fixture
def fired(monkeypatch) -> List[Dict[str, Any]]:
    """Record every lifecycle dispatch, in order, with its value and history."""
    calls: List[Dict[str, Any]] = []

    def _start(agent, session_id, *, source="startup"):
        # Injecting here is what a real ``SessionStart`` hook's
        # ``additionalContext`` does, and lets a test see *where* in history
        # the context landed relative to the restored conversation.
        agent.add_message("user", "[hook_additional_context] context: ctx")
        calls.append({
            "event": "start", "value": source, "session": session_id,
            "history_len": len(agent.messages),
        })
        return ["start-notice"]

    def _end(agent, session_id, *, reason="other"):
        calls.append({"event": "end", "value": reason, "session": session_id})
        return ["end-notice"]

    # Patch where they are *bound*: ``acp/_lifecycle.py`` imports the two
    # functions by name at module load, so patching the source module would
    # not be seen.
    monkeypatch.setattr("agentao.acp._lifecycle.fire_session_start", _start)
    monkeypatch.setattr("agentao.acp._lifecycle.fire_session_end", _end)
    return calls


def _notifications(server) -> List[Dict[str, Any]]:
    out = []
    for line in server._out.getvalue().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _notice_texts(server) -> List[str]:
    texts = []
    for msg in _notifications(server):
        update = (msg.get("params") or {}).get("update") or {}
        text = (update.get("content") or {}).get("text") or ""
        if "notice" in text:
            texts.append(text)
    return texts


def _new_params(cwd: Path) -> Dict[str, Any]:
    return {"cwd": str(cwd), "mcpServers": []}


def _persist(cwd: Path, session_id: str, messages: List[Dict[str, Any]]) -> None:
    save_session(
        messages=messages, model="test-model", active_skills=[],
        session_id=session_id, project_root=cwd,
    )


# ── the behaviour table, one test per row ─────────────────────────────────


def test_a_new_session_reports_startup(fired, tmp_path):
    server = make_initialized_server()
    session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(_HookedAgent()),
    )
    assert [(c["event"], c["value"]) for c in fired] == [("start", "startup")]


def test_session_load_reports_resume(fired, tmp_path):
    _persist(tmp_path, "s-load", [{"role": "user", "content": "old"}])
    server = make_initialized_server()
    session_load.handle_session_load(
        server,
        {"sessionId": "s-load", "cwd": str(tmp_path), "mcpServers": []},
        agent_factory=make_factory(_HookedAgent()),
    )
    assert [(c["event"], c["value"]) for c in fired] == [("start", "resume")]


def test_a_startup_resume_reports_resume(fired, tmp_path):
    _persist(tmp_path, "s-boot", [{"role": "user", "content": "old"}])
    server = make_initialized_server()
    server.resume_directive = ResumeDirective(session_id="s-boot")
    session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(_HookedAgent()),
    )
    assert [(c["event"], c["value"]) for c in fired] == [("start", "resume")]


def test_a_startup_resume_that_falls_back_reports_startup(fired, tmp_path):
    # Nothing on disk to restore, so the seam falls through to a fresh session.
    # The value has to follow what happened, not which method was called.
    server = make_initialized_server()
    server.resume_directive = ResumeDirective(session_id="s-missing")
    session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(_HookedAgent()),
    )
    assert [(c["event"], c["value"]) for c in fired] == [("start", "startup")]


def test_closing_a_session_reports_other(fired, tmp_path):
    server = make_initialized_server()
    state = session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(_HookedAgent()),
    )
    state = server.sessions.require(state["sessionId"])
    state.close()
    assert [(c["event"], c["value"]) for c in fired] == [
        ("start", "startup"), ("end", "other"),
    ]


def test_a_failed_load_dispatches_nothing(fired, tmp_path):
    server = make_initialized_server()
    with pytest.raises(JsonRpcHandlerError):
        session_load.handle_session_load(
            server,
            {"sessionId": "s-nope", "cwd": str(tmp_path), "mcpServers": []},
            agent_factory=make_factory(_HookedAgent()),
        )
    assert fired == []


def test_a_duplicate_load_dispatches_nothing_new(fired, tmp_path):
    """A rejected registration must run no user commands.

    ``SessionStart`` hooks are arbitrary shell commands, so firing them for a
    load that then fails on a duplicate id would run side effects for a session
    that never existed. The dispatch sits behind the duplicate check inside the
    registration lock for exactly this.
    """
    _persist(tmp_path, "s-dup", [{"role": "user", "content": "old"}])
    server = make_initialized_server()
    params = {"sessionId": "s-dup", "cwd": str(tmp_path), "mcpServers": []}
    session_load.handle_session_load(
        server, params, agent_factory=make_factory(_HookedAgent()),
    )
    assert len(fired) == 1

    with pytest.raises(JsonRpcHandlerError):
        session_load.handle_session_load(
            server, params, agent_factory=make_factory(_HookedAgent()),
        )
    assert len(fired) == 1


def test_cancelling_a_turn_is_not_a_session_ending(fired, tmp_path):
    from agentao.acp import session_cancel

    server = make_initialized_server()
    created = session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(_HookedAgent()),
    )
    session_cancel.handle_session_cancel(server, {"sessionId": created["sessionId"]})
    assert [c["event"] for c in fired] == ["start"]


# ── the properties that make it multi-session-safe ────────────────────────


def test_two_sessions_do_not_affect_each_others_events(fired, tmp_path):
    server = make_initialized_server()
    a = session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(_HookedAgent()),
    )
    b = session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(_HookedAgent()),
    )
    server.sessions.require(a["sessionId"]).close()

    # Creating or loading a session ends nothing, and closing one leaves the
    # other alone — ACP holds several at once.
    assert [(c["event"], c["session"]) for c in fired] == [
        ("start", a["sessionId"]),
        ("start", b["sessionId"]),
        ("end", a["sessionId"]),
    ]
    assert not server.sessions.require(b["sessionId"]).closed


def test_hook_context_is_in_history_before_the_first_turn(fired, tmp_path):
    _persist(tmp_path, "s-ctx", [
        {"role": "user", "content": "old-1"},
        {"role": "assistant", "content": "old-2"},
    ])
    server = make_initialized_server()
    agent = _HookedAgent()
    session_load.handle_session_load(
        server,
        {"sessionId": "s-ctx", "cwd": str(tmp_path), "mcpServers": []},
        agent_factory=make_factory(agent),
    )

    # Fired *after* the restore, so the context is the newest message rather
    # than something the restore overwrote, and it is already there when the
    # session first becomes reachable.
    assert agent.messages[-1]["content"].startswith("[hook_additional_context]")
    assert fired[0]["history_len"] == 3
    state = server.sessions.require("s-ctx")
    assert state.agent.messages[-1]["content"].startswith("[hook_additional_context]")


def test_closing_twice_dispatches_one_end(fired, tmp_path):
    server = make_initialized_server()
    created = session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(_HookedAgent()),
    )
    state = server.sessions.require(created["sessionId"])
    state.close()
    state.close()
    assert [c["event"] for c in fired].count("end") == 1


def test_hook_notices_reach_the_client(fired, tmp_path):
    server = make_initialized_server()
    created = session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(_HookedAgent()),
    )
    server.sessions.require(created["sessionId"]).close()

    # Both events' notices go out as session/update chunks — ACP has no notice
    # channel of its own, and exit 2 on these two events *is* the user channel.
    texts = _notice_texts(server)
    assert any("start-notice" in t for t in texts)
    assert any("end-notice" in t for t in texts)


# ── layering ──────────────────────────────────────────────────────────────


def test_the_acp_lifecycle_glue_does_not_import_the_cli():
    """The reason the dispatch moved out of ``cli/session.py`` at all.

    ``tests/test_import_layering.py`` rule 1 covers the package as a whole;
    this pins the specific module the ACP wiring depends on, because the
    tempting shortcut was to import the CLI helpers by name.
    """
    import agentao.acp._lifecycle as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "agentao.cli" not in source
    assert "from ..cli" not in source
    assert ".cli import" not in source


def test_a_session_without_hook_rules_dispatches_nothing(tmp_path):
    """The common case must not pay for the feature.

    Not patched: this exercises the real ``fire_session_*``, whose first act is
    to return empty when the agent has no rules.
    """
    server = make_initialized_server()
    agent = FakeAgent()                       # no ``_plugin_hook_rules``
    created = session_new.handle_session_new(
        server, _new_params(tmp_path), agent_factory=make_factory(agent),
    )
    state = server.sessions.require(created["sessionId"])
    state.close()
    assert isinstance(state, AcpSessionState)
    assert state.closed


def test_close_cancels_the_live_turn_before_running_end_hooks(monkeypatch, tmp_path):
    """Order inside ``close()``: save, cancel, hooks, tear down the agent.

    ``SessionEnd`` hooks are user commands with their own timeouts, run
    serially. Firing them before the cancel would hold an in-flight turn's LLM
    call and tools open for the whole hook budget, and at shutdown that budget
    is paid on the thread that has to finish teardown. Nothing a hook can
    observe changes at the cancel — the token is not in its payload — so the
    old order bought only a slower teardown.
    """
    order: List[str] = []

    class _Token:
        def cancel(self, _reason):
            order.append("cancel")

    class _Agent(_HookedAgent):
        def close(self):
            order.append("agent-close")

    monkeypatch.setattr(
        "agentao.acp._lifecycle.fire_end_for_state",
        lambda state: order.append("hooks"),
    )
    state = AcpSessionState(
        session_id="s", agent=_Agent(), cwd=tmp_path, cancel_token=_Token(),
    )
    state.close()
    assert order == ["cancel", "hooks", "agent-close"]
