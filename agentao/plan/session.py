"""Plan mode session state — the single source of truth for host-side plan state."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class PlanPhase(Enum):
    """Host-side plan mode phases (3-state FSM).

    RESEARCH / CLARIFY / FINALIZE live in the system prompt as model
    collaboration guidance — they are NOT tracked here.
    """
    INACTIVE = "inactive"
    ACTIVE = "active"
    APPROVAL_PENDING = "approval_pending"


@dataclass
class PlanSession:
    """All plan-mode state in one place.

    Shared by reference between CLI and Agent.  The CLI mutates via
    PlanController; the Agent reads ``is_active`` for prompt building.
    """

    phase: PlanPhase = PlanPhase.INACTIVE
    draft: Optional[str] = None
    draft_id: Optional[str] = None
    current_plan_path: Path = field(default_factory=lambda: Path(".agentao/plan.md"))
    history_dir: Path = field(default_factory=lambda: Path(".agentao/plan-history"))
    pre_plan_mode: Optional[object] = None   # PermissionMode; restored on exit
    pre_plan_allow_all: bool = False
    _approval_requested: bool = field(default=False, repr=False)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self.phase != PlanPhase.INACTIVE

    # ------------------------------------------------------------------
    # One-shot approval flag
    # ------------------------------------------------------------------

    def consume_approval_request(self) -> bool:
        """Return *True* exactly once after ``plan_finalize`` fires.

        The CLI calls this after each agent turn.  Prevents repeated
        "Execute?" prompts when the phase is APPROVAL_PENDING but the
        approval was already presented.
        """
        if self._approval_requested:
            self._approval_requested = False
            return True
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all state back to INACTIVE."""
        self.phase = PlanPhase.INACTIVE
        self.draft = None
        self.draft_id = None
        self.pre_plan_mode = None
        self.pre_plan_allow_all = False
        self._approval_requested = False
