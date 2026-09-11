"""`SessionStart.source` / `SessionEnd.reason` carry real values, per entry point.

Both fields are **matched against** by a profile hook rule
(`plugins/hooks/_dispatcher.py`), so a constant makes every non-`*` matcher on
these two events dead and fires the one matcher that happens to equal the
constant. Design: `docs/design/session-lifecycle-source-vs-codex.md` §6.

Organized around **real entry points**, not one test per enum value: the bug
these replace was never in the vocabulary, it was in the four call sites that
took the default. The resume tests assert **order and count**, which is where
rev 2 of the design was wrong.
"""

from __future__ import annotations

import pytest

from agentao.cli.app import AgentaoCLI
from agentao.plugins.hooks._profile import PROFILE_ID
from agentao.plugins.models import ParsedHookRule

from .._hook_commands import as_kwargs, emitting

LEGACY = "agentao-v1"


# ── stubs ─────────────────────────────────────────────────────────────────


class _RecordingCli:
    """Records the lifecycle values instead of running the real dispatch."""

    def __init__(self, session_id="s-old"):
        self.current_session_id = session_id
        self.events = []           # [("start"|"end", value, session_id)]
        self._staged_images = []
        self._pending_session_start_source = None

    def on_session_start(self, *, source="startup"):
        self.events.append(("start", source, self.current_session_id))

    def on_session_end(self, *, reason="other"):
        self.events.append(("end", reason, self.current_session_id))

    # run_loop only needs these two beyond the lifecycle pair
    def _flush_acp_inbox(self):
        pass

    def _get_user_input(self):
        return "/exit"

    def _save_session_on_exit(self):
        self.on_session_end(reason="prompt_input_exit")


# ── entry point: interactive startup ──────────────────────────────────────


def test_startup_reports_startup():
    from agentao.cli.input_loop import run_loop

    cli = _RecordingCli()
    run_loop(cli)
    assert cli.events[0] == ("start", "startup", "s-old")


def test_interactive_exit_reports_prompt_input_exit():
    # The real method, with a stub ``self`` — the value is the whole content of
    # this one-liner, so calling it through anything else would prove nothing.
    seen = {}

    class _S:
        def on_session_end(self, *, reason="other"):
            seen["reason"] = reason

    AgentaoCLI._save_session_on_exit(_S())
    assert seen["reason"] == "prompt_input_exit"


# ── entry point: /clear and /new (one shared reset path) ──────────────────


class _StubPlanSession:
    is_active = False


class _StubMemory:
    def clear(self):
        pass

    def clear_all_session_summaries(self):
        pass


class _StubAgent:
    def __init__(self):
        self.memory_manager = _StubMemory()

    def clear_history(self):
        pass


class _ResetCli(_RecordingCli):
    def __init__(self):
        super().__init__()
        self._plan_session = _StubPlanSession()
        self.agent = _StubAgent()
        self.permission_mode = None
        self.last_response = None
        self._cached_ctx_pct = 0.0

    def _apply_mode(self, mode):
        self.permission_mode = mode


@pytest.mark.parametrize("handler_name", ["handle_clear_command", "handle_new_command"])
def test_clear_and_new_both_report_clear_on_both_events(handler_name):
    # `/new` shares `_reset_session` with `/clear` and upstream has no value of
    # its own for it, so both report `clear` — the nearest true value. Before
    # this, `/clear` reported `startup`/`other`, i.e. a *named* cause as an
    # unnamed one.
    from agentao.cli.commands import reset

    cli = _ResetCli()
    getattr(reset, handler_name)(cli)
    assert [(kind, value) for kind, value, _ in cli.events] == [
        ("end", "clear"), ("start", "clear"),
    ]


# ── entry point: the two resume paths ─────────────────────────────────────


class _StubCtx:
    def invalidate_token_anchor(self):
        pass


class _StubSkills:
    def activate_skill(self, name, why):
        pass


class _StubToolRunner:
    _session_id = None


class _StubMemoryManager:
    def __init__(self):
        self.archived = 0

    def archive_session(self):
        self.archived += 1


class _ResumeAgent:
    def __init__(self, tmp_path):
        self.messages = [{"role": "user", "content": "old"}]
        self.working_directory = tmp_path
        self.context_manager = _StubCtx()
        self.skill_manager = _StubSkills()
        self.tool_runner = _StubToolRunner()
        self.memory_manager = _StubMemoryManager()
        self._session_id = None

    def end_replay(self):
        pass

    def reload_replay_config(self):
        pass

    def start_replay(self, sid):
        pass

    def get_current_model(self):
        return "m"


class _ResumeCli(_RecordingCli):
    def __init__(self, tmp_path):
        super().__init__()
        self.agent = _ResumeAgent(tmp_path)


@pytest.fixture
def resumable(monkeypatch, tmp_path):
    """A loadable session, plus a recorder for both hook dispatches.

    ``resume_session`` dispatches **hooks only** on both sides, so the recorder
    sits on the two ``_dispatch_session_*_hooks`` helpers rather than on
    ``on_session_*``. That asymmetry with `/clear` is the point: the full
    lifecycle pair would re-derive the session id and persist the outgoing
    conversation, neither of which this command owes.
    """
    fired = []
    monkeypatch.setattr(
        "agentao.embedding.sessions.list_sessions",
        lambda project_root=None: [{"id": "f1", "session_id": "s-new", "title": "t"}],
    )
    monkeypatch.setattr(
        "agentao.embedding.sessions.load_session",
        lambda file_id, project_root=None: ([{"role": "user", "content": "new"}], "m", []),
    )
    monkeypatch.setattr(
        "agentao.cli.session._dispatch_session_start_hooks",
        lambda cli, *, source="startup": fired.append(
            ("start", source, cli.current_session_id)),
    )
    monkeypatch.setattr(
        "agentao.cli.session._dispatch_session_end_hooks",
        lambda cli, *, reason="other": fired.append(
            ("end", reason, cli.current_session_id)),
    )
    return _ResumeCli(tmp_path), fired


def test_interactive_resume_ends_the_old_session_then_starts_the_new(resumable):
    from agentao.cli.commands.sessions import resume_session

    cli, fired = resumable
    resume_session(cli)

    # Exactly one of each, End first, carrying the **outgoing** session id where
    # Start carries the incoming one.
    assert fired == [("end", "resume", "s-old"), ("start", "resume", "s-new")]
    # Hooks only: the full ``on_session_end`` would also persist the outgoing
    # conversation, which this command has never done.
    assert cli.events == []
    assert cli._pending_session_start_source is None


def test_startup_resume_leaves_one_marker_and_dispatches_nothing(resumable):
    from agentao.cli.commands.sessions import resume_session

    cli, fired = resumable
    resume_session(cli, at_launch=True)

    # No End: nothing had started. No Start either — ``run_loop`` owns the only
    # one, or the launch path emits `resume` and then `startup` for one session.
    assert cli.events == []
    assert fired == []
    assert cli._pending_session_start_source == "resume"


def test_the_marker_makes_run_loop_report_resume_exactly_once(resumable):
    from agentao.cli.commands.sessions import resume_session
    from agentao.cli.input_loop import run_loop

    cli, _ = resumable
    resume_session(cli, at_launch=True)
    run_loop(cli)

    starts = [e for e in cli.events if e[0] == "start"]
    assert starts == [("start", "resume", "s-new")]
    assert cli._pending_session_start_source is None    # one-shot


# ── the two failures, kept apart ──────────────────────────────────────────


def test_a_failed_interactive_resume_dispatches_neither_event(monkeypatch, tmp_path):
    from agentao.cli.commands.sessions import resume_session

    monkeypatch.setattr(
        "agentao.embedding.sessions.list_sessions", lambda project_root=None: [],
    )
    cli = _ResumeCli(tmp_path)
    resume_session(cli)

    assert cli.events == []
    assert cli.agent.messages == [{"role": "user", "content": "old"}]  # intact
    assert cli._pending_session_start_source is None


def test_a_failed_startup_resume_still_reports_startup(monkeypatch, tmp_path):
    # The other half of the same failure, and the reason it cannot share a rule:
    # ``entrypoints.py`` runs ``cli.run()`` unconditionally, so a real new
    # session *does* begin. Writing "a failed load dispatches neither" as one
    # rule would silence it.
    from agentao.cli.commands.sessions import resume_session
    from agentao.cli.input_loop import run_loop

    monkeypatch.setattr(
        "agentao.embedding.sessions.list_sessions", lambda project_root=None: [],
    )
    cli = _ResumeCli(tmp_path)
    resume_session(cli, at_launch=True)
    assert cli._pending_session_start_source is None    # marker only on success

    run_loop(cli)
    starts = [e for e in cli.events if e[0] == "start"]
    assert starts == [("start", "startup", "s-old")]
    assert not any(e[0] == "end" and e[1] == "resume" for e in cli.events)


# ── v1 regression (design §5) ─────────────────────────────────────────────


def test_a_v1_rule_cannot_scope_itself_to_one_source(tmp_path):
    """Why a *new* dispatch site is a different risk from a *changed value*.

    An `agentao-v1` `SessionStart` rule has no matcher for `source` — the flat
    matcher only reads `toolName`, which this event has none of. So the same
    rule fires for every source, and a v1 author cannot opt out of a dispatch
    site that did not exist when they wrote it. Any new site must therefore
    assert its change in execution count rather than inherit one.
    """
    from agentao.plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher

    log = tmp_path / "fired.log"
    rule = ParsedHookRule(
        event="SessionStart", hook_type="command",
        **as_kwargs(emitting(touch=(log,))),
        timeout=30, contract=LEGACY, plugin_name="p",
    )
    adapter = ClaudeHookPayloadAdapter()
    for source in ("startup", "resume", "clear"):
        payload = adapter.build_session_start(session_id="s", cwd=tmp_path, source=source)
        PluginHookDispatcher(cwd=tmp_path).dispatch_session_start(
            payload=payload, rules=[rule],
        )
    # The command really ran three times — counted from the side effect, not
    # from the loop, which would assert nothing.
    assert log.read_text(encoding="utf-8").count("fired") == 3

    # The profile contract is the half that *can* scope: same rule, same
    # payloads, and only the matching source runs it.
    scoped = ParsedHookRule(
        event="SessionStart", hook_type="command",
        **as_kwargs(emitting(touch=(log,))),
        timeout=30, contract=PROFILE_ID, plugin_name="p", matcher_pattern="clear",
    )
    dispatcher = PluginHookDispatcher(cwd=tmp_path)
    assert dispatcher._matches(
        scoped, adapter.build_session_start(session_id="s", cwd=tmp_path, source="clear"),
    )
    assert not dispatcher._matches(
        scoped, adapter.build_session_start(session_id="s", cwd=tmp_path, source="startup"),
    )


def test_the_new_resume_dispatch_site_runs_an_existing_v1_hook(monkeypatch, tmp_path):
    """§6.5's other half: the new site's change in **execution count**.

    The test above proves a v1 rule *cannot* scope itself to a source. This one
    proves what that costs: `/sessions resume` had no `SessionStart` dispatch
    before, so an existing `agentao-v1` hook now runs once where it ran zero
    times, and its author has no matcher with which to opt out. Counted from the
    hook's own side effect — the dispatcher swallows its exceptions, so
    asserting on the call would pass even if nothing ran.
    """
    from agentao.cli.commands.sessions import resume_session

    monkeypatch.setattr(
        "agentao.embedding.sessions.list_sessions",
        lambda project_root=None: [{"id": "f1", "session_id": "s-new", "title": "t"}],
    )
    monkeypatch.setattr(
        "agentao.embedding.sessions.load_session",
        lambda file_id, project_root=None: ([{"role": "user", "content": "new"}], "m", []),
    )

    log = tmp_path / "v1-fired.log"
    cli = _ResumeCli(tmp_path)
    cli.agent._plugin_hook_rules = [ParsedHookRule(
        event="SessionStart", hook_type="command",
        **as_kwargs(emitting(touch=(log,))),
        timeout=30, contract=LEGACY, plugin_name="p",
    )]

    resume_session(cli)

    assert log.read_text(encoding="utf-8").count("fired") == 1


def test_a_corrupt_session_file_does_not_brick_a_startup_resume(monkeypatch, tmp_path):
    """A truncated / hand-edited session file must not stop the CLI starting.

    ``load_session_record`` raises ``ValueError`` (``json.JSONDecodeError``) on a
    bad file, and ``entrypoints.main`` wraps the launch resume in the
    fatal-error handler that exits 1 — so leaving it uncaught turned one corrupt
    file into "``--resume`` cannot start at all", contradicting the documented
    row "failed load, CLI starts anyway → `startup`".
    """
    from agentao.cli.commands.sessions import resume_session

    monkeypatch.setattr(
        "agentao.embedding.sessions.list_sessions",
        lambda project_root=None: [{"id": "f1", "session_id": "s-new", "title": "t"}],
    )

    def _boom(file_id, project_root=None):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr("agentao.embedding.sessions.load_session", _boom)

    cli = _ResumeCli(tmp_path)
    resume_session(cli, at_launch=True)          # must not raise

    assert cli.events == []
    assert cli._pending_session_start_source is None   # → run_loop reports startup
    assert cli.agent.messages == [{"role": "user", "content": "old"}]  # intact


def test_interactive_resume_archives_the_outgoing_memory_session(resumable):
    """The incoming session owes every step ``on_session_start`` owns.

    ``archive_session`` advances ``MemoryManager._session_id``; skipping it left
    the abandoned conversation's session summaries bound to the resumed session,
    where they keep being injected into its prompts.
    """
    from agentao.cli.commands.sessions import resume_session

    cli, _ = resumable
    resume_session(cli)
    assert cli.agent.memory_manager.archived == 1


def test_a_startup_resume_leaves_the_memory_archive_to_run_loop(resumable):
    # ``run_loop``'s single ``on_session_start`` does it; doing it here too
    # would advance the memory session twice for one launch.
    from agentao.cli.commands.sessions import resume_session

    cli, _ = resumable
    resume_session(cli, at_launch=True)
    assert cli.agent.memory_manager.archived == 0
