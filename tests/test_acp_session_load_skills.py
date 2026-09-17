"""ACP restores a loaded session's active skills (#271).

Both ACP entry points read the persisted ``active_skills`` list off disk and
then dropped it: ``session/load`` bound it and never used it, and the startup
``--resume`` path spelled it ``_active_skills``. The transcript came back and
the activations did not, so the ``SKILL.md`` bodies that had been shaping the
conversation silently left the system prompt — while the CLI's
``/sessions resume`` restored them from the same file.

Both entry points go through ``_instantiate_loaded_session``, so both are
covered here rather than split across ``test_acp_session_load.py`` and
``test_acp_resume_on_startup.py``: the fix is one seam and the regression
risk is that a future caller of that seam forgets the argument again.

The failure shapes are deliberately separated. ``activate_skill`` **answers**
``"Error: ..."`` for a skill that is unknown or has been disabled since the
session was saved (#266) — it does not raise — so a restore that only
guarded against exceptions would report every refusal as a success. Both are
exercised, alongside a runtime with no skill manager at all, and a persisted
list that is not a list of names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agentao.acp import session_load as acp_session_load
from agentao.acp import session_new as acp_session_new
from agentao.acp.models import ResumeDirective
from agentao.embedding.sessions import save_session

from .support.acp_agents import FakeAgent, FakeSkillManager, make_factory
from .support.acp_server import make_initialized_server

RESTORE_TASK = "Restored from session"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def initialized_server():
    return make_initialized_server()


def _persist(
    cwd: Path,
    session_id: str,
    messages: List[Dict[str, Any]],
    active_skills: Optional[List[str]] = None,
) -> None:
    save_session(
        messages=messages,
        model="test-model",
        active_skills=active_skills or [],
        session_id=session_id,
        project_root=cwd,
    )


def _session_file(cwd: Path) -> Path:
    """The single persisted session file under ``cwd``.

    Used by the tests that need a shape ``save_session`` cannot write — a
    legacy file predating the field, or a hand-edited one.
    """
    files = sorted((cwd / ".agentao" / "sessions").glob("*.json"))
    assert len(files) == 1, files
    return files[0]


def _rewrite_active_skills(cwd: Path, value: Any, *, drop: bool = False) -> None:
    path = _session_file(cwd)
    data = json.loads(path.read_text(encoding="utf-8"))
    if drop:
        data.pop("active_skills", None)
    else:
        data["active_skills"] = value
    path.write_text(json.dumps(data), encoding="utf-8")


def _load(server: Any, cwd: Path, session_id: str, agent: Any) -> Dict[str, Any]:
    return acp_session_load.handle_session_load(
        server,
        {"sessionId": session_id, "cwd": str(cwd), "mcpServers": []},
        agent_factory=make_factory(agent),
    )


def _resume(server: Any, cwd: Path, agent: Any) -> Dict[str, Any]:
    server.resume_directive = ResumeDirective(session_id=None)
    return acp_session_new.handle_session_new(
        server,
        {"cwd": str(cwd), "mcpServers": []},
        agent_factory=make_factory(agent),
    )


# ---------------------------------------------------------------------------
# Both entry points restore
# ---------------------------------------------------------------------------

def test_session_load_restores_persisted_skills(initialized_server, tmp_path):
    sid = "11111111-1111-1111-1111-111111111111"
    history = [{"role": "user", "content": "keep going"}]
    _persist(tmp_path, sid, history, ["alpha", "beta"])

    agent = FakeAgent()
    _load(initialized_server, tmp_path, sid, agent)

    assert agent.skill_manager.activate_calls == [
        ("alpha", RESTORE_TASK),
        ("beta", RESTORE_TASK),
    ]
    assert sorted(agent.skill_manager.get_active_skills()) == ["alpha", "beta"]
    # The history is the history — restoring is a side effect on the manager,
    # never an extra message.
    assert agent.messages == history


def test_startup_resume_restores_persisted_skills(initialized_server, tmp_path):
    """The ``--resume`` seam consumed by the first ``session/new``."""
    history = [{"role": "user", "content": "hello there"}]
    _persist(tmp_path, "sess_resume_skills", history, ["alpha", "beta"])

    agent = FakeAgent()
    result = _resume(initialized_server, tmp_path, agent)

    assert result["sessionId"] == "sess_resume_skills"
    assert agent.skill_manager.activate_calls == [
        ("alpha", RESTORE_TASK),
        ("beta", RESTORE_TASK),
    ]
    assert sorted(agent.skill_manager.get_active_skills()) == ["alpha", "beta"]
    assert agent.messages == history


def test_a_session_saved_with_no_skills_activates_nothing(
    initialized_server, tmp_path
):
    sid = "22222222-2222-2222-2222-222222222222"
    _persist(tmp_path, sid, [{"role": "user", "content": "x"}], [])

    agent = FakeAgent()
    _load(initialized_server, tmp_path, sid, agent)

    assert agent.skill_manager.activate_calls == []


def test_a_legacy_session_without_the_field_activates_nothing(
    initialized_server, tmp_path
):
    """A file written before ``active_skills`` existed has no such key."""
    sid = "33333333-3333-3333-3333-333333333333"
    _persist(tmp_path, sid, [{"role": "user", "content": "x"}], ["alpha"])
    _rewrite_active_skills(tmp_path, None, drop=True)

    agent = FakeAgent()
    _load(initialized_server, tmp_path, sid, agent)

    assert agent.skill_manager.activate_calls == []
    assert sid in initialized_server.sessions


# ---------------------------------------------------------------------------
# One bad skill does not take the others, or the load, down
# ---------------------------------------------------------------------------

def test_a_refused_skill_does_not_block_the_others(initialized_server, tmp_path):
    """A skill disabled since the save is *answered* with ``Error:`` (#266)."""
    sid = "44444444-4444-4444-4444-444444444444"
    history = [{"role": "user", "content": "x"}]
    _persist(tmp_path, sid, history, ["alpha", "gone", "beta"])

    agent = FakeAgent(skill_manager=FakeSkillManager(refuse={"gone"}))
    _load(initialized_server, tmp_path, sid, agent)

    # Every name was attempted — a refusal is not a reason to stop.
    assert [name for name, _ in agent.skill_manager.activate_calls] == [
        "alpha",
        "gone",
        "beta",
    ]
    assert sorted(agent.skill_manager.get_active_skills()) == ["alpha", "beta"]
    # And the load itself still completed.
    assert initialized_server.sessions.require(sid).agent is agent
    assert agent.messages == history


def test_a_raising_activation_does_not_block_the_others(
    initialized_server, tmp_path
):
    sid = "55555555-5555-5555-5555-555555555555"
    _persist(tmp_path, sid, [{"role": "user", "content": "x"}], ["alpha", "bad", "beta"])

    agent = FakeAgent(skill_manager=FakeSkillManager(raise_on={"bad"}))
    _load(initialized_server, tmp_path, sid, agent)

    assert sorted(agent.skill_manager.get_active_skills()) == ["alpha", "beta"]
    assert sid in initialized_server.sessions


def test_a_runtime_with_no_skill_manager_still_loads(initialized_server, tmp_path):
    """An embedder's duck-typed agent may have no skill manager at all."""
    sid = "66666666-6666-6666-6666-666666666666"
    history = [{"role": "user", "content": "x"}]
    _persist(tmp_path, sid, history, ["alpha"])

    agent = FakeAgent()
    del agent.skill_manager

    _load(initialized_server, tmp_path, sid, agent)

    assert initialized_server.sessions.require(sid).agent is agent
    assert agent.messages == history


def test_a_skill_manager_that_raises_on_access_still_loads(
    initialized_server, tmp_path
):
    """``getattr(..., None)`` defaults a *missing* attribute; it does not
    swallow one that raises. Unguarded, that escapes into the loader's
    cleanup block and tears down an otherwise complete session."""
    sid = "77777777-7777-7777-7777-777777777777"
    history = [{"role": "user", "content": "x"}]
    _persist(tmp_path, sid, history, ["alpha"])

    class HostileAgent(FakeAgent):
        @property
        def skill_manager(self) -> Any:
            raise RuntimeError("no skills for you")

        @skill_manager.setter
        def skill_manager(self, value: Any) -> None:
            pass  # swallow FakeAgent.__init__'s assignment

    agent = HostileAgent()
    _load(initialized_server, tmp_path, sid, agent)

    assert initialized_server.sessions.require(sid).agent is agent
    assert agent.messages == history
    assert agent.close_calls == 0


def test_a_malformed_skill_list_is_ignored(initialized_server, tmp_path):
    """A hand-edited file holding a bare string must not be iterated
    character by character into ``activate_skill("p")``."""
    sid = "88888888-8888-8888-8888-888888888888"
    _persist(tmp_path, sid, [{"role": "user", "content": "x"}], ["alpha"])
    _rewrite_active_skills(tmp_path, "pdf")

    agent = FakeAgent()
    _load(initialized_server, tmp_path, sid, agent)

    assert agent.skill_manager.activate_calls == []
    assert sid in initialized_server.sessions


def test_non_string_entries_are_dropped_and_the_rest_restored(
    initialized_server, tmp_path
):
    sid = "99999999-9999-9999-9999-999999999999"
    _persist(tmp_path, sid, [{"role": "user", "content": "x"}], ["alpha"])
    _rewrite_active_skills(tmp_path, ["alpha", 7, None, "", "beta"])

    agent = FakeAgent()
    _load(initialized_server, tmp_path, sid, agent)

    assert [name for name, _ in agent.skill_manager.activate_calls] == [
        "alpha",
        "beta",
    ]


# ---------------------------------------------------------------------------
# End to end against the real SkillManager
# ---------------------------------------------------------------------------

def test_restored_skill_body_returns_to_the_prompt_not_to_the_history(
    initialized_server, tmp_path
):
    """The point of the fix, measured where it matters.

    The fake manager above records calls; this one asserts the *effect*:
    ``SystemPromptBuilder`` renders the active-skills section straight from
    ``skill_manager.get_skills_context()`` (``prompts/builder.py``), and that
    string is what carries the ``SKILL.md`` body back into every later turn.
    The same assertion pins the other half — the activation text is
    model-facing but belongs to no turn, so it must not land in the history.
    """
    from agentao.skills.manager import SkillManager

    skills_dir = tmp_path / "skills"
    (skills_dir / "invoice-parser").mkdir(parents=True)
    (skills_dir / "invoice-parser" / "SKILL.md").write_text(
        "---\nname: invoice-parser\ndescription: Parse invoices\n---\n\n"
        "Always cross-check the VAT line.\n",
        encoding="utf-8",
    )
    # ``working_directory=`` matters even with an explicit ``skills_dir``: it
    # is what scopes ``skills_config.json``. Left off, the manager falls back
    # to the module-level constant, which was resolved from ``Path.cwd()`` at
    # *import* time — so the test would read the agentao repo's own
    # disabled-skills file and go red (or, worse for the sibling test below,
    # green for the wrong reason) on a checkout that disables one of these
    # names.
    manager = SkillManager(skills_dir=str(skills_dir), working_directory=tmp_path)
    assert "invoice-parser" in manager.available_skills
    assert manager.get_skills_context() == ""

    sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    history = [
        {"role": "user", "content": "parse this"},
        {"role": "assistant", "content": "done"},
    ]
    _persist(tmp_path, sid, history, ["invoice-parser"])

    agent = FakeAgent(skill_manager=manager)
    _load(initialized_server, tmp_path, sid, agent)

    context = manager.get_skills_context()
    assert "invoice-parser" in context
    assert "Always cross-check the VAT line." in context
    assert f"Task: {RESTORE_TASK}" in context

    assert agent.messages == history


def test_a_disabled_skill_is_not_restored_by_the_real_manager(
    initialized_server, tmp_path
):
    """#266's refusal reaches this path as a return value, not an exception."""
    from agentao.skills.manager import SkillManager

    skills_dir = tmp_path / "skills"
    for name in ("kept", "banned"):
        (skills_dir / name).mkdir(parents=True)
        (skills_dir / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\n\nbody\n", encoding="utf-8"
        )
    manager = SkillManager(skills_dir=str(skills_dir), working_directory=tmp_path)
    manager.disabled_skills.add("banned")
    # Pin the premise: without this, a checkout whose own
    # ``skills_config.json`` disabled ``kept`` would leave the assertion
    # below passing while measuring nothing.
    assert manager.disabled_skills == {"banned"}

    sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    _persist(tmp_path, sid, [{"role": "user", "content": "x"}], ["banned", "kept"])

    agent = FakeAgent(skill_manager=manager)
    _load(initialized_server, tmp_path, sid, agent)

    assert sorted(manager.get_active_skills()) == ["kept"]


# ---------------------------------------------------------------------------
# The log line is the whole client-facing surface
# ---------------------------------------------------------------------------

def test_a_skipped_skill_is_named_in_a_warning(
    initialized_server, tmp_path, caplog
):
    """Neither response has a field for a skipped skill, so the WARNING in
    ``agentao.log`` is the *only* place it is reported. Untested, a later
    refactor could drop the name or downgrade the level and lose the one
    remedy the CHANGELOG points the user at."""
    sid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    _persist(tmp_path, sid, [{"role": "user", "content": "x"}], ["alpha", "gone"])

    agent = FakeAgent(skill_manager=FakeSkillManager(refuse={"gone"}))
    with caplog.at_level("WARNING", logger="agentao.embedding.sessions"):
        _load(initialized_server, tmp_path, sid, agent)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("gone" in r.getMessage() and sid in r.getMessage() for r in warnings), (
        [r.getMessage() for r in warnings]
    )
    # And a restored one is not reported as a problem.
    assert not any("'alpha'" in r.getMessage() for r in warnings)


def test_a_runtime_with_no_skill_manager_names_what_it_dropped(
    initialized_server, tmp_path, caplog
):
    """The whole-list skip reports a count *and* the names — a count alone
    leaves an embedder unable to tell which activations went missing."""
    sid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    _persist(tmp_path, sid, [{"role": "user", "content": "x"}], ["alpha", "beta"])

    agent = FakeAgent()
    del agent.skill_manager

    with caplog.at_level("WARNING", logger="agentao.embedding.sessions"):
        _load(initialized_server, tmp_path, sid, agent)

    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("alpha" in m and "beta" in m for m in messages), messages


# ---------------------------------------------------------------------------
# The hydration block the restore sits next to
# ---------------------------------------------------------------------------

def test_hydration_completes_before_the_skills_are_restored(
    initialized_server, tmp_path
):
    """``invalidate_token_anchor`` and ``purge_thinking_artifacts`` share one
    try/except with the ``agent.messages`` assignment, so an ``AttributeError``
    on the first of them silently skips the second. Pin that the whole block
    runs — this is what makes the sibling ``agent.messages == history``
    assertions mean "hydrated", not "hydrated and then crashed"."""
    sid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    history = [
        {
            "role": "assistant",
            "content": "hi",
            "reasoning_content": "minted by a model this process is not bound to",
        }
    ]
    _persist(tmp_path, sid, history, ["alpha"])

    agent = FakeAgent()
    _load(initialized_server, tmp_path, sid, agent)

    assert agent.context_manager.invalidate_calls == 1
    assert "reasoning_content" not in agent.messages[0]
    assert agent.skill_manager.activate_calls == [("alpha", RESTORE_TASK)]


# ---------------------------------------------------------------------------
# A file the client named but the server cannot parse
# ---------------------------------------------------------------------------

def test_a_corrupt_session_file_is_the_clients_error_not_the_servers(
    initialized_server, tmp_path
):
    """``load_session`` raises ``ValueError`` (``json.JSONDecodeError``) for a
    corrupt file, reachable through the timestamp-prefix branch of the
    selector, which matches on the file *stem* and so does not skip a file it
    cannot parse. Uncaught it becomes ``-32603`` INTERNAL_ERROR — the server
    blaming itself for the client's file. ``resume_session_on_new`` already
    caught this pair."""
    from agentao.acp.protocol import INVALID_REQUEST
    from agentao.acp.server import JsonRpcHandlerError

    _persist(tmp_path, "ffffffff-ffff-ffff-ffff-ffffffffffff",
             [{"role": "user", "content": "x"}], ["alpha"])
    stem = _session_file(tmp_path).stem
    _session_file(tmp_path).write_text("{ truncated", encoding="utf-8")

    with pytest.raises(JsonRpcHandlerError) as excinfo:
        _load(initialized_server, tmp_path, stem, FakeAgent())
    assert excinfo.value.code == INVALID_REQUEST

    # Same for a file that parses but is not a JSON object.
    _session_file(tmp_path).write_text("[]", encoding="utf-8")
    with pytest.raises(JsonRpcHandlerError) as excinfo:
        _load(initialized_server, tmp_path, stem, FakeAgent())
    assert excinfo.value.code == INVALID_REQUEST
