"""Tests for PlanSession state management."""

from agentao.plan.session import PlanPhase, PlanSession


def test_defaults():
    s = PlanSession()
    assert s.phase == PlanPhase.INACTIVE
    assert s.draft is None
    assert s.draft_id is None
    assert not s.is_active


def test_is_active_for_active():
    s = PlanSession()
    s.phase = PlanPhase.ACTIVE
    assert s.is_active


def test_is_active_for_approval_pending():
    s = PlanSession()
    s.phase = PlanPhase.APPROVAL_PENDING
    assert s.is_active


def test_reset():
    s = PlanSession()
    s.phase = PlanPhase.ACTIVE
    s.draft = "some plan"
    s.draft_id = "123"
    s.pre_plan_mode = "fake_mode"
    s.pre_plan_allow_all = True
    s._approval_requested = True

    s.reset()

    assert s.phase == PlanPhase.INACTIVE
    assert s.draft is None
    assert s.draft_id is None
    assert s.pre_plan_mode is None
    assert not s.pre_plan_allow_all
    assert not s._approval_requested


def test_consume_approval_once():
    s = PlanSession()
    s._approval_requested = True

    assert s.consume_approval_request() is True
    assert s.consume_approval_request() is False  # consumed


def test_consume_approval_not_set():
    s = PlanSession()
    assert s.consume_approval_request() is False
