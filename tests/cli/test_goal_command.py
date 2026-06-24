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
