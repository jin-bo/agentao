"""Tests for the /goal command parsing + the continuation loop.

The pure pieces (flag parsing, default resolution) are tested directly; the
keystone continuation loop is tested through its ``_run_turn`` injection seam
with a fake CLI/agent so no real LLM is needed.
"""

import pytest

from agentao.cli.commands.goal import (
    _classify,
    _parse_goal_flags,
    _parse_turns,
    _resolve_budget,
)
from agentao.cli.duration import DurationParseError, parse_duration
from agentao.cli.goal_state import GoalState, GoalStatus


# ── flag parsing ──────────────────────────────────────────────────────────


def test_parse_objective_only():
    assert _parse_goal_flags("Fix the login bug") == ("Fix the login bug", None, None, False)


def test_parse_with_flags():
    obj, t, n, unb = _parse_goal_flags("Refactor auth --for 30m --turns 5")
    assert obj == "Refactor auth"
    assert t == 1800
    assert n == 5
    assert unb is False


def test_parse_flags_interleaved():
    obj, t, n, unb = _parse_goal_flags("--turns 3 Migrate the API --for 1h")
    assert obj == "Migrate the API"
    assert t == 3600
    assert n == 3


def test_parse_unbounded():
    assert _parse_goal_flags("Big task --unbounded") == ("Big task", None, None, True)


@pytest.mark.parametrize(
    "args",
    ["x --for", "x --turns", "x --turns abc", "x --turns 0", "x --turns -2"],
)
def test_parse_bad_flags_raise_valueerror(args):
    with pytest.raises(ValueError):
        _parse_goal_flags(args)


def test_parse_bad_duration_raises():
    with pytest.raises(DurationParseError):
        _parse_goal_flags("x --for 30")  # unit-less


def test_parse_turns_helper():
    assert _parse_turns("7") == 7
    with pytest.raises(ValueError):
        _parse_turns("0")


# ── subcommand vs objective classification (namespace-collision guard) ─────


@pytest.mark.parametrize(
    "args,kind,rest",
    [
        ("", "show", ""),
        ("show", "show", ""),
        ("clear", "clear", ""),
        # reserved verb + trailing text → it's an OBJECTIVE, not the subcommand
        ("clear out the stale temp files", "set", "clear out the stale temp files"),
        ("pause", "pause", ""),
        ("pause the deployment until Monday", "set", "pause the deployment until Monday"),
        ("resume", "resume", ""),
        # arg-taking subcommands consume their rest (case preserved)
        ("edit New objective text", "edit", "New objective text"),
        ("budget --turns 5", "budget", "--turns 5"),
        ("Fix the login bug", "set", "Fix the login bug"),
        ("Show me the logs --turns 3", "set", "Show me the logs --turns 3"),
    ],
)
def test_classify(args, kind, rest):
    assert _classify(args) == (kind, rest)


# ── default resolution ────────────────────────────────────────────────────


def test_resolve_defaults_empty_settings():
    t, n = _resolve_budget({}, None, None, unbounded=False)
    assert t == parse_duration("120m")  # 7200
    assert n == 25


def test_resolve_unbounded():
    assert _resolve_budget({"default_max_turns": 25}, None, None, unbounded=True) == (None, None)


def test_resolve_settings_override():
    settings = {"default_time_budget": "1h", "default_max_turns": 50}
    assert _resolve_budget(settings, None, None, unbounded=False) == (3600, 50)


def test_resolve_explicit_flags_kept():
    # explicit flags win over defaults
    assert _resolve_budget({"default_max_turns": 50}, 900, 7, unbounded=False) == (900, 7)


def test_resolve_zero_turn_default_means_no_cap():
    t, n = _resolve_budget({"default_max_turns": 0}, None, None, unbounded=False)
    assert n is None


def test_resolve_bad_time_default_falls_back():
    t, n = _resolve_budget({"default_time_budget": "banana"}, None, None, unbounded=False)
    assert t == parse_duration("120m")


# ── continuation loop (keystone) ──────────────────────────────────────────


class _FakeAgent:
    def __init__(self, working_directory):
        self.working_directory = working_directory
        self.added = []
        self.removed = []

    def add_tool(self, tool, replace=False):
        self.added.append(tool.name)

    def remove_tool(self, name):
        self.removed.append(name)
        return True


class _FakeCLI:
    def __init__(self, working_directory):
        self.agent = _FakeAgent(working_directory)


def _run(goal, tmp_path, fake_turn):
    from agentao.cli.input_loop import run_goal_continuation

    cli = _FakeCLI(tmp_path)
    run_goal_continuation(cli, goal, _run_turn=fake_turn)
    return cli


def test_loop_turn_cap_one_wrapup(tmp_path):
    goal = GoalState(objective="obj", max_turns=2)
    msgs = []
    cli = _run(goal, tmp_path, lambda m: msgs.append(m))
    assert goal.turns_used == 2
    assert goal.status == GoalStatus.LIMIT_REACHED
    assert msgs[0] == "obj"                     # first turn = the objective
    assert "Continue working" in msgs[1]        # later turns = continuation
    assert "budget" in msgs[2].lower()          # exactly one wrap-up turn
    assert len(msgs) == 3
    assert cli.agent.added == ["update_goal"]   # injected once
    assert cli.agent.removed == ["update_goal"]  # removed in finally


def test_loop_agent_completes(tmp_path):
    goal = GoalState(objective="obj", max_turns=10)
    msgs = []

    def fake_turn(m):
        msgs.append(m)
        if len(msgs) == 2:
            goal.mark_complete()  # simulate the agent calling update_goal

    _run(goal, tmp_path, fake_turn)
    assert goal.status == GoalStatus.COMPLETE
    assert goal.turns_used == 2
    assert len(msgs) == 2  # no wrap-up turn after completion


def test_loop_agent_blocked(tmp_path):
    goal = GoalState(objective="obj", max_turns=10)

    def fake_turn(m):
        goal.mark_blocked()

    _run(goal, tmp_path, fake_turn)
    assert goal.status == GoalStatus.BLOCKED
    assert goal.turns_used == 1


def test_loop_time_precheck_only_wrapup(tmp_path):
    goal = GoalState(objective="obj", time_budget_seconds=100)
    goal.time_used_seconds = 100  # already at cap before any turn
    msgs = []
    _run(goal, tmp_path, lambda m: msgs.append(m))
    assert goal.status == GoalStatus.LIMIT_REACHED
    assert goal.turns_used == 0
    assert len(msgs) == 1  # just the wrap-up turn


def test_loop_keyboard_interrupt_pauses(tmp_path):
    goal = GoalState(objective="obj", max_turns=10)

    def fake_turn(m):
        raise KeyboardInterrupt

    cli = _run(goal, tmp_path, fake_turn)
    assert goal.status == GoalStatus.PAUSED
    assert cli.agent.removed == ["update_goal"]  # finally still ran


def test_loop_interrupt_sentinel_pauses(tmp_path):
    # chat() absorbs Ctrl-C and RETURNS the sentinel instead of raising; the
    # loop must detect that and pause (the production interrupt path).
    goal = GoalState(objective="obj", max_turns=10)
    msgs = []

    def fake_turn(m):
        msgs.append(m)
        return "[Interrupted by user]"

    _run(goal, tmp_path, fake_turn)
    assert goal.status == GoalStatus.PAUSED
    assert goal.turns_used == 1   # the interrupted turn is still counted
    assert len(msgs) == 1


def test_loop_exception_pauses_not_strands(tmp_path):
    # A turn error must not leave the goal stranded in ACTIVE (unresumable).
    goal = GoalState(objective="obj", max_turns=10)

    def fake_turn(m):
        raise RuntimeError("llm boom")

    from agentao.cli.input_loop import run_goal_continuation

    cli = _FakeCLI(tmp_path)
    with pytest.raises(RuntimeError):
        run_goal_continuation(cli, goal, _run_turn=fake_turn)
    assert goal.status == GoalStatus.PAUSED          # resumable, not stranded
    assert cli.agent.removed == ["update_goal"]      # finally still ran


class _Registry:
    def __init__(self):
        self.tools = {}


class _AgentWithRegistry(_FakeAgent):
    def __init__(self, wd):
        super().__init__(wd)
        self.tools = _Registry()

    def add_tool(self, tool, replace=False):
        super().add_tool(tool, replace=replace)
        self.tools.tools[tool.name] = tool

    def remove_tool(self, name):
        super().remove_tool(name)
        self.tools.tools.pop(name, None)
        return True


class _DummyTool:
    name = "update_goal"


def test_loop_restores_host_update_goal_tool(tmp_path):
    # A host that ships its own 'update_goal' tool must get it back, not have it
    # permanently deleted by the loop's replace+remove.
    from agentao.cli.input_loop import run_goal_continuation

    cli = _FakeCLI(tmp_path)
    cli.agent = _AgentWithRegistry(tmp_path)
    host_tool = _DummyTool()
    cli.agent.tools.tools["update_goal"] = host_tool

    goal = GoalState(objective="obj", max_turns=1)
    run_goal_continuation(cli, goal, _run_turn=lambda m: None)

    assert cli.agent.tools.tools.get("update_goal") is host_tool  # restored


def test_staged_images_payload_does_not_clear():
    from agentao.cli.input_loop import _staged_images_payload

    cli = type("C", (), {"_staged_images": [{"data": "d", "mimeType": "image/png"}]})()
    payload = _staged_images_payload(cli)
    assert payload == [{"data": "d", "mimeType": "image/png", "_source": "image"}]
    assert cli._staged_images  # NOT cleared — caller clears on first-turn success


def test_first_goal_turn_consumes_images_on_success(tmp_path, monkeypatch):
    import agentao.cli.input_loop as il

    seen = []
    monkeypatch.setattr(il, "_run_agent_turn",
                        lambda cli, msg, images=None: seen.append(images) or "ok")
    cli = _FakeCLI(tmp_path)
    cli._staged_images = [{"data": "d", "mimeType": "image/png"}]
    il.run_goal_continuation(cli, GoalState(objective="obj", max_turns=2))

    assert seen[0] == [{"data": "d", "mimeType": "image/png", "_source": "image"}]
    assert seen[1] is None                    # later turns carry no images
    assert cli._staged_images == []           # cleared only after success


def test_first_goal_turn_keeps_images_on_failure(tmp_path, monkeypatch):
    import agentao.cli.input_loop as il

    def boom(cli, msg, images=None):
        raise RuntimeError("transient")

    monkeypatch.setattr(il, "_run_agent_turn", boom)
    cli = _FakeCLI(tmp_path)
    staged = [{"data": "d", "mimeType": "image/png"}]
    cli._staged_images = list(staged)
    goal = GoalState(objective="obj", max_turns=2)
    with pytest.raises(RuntimeError):
        il.run_goal_continuation(cli, goal)

    assert cli._staged_images == staged       # NOT cleared on failure
    assert goal.status == GoalStatus.PAUSED    # paused for /goal resume


def test_first_goal_turn_keeps_images_on_interrupt(tmp_path, monkeypatch):
    # chat() returns the interrupt sentinel rather than raising; an interrupted
    # first turn must NOT clear staged images (resume must be able to resend).
    import agentao.cli.input_loop as il

    monkeypatch.setattr(il, "_run_agent_turn",
                        lambda cli, msg, images=None: "[Interrupted by user]")
    cli = _FakeCLI(tmp_path)
    staged = [{"data": "d", "mimeType": "image/png"}]
    cli._staged_images = list(staged)
    goal = GoalState(objective="obj", max_turns=5)
    il.run_goal_continuation(cli, goal)

    assert cli._staged_images == staged        # NOT cleared on interrupt
    assert goal.status == GoalStatus.PAUSED


def test_loop_no_prior_tool_is_removed(tmp_path):
    from agentao.cli.input_loop import run_goal_continuation

    cli = _FakeCLI(tmp_path)
    cli.agent = _AgentWithRegistry(tmp_path)  # registry starts empty
    goal = GoalState(objective="obj", max_turns=1)
    run_goal_continuation(cli, goal, _run_turn=lambda m: None)

    assert "update_goal" not in cli.agent.tools.tools  # cleaned up


# ── resume acceptance (restart-survival of a stranded ACTIVE goal) ─────────


class _ResumeFakeCLI:
    """Minimal CLI surface for _resume_goal: just settings + plan-mode probe."""

    def __init__(self):
        self.agent = type("A", (), {"working_directory": None})()

    def _load_settings(self):
        return {}


@pytest.mark.parametrize(
    "make_status,should_launch",
    [
        (lambda g: None, True),                 # active (default) → resumes (stranded)
        (lambda g: g.pause(), True),            # paused → resumes
        (lambda g: g.mark_blocked(), True),     # blocked → resumes
        (lambda g: g.mark_complete(), False),   # complete → rejected
        (lambda g: g.mark_limit_reached(), False),  # limit_reached → rejected
    ],
)
def test_resume_accepts_active_paused_blocked(tmp_path, monkeypatch, make_status, should_launch):
    import agentao.cli.commands.goal as gmod

    launched = []
    monkeypatch.setattr(gmod, "_run_continuation", lambda cli, goal: launched.append(goal))

    goal = GoalState(objective="x", max_turns=5)
    make_status(goal)
    gmod._resume_goal(_ResumeFakeCLI(), goal, tmp_path)

    assert bool(launched) is should_launch
    if should_launch:
        assert goal.status == GoalStatus.ACTIVE  # ends active and is re-driven


# ── no-progress guard ─────────────────────────────────────────────────────
#
# The budget caps bound how much a goal may *do*. This guard bounds how long it
# may do *nothing* — the orthogonal runaway, and the only one that survives
# `--unbounded` / `default_max_turns: 0`.


class _OutcomeAgent(_FakeAgent):
    """A fake agent whose ``last_turn`` the test drives, turn by turn."""

    def __init__(self, working_directory, outcomes):
        super().__init__(working_directory)
        self._outcomes = list(outcomes)
        self.last_turn = None

    def advance(self):
        self.last_turn = self._outcomes.pop(0) if self._outcomes else None


class _Outcome:
    def __init__(self, incomplete_reason=None, tool_count=0):
        self.incomplete_reason = incomplete_reason
        self.tool_count = tool_count


def _run_with_outcomes(goal, tmp_path, outcomes):
    from agentao.cli.input_loop import run_goal_continuation

    cli = _FakeCLI(tmp_path)
    cli.agent = _OutcomeAgent(tmp_path, outcomes)

    def fake_turn(_msg):
        cli.agent.advance()
        # Never an empty string: the chat loop substitutes a placeholder for an
        # empty answer, which is precisely why the guard cannot read the text.
        return "[no response generated]"

    run_goal_continuation(cli, goal, _run_turn=fake_turn)
    return cli


def test_a_goal_with_no_caps_has_nothing_else_to_stop_it(tmp_path):
    # Why the guard exists: `--unbounded` (and the documented
    # `default_max_turns: 0`) leave the loop with no budget to trip.
    assert GoalState(objective="obj").budget_tripped() is False


def test_no_progress_blocks_the_goal(tmp_path):
    # A turn cap well above the threshold, deliberately: a regression then fails
    # on the status instead of hanging the suite, which an uncapped goal would.
    goal = GoalState(objective="obj", max_turns=10)
    _run_with_outcomes(goal, tmp_path, [_Outcome("no_output")] * 10)
    assert goal.status == GoalStatus.BLOCKED
    assert goal.turns_used == 3                # stopped at the threshold, not 10


def test_no_progress_streak_resets_on_an_answered_turn(tmp_path):
    goal = GoalState(objective="obj", max_turns=6)
    _run_with_outcomes(goal, tmp_path, [
        _Outcome("no_output"),
        _Outcome("no_output"),
        _Outcome(None),                        # a real answer resets the streak
        _Outcome("no_output"),
        _Outcome("no_output"),
    ])
    # Never reached three in a row, so the turn cap is what ends it.
    assert goal.status == GoalStatus.LIMIT_REACHED


def test_a_turn_that_called_tools_counts_as_progress(tmp_path):
    goal = GoalState(objective="obj", max_turns=4)
    _run_with_outcomes(goal, tmp_path, [_Outcome("no_output", tool_count=2)] * 4)
    assert goal.status == GoalStatus.LIMIT_REACHED


def test_repeated_llm_errors_block_the_goal(tmp_path):
    goal = GoalState(objective="obj", max_turns=10)
    _run_with_outcomes(goal, tmp_path, [_Outcome("llm_error")] * 10)
    assert goal.status == GoalStatus.BLOCKED
    assert goal.turns_used == 3


def test_no_progress_reason_reads_a_closed_set(tmp_path):
    from agentao.cli.input_loop import _no_progress_reason

    class _CLI:
        def __init__(self, outcome):
            self.agent = type("A", (), {"last_turn": outcome})()

    assert _no_progress_reason(_CLI(_Outcome("no_output"))) == "no_output"
    assert _no_progress_reason(_CLI(_Outcome("reasoning_only"))) == "reasoning_only"
    assert _no_progress_reason(_CLI(_Outcome(None))) is None
    assert _no_progress_reason(_CLI(_Outcome("max_iterations"))) is None
    assert _no_progress_reason(_CLI(_Outcome("no_output", tool_count=1))) is None
    assert _no_progress_reason(_CLI(None)) is None


def test_a_capped_turn_is_not_no_progress(tmp_path):
    # max_iterations / doom_loop / length_truncated turns did work and hit a
    # ceiling. Counting them here would block a busy goal.
    goal = GoalState(objective="obj", max_turns=4)
    _run_with_outcomes(goal, tmp_path, [
        _Outcome("max_iterations"), _Outcome("doom_loop"),
        _Outcome("length_truncated"), _Outcome("hook_stop"),
    ])
    assert goal.status == GoalStatus.LIMIT_REACHED


def test_an_agent_without_last_turn_never_blocks(tmp_path):
    # The pre-existing fake has no ``last_turn`` at all. A host that never
    # populates it must degrade to "made progress", not to a blocked goal.
    goal = GoalState(objective="obj", max_turns=3)
    _run(goal, tmp_path, lambda m: None)
    assert goal.status == GoalStatus.LIMIT_REACHED


def test_a_magicmock_agent_never_blocks(tmp_path):
    # A MagicMock answers every attribute, so a truthiness test on
    # ``incomplete_reason`` would block on turn three. The closed-set string
    # check is what keeps this honest.
    from unittest.mock import MagicMock

    from agentao.cli.input_loop import run_goal_continuation

    goal = GoalState(objective="obj", max_turns=3)
    cli = _FakeCLI(tmp_path)
    cli.agent = MagicMock()
    cli.agent.working_directory = tmp_path
    run_goal_continuation(cli, goal, _run_turn=lambda m: "text")
    assert goal.status == GoalStatus.LIMIT_REACHED


def test_a_provider_failure_is_no_progress_even_when_tools_ran(tmp_path):
    """The one member of the set for which tool calls are not a reset.

    ``tool_count`` accumulates across a turn's *iterations*, so a turn whose
    first iteration called a tool and whose second died at the provider carries
    both ``llm_error`` and a non-zero count. Treating that as progress would
    reset the streak on every turn of an outage and let the goal spin on a dead
    provider forever — the runaway ``llm_error`` is in the set to bound.
    """
    goal = GoalState(objective="obj", max_turns=10)
    _run_with_outcomes(goal, tmp_path, [_Outcome("llm_error", tool_count=1)] * 10)
    assert goal.status == GoalStatus.BLOCKED
    assert goal.turns_used == 3


def test_no_progress_reasons_are_runtime_vocabulary():
    """The literals must name real ``INCOMPLETE_*`` values.

    ``_NO_PROGRESS_REASONS`` hand-copies them rather than importing runtime
    internals at module scope, matching ``cli/run.py::_INCOMPLETE_OUTCOMES``.
    That duplication is safe only while it stays a subset of the runtime's own
    closed vocabulary: a renamed constant would otherwise turn this guard off
    silently, and a goal would go back to burning its whole budget on nothing.
    """
    from agentao.cli.input_loop import _LLM_ERROR_REASON, _NO_PROGRESS_REASONS
    from agentao.runtime.chat_loop import INCOMPLETE_ANSWER_REASONS, INCOMPLETE_LLM_ERROR

    assert _NO_PROGRESS_REASONS < INCOMPLETE_ANSWER_REASONS   # strict subset
    assert _LLM_ERROR_REASON == INCOMPLETE_LLM_ERROR
    assert _LLM_ERROR_REASON in _NO_PROGRESS_REASONS


def test_a_no_progress_stop_does_not_claim_the_agent_asked_for_input(tmp_path, capsys):
    """The generic `blocked` line names the wrong actor for a host-set block.

    `blocked` is normally the agent saying "I need you" through `update_goal`.
    The no-progress guard sets the same status from the host loop, where
    nothing asked the user anything — so the outcome report has to say which
    one happened, or it sends the user looking for a question that was never
    asked.
    """
    goal = GoalState(objective="obj", max_turns=10)
    _run_with_outcomes(goal, tmp_path, [_Outcome("llm_error")] * 10)
    assert goal.status == GoalStatus.BLOCKED

    out = capsys.readouterr().out
    assert "no progress" in out
    assert "llm_error" in out
    assert "needs your input" not in out


def test_an_agent_set_block_still_says_it_needs_your_input(tmp_path, capsys):
    from agentao.cli.input_loop import run_goal_continuation

    goal = GoalState(objective="obj", max_turns=10)
    cli = _FakeCLI(tmp_path)

    def fake_turn(_msg):
        goal.mark_blocked()
        return "text"

    run_goal_continuation(cli, goal, _run_turn=fake_turn)
    out = capsys.readouterr().out
    assert "needs your input" in out
    assert "no progress" not in out
