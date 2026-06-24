"""``/goal`` — long-task goal with a time/turn budget (CLI continuation).

The user sets an objective; the host (this CLI) drives a continuation loop
(``run_goal_continuation`` in ``input_loop.py``) that keeps calling
``agent.chat`` toward the objective until the agent marks it complete/blocked
or a time/turn budget trips. See ``docs/design/codex-goal-mechanism-review.md``
§11.1 for the surface and ``docs/guides/goal.md`` for usage.

The continuation loop lives in ``input_loop.py`` and is imported lazily inside
the handlers below to avoid an import cycle (``input_loop`` → ``commands`` →
``commands.goal`` → ``input_loop``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

import readchar

from .._globals import console, split_subcommand
from ..duration import DurationParseError, parse_duration
from ..goal_state import (
    GoalState,
    budget_summary,
    clear_goal,
    load_goal,
    save_goal,
)

if TYPE_CHECKING:
    from ..app import AgentaoCLI

# Fallback defaults if settings.json has no goal.* block (§11.1 C).
_DEFAULT_MAX_TURNS = 25
_DEFAULT_TIME_BUDGET = "120m"

_USAGE = "/goal <objective> [--for 30m] [--turns 10] [--unbounded]"


def handle_goal_command(cli: "AgentaoCLI", args: str) -> None:
    """Dispatch ``/goal`` and its subcommands."""
    project_root = Path(cli.agent.working_directory or Path.cwd())
    goal = load_goal(project_root)
    kind, rest = _classify(args)

    if kind == "show":
        _show_goal(goal)
    elif kind == "clear":
        if clear_goal(project_root):
            console.print("\n[success]Goal cleared.[/success]\n")
        else:
            console.print("\n[info]No goal to clear.[/info]\n")
    elif kind == "pause":
        _pause_goal(goal, project_root)
    elif kind == "resume":
        _resume_goal(cli, goal, project_root)
    elif kind == "edit":
        _edit_goal(goal, rest, project_root)
    elif kind == "budget":
        _set_budget(cli, goal, rest, project_root)
    else:  # kind == "set" — a new objective: "/goal <objective> [flags]".
        _set_goal(cli, rest, goal, project_root)


# Stateless subcommands take NO trailing text — so `/goal clear out the temp
# files` is an *objective*, not the `clear` subcommand (which would destroy the
# goal). Arg-taking subcommands consume their rest.
_NOARG_SUBS = frozenset({"show", "pause", "resume", "clear"})
_ARG_SUBS = frozenset({"edit", "budget"})


def _classify(args: str) -> tuple:
    """Decide whether ``args`` is a subcommand or a new objective.

    Returns ``(kind, rest)`` where ``kind`` is one of the subcommand names or
    ``"set"``; ``rest`` is the subcommand argument (or the full objective for
    ``"set"``, with original casing preserved).
    """
    sub, rest = split_subcommand(args, default="", lower=True)
    if sub == "":
        return ("show", "")
    if sub in _NOARG_SUBS and rest == "":
        return (sub, "")
    if sub in _ARG_SUBS:
        return (sub, rest)
    return ("set", args)


def _loop_blocked_reason(cli: "AgentaoCLI") -> Optional[str]:
    """Why the continuation loop must NOT start now (or ``None`` if it may).

    Checked at *every* launch site — set, resume, and re-budget — so the
    kill-switch and the plan-mode guard cannot be bypassed via a pre-existing
    goal.
    """
    if _goal_settings(cli).get("enabled", True) is False:
        return ("the /goal feature is disabled "
                "(goal.enabled=false in .agentao/settings.json)")
    plan_session = getattr(cli, "_plan_session", None)
    if plan_session is not None and getattr(plan_session, "is_active", False):
        return ("plan mode is active — the agent would only plan, never execute. "
                "Exit with /plan implement or /plan clear first")
    return None


def _run_continuation(cli: "AgentaoCLI", goal: GoalState) -> None:
    """Lazy-import shim (avoids the input_loop ↔ commands import cycle)."""
    from ..input_loop import run_goal_continuation

    run_goal_continuation(cli, goal)


# ── set / show ──────────────────────────────────────────────────────────


def _set_goal(cli: "AgentaoCLI", args: str, existing: Optional[GoalState], project_root: Path) -> None:
    try:
        objective, time_budget, max_turns, unbounded = _parse_goal_flags(args)
    except (ValueError, DurationParseError) as exc:
        console.print(f"\n[error]{exc}[/error]\n")
        return
    if not objective:
        console.print(f"\n[error]Usage: {_USAGE}[/error]\n")
        return

    reason = _loop_blocked_reason(cli)
    if reason:
        console.print(f"\n[warning]Cannot start a goal: {reason}.[/warning]\n")
        return

    if existing is not None and not existing.is_stopped:
        console.print(
            f"\n[warning]A goal is already in progress "
            f"({existing.status_label()}): {existing.objective!r}.[/warning]"
        )
        console.print("[warning]Replace it? Press 1 to confirm, any other key to cancel.[/warning]")
        if readchar.readkey() != "1":
            console.print("\n[info]Cancelled.[/info]\n")
            return

    time_budget, max_turns = _resolve_budget(_goal_settings(cli), time_budget, max_turns, unbounded)
    goal = GoalState(objective=objective, time_budget_seconds=time_budget, max_turns=max_turns)
    if not save_goal(goal, project_root):
        console.print(
            "\n[warning]Could not persist the goal to .agentao/goal.json "
            "(read-only dir or full disk); it will not survive a restart.[/warning]"
        )
    console.print(f"\n[success]Goal set:[/success] {objective}")
    console.print(f"[dim]budget: {budget_summary(goal)}[/dim]\n")

    _run_continuation(cli, goal)


def _show_goal(goal: Optional[GoalState]) -> None:
    if goal is None:
        console.print("\n[info]No goal set. Start one with /goal <objective>.[/info]\n")
        return
    console.print(f"\n[info]Goal[/info] ([bold]{goal.status_label()}[/bold]): {goal.objective}")
    console.print(f"[dim]budget: {budget_summary(goal)}[/dim]")
    console.print(f"[dim]id {goal.goal_id[:8]} · created {goal.created_at}[/dim]\n")


# ── subcommands ───────────────────────────────────────────────────────────


def _pause_goal(goal: Optional[GoalState], project_root: Path) -> None:
    if goal is None:
        console.print("\n[info]No goal to pause.[/info]\n")
        return
    if goal.pause():
        save_goal(goal, project_root)
        console.print("\n[success]Goal paused.[/success] [dim]Resume with /goal resume.[/dim]\n")
    else:
        console.print(f"\n[warning]Cannot pause a '{goal.status_label()}' goal.[/warning]\n")


def _resume_goal(cli: "AgentaoCLI", goal: Optional[GoalState], project_root: Path) -> None:
    if goal is None:
        console.print("\n[info]No goal to resume.[/info]\n")
        return
    status = goal.status_label()
    # 'active' is included because the continuation loop runs *inline*: a goal
    # found 'active' at command-dispatch time is never being driven — it was
    # stranded on disk by a prior process that was killed/crashed mid-run
    # before it could pause or finish. Resume re-drives it as-is.
    if status not in ("active", "paused", "blocked"):
        console.print(
            f"\n[warning]Cannot resume a '{status}' goal "
            "(only active/paused/blocked goals resume; re-budget a "
            "limit-reached goal with /goal budget).[/warning]\n"
        )
        return
    reason = _loop_blocked_reason(cli)
    if reason:
        console.print(f"\n[warning]Cannot resume: {reason}.[/warning]\n")
        return
    goal.resume()  # paused/blocked → active; an already-active goal stays active
    save_goal(goal, project_root)
    console.print(
        f"\n[success]{'Resuming interrupted goal.' if status == 'active' else 'Resuming goal.'}"
        "[/success]\n"
    )
    _run_continuation(cli, goal)


def _edit_goal(goal: Optional[GoalState], new_objective: str, project_root: Path) -> None:
    if goal is None:
        console.print("\n[info]No goal to edit. Set one with /goal <objective>.[/info]\n")
        return
    if not new_objective.strip():
        console.print("\n[error]Usage: /goal edit <new objective>[/error]\n")
        return
    goal.objective = new_objective.strip()
    goal._touch()
    save_goal(goal, project_root)
    console.print(f"\n[success]Objective updated:[/success] {goal.objective}\n")


def _set_budget(cli: "AgentaoCLI", goal: Optional[GoalState], rest: str, project_root: Path) -> None:
    if goal is None:
        console.print("\n[info]No goal to budget. Set one with /goal <objective>.[/info]\n")
        return

    tokens = rest.split()
    if "--clear" in tokens:
        goal.time_budget_seconds = None
        goal.max_turns = None
        _post_budget(cli, goal, project_root, note="caps removed (unbounded)")
        return

    try:
        _, time_budget, max_turns, unbounded = _parse_goal_flags(rest)
    except (ValueError, DurationParseError) as exc:
        console.print(f"\n[error]{exc}[/error]\n")
        return
    if unbounded:
        goal.time_budget_seconds = None
        goal.max_turns = None
    elif time_budget is None and max_turns is None:
        console.print(
            "\n[error]Usage: /goal budget [--for <duration>] [--turns <n>] | --clear[/error]\n"
        )
        return
    else:
        if time_budget is not None:
            goal.time_budget_seconds = time_budget
        if max_turns is not None:
            goal.max_turns = max_turns
    _post_budget(cli, goal, project_root, note=budget_summary(goal))


def _post_budget(cli: "AgentaoCLI", goal: GoalState, project_root: Path, *, note: str) -> None:
    """Persist a re-budget; reactivate + resume a limit-reached goal (§E)."""
    if goal.status_label() == "limit_reached":
        # Only revive if the *new* budget actually leaves headroom — otherwise
        # the loop would mark limit_reached again on the first pre-check and the
        # user would see "resuming" followed by an instant stop.
        if goal.budget_tripped():
            over = (
                "turn" if goal.max_turns is not None and goal.turns_used >= goal.max_turns
                else "time"
            )
            goal._touch()
            save_goal(goal, project_root)
            console.print(
                f"\n[warning]Budget updated: {note}. The {over} cap is still "
                "exhausted — raise it (or /goal clear) to continue.[/warning]\n"
            )
            return
        reason = _loop_blocked_reason(cli)
        if reason:
            goal._touch()
            save_goal(goal, project_root)
            console.print(
                f"\n[success]Budget updated:[/success] {note}. "
                f"[dim]Not resuming: {reason}.[/dim]\n"
            )
            return
        goal.reactivate_from_limit()
        save_goal(goal, project_root)
        console.print(f"\n[success]Budget updated:[/success] {note} — resuming.\n")
        _run_continuation(cli, goal)
        return
    goal._touch()
    save_goal(goal, project_root)
    console.print(f"\n[success]Budget updated:[/success] {note}\n")


# ── parsing / defaults (pure, unit-tested) ────────────────────────────────


def _parse_goal_flags(args: str) -> Tuple[str, Optional[int], Optional[int], bool]:
    """Split ``<objective> [--for D] [--turns N] [--unbounded]``.

    Returns ``(objective, time_budget_seconds|None, max_turns|None,
    unbounded)``. Flags may appear anywhere; the remaining text is the
    objective. Raises ``ValueError`` / ``DurationParseError`` on malformed
    flags.
    """
    tokens = args.split()
    objective_parts = []
    time_budget: Optional[int] = None
    max_turns: Optional[int] = None
    unbounded = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--for":
            if i + 1 >= len(tokens):
                raise ValueError("--for needs a duration, e.g. --for 30m")
            time_budget = parse_duration(tokens[i + 1])
            i += 2
        elif tok == "--turns":
            if i + 1 >= len(tokens):
                raise ValueError("--turns needs a number, e.g. --turns 10")
            max_turns = _parse_turns(tokens[i + 1])
            i += 2
        elif tok == "--unbounded":
            unbounded = True
            i += 1
        else:
            objective_parts.append(tok)
            i += 1
    return " ".join(objective_parts).strip(), time_budget, max_turns, unbounded


def _parse_turns(text: str) -> int:
    try:
        n = int(text)
    except (TypeError, ValueError):
        raise ValueError(f"--turns must be a positive integer, got {text!r}")
    if n <= 0:
        raise ValueError(f"--turns must be positive, got {text!r}")
    return n


def _resolve_budget(
    settings: dict,
    time_budget: Optional[int],
    max_turns: Optional[int],
    unbounded: bool,
) -> Tuple[Optional[int], Optional[int]]:
    """Apply settings defaults to omitted caps (unless ``--unbounded``)."""
    if unbounded:
        return None, None
    if time_budget is None:
        raw = settings.get("default_time_budget", _DEFAULT_TIME_BUDGET)
        if raw:
            try:
                time_budget = parse_duration(str(raw))
            except DurationParseError:
                time_budget = parse_duration(_DEFAULT_TIME_BUDGET)
    if max_turns is None:
        raw = settings.get("default_max_turns", _DEFAULT_MAX_TURNS)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = _DEFAULT_MAX_TURNS
        max_turns = n if n > 0 else None
    return time_budget, max_turns


def _goal_settings(cli: "AgentaoCLI") -> dict:
    try:
        block = cli._load_settings().get("goal", {})
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}
