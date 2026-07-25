"""``/copy`` clipboard fallback chain.

The pre-refactor implementation called ``subprocess.run`` three times with
no ``timeout=``, so a wedged ``pbcopy`` blocked the input loop forever.

Two layers of test, deliberately:

- **Branch logic** — stubs ``_run_clipboard_command`` to drive the
  fall-through / error-reporting paths cheaply.
- **Real process semantics** — spawns actual child processes through the
  production code path. The first cut of this file stubbed *everything*,
  which is precisely why it shipped green with a regression in the part
  the refactor actually changed: routing through ``run_captured`` piped
  stdout/stderr, and ``communicate()`` then waits for every descendant to
  close the write ends — so a helper like ``xclip``, which forks a
  background process to own the X selection, could never reach EOF and
  burned the whole timeout. A stub cannot see that; a forking child can.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

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
    """Install a ``_run_clipboard_command`` stub driven by ``behavior(cmd)``."""
    recorded: list[tuple[str, ...]] = []

    def fake(cmd, payload, timeout):
        recorded.append(cmd)
        assert timeout, "every clipboard attempt must be bounded"
        return behavior(cmd)

    monkeypatch.setattr(input_loop, "_run_clipboard_command", fake)
    return recorded


def _ok(returncode=0, stderr=""):
    return (returncode, stderr)


# ── Branch logic ────────────────────────────────────────────────────


def test_no_response_yet_does_not_shell_out(monkeypatch, printed):
    called = _stub(monkeypatch, lambda cmd: _ok())
    input_loop._copy_last_response(_Cli(last_response=None))
    assert called == []
    assert "No response to copy yet" in printed[0]


def test_first_utility_wins_and_is_bounded(monkeypatch, printed):
    called = _stub(monkeypatch, lambda cmd: _ok())
    input_loop._copy_last_response(_Cli())
    assert called == [("pbcopy",)]
    assert "Copied to clipboard" in printed[0]


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
    def behavior(cmd):
        if cmd[0] == "pbcopy":
            raise subprocess.TimeoutExpired(cmd, 5.0)
        return _ok()

    called = _stub(monkeypatch, behavior)
    input_loop._copy_last_response(_Cli())
    assert [c[0] for c in called] == ["pbcopy", "xclip"]
    assert "Copied to clipboard" in printed[-1]


def test_nonzero_exit_falls_through_then_reports_last_error(monkeypatch, printed):
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


def test_child_stderr_is_markup_escaped(monkeypatch, printed):
    """Raw stderr reaches a Rich markup string; a bracketed path in it
    would otherwise parse as a closing tag and raise MarkupError out of
    the input loop, losing the real diagnostic."""
    import io

    from rich.console import Console

    _stub(monkeypatch, lambda cmd: _ok(returncode=1, stderr="xclip: [/dev/null] bad"))
    input_loop._copy_last_response(_Cli())
    rendered = printed[-1]
    # The real console must be able to parse what we produced.
    Console(file=io.StringIO(), force_terminal=False).print(rendered)
    assert "/dev/null" in rendered


# ── Real process semantics ──────────────────────────────────────────

_FORK_AND_LINGER = textwrap.dedent(
    """
    import os, sys, time
    sys.stdin.buffer.read()          # consume the payload like a real tool
    if os.fork() != 0:
        sys.exit(0)                  # parent exits immediately, like xclip
    time.sleep(30)                   # child lives on holding inherited fds
    """
)

_ECHO_STDIN = textwrap.dedent(
    """
    import sys, pathlib
    pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="fork() is POSIX-only")
def test_forking_helper_does_not_burn_the_timeout(tmp_path):
    """The regression this rewrite exists for.

    A clipboard helper that forks a lingering background process (xclip's
    selection owner) must not keep ``/copy`` waiting. Under the previous
    ``run_captured`` routing this blocked for the full 5s and then killed
    the very process that owned the selection.
    """
    script = tmp_path / "forker.py"
    script.write_text(_FORK_AND_LINGER)

    t0 = time.monotonic()
    returncode, _ = input_loop._run_clipboard_command(
        (sys.executable, str(script)), "payload", 5.0
    )
    elapsed = time.monotonic() - t0

    assert returncode == 0
    assert elapsed < 2.0, f"forking helper blocked for {elapsed:.2f}s"


@pytest.mark.skipif(sys.platform == "win32", reason="fork() is POSIX-only")
def test_payload_reaches_the_child_as_utf8(tmp_path):
    """Encoding is pinned to UTF-8, not the process locale.

    ``run_captured`` is text-mode and encodes with
    ``locale.getpreferredencoding()`` and ``errors="replace"`` — under a
    non-UTF-8 locale that silently turned emoji into ``?`` while still
    reporting success.
    """
    script = tmp_path / "echo.py"
    script.write_text(_ECHO_STDIN)
    sink = tmp_path / "out.bin"

    text = "ünïcode ✓ 中文 🎉"
    returncode, _ = input_loop._run_clipboard_command(
        (sys.executable, str(script), str(sink)), text, 5.0
    )

    assert returncode == 0
    assert sink.read_bytes() == text.encode("utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_genuinely_hung_child_hits_the_bound_and_is_killed(tmp_path):
    script = tmp_path / "hang.py"
    script.write_text("import time\ntime.sleep(30)\n")

    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        input_loop._run_clipboard_command((sys.executable, str(script)), "payload", 0.5)
    assert time.monotonic() - t0 < 3.0


def test_missing_binary_raises_filenotfound():
    with pytest.raises(FileNotFoundError):
        input_loop._run_clipboard_command(
            ("definitely-not-a-real-binary-xyz",), "payload", 1.0
        )


def test_stderr_is_captured_for_the_error_message(tmp_path):
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys\nsys.stdin.buffer.read()\n"
        "sys.stderr.write('nope\\n')\nsys.exit(3)\n"
    )
    returncode, stderr_text = input_loop._run_clipboard_command(
        (sys.executable, str(script)), "payload", 5.0
    )
    assert returncode == 3
    assert "nope" in stderr_text


def test_large_payload_does_not_deadlock_on_a_non_reading_child(tmp_path):
    """A child that never reads stdin must not block us before wait().

    A payload larger than the pipe buffer would otherwise hang the write
    and the timeout could never fire.
    """
    script = tmp_path / "ignore_stdin.py"
    script.write_text("import sys\nsys.exit(0)\n")

    t0 = time.monotonic()
    returncode, _ = input_loop._run_clipboard_command(
        (sys.executable, str(script)), "x" * (4 * 1024 * 1024), 5.0
    )
    assert returncode == 0
    assert time.monotonic() - t0 < 3.0
