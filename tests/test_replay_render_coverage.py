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
    #
    # ``session_ended`` was wrongly listed here: ``ReplayManager.end()``
    # records it on /clear, /new and exit, so it is in *every* completed
    # replay file. The exemption certified the exact key-preview
    # degradation this module exists to catch.
    "session_forked",
    "session_loaded",
    "session_saved",
    # Emitted (replay/adapter.py), but structural: it opens a turn and is
    # consumed by ``_group_events_into_turns``. Rendering it as its own
    # line would be noise.
    "turn_started",
}


def _emission_sites(kind: str) -> int:
    """Count non-declaration references to ``kind`` in the package.

    The exemptions above all rest on "this is never recorded". That claim
    decays silently the moment someone wires an emission point, so check
    it rather than trust the comment.
    """
    import subprocess

    const = kind.upper()
    out = subprocess.run(
        ["git", "grep", "-l", "-e", f"EventKind.{const}", "-e", f'"{kind}"',
         "--", "agentao"],
        capture_output=True, text=True,
    ).stdout
    hits = {
        line for line in out.splitlines()
        # events.py declares the vocabulary; schema.py maps it to variants.
        if not line.endswith(("replay/events.py", "replay/schema.py"))
    }
    return len(hits)


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


@pytest.mark.parametrize(
    "kind", sorted(_NO_SUMMARY_EXPECTED - {"turn_started"})
)
def test_exempted_kinds_really_have_no_emission_site(kind):
    """The 'never recorded' justification, checked instead of trusted.

    ``session_ended`` sat in this list with that exact comment while
    ``ReplayManager.end()`` wrote it to every replay file. Wire an
    emission point for one of the remaining three and this fails, which
    is the moment it needs a renderer.
    """
    assert _emission_sites(kind) == 0, (
        f"{kind} is referenced outside its declaration — it may now be "
        f"emitted, in which case it needs a summary branch"
    )


def test_the_session_ended_regression_stays_fixed():
    """It ships in every completed replay; it must not read as a key list."""
    out = _summarize_replay_event(
        {"kind": "session_ended", "payload": {"session_id": "abc12345xyz"}}
    )
    assert "session_id" not in out, "degraded back to the payload-key preview"
    assert "abc12345" in out


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


# Kinds the grouped turn view deliberately does not print as their own
# line, each with the reason. This is the ``_print_turn`` counterpart of
# ``_NO_SUMMARY_EXPECTED``; the summary guard alone cannot catch a kind
# that gets a summary branch but no ``elif`` in ``_turn.py`` — which is
# exactly the half of the original defect that was worse.
_NOT_IN_TURN_VIEW = {
    # Rendered structurally by the turn header / body / footer.
    "turn_started", "turn_completed", "user_message",
    "assistant_text_chunk", "assistant_thought_chunk",
    # Folded into the per-turn `tools` table by _collect_tool_rows.
    "tool_started", "tool_completed", "tool_output_chunk", "tool_result",
    "tool_confirmation_requested", "tool_confirmation_resolved",
    # Folded into the per-turn llm aggregation line.
    "llm_call_started", "llm_call_completed", "llm_call_delta", "llm_call_io",
    # Session-scoped: carry no turn_id, rendered in the session block.
    "replay_header", "replay_footer", "session_started", "session_ended",
    "session_saved", "session_loaded", "session_forked",
    # Rendered by the subagent section rather than the one-liner loop.
    "subagent_started", "subagent_completed",
    # Memory/skill writes are session-scoped bookkeeping, not turn narrative.
    "memory_write", "memory_delete", "memory_cleared",
    "skill_activated", "skill_deactivated",
    "background_notification_injected",
}


# Kinds the turn view renders only for the *interesting* outcome, so the
# probe has to supply one — see the filtering rationale in _turn.py.
_PROBE_PAYLOADS = {
    "tool_lifecycle": {
        "tool_name": "t", "phase": "failed", "outcome": "error",
        "tool_call_id": "c1",
    },
    "permission_decision": {
        "tool_name": "t", "outcome": "deny", "mode": "read-only",
    },
}


def _turn_view_renders(kind: str) -> bool:
    """Does adding an event of ``kind`` change what the turn view prints?

    Differential, not a substring search: several handled kinds print a
    *label* rather than the kind name (``compact``, ``ask``, ``hook``), so
    looking for the kind string reports them as dropped. Comparing the
    render with and against without the event is label-agnostic.
    """
    frame = [
        {"seq": 1, "kind": "turn_started", "ts": "", "turn_id": "t1", "payload": {}},
        {"seq": 3, "kind": "turn_completed", "ts": "", "turn_id": "t1",
         "payload": {"status": "ok", "final_text": ""}},
    ]
    probe = {
        "seq": 2, "kind": kind, "ts": "", "turn_id": "t1",
        "payload": dict(_PROBE_PAYLOADS.get(kind, {"__probe__": "ZZ"})),
    }

    def _render_events(events):
        console = Console(width=120, record=True, force_terminal=False)
        _render_replay_grouped(events, _Meta(), console)
        return console.export_text()

    without = _render_events(frame)
    with_ = _render_events([frame[0], probe, frame[1]])
    return with_ != without


def test_every_event_kind_is_handled_by_the_turn_view():
    """A kind with a summary but no ``elif`` in _turn.py is silently
    dropped from the *default* view — correct in JSONL, visible in
    ``--raw``, invisible where people actually look."""
    unhandled = {
        k for k in _all_kinds()
        if k not in _NOT_IN_TURN_VIEW and not _turn_view_renders(k)
    }
    assert not unhandled, (
        f"kind(s) dropped by _print_turn's allowlist: {sorted(unhandled)} — "
        f"add an elif in _turn.py or justify it in _NOT_IN_TURN_VIEW"
    )


def test_turn_view_exemptions_are_all_real_event_kinds():
    assert _NOT_IN_TURN_VIEW <= _all_kinds()


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
