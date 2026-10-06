"""The foreground shell's inactivity clock is reset by each line of output.

``LocalShellExecutor.run`` read each pipe with ``read(4096)`` on a buffered
stream, which blocks until it has 4096 bytes or EOF. A command printing a short
line every half second therefore never reset the clock and was killed as idle
while it was talking, and ``on_chunk`` saw nothing until the kill.
"""

from __future__ import annotations

import os
import sys
import time
from types import MappingProxyType

from agentao.capabilities.shell import LocalShellExecutor, ShellRequest
from agentao.capabilities.shell_spec import AbsPath, LegacyLaunch


def _python_launch(tmp_path, body: str) -> LegacyLaunch:
    script = tmp_path / "talk.py"
    script.write_text(body, encoding="utf-8")
    return LegacyLaunch(
        command=f'"{sys.executable}" "{script}"',
        cwd=AbsPath(str(tmp_path)),
        env=MappingProxyType(dict(os.environ)),
    )


def test_a_command_printing_lines_is_not_killed_as_idle(tmp_path):
    launch = _python_launch(
        tmp_path,
        "import time\n"
        "for i in range(6):\n"
        "    print(i, flush=True)\n"
        "    time.sleep(0.4)\n",
    )
    result = LocalShellExecutor().run(ShellRequest(launch=launch, timeout=1.5))
    assert not result.timed_out, result
    assert result.stdout.decode().split() == [str(i) for i in range(6)]


def test_on_chunk_sees_a_line_before_the_command_ends(tmp_path):
    launch = _python_launch(
        tmp_path,
        "import time\n"
        "print('first', flush=True)\n"
        "time.sleep(1.5)\n"
        "print('last', flush=True)\n",
    )
    started = time.monotonic()
    first_seen = []

    def on_chunk(text: str) -> None:
        if not first_seen:
            first_seen.append(time.monotonic() - started)

    result = LocalShellExecutor().run(
        ShellRequest(launch=launch, timeout=30, on_chunk=on_chunk)
    )
    assert not result.timed_out
    assert first_seen and first_seen[0] < 1.2, first_seen


def test_a_multibyte_character_split_across_reads_is_not_garbled(tmp_path):
    # The first write ends inside the three-byte "中"; a per-chunk decode would
    # turn both halves into U+FFFD.
    launch = _python_launch(
        tmp_path,
        "import sys, time\n"
        "data = '中文'.encode('utf-8')\n"
        "sys.stdout.buffer.write(data[:1]); sys.stdout.buffer.flush()\n"
        "time.sleep(0.3)\n"
        "sys.stdout.buffer.write(data[1:]); sys.stdout.buffer.flush()\n",
    )
    seen: list = []
    LocalShellExecutor().run(
        ShellRequest(launch=launch, timeout=30, on_chunk=seen.append)
    )
    assert "".join(seen) == "中文"
