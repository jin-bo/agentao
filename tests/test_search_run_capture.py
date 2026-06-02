"""Regression coverage for ``search._run_capture`` — the hardened subprocess
runner behind ``search_file_content``.

Every other search test stubs ``_run_capture`` out to assert argv shape, so
the hardening it exists for (stdin detached, own process group, kill-the-tree
on timeout) had no coverage. These drive the *real* helper end to end. They
spawn ``sys.executable`` so they run identically on POSIX and Windows.
"""

import subprocess
import sys
import time

import pytest

from agentao.tools.search import _run_capture


def test_run_capture_returns_completed_process(tmp_path):
    """Happy path: captures text stdout and the real exit code."""
    result = _run_capture(
        [sys.executable, "-c", "print('hello')"],
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_run_capture_stdin_is_devnull(tmp_path):
    """Children get an immediate-EOF stdin, never the host's stdin stream."""
    result = _run_capture(
        [sys.executable, "-c", "import sys; sys.stdout.write(str(len(sys.stdin.read())))"],
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "0"


def test_run_capture_decodes_non_utf8_without_raising(tmp_path):
    """A non-UTF-8 byte on stdout must not raise UnicodeDecodeError.

    ``errors='replace'`` keeps the helper from aborting the whole search
    (callers only catch SubprocessError/FileNotFoundError) when a match line
    isn't valid UTF-8.
    """
    result = _run_capture(
        [sys.executable, "-c", r"import sys; sys.stdout.buffer.write(b'\xff\xfe')"],
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result.returncode == 0  # decoded with replacement, no crash


def test_run_capture_timeout_raises_and_does_not_hang_on_grandchild(tmp_path):
    """The core fix: a child that spawns a grandchild holding the pipe open
    must still time out promptly.

    Plain ``subprocess.run(timeout=)`` signals only the direct child, so the
    grandchild keeps the inherited pipe's write end open and ``communicate``
    blocks far past the timeout. ``_run_capture`` reaps the whole tree, so the
    call returns near the timeout, not near the grandchild's sleep.
    """
    # Child spawns a 30s-sleeping grandchild, then exits immediately.
    child = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "print('started', flush=True)"
    )
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run_capture([sys.executable, "-c", child], cwd=str(tmp_path), timeout=2)
    elapsed = time.monotonic() - start
    # Generous bound: well under the grandchild's 30s sleep. A regression that
    # drops the kill-tree teardown would blow past this (hang ~= 30s).
    assert elapsed < 15, f"timeout path hung for {elapsed:.1f}s — tree not reaped"
