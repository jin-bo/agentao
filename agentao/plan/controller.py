"""Plan mode lifecycle controller.

Centralises enter / save / finalize / reject / exit / archive / restore
so that ``cli.py`` only does command parsing and Rich UI.
"""

from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .session import PlanPhase, PlanSession


class PlanController:
    """Manages all plan-mode lifecycle operations.

    Parameters
    ----------
    session : PlanSession
        Shared session state (also held by Agent).
    permission_engine : PermissionEngine
        The live permission engine instance.
    apply_mode_fn : callable
        ``CLI._apply_mode`` — switches mode, resets allow_all, etc.
    load_settings_fn : callable
        ``CLI._load_settings`` — reads persisted mode from settings.json.
    """

    def __init__(
        self,
        session: PlanSession,
        permission_engine: object,
        apply_mode_fn: Callable,
        load_settings_fn: Callable[[], Dict],
    ):
        self._session = session
        self._engine = permission_engine
        self._apply_mode = apply_mode_fn
        self._load_settings = load_settings_fn

    @property
    def session(self) -> PlanSession:
        return self._session

    # ------------------------------------------------------------------
    # Enter
    # ------------------------------------------------------------------

    def enter(self, current_mode: object, allow_all: bool) -> None:
        """Enter plan mode.  Saves current permissions, switches to PLAN preset."""
        from ..permissions import PermissionMode

        self._session.pre_plan_mode = current_mode
        self._session.pre_plan_allow_all = allow_all
        self._session.phase = PlanPhase.ACTIVE
        self._engine.set_mode(PermissionMode.PLAN)

    # ------------------------------------------------------------------
    # Draft management
    # ------------------------------------------------------------------

    def save_draft(self, content: str) -> str:
        """Archive existing plan file, write new draft, return ``draft_id``."""
        self._archive_plan()
        draft_id = datetime.now().strftime("%Y%m%d%H%M%S")
        self._session.draft = content
        self._session.draft_id = draft_id
        # Persist to disk
        self._session.current_plan_path.parent.mkdir(parents=True, exist_ok=True)
        file_content = self._build_plan_file_content(content)
        self._session.current_plan_path.write_text(file_content, encoding="utf-8")
        return draft_id

    def auto_save_response(self, response: str) -> bool:
        """Save response text as draft if it looks like a plan document.

        Called by CLI after each plan-mode turn where the model did NOT call
        plan_save itself.  Returns True if auto-save occurred.
        """
        if not response or not response.strip():
            return False
        # Heuristic: treat as a plan if the response contains at least one
        # Markdown heading that looks like a plan section.
        import re
        plan_headings = {"context", "objective", "approach", "verification",
                         "critical files", "assumptions", "risks", "open questions"}
        found = {h.lower() for h in re.findall(r"^##\s+(.+)$", response, re.MULTILINE)}
        if found & plan_headings:
            self.save_draft(response)
            return True
        return False

    def show_draft(self) -> Optional[str]:
        """Return current plan text (from session or disk)."""
        if self._session.draft is not None:
            return self._session.draft
        if self._session.current_plan_path.exists():
            return self._session.current_plan_path.read_text(encoding="utf-8")
        return None

    # ------------------------------------------------------------------
    # Finalize / Approval
    # ------------------------------------------------------------------

    def finalize(self, draft_id: str) -> None:
        """Mark the current draft as ready for user approval.

        Raises ``ValueError`` if *draft_id* does not match the current draft
        (stale finalize attempt).
        """
        if self._session.draft_id is None:
            raise ValueError("No draft has been saved yet.  Call plan_save first.")
        if draft_id != self._session.draft_id:
            raise ValueError(
                f"Stale draft_id '{draft_id}' — current draft is '{self._session.draft_id}'.  "
                "Save a new draft with plan_save and retry."
            )
        # Re-write plan file with "Awaiting Approval" status header
        if self._session.draft and self._session.current_plan_path.exists():
            try:
                self._session.current_plan_path.write_text(
                    self._build_plan_file_content(self._session.draft, status="Awaiting Approval"),
                    encoding="utf-8",
                )
            except OSError:
                pass  # best-effort; approval flow still proceeds
        self._session.phase = PlanPhase.APPROVAL_PENDING
        self._session._approval_requested = True

    def reject_approval(self) -> None:
        """User rejected the plan.  Return to ACTIVE so the model can continue."""
        # Revert plan file status header from "Awaiting Approval" back to "Draft"
        if self._session.draft and self._session.current_plan_path.exists():
            try:
                self._session.current_plan_path.write_text(
                    self._build_plan_file_content(self._session.draft, status="Draft"),
                    encoding="utf-8",
                )
            except OSError:
                pass
        self._session.phase = PlanPhase.ACTIVE
        self._session._approval_requested = False

    # ------------------------------------------------------------------
    # Exit (THE single restore-permissions path)
    # ------------------------------------------------------------------

    def exit_plan_mode(self) -> object:
        """Exit plan mode and restore prior permissions.

        Returns the restored ``PermissionMode``.  Used by both
        ``/plan implement`` and ``/plan clear``.
        """
        from ..permissions import PermissionMode

        restore = self._session.pre_plan_mode or PermissionMode.WORKSPACE_WRITE
        restore_allow_all = self._session.pre_plan_allow_all

        # If restoring FULL_ACCESS that came from a session-only escalation
        # (allow_all was active before plan mode), read the persisted mode
        # from disk to avoid baking a temporary grant into settings.json.
        if restore == PermissionMode.FULL_ACCESS and restore_allow_all:
            saved = self._load_settings().get("mode", PermissionMode.WORKSPACE_WRITE.value)
            try:
                disk_mode = PermissionMode(saved)
                restore = disk_mode if disk_mode != PermissionMode.PLAN else PermissionMode.WORKSPACE_WRITE
            except ValueError:
                restore = PermissionMode.WORKSPACE_WRITE

        self._apply_mode(restore)

        # _apply_mode resets allow_all to False; restore the saved value *after*.
        # We return restore_allow_all so the CLI can set it on itself.
        self._session.reset()
        return restore, restore_allow_all

    # ------------------------------------------------------------------
    # Archive / Clear
    # ------------------------------------------------------------------

    def archive_and_clear(self) -> tuple:
        """Archive the plan file and, if plan mode is active, exit it.

        Returns ``(restored_mode, restore_allow_all)`` when plan mode was
        active, otherwise ``(None, None)``.  The CLI must apply
        ``restore_allow_all`` itself (same contract as ``exit_plan_mode``).
        """
        self._archive_plan()
        if self._session.current_plan_path.exists():
            self._session.current_plan_path.unlink()
        if self._session.is_active:
            return self.exit_plan_mode()
        # Not in plan mode — only clean up disk; do not touch permissions.
        return None, None

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def list_history(self, limit: int = 10) -> List[Path]:
        """Return archived plan files, most recent first."""
        hist = self._session.history_dir
        if not hist.exists():
            return []
        return sorted(hist.glob("*.md"), reverse=True)[:limit]

    # ------------------------------------------------------------------
    # Private helpers (migrated from cli.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_plan_file_content(response_text: str, status: str = "Draft") -> str:
        """Wrap plan text in a file header with timestamp and status."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"# Agentao Plan\n\n"
            f"_Saved: {now} · Status: {status}_\n\n"
            f"---\n\n{response_text.strip()}\n"
        )

    def _archive_plan(self) -> Optional[Path]:
        """Copy plan file into plan-history/ before it is overwritten.

        Returns the archive path, or None if the file did not exist.
        """
        plan_file = self._session.current_plan_path
        if not plan_file.exists():
            return None
        try:
            self._session.history_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = self._session.history_dir / f"{ts}.md"
            dest.write_text(plan_file.read_text(encoding="utf-8"), encoding="utf-8")
            return dest
        except Exception:
            return None
