"""Tests for plan_save and plan_finalize model tools."""

import tempfile
from pathlib import Path
from unittest.mock import Mock

from agentao.plan.session import PlanPhase, PlanSession
from agentao.plan.controller import PlanController
from agentao.tools.plan import PlanSaveTool, PlanFinalizeTool


def _make_tools(tmp_path: Path):
    session = PlanSession()
    session.current_plan_path = tmp_path / "plan.md"
    session.history_dir = tmp_path / "plan-history"

    ctrl = PlanController(
        session=session,
        permission_engine=Mock(),
        apply_mode_fn=Mock(),
        load_settings_fn=Mock(return_value={}),
    )
    save = PlanSaveTool(ctrl)
    finalize = PlanFinalizeTool(ctrl)
    return save, finalize, session, ctrl


def test_plan_save_returns_draft_id(tmp_path):
    save, _, session, _ = _make_tools(tmp_path)
    session.phase = PlanPhase.ACTIVE

    result = save.execute(content="# Plan\n\nContent here.")
    assert "draft_id:" in result
    assert session.draft == "# Plan\n\nContent here."


def test_plan_finalize_with_valid_id(tmp_path):
    save, finalize, session, _ = _make_tools(tmp_path)
    session.phase = PlanPhase.ACTIVE

    save_result = save.execute(content="# Plan")
    draft_id = save_result.split("draft_id: ")[1]

    result = finalize.execute(draft_id=draft_id)
    assert "finalized" in result.lower()
    assert session.phase == PlanPhase.APPROVAL_PENDING


def test_plan_finalize_with_stale_id(tmp_path):
    save, finalize, session, _ = _make_tools(tmp_path)
    session.phase = PlanPhase.ACTIVE

    save.execute(content="# Plan v1")
    save.execute(content="# Plan v2")  # new draft_id

    result = finalize.execute(draft_id="wrong_id")
    assert "Error" in result
    assert session.phase == PlanPhase.ACTIVE  # not changed


def test_tools_error_when_inactive(tmp_path):
    save, finalize, session, _ = _make_tools(tmp_path)
    assert session.phase == PlanPhase.INACTIVE

    result = save.execute(content="something")
    assert "not in plan mode" in result.lower()

    result = finalize.execute(draft_id="any")
    assert "not in plan mode" in result.lower()


def test_plan_save_empty_content(tmp_path):
    save, _, session, _ = _make_tools(tmp_path)
    session.phase = PlanPhase.ACTIVE

    result = save.execute(content="")
    assert "empty" in result.lower()


def test_user_rejects_then_model_continues(tmp_path):
    save, finalize, session, ctrl = _make_tools(tmp_path)
    session.phase = PlanPhase.ACTIVE

    # First attempt: save + finalize
    r1 = save.execute(content="# Plan v1")
    draft_id_1 = r1.split("draft_id: ")[1]
    finalize.execute(draft_id=draft_id_1)
    assert session.phase == PlanPhase.APPROVAL_PENDING

    # User rejects
    ctrl.reject_approval()
    assert session.phase == PlanPhase.ACTIVE

    # Model refines and re-finalizes
    r2 = save.execute(content="# Plan v2 (revised)")
    draft_id_2 = r2.split("draft_id: ")[1]
    finalize.execute(draft_id=draft_id_2)
    assert session.phase == PlanPhase.APPROVAL_PENDING
    assert session.draft == "# Plan v2 (revised)"
