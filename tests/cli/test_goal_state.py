"""Tests for GoalState — model, state machine, persistence (agentao/cli/goal_state.py)."""

import json

from agentao.cli.goal_state import (
    GoalState,
    GoalStatus,
    budget_summary,
    clear_goal,
    format_duration,
    goal_path,
    load_goal,
    save_goal,
)


def test_defaults():
    g = GoalState(objective="do the thing")
    assert g.status == GoalStatus.ACTIVE
    assert g.is_active
    assert g.time_budget_seconds is None
    assert g.max_turns is None
    assert g.turns_used == 0
    assert g.goal_id  # auto uuid
    assert not g.is_stopped


def test_round_trip():
    g = GoalState(objective="ship it", time_budget_seconds=7200, max_turns=25, turns_used=3)
    g.time_used_seconds = 12.5
    restored = GoalState.from_dict(g.to_dict())
    assert restored == g
    assert restored.status == GoalStatus.ACTIVE


def test_from_dict_tolerates_unknown_keys():
    # Forward-compat: a newer schema with extra keys must not crash load.
    d = GoalState(objective="x").to_dict()
    d["tokens_used"] = 999  # a key this version dropped
    g = GoalState.from_dict(d)
    assert g.objective == "x"


def test_from_dict_coerces_numeric_strings():
    g = GoalState.from_dict(
        {"objective": "x", "max_turns": "10", "turns_used": "3", "time_used_seconds": "5.5"}
    )
    assert g.max_turns == 10
    assert g.turns_used == 3
    assert g.time_used_seconds == 5.5


def test_from_dict_null_numeric_falls_back_to_default():
    g = GoalState.from_dict({"objective": "x", "turns_used": None, "time_used_seconds": None})
    assert g.turns_used == 0
    assert g.time_used_seconds == 0.0
    # None caps stay None (= no cap)
    g2 = GoalState.from_dict({"objective": "x", "max_turns": None})
    assert g2.max_turns is None


def test_corrupt_numeric_field_loads_as_no_goal(tmp_path):
    # A poisoned numeric value must surface as "no goal", never crash the loop.
    p = goal_path(tmp_path)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps({"objective": "x", "time_used_seconds": "oops"}), encoding="utf-8")
    assert load_goal(tmp_path) is None


def test_save_goal_returns_bool(tmp_path):
    assert save_goal(GoalState(objective="x"), tmp_path) is True


# ── state machine ──────────────────────────────────────────────────────


def test_complete_only_from_active():
    g = GoalState(objective="x")
    assert g.mark_complete() is True
    assert g.status == GoalStatus.COMPLETE
    # second call is a no-op — terminal state is immutable
    assert g.mark_complete() is False
    assert g.status == GoalStatus.COMPLETE
    assert g.is_stopped


def test_blocked_and_resume():
    g = GoalState(objective="x")
    assert g.mark_blocked() is True
    assert g.status == GoalStatus.BLOCKED
    # resume revives a blocked goal (dormant → active)
    assert g.resume() is True
    assert g.status == GoalStatus.ACTIVE


def test_pause_resume():
    g = GoalState(objective="x")
    assert g.pause() is True
    assert g.status == GoalStatus.PAUSED
    assert g.resume() is True
    assert g.is_active
    # cannot pause a non-active goal
    g.mark_complete()
    assert g.pause() is False


def test_limit_reached_not_resumable_but_reactivatable():
    g = GoalState(objective="x", max_turns=1)
    assert g.mark_limit_reached() is True
    assert g.status == GoalStatus.LIMIT_REACHED
    # resume does NOT revive a limit_reached goal
    assert g.resume() is False
    assert g.status == GoalStatus.LIMIT_REACHED
    # re-budget (reactivate) does
    assert g.reactivate_from_limit() is True
    assert g.is_active


def test_mark_limit_reached_guarded():
    g = GoalState(objective="x")
    g.mark_complete()
    # host cannot stomp a terminal complete with limit_reached
    assert g.mark_limit_reached() is False
    assert g.status == GoalStatus.COMPLETE


# ── budget ─────────────────────────────────────────────────────────────


def test_budget_tripped_turns():
    g = GoalState(objective="x", max_turns=3)
    g.turns_used = 2
    assert g.budget_tripped() is False
    g.turns_used = 3
    assert g.budget_tripped() is True


def test_budget_tripped_time():
    g = GoalState(objective="x", time_budget_seconds=100)
    g.time_used_seconds = 99
    assert g.budget_tripped() is False
    g.time_used_seconds = 100
    assert g.budget_tripped() is True


def test_budget_tripped_neither():
    g = GoalState(objective="x")  # no caps
    g.turns_used = 10_000
    g.time_used_seconds = 1e9
    assert g.budget_tripped() is False


def test_first_to_trip_wins():
    g = GoalState(objective="x", max_turns=5, time_budget_seconds=100)
    g.turns_used = 5          # turn cap reached, time not
    g.time_used_seconds = 1
    assert g.budget_tripped() is True


# ── persistence ────────────────────────────────────────────────────────


def test_save_load_clear(tmp_path):
    assert load_goal(tmp_path) is None
    g = GoalState(objective="persist me", max_turns=10)
    save_goal(g, tmp_path)
    assert goal_path(tmp_path).exists()
    loaded = load_goal(tmp_path)
    assert loaded is not None
    assert loaded.objective == "persist me"
    assert loaded.max_turns == 10
    assert clear_goal(tmp_path) is True
    assert load_goal(tmp_path) is None
    assert clear_goal(tmp_path) is False  # already gone


def test_load_corrupt_returns_none(tmp_path):
    p = goal_path(tmp_path)
    p.parent.mkdir(exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert load_goal(tmp_path) is None


# ── display helpers ────────────────────────────────────────────────────


def test_format_duration():
    assert format_duration(45) == "45s"
    assert format_duration(90) == "1m30s"
    assert format_duration(1800) == "30m"
    assert format_duration(3600) == "1h"
    assert format_duration(5400) == "1h30m"


def test_budget_summary():
    assert budget_summary(GoalState(objective="x")) == "unbounded"
    g = GoalState(objective="x", max_turns=25, time_budget_seconds=7200, turns_used=3)
    g.time_used_seconds = 600
    s = budget_summary(g)
    assert "3/25 turns" in s
    assert "10m/2h" in s
