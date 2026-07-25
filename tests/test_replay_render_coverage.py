"""Exhaustiveness guard over the replay renderers' event vocabulary.

The replay event vocabulary is written down in three places — the
recorder side (``EventKind``), the one-line summarizer
(``_summarize_replay_event``) and the per-turn narrative
(``_print_turn``) — with nothing tying them together. That is how the
three **v1.2** audit kinds (``tool_lifecycle`` / ``subagent_lifecycle``
/ ``permission_decision``) shipped recorded-but-unrendered: the JSONL
was correct, ``--raw`` degraded to a payload-key preview, and the
default grouped view — whose event loop is an *allowlist* — dropped
them silently.

These tests make the next added kind fail loudly instead.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from agentao.cli.replay_render import _summarize_replay_event
from agentao.cli.replay_render._views import (
    _render_replay_grouped,
    _render_replay_raw,
)
from agentao.replay.events import EventKind

# A payload key no renderer knows about. If the summarizer falls through
# to its "preview the payload keys" branch, this leaks into the output —
# which is exactly the signal we want to detect.
_PROBE = {"__probe__": 1}

# Kinds deliberately without a summary, each for a stated reason. Keep
# this list short and justified; growing it is the thing to push back on
# in review.
_NO_SUMMARY_EXPECTED = {
    # Declared so readers/schema can whitelist the vocabulary up front,
    # but never passed to ``recorder.record`` anywhere in the tree.
    # ``session_saved`` is marked "reserved; not emitted in v1" in
    # EventKind's own docstring.
    "session_ended",
    "session_forked",
    "session_loaded",
    "session_saved",
    # Emitted (replay/adapter.py), but structural: it opens a turn and is
    # consumed by ``_group_events_into_turns``. Rendering it as its own
    # line would be noise.
    "turn_started",
}


def _all_kinds() -> set[str]:
    return {
        v for name, v in vars(EventKind).items()
        if isinstance(v, str) and not name.startswith("_")
    }


def _falls_through(kind: str) -> bool:
    return "__probe__" in _summarize_replay_event({"kind": kind, "payload": dict(_PROBE)})


def test_every_event_kind_has_a_summary():
    """Add a kind to EventKind → add a summary, or justify it above."""
    unhandled = {k for k in _all_kinds() if _falls_through(k)}
    unexpected = unhandled - _NO_SUMMARY_EXPECTED
    assert not unexpected, (
        f"event kind(s) with no summary renderer: {sorted(unexpected)} — "
        f"add a branch to _summary.py or justify it in _NO_SUMMARY_EXPECTED"
    )


def test_no_summary_list_has_not_gone_stale():
    """If a kind gains a renderer, drop it from the exemption list."""
    stale = {k for k in _NO_SUMMARY_EXPECTED if not _falls_through(k)}
    assert not stale, f"now rendered, remove from _NO_SUMMARY_EXPECTED: {sorted(stale)}"


def test_exemptions_are_all_real_event_kinds():
    assert _NO_SUMMARY_EXPECTED <= _all_kinds()


# ── The v1.2 audit kinds specifically ───────────────────────────────

_V12_AUDIT = ("tool_lifecycle", "subagent_lifecycle", "permission_decision")


@pytest.mark.parametrize("kind", _V12_AUDIT)
def test_v12_audit_kind_has_a_summary(kind):
    assert not _falls_through(kind)


class _Meta:
    """Banner stub — any attribute resolves to a harmless blank."""

    full_id = "replay-under-test"

    def __getattr__(self, name):
        return ""


def _audit_events() -> list[dict]:
    return [
        {"seq": 1, "kind": "replay_header", "ts": "", "turn_id": None,
         "payload": {"schema_version": "1.2"}},
        {"seq": 2, "kind": "turn_started", "ts": "", "turn_id": "t1", "payload": {}},
        {"seq": 3, "kind": "permission_decision", "ts": "", "turn_id": "t1",
         "payload": {"tool_name": "run_shell_command", "outcome": "deny",
                     "mode": "read-only", "reason": "blocked in read-only",
                     "decision_id": "d1", "loaded_sources": []}},
        {"seq": 4, "kind": "tool_lifecycle", "ts": "", "turn_id": "t1",
         "payload": {"tool_name": "run_shell_command", "phase": "failed",
                     "outcome": "error", "error_type": "PermissionDenied",
                     "tool_call_id": "c1"}},
        {"seq": 5, "kind": "subagent_lifecycle", "ts": "", "turn_id": "t1",
         "payload": {"child_task_id": "bg-7", "phase": "completed",
                     "task_summary": "audit the config"}},
        {"seq": 6, "kind": "turn_completed", "ts": "", "turn_id": "t1",
         "payload": {"status": "ok", "final_text": "done"}},
    ]


def _render(fn) -> str:
    console = Console(width=120, record=True, force_terminal=False)
    fn(_audit_events(), _Meta(), console)
    return console.export_text()


def test_grouped_view_shows_audit_events():
    """The regression: these were dropped by _print_turn's allowlist."""
    out = _render(_render_replay_grouped)
    assert "run_shell_command" in out
    assert "deny" in out, "a denied permission decision must be visible"
    assert "PermissionDenied" in out
    assert "bg-7" in out


def test_raw_view_shows_audit_events_not_key_names():
    out = _render(_render_replay_raw)
    assert "deny" in out
    assert "read-only" in out
    # The old behavior: a bare sorted key list instead of a summary.
    assert "decision_id, loaded_sources" not in out


def test_denied_decision_is_not_silently_equal_to_allowed():
    """An audit view that renders deny and allow identically is useless."""
    base = {"tool_name": "t", "mode": "workspace-write", "decision_id": "d",
            "loaded_sources": []}
    allow = _summarize_replay_event(
        {"kind": "permission_decision", "payload": {**base, "outcome": "allow"}})
    deny = _summarize_replay_event(
        {"kind": "permission_decision", "payload": {**base, "outcome": "deny"}})
    assert allow != deny
    assert "allow" in allow and "deny" in deny


def test_tool_lifecycle_prefers_outcome_over_phase():
    """``phase`` is coarse (failed); ``outcome`` distinguishes error from
    cancelled, which is the distinction an auditor actually needs."""
    cancelled = _summarize_replay_event({
        "kind": "tool_lifecycle",
        "payload": {"tool_name": "t", "phase": "failed", "outcome": "cancelled"},
    })
    errored = _summarize_replay_event({
        "kind": "tool_lifecycle",
        "payload": {"tool_name": "t", "phase": "failed", "outcome": "error"},
    })
    assert "cancelled" in cancelled
    assert "error" in errored
    assert cancelled != errored


def test_summaries_survive_a_missing_optional_field():
    """Every optional field is Optional in the host model; a summary must
    not raise when one is absent."""
    for kind in _V12_AUDIT:
        assert isinstance(
            _summarize_replay_event({"kind": kind, "payload": {}}), str
        )
