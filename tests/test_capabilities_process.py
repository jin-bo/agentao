"""Coverage for ``capabilities.process.run_captured`` — the hardened
subprocess runner shared by ``search_file_content`` and plugin hook
dispatch.

The callers stub ``run_captured`` out in their own tests (argv-shape
assertions), so the hardening it exists for — stdin handling, own process
group, kill-the-tree-on-timeout — is exercised here against real
processes. Tests spawn ``sys.executable`` so they run identically on POSIX
and Windows.
"""

import subprocess
import sys
import time

import pytest

from agentao.capabilities.process import run_captured


def test_returns_completed_process(tmp_path):
    """Happy path: captures text stdout and the real exit code."""
    result = run_captured(
        [sys.executable, "-c", "print('hello')"],
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_stdin_detached_when_no_input(tmp_path):
    """Without ``input`` the child gets immediate-EOF stdin, never the
    host's stdin stream."""
    result = run_captured(
        [sys.executable, "-c", "import sys; sys.stdout.write(str(len(sys.stdin.read())))"],
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "0"


def test_input_is_fed_on_stdin(tmp_path):
    """``input`` is delivered over a pipe (the hook-dispatch payload path)."""
    payload = '{"k": "v"}'
    result = run_captured(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        cwd=str(tmp_path),
        timeout=10,
        input=payload,
    )
    assert result.returncode == 0
    assert result.stdout == payload


def test_shell_true(tmp_path):
    """``shell=True`` runs a command string (the hook-dispatch invocation)."""
    result = run_captured(
        "exit 3",
        cwd=str(tmp_path),
        timeout=10,
        shell=True,
    )
    assert result.returncode == 3


def test_non_utf8_output_does_not_raise(tmp_path):
    """A non-UTF-8 byte on stdout decodes with replacement, never raising
    UnicodeDecodeError past the caller's except clauses."""
    result = run_captured(
        [sys.executable, "-c", r"import sys; sys.stdout.buffer.write(b'\xff\xfe')"],
        cwd=str(tmp_path),
        timeout=10,
    )
    assert result.returncode == 0  # decoded with replacement, no crash


def test_missing_binary_raises_filenotfound(tmp_path):
    """Spawn failure surfaces as FileNotFoundError (an OSError), as it would
    under subprocess.run — callers rely on catching it."""
    with pytest.raises(FileNotFoundError):
        run_captured(
            ["this-binary-does-not-exist-zzz"],
            cwd=str(tmp_path),
            timeout=10,
        )


def test_timeout_raises_and_reaps_grandchild_without_hanging(tmp_path):
    """The core fix: a child that spawns a grandchild holding the pipe open
    must still time out promptly.

    Plain ``subprocess.run(timeout=)`` signals only the direct child, so the
    grandchild keeps the inherited pipe's write end open and ``communicate``
    blocks far past the timeout. ``run_captured`` reaps the whole tree, so
    the call returns near the timeout, not near the grandchild's sleep.
    """
    child = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "print('started', flush=True)"
    )
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_captured([sys.executable, "-c", child], cwd=str(tmp_path), timeout=2)
    elapsed = time.monotonic() - start
    # Generous bound, well under the grandchild's 30s sleep. A regression
    # that drops the kill-tree teardown would blow past this (hang ~= 30s).
    assert elapsed < 15, f"timeout path hung for {elapsed:.1f}s — tree not reaped"
