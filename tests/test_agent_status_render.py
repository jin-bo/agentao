"""Render-path tests for `/agent status` and the background dashboard.

These exist because of a real regression. When incomplete sub-agent runs
started being recorded as ``status="failed"`` (0.4.15), the CLI's status
view still read that as "crashed" and printed only ``error`` — silently
discarding the ``result`` of a background agent that had run for a long
time before stopping short.

``test_bg_task_store.py`` covers the store; nothing covered the *display*,
which is exactly where the bug lived. A record can be perfectly correct on
disk and still have its contents thrown away on the way to the screen.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentao.agents.bg_store import BackgroundTaskStore
from agentao.cli.commands_ext.agents import handle_agent_command


PARTIAL = "# Findings\n\nAudited 41 of 60 modules before stopping."


def _cli_with(record_kwargs: dict) -> tuple[object, str]:
    """A CLI stub whose store holds one finished task."""
    store = BackgroundTaskStore()
    store.register("bg-1", "auditor", "audit the codebase")
    store.mark_running("bg-1")
    store.update("bg-1", **record_kwargs)
    cli = SimpleNamespace(agent=SimpleNamespace(bg_store=store))
    return cli, "bg-1"


def _render(capsys, cli, args: str) -> str:
    handle_agent_command(cli, args)
    return capsys.readouterr().out


class TestStatusShowsPartialWork:
    def test_incomplete_run_surfaces_its_result(self, capsys):
        """The regression: budget exhausted, work done, output reachable."""
        cli, tid = _cli_with(dict(
            status="failed",
            result=PARTIAL,
            error="reached its iteration budget",
            incomplete_reason="max_iterations",
        ))
        out = _render(capsys, cli, f"status {tid}")

        assert "Audited 41 of 60 modules" in out, (
            "the partial result was discarded -- this is the regression"
        )
        assert "max_iterations" in out or "iteration budget" in out

    def test_incomplete_run_is_not_framed_as_an_error(self, capsys):
        cli, tid = _cli_with(dict(
            status="failed",
            result=PARTIAL,
            error="reached its iteration budget",
            incomplete_reason="max_iterations",
        ))
        out = _render(capsys, cli, f"status {tid}")
        assert "Did not finish" in out
        assert "Error:" not in out

    def test_a_real_crash_still_reads_as_an_error(self, capsys):
        """The distinction has to cut both ways or it is not a distinction."""
        cli, tid = _cli_with(dict(
            status="failed",
            error="ZeroDivisionError: division by zero",
        ))
        out = _render(capsys, cli, f"status {tid}")
        assert "Error:" in out
        assert "ZeroDivisionError" in out
        assert "Did not finish" not in out

    def test_completed_run_is_unchanged(self, capsys):
        cli, tid = _cli_with(dict(status="completed", result=PARTIAL))
        out = _render(capsys, cli, f"status {tid}")
        assert "Audited 41 of 60 modules" in out
        assert "Did not finish" not in out

    def test_incomplete_with_no_result_still_reports_the_reason(self, capsys):
        """A run that stopped short having produced nothing must not go silent."""
        cli, tid = _cli_with(dict(
            status="failed",
            error="produced no output",
            incomplete_reason="no_output",
        ))
        out = _render(capsys, cli, f"status {tid}")
        assert "Did not finish" in out
        assert "no output" in out


class TestLegacyRecordsStillRender:
    """Records written before 0.4.15 have no `incomplete_reason` key at all."""

    def test_missing_key_is_treated_as_a_crash_not_a_KeyError(self, capsys):
        store = BackgroundTaskStore()
        store.register("bg-old", "auditor", "audit")
        store.mark_running("bg-old")
        store.update("bg-old", status="failed", error="boom")
        # Simulate a record loaded from an older on-disk snapshot.
        rec = store.get("bg-old")
        rec.pop("incomplete_reason", None)
        cli = SimpleNamespace(agent=SimpleNamespace(bg_store=store))

        out = _render(capsys, cli, "status bg-old")
        assert "Error:" in out
        assert "boom" in out


class TestStoreCarriesTheReason:
    def test_reason_round_trips(self):
        store = BackgroundTaskStore()
        store.register("bg-2", "a", "t")
        store.update("bg-2", status="failed", result="x",
                     error="d", incomplete_reason="doom_loop")
        assert store.get("bg-2")["incomplete_reason"] == "doom_loop"

    def test_reason_defaults_to_none(self):
        store = BackgroundTaskStore()
        store.register("bg-3", "a", "t")
        store.update("bg-3", status="completed", result="x")
        assert store.get("bg-3")["incomplete_reason"] is None

    def test_orphan_recovery_is_a_crash_not_an_incomplete_run(self):
        """`process exited before task finished` is a genuine failure.

        It must NOT acquire an incomplete_reason, or a killed process would
        masquerade as an agent that merely ran out of turns.
        """
        store = BackgroundTaskStore()
        store.register("bg-4", "a", "t")
        store.mark_running("bg-4")
        rec = store.get("bg-4")
        assert rec.get("incomplete_reason") is None
