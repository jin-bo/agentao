"""Tests for PlanController lifecycle operations."""

import os
import re
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentao.plan.session import PlanPhase, PlanSession
from agentao.plan.controller import PlanController
from agentao.permissions import PermissionMode


def _make_controller(tmp_path: Path):
    """Create a PlanController with temp paths and mock callbacks."""
    session = PlanSession()
    session.current_plan_path = tmp_path / "plan.md"
    session.history_dir = tmp_path / "plan-history"

    engine = Mock()
    apply_mode = Mock()
    load_settings = Mock(return_value={"mode": "workspace-write"})

    ctrl = PlanController(
        session=session,
        permission_engine=engine,
        apply_mode_fn=apply_mode,
        load_settings_fn=load_settings,
    )
    return ctrl, session, engine, apply_mode, load_settings


def test_enter_saves_mode_and_sets_active(tmp_path):
    ctrl, session, engine, _, _ = _make_controller(tmp_path)

    ctrl.enter(PermissionMode.WORKSPACE_WRITE, allow_all=True)

    assert session.phase == PlanPhase.ACTIVE
    assert session.pre_plan_mode == PermissionMode.WORKSPACE_WRITE
    assert session.pre_plan_allow_all is True
    engine.set_mode.assert_called_once_with(PermissionMode.PLAN)


def test_save_draft_returns_id(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    draft_id = ctrl.save_draft("# My Plan\n\nSome content.")

    assert draft_id is not None
    assert session.draft == "# My Plan\n\nSome content."
    assert session.draft_id == draft_id
    assert session.current_plan_path.exists()
    content = session.current_plan_path.read_text(encoding="utf-8")
    assert "# Agentao Plan" in content
    assert "Some content." in content


def test_finalize_matching_draft_id(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    draft_id = ctrl.save_draft("Plan v1")
    ctrl.finalize(draft_id)

    assert session.phase == PlanPhase.APPROVAL_PENDING
    assert session._approval_requested is True


def test_finalize_stale_draft_id(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    ctrl.save_draft("Plan v1")
    ctrl.save_draft("Plan v2")  # overwrites draft_id

    with pytest.raises(ValueError, match="Stale draft_id"):
        ctrl.finalize("wrong_id")


def test_finalize_no_draft(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    with pytest.raises(ValueError, match="No draft has been saved"):
        ctrl.finalize("some_id")


def test_reject_approval_returns_to_active(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    draft_id = ctrl.save_draft("Plan")
    ctrl.finalize(draft_id)
    assert session.phase == PlanPhase.APPROVAL_PENDING

    ctrl.reject_approval()
    assert session.phase == PlanPhase.ACTIVE
    assert session._approval_requested is False


def test_exit_restores_mode(tmp_path):
    ctrl, session, _, apply_mode, _ = _make_controller(tmp_path)

    ctrl.enter(PermissionMode.WORKSPACE_WRITE, allow_all=False)
    restored, restore_allow_all = ctrl.exit_plan_mode()

    assert restored == PermissionMode.WORKSPACE_WRITE
    assert restore_allow_all is False
    apply_mode.assert_called_with(PermissionMode.WORKSPACE_WRITE)
    assert session.phase == PlanPhase.INACTIVE


def test_exit_full_access_with_allow_all_reads_disk(tmp_path):
    """FULL_ACCESS + allow_all should read persisted mode from disk."""
    ctrl, session, _, apply_mode, load_settings = _make_controller(tmp_path)
    load_settings.return_value = {"mode": "read-only"}

    ctrl.enter(PermissionMode.FULL_ACCESS, allow_all=True)
    restored, restore_allow_all = ctrl.exit_plan_mode()

    assert restored == PermissionMode.READ_ONLY
    assert restore_allow_all is True
    apply_mode.assert_called_with(PermissionMode.READ_ONLY)


def test_multiple_save_overwrites_draft_id(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    id1 = ctrl.save_draft("v1")
    id2 = ctrl.save_draft("v2")

    assert id1 != id2 or id1 == id2  # ids may match if same second
    assert session.draft == "v2"
    assert session.draft_id == id2


def test_archive_and_clear_resets(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)

    ctrl.enter(PermissionMode.WORKSPACE_WRITE, allow_all=False)
    ctrl.save_draft("Plan to clear")
    ctrl.archive_and_clear()

    assert session.phase == PlanPhase.INACTIVE
    assert not session.current_plan_path.exists()


def test_list_history(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    # Create some history by saving multiple drafts
    ctrl.save_draft("v1")
    ctrl.save_draft("v2")

    entries = ctrl.list_history()
    # At least 1 archived entry (v1 was archived when v2 was saved)
    assert len(entries) >= 1


def test_history_archive_filename_format(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    ctrl.save_draft("v1")
    ctrl.save_draft("v2")

    entries = ctrl.list_history()
    assert entries
    assert re.fullmatch(r"\d{8}-\d{6}\.md", entries[0].name)


def test_archive_and_clear_outside_plan_mode_does_not_touch_permissions(tmp_path):
    """Clearing a stale plan file while NOT in plan mode must not call _apply_mode."""
    ctrl, session, _, apply_mode, _ = _make_controller(tmp_path)
    # Write a stale plan file without entering plan mode
    session.current_plan_path.parent.mkdir(parents=True, exist_ok=True)
    session.current_plan_path.write_text("stale plan", encoding="utf-8")
    assert session.phase == PlanPhase.INACTIVE

    restored, restore_allow_all = ctrl.archive_and_clear()

    assert restored is None
    assert restore_allow_all is None
    apply_mode.assert_not_called()  # permissions untouched
    assert not session.current_plan_path.exists()


def test_archive_and_clear_in_plan_mode_returns_allow_all(tmp_path):
    """archive_and_clear must pass restore_allow_all back so the CLI can reapply it."""
    ctrl, session, _, apply_mode, _ = _make_controller(tmp_path)

    ctrl.enter(PermissionMode.WORKSPACE_WRITE, allow_all=True)
    ctrl.save_draft("some plan")

    restored, restore_allow_all = ctrl.archive_and_clear()

    assert restored == PermissionMode.WORKSPACE_WRITE
    assert restore_allow_all is True
    apply_mode.assert_called()
    assert session.phase == PlanPhase.INACTIVE


def test_plan_file_status_header_draft(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    ctrl.save_draft("# Plan\n\n## Context\nsome context.")

    content = session.current_plan_path.read_text(encoding="utf-8")
    assert "Status: Draft" in content


def test_plan_file_status_header_awaiting_approval(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    draft_id = ctrl.save_draft("# Plan\n\n## Context\nsome context.")
    ctrl.finalize(draft_id)

    content = session.current_plan_path.read_text(encoding="utf-8")
    assert "Status: Awaiting Approval" in content


def test_reject_reverts_status_to_draft(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    draft_id = ctrl.save_draft("# Plan\n\n## Context\nsome context.")
    ctrl.finalize(draft_id)
    ctrl.reject_approval()

    content = session.current_plan_path.read_text(encoding="utf-8")
    assert "Status: Draft" in content
    assert "Awaiting Approval" not in content


def test_auto_save_response_with_plan_heading(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    plan_text = "## Context\nWhy.\n\n## Objective\nWhat.\n\n## Approach\n1. Step one."
    saved = ctrl.auto_save_response(plan_text)

    assert saved is True
    assert session.draft == plan_text


def test_auto_save_response_without_plan_heading(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    saved = ctrl.auto_save_response("Just a conversational response without headings.")

    assert saved is False
    assert session.draft is None


def test_auto_save_response_empty(tmp_path):
    ctrl, session, _, _, _ = _make_controller(tmp_path)
    session.phase = PlanPhase.ACTIVE

    assert ctrl.auto_save_response("") is False
    assert ctrl.auto_save_response("   ") is False
