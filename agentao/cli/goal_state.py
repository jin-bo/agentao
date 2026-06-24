"""Persistent state for the CLI ``/goal`` long-task continuation.

:class:`GoalState` is the host-side (CLI) record that drives
``run_goal_continuation`` in ``input_loop.py``. It is persisted to
``.agentao/goal.json`` so a goal survives across turns and process restarts.

State machine (this module enforces only the **legal transitions** — *who*
may trigger each is enforced by the callers: the ``update_goal`` tool for the
agent, the ``/goal`` command for the user, the continuation loop for the
host)::

    active ──mark_complete──────▶ complete       (terminal)
    active ──mark_blocked───────▶ blocked         (dormant; /goal resume restarts)
    active ──mark_limit_reached─▶ limit_reached   (terminal until user clear/edit/re-budget)
    active ──pause──▶ paused ──resume──▶ active
    blocked ─────────resume──────────────▶ active

Drops codex's ``token_budget`` / ``tokens_used`` (time/turn budgets only —
see ``docs/design/codex-goal-mechanism-review.md`` §D) and collapses codex's
``UsageLimited`` + ``BudgetLimited`` into a single ``limit_reached``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    LIMIT_REACHED = "limit_reached"


# Terminal states the continuation loop will not drive. ``blocked`` is dormant
# rather than strictly terminal (``/goal resume`` revives it), but the loop
# stops on it all the same.
_STOP_STATES = frozenset(
    {GoalStatus.COMPLETE, GoalStatus.BLOCKED, GoalStatus.LIMIT_REACHED}
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class GoalState:
    """One long-task goal with an optional time/turn budget."""

    objective: str
    goal_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: GoalStatus = GoalStatus.ACTIVE
    time_budget_seconds: Optional[int] = None  # None = no time cap
    max_turns: Optional[int] = None            # None = no turn cap
    time_used_seconds: float = 0.0             # accumulated active (excludes paused)
    turns_used: int = 0
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    # ── state-machine transitions (legal transitions only) ──────────────

    def _touch(self) -> None:
        self.updated_at = _now_iso()

    def mark_complete(self) -> bool:
        """Agent: objective achieved. Active → complete; else no-op (False)."""
        if self.status != GoalStatus.ACTIVE:
            return False
        self.status = GoalStatus.COMPLETE
        self._touch()
        return True

    def mark_blocked(self) -> bool:
        """Agent: cannot proceed without the user. Active → blocked; else no-op."""
        if self.status != GoalStatus.ACTIVE:
            return False
        self.status = GoalStatus.BLOCKED
        self._touch()
        return True

    def mark_limit_reached(self) -> bool:
        """Host loop: a budget cap tripped. Active → limit_reached; else no-op."""
        if self.status != GoalStatus.ACTIVE:
            return False
        self.status = GoalStatus.LIMIT_REACHED
        self._touch()
        return True

    def pause(self) -> bool:
        """User: pause. Active → paused; else no-op (False)."""
        if self.status != GoalStatus.ACTIVE:
            return False
        self.status = GoalStatus.PAUSED
        self._touch()
        return True

    def resume(self) -> bool:
        """User: resume a paused **or** blocked goal → active; else no-op.

        ``limit_reached`` is intentionally NOT resumable — the user lifts it by
        re-budgeting or clearing (per §E).
        """
        if self.status not in (GoalStatus.PAUSED, GoalStatus.BLOCKED):
            return False
        self.status = GoalStatus.ACTIVE
        self._touch()
        return True

    def reactivate_from_limit(self) -> bool:
        """User re-budget lifts a ``limit_reached`` goal back to active."""
        if self.status != GoalStatus.LIMIT_REACHED:
            return False
        self.status = GoalStatus.ACTIVE
        self._touch()
        return True

    # ── predicates ──────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.status == GoalStatus.ACTIVE

    @property
    def is_stopped(self) -> bool:
        """Whether the continuation loop should not drive this goal."""
        return self.status in _STOP_STATES

    def status_label(self) -> str:
        return self.status.value

    def budget_tripped(self) -> bool:
        """Whether either configured cap has been reached (first to trip wins)."""
        if (
            self.time_budget_seconds is not None
            and self.time_used_seconds >= self.time_budget_seconds
        ):
            return True
        if self.max_turns is not None and self.turns_used >= self.max_turns:
            return True
        return False

    # ── serialization ───────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "GoalState":
        data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        if "status" in data:
            data["status"] = GoalStatus(data["status"])
        # Coerce numeric fields so a hand-edited / partially-written goal.json
        # surfaces as "no goal" (load_goal catches the raised error) instead of
        # loading a poisoned value that crashes the loop later at
        # budget_tripped() / time accounting.
        for key in ("time_budget_seconds", "max_turns"):
            if data.get(key) is not None:          # None is valid (= no cap)
                data[key] = int(data[key])
        if data.get("turns_used") is None:
            data.pop("turns_used", None)           # fall back to default (0)
        elif "turns_used" in data:
            data["turns_used"] = int(data["turns_used"])
        if data.get("time_used_seconds") is None:
            data.pop("time_used_seconds", None)    # fall back to default (0.0)
        elif "time_used_seconds" in data:
            data["time_used_seconds"] = float(data["time_used_seconds"])
        return cls(**data)


# ── persistence + display helpers ───────────────────────────────────────


def goal_path(project_root: Path) -> Path:
    return Path(project_root) / ".agentao" / "goal.json"


def load_goal(project_root: Path) -> Optional[GoalState]:
    """Load the persisted goal, or ``None`` if absent/unreadable."""
    path = goal_path(project_root)
    if not path.exists():
        return None
    try:
        return GoalState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def save_goal(goal: GoalState, project_root: Path) -> bool:
    """Persist the goal. Returns whether the write succeeded (``False`` on a
    read-only dir / full disk) so callers can warn instead of losing state
    silently."""
    path = goal_path(project_root)
    try:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(goal.to_dict(), indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def clear_goal(project_root: Path) -> bool:
    """Delete the persisted goal. Returns whether a file was present."""
    try:
        goal_path(project_root).unlink()
        return True
    except (FileNotFoundError, OSError):
        return False


def format_duration(seconds: float) -> str:
    """Render seconds as a compact ``1h30m`` / ``45s`` style string."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m}m" if m else f"{h}h"
    if m:
        return f"{m}m{s}s" if s else f"{m}m"
    return f"{s}s"


def budget_summary(goal: GoalState) -> str:
    """One-line ``used/cap`` summary across whichever axes are configured."""
    parts = []
    if goal.max_turns is not None:
        parts.append(f"{goal.turns_used}/{goal.max_turns} turns")
    if goal.time_budget_seconds is not None:
        parts.append(
            f"{format_duration(goal.time_used_seconds)}/"
            f"{format_duration(goal.time_budget_seconds)}"
        )
    return ", ".join(parts) if parts else "unbounded"
