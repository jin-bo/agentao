"""``/copy`` clipboard fallback chain.

The pre-refactor implementation called ``subprocess.run`` three times with
no ``timeout=``, so a wedged ``pbcopy`` blocked the input loop forever.
These tests pin the replacement contract: every attempt is bounded, a
missing binary falls through to the next candidate, and a hung one does
not abort the chain.
"""

from __future__ import annotations

import subprocess
import types

import pytest

from agentao.cli import input_loop


class _Cli:
    def __init__(self, last_response="hello"):
        self.last_response = last_response


@pytest.fixture
def printed(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(
        input_loop.console, "print", lambda *a, **k: out.append(" ".join(map(str, a)))
    )
    return out


def _stub(monkeypatch, behavior):
    """Install a ``run_captured`` stub driven by ``behavior(cmd) -> result``.

    ``behavior`` may return a CompletedProcess-like object or raise.
    """
    recorded: list[list[str]] = []

    def fake(cmd, **kwargs):
        recorded.append(cmd)
        assert kwargs.get("timeout"), "every clipboard attempt must be bounded"
        return behavior(cmd)

    monkeypatch.setattr(input_loop, "run_captured", fake)
    return recorded


def _ok(returncode=0, stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)


def test_no_response_yet_does_not_shell_out(monkeypatch, printed):
    called = _stub(monkeypatch, lambda cmd: _ok())
    input_loop._copy_last_response(_Cli(last_response=None))
    assert called == []
    assert "No response to copy yet" in printed[0]


def test_first_utility_wins_and_is_bounded(monkeypatch, printed):
    called = _stub(monkeypatch, lambda cmd: _ok())
    input_loop._copy_last_response(_Cli())
    # Only the first candidate runs; the rest are never spawned.
    assert called == [["pbcopy"]]
    assert "Copied to clipboard" in printed[0]


def test_every_attempt_passes_a_timeout(monkeypatch, printed):
    """The regression this refactor exists for: no unbounded child."""
    seen: list[float] = []

    def fake(cmd, **kwargs):
        seen.append(kwargs["timeout"])
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(input_loop, "run_captured", fake)
    input_loop._copy_last_response(_Cli())
    assert len(seen) == len(input_loop._CLIPBOARD_COMMANDS)
    assert all(t and t > 0 for t in seen)


def test_missing_binary_falls_through_to_next(monkeypatch, printed):
    def behavior(cmd):
        if cmd[0] in ("pbcopy", "xclip"):
            raise FileNotFoundError(cmd[0])
        return _ok()

    called = _stub(monkeypatch, behavior)
    input_loop._copy_last_response(_Cli())
    assert [c[0] for c in called] == ["pbcopy", "xclip", "xsel"]
    assert "Copied to clipboard" in printed[-1]


def test_all_missing_reports_no_utility(monkeypatch, printed):
    _stub(monkeypatch, lambda cmd: (_ for _ in ()).throw(FileNotFoundError(cmd[0])))
    input_loop._copy_last_response(_Cli())
    assert "No clipboard utility found" in printed[-1]


def test_timeout_does_not_abort_the_chain(monkeypatch, printed):
    """A hung utility must not swallow the remaining candidates."""

    def behavior(cmd):
        if cmd[0] == "pbcopy":
            raise subprocess.TimeoutExpired(cmd, 5.0)
        return _ok()

    called = _stub(monkeypatch, behavior)
    input_loop._copy_last_response(_Cli())
    assert [c[0] for c in called] == ["pbcopy", "xclip"]
    assert "Copied to clipboard" in printed[-1]


def test_nonzero_exit_falls_through_then_reports_last_error(monkeypatch, printed):
    """Old code stopped at a failing pbcopy; now it keeps trying."""
    called = _stub(monkeypatch, lambda cmd: _ok(returncode=1, stderr=f"{cmd[0]} boom"))
    input_loop._copy_last_response(_Cli())
    assert [c[0] for c in called] == ["pbcopy", "xclip", "xsel"]
    assert "Copy failed" in printed[-1]
    assert "xsel boom" in printed[-1]


def test_all_timing_out_reports_timeout_not_missing_utility(monkeypatch, printed):
    _stub(
        monkeypatch,
        lambda cmd: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, 5.0)),
    )
    input_loop._copy_last_response(_Cli())
    assert "Copy failed" in printed[-1]
    assert "timed out" in printed[-1]


def test_payload_is_sent_as_text_not_bytes(monkeypatch, printed):
    """``run_captured`` is text-mode; passing bytes would raise."""
    seen = {}

    def fake(cmd, **kwargs):
        seen["input"] = kwargs.get("input")
        return _ok()

    monkeypatch.setattr(input_loop, "run_captured", fake)
    input_loop._copy_last_response(_Cli(last_response="ünïcode ✓"))
    assert seen["input"] == "ünïcode ✓"
    assert isinstance(seen["input"], str)
