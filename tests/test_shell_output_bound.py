"""The local shell executor holds a bounded head and tail of each stream, not all of it.

The tool shows the model 40,000 characters of a command's output, but the executor
used to keep every byte until the child exited: a command printing 400 MB peaked at
about 1 GB RSS. Now each stream keeps its first and last ``_MAX_RETAINED_BYTES / 2``,
counts what it dropped between them and says where, and the tool shows the model the
head and the tail with the gap marked.
"""

from __future__ import annotations

import os
import sys
import tracemalloc
from types import MappingProxyType

import pytest

from agentao.capabilities import shell as shell_mod
from agentao.capabilities.powershell import CLIXML_MARKER
from agentao.capabilities.shell import (
    LocalShellExecutor,
    ShellRequest,
    ShellResult,
    _HeadTailBuffer,
)
from agentao.capabilities.shell_spec import AbsPath, LegacyLaunch
from agentao.runtime.tool_result_formatter import TOOL_OUTPUT_SAVE_THRESHOLD
from agentao.tools.shell import _MAX_OUTPUT_CHARS, ShellTool


def _run(tmp_path, code: str) -> ShellResult:
    command = f'"{sys.executable}" -c "{code}"'
    return LocalShellExecutor().run(
        ShellRequest(
            launch=LegacyLaunch(
                command=command,
                cwd=AbsPath(str(tmp_path)),
                env=MappingProxyType(dict(os.environ)),
            ),
            timeout=60,
        )
    )


def _feed(buf: _HeadTailBuffer, data: bytes, step: int = 300) -> None:
    for i in range(0, len(data), step):
        buf.append(data[i:i + step])


# ------------------------------------------------------------------ the buffer


def test_output_under_the_limit_is_kept_whole():
    buf = _HeadTailBuffer(100)
    _feed(buf, b"a" * 40 + b"b" * 40 + b"c" * 20, step=40)
    assert buf.snapshot() == (b"a" * 40 + b"b" * 40 + b"c" * 20, 0, 0)


def test_output_over_the_limit_keeps_the_head_and_the_tail():
    data = bytes(range(32, 127)) * 110  # 10,450 ASCII bytes, so no edge moves for UTF-8
    buf = _HeadTailBuffer(1000)
    _feed(buf, data)
    kept, omitted, at = buf.snapshot()
    assert at == 500
    assert kept[:at] == data[:500]
    assert kept[at:] == data[-500:]
    assert omitted + len(kept) == len(data)


def test_both_edges_of_the_gap_fall_on_utf8_character_boundaries():
    data = ("x" + "é" * 50 + "中" * 50 + "😀" * 50).encode("utf-8")
    for limit in range(8, 60):
        buf = _HeadTailBuffer(limit)
        _feed(buf, data, step=7)
        kept, omitted, at = buf.snapshot()
        kept[:at].decode("utf-8")  # strict: raises on a severed character
        kept[at:].decode("utf-8")
        assert omitted + len(kept) == len(data)


def test_snapshot_has_no_side_effects():
    buf = _HeadTailBuffer(10)
    buf.append(b"x" * 25)
    assert buf.snapshot() == buf.snapshot() == (b"x" * 10, 15, 5)


# ---------------------------------------------------------------- the executor


def test_the_executor_returns_head_and_tail_and_says_where_the_gap_is(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_mod, "_MAX_RETAINED_BYTES", 64 * 1024)
    total = 2 * 1024 * 1024
    result = _run(
        tmp_path,
        f"import sys; sys.stdout.write('START' + 'a' * {total - 8} + 'END'); sys.stdout.flush()",
    )
    assert result.returncode == 0
    assert len(result.stdout) == 64 * 1024
    assert result.stdout.startswith(b"START")
    assert result.stdout.endswith(b"END")
    assert result.stdout_omitted_at == 32 * 1024
    assert result.stdout_omitted_bytes + len(result.stdout) == total
    assert result.stderr_omitted_bytes == 0


def test_the_executor_does_not_hold_the_whole_stream(tmp_path):
    """The point of the change: memory, not just what is returned — at the real limit.

    With every byte kept the peak traced allocation grew past the output (16 MB here);
    at 8 MiB a stream it would still pass 16 MB for two streams' worth of joins.
    """
    total = 16 * 1024 * 1024
    tracemalloc.start()
    try:
        result = _run(
            tmp_path,
            f"import sys; sys.stdout.write('a' * {total}); sys.stdout.flush()",
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(result.stdout) == shell_mod._MAX_RETAINED_BYTES
    assert result.stdout_omitted_bytes + len(result.stdout) == total
    assert peak < 6 * 1024 * 1024, f"peak traced allocation {peak:,} bytes"


# -------------------------------------------------------------------- the tool


def test_the_gap_is_marked_where_it_is():
    out = ShellTool()._format_result(
        0, b"HEAD LINE\nTAIL LINE\n", b"", stdout_omitted=12_345, stdout_omitted_at=10,
    )
    assert out.index("HEAD LINE") < out.index("12,345 bytes of output not kept") < out.index("TAIL LINE")


def test_a_tail_only_host_result_is_marked_at_the_front():
    out = ShellTool()._format_result(0, b"tail line\n", b"", stdout_omitted=12_345)
    assert out.startswith("STDOUT:\n[... 12,345 bytes of output not kept ...]\ntail line")


def test_a_long_stream_shows_its_head_and_its_tail():
    out = ShellTool()._format_result(0, b"BEGIN" + b"h" * 100_000 + b"t" * 100_000 + b"END", b"")
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert out.startswith("STDOUT:\nBEGIN")
    assert out.endswith("END")
    assert "chars omitted" in out
    # A fifth from the start, the rest from the end.
    assert 7_000 < out.count("h") < 9_000
    assert 30_000 < out.count("t") < 33_000


def test_a_gap_and_a_cut_are_both_reported():
    out = ShellTool()._format_result(
        0, b"h" * 100_000 + b"t" * 100_000, b"",
        stdout_omitted=5_000_000, stdout_omitted_at=100_000,
    )
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert "5,000,000 bytes of output not kept, and " in out
    assert "more chars omitted" in out


def test_a_short_tail_gives_its_share_back_to_the_head():
    out = ShellTool()._format_result(
        0, b"h" * 100_000 + b"tail", b"", stdout_omitted=1, stdout_omitted_at=100_000,
    )
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert out.endswith("tail")
    assert out.count("h") > 39_000


def test_a_binary_stream_counts_the_dropped_bytes_once():
    out = ShellTool()._format_result(0, b"\x00" * 100, b"", stdout_omitted=900, stdout_omitted_at=50)
    assert "1,000 bytes not shown" in out
    assert "not kept" not in out


def test_a_clixml_stream_with_a_gap_is_shown_raw_not_stitched():
    """Head + tail read as one stream would join the elements on either side of the hole.

    The gap's position comes with the result, so the envelope is not unwrapped across it:
    it is shown raw, with the gap marked between the two halves.
    """
    envelope = (
        CLIXML_MARKER + "\r\n<Objs>"
        + "<S S=\"Error\">first message</S>"
        + "<S S=\"Error\">filler</S>" * 200
        + "<S S=\"Error\">last message</S></Objs>"
    ).encode("utf-8")
    buf = _HeadTailBuffer(len(envelope) // 2)
    buf.append(envelope)
    kept, omitted, at = buf.snapshot()

    out = ShellTool()._format_result(
        1, b"", kept, powershell=True, stderr_omitted=omitted, stderr_omitted_at=at,
    )
    assert CLIXML_MARKER in out  # raw, not unwrapped
    gap = out.index(f"{omitted:,} bytes of output not kept")
    assert out.index("first message") < gap < out.index("last message")


def test_a_clixml_stream_kept_whole_is_still_unwrapped():
    envelope = (CLIXML_MARKER + "\r\n<Objs><S S=\"Error\">the error</S></Objs>").encode()
    out = ShellTool()._format_result(1, b"", envelope, powershell=True)
    assert "the error" in out
    assert CLIXML_MARKER not in out


@pytest.mark.parametrize(
    "count, at", [(None, 0), ("12", 0), (-1, 0), (1.5, 0), (True, 0), (5, -1), (5, 10**9), (5, "3")],
)
def test_a_host_result_with_unusable_fields_reads_safely(tmp_path, count, at):
    class HostResult:
        returncode = 0
        stdout = b"from the host\n"
        stderr = b""
        timed_out = False
        stdout_omitted_bytes = count
        stdout_omitted_at = at

    class HostShell:
        def run(self, request):
            return HostResult()

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = HostShell()
    out = tool._run_foreground("echo", tmp_path, 5)
    assert "from the host" in out
    if type(count) is int and count > 0:
        # A usable count with an unusable offset reads as a dropped front.
        assert out.index("5 bytes of output not kept") < out.index("from the host")
    else:
        assert "not kept" not in out


# ------------------------------------------- the returned string, not the streams
#
# The tool cut each stream to 40,000 characters and then added headers, a status line
# and notices on top. At 40,066 the result layer (whose threshold is also 40,000) saved
# that already-cut tail to disk and told the model it was the command's "Full output".


def test_the_shell_cap_is_within_the_result_layers_save_threshold():
    assert _MAX_OUTPUT_CHARS <= TOOL_OUTPUT_SAVE_THRESHOLD


def test_output_at_the_cap_plus_headers_stays_within_it():
    out = ShellTool()._format_result(0, b"a" * _MAX_OUTPUT_CHARS, b"")
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert "chars omitted" in out


def test_stdout_only_stays_within_the_cap():
    out = ShellTool()._format_result(1, b"BEGIN" + b"a" * 200_000 + b"END", b"")
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert out.startswith("STDOUT:\nBEGIN")
    assert "END" in out
    assert out.endswith("Exit code: 1")


def test_both_streams_and_a_status_stay_within_the_cap():
    out = ShellTool()._format_result(-9, b"o" * 300_000, b"e" * 100_000)
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert "STDOUT:\n" in out and "STDERR:\n" in out
    assert out.endswith("Signal: 9")


def test_a_short_error_beside_a_huge_stdout_is_kept_whole():
    error = "fatal: the actual reason the command failed, in full"
    out = ShellTool()._format_result(2, b"o" * 1_000_000, error.encode())
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert f"STDERR:\n{error}" in out


def test_gaps_are_still_reported_at_the_cap():
    out = ShellTool()._format_result(
        1, b"o" * 300_000, b"e" * 300_000,
        stdout_omitted=9_000_000, stderr_omitted=7_000_000,
        stdout_omitted_at=150_000, stderr_omitted_at=150_000,
    )
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert "9,000,000 bytes of output not kept" in out
    assert "7,000,000 bytes of output not kept" in out


@pytest.mark.parametrize("stdout_len", [0, 1, 39_990, 40_000, 40_050, 123_457])
@pytest.mark.parametrize("stderr_len", [0, 7, 20_000, 40_000, 80_001])
@pytest.mark.parametrize("returncode", [0, 1, -15])
@pytest.mark.parametrize("gap_at", [None, 0, "middle"])
def test_no_combination_exceeds_the_cap(stdout_len, stderr_len, returncode, gap_at):
    kwargs = {}
    if gap_at is not None and stdout_len:
        kwargs = {
            "stdout_omitted": 12_345,
            "stdout_omitted_at": stdout_len // 2 if gap_at == "middle" else 0,
        }
    out = ShellTool()._format_result(returncode, b"o" * stdout_len, b"e" * stderr_len, **kwargs)
    assert len(out) <= _MAX_OUTPUT_CHARS


class _Fixed:
    """An executor that answers every run with one result."""

    def __init__(self, result):
        self._result = result

    def run(self, request):
        return self._result

    def run_background(self, request):
        from agentao.capabilities.shell import BackgroundHandle

        return BackgroundHandle(pid=4242, pgid=4242)


def _tool_with(result, tmp_path) -> ShellTool:
    tool = ShellTool()
    tool.shell = _Fixed(result)
    tool.working_directory = str(tmp_path)
    return tool


def _timed_out(**kwargs) -> ShellResult:
    return ShellResult(returncode=-1, timed_out=True, **kwargs)


def test_the_timeout_path_stays_within_the_cap(tmp_path):
    result = _timed_out(
        stdout=b"FIRST" + b"p" * 200_000 + b"LAST",
        stdout_omitted_bytes=5_000_000, stdout_omitted_at=100_000,
    )
    out = _tool_with(result, tmp_path)._run_foreground("sleep 999", tmp_path, 1)
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert "timed out" in out
    assert "5,000,000 bytes of output not kept" in out
    assert "FIRST" in out
    assert out.endswith("LAST")


def test_a_long_command_echo_is_cut_before_the_output(tmp_path):
    command = "cat <<'EOF' > big.txt\n" + "x" * 100_000 + "\nEOF"
    result = _timed_out(stdout=b"p" * 60_000 + b"LAST", stdout_omitted_bytes=3_000)
    out = _tool_with(result, tmp_path)._run_foreground(command, tmp_path, 1)
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert "more chars of the command not shown" in out
    assert "3,000 bytes of output not kept" in out
    assert out.endswith("LAST")
    # The output, not the echo, gets most of the budget.
    assert out.count("p") > 30_000


def test_a_timeout_marks_each_streams_gap_inside_that_stream(tmp_path):
    result = _timed_out(
        stdout=b"OUT-HEAD" + b"o" * 100_000,
        stderr=b"ERR-HEAD" + b"e" * 100_000,
        stdout_omitted_bytes=1_111_111, stdout_omitted_at=8,
        stderr_omitted_bytes=2_222_222, stderr_omitted_at=8,
    )
    out = _tool_with(result, tmp_path)._run_foreground("slow", tmp_path, 1)
    assert len(out) <= _MAX_OUTPUT_CHARS
    out_gap = out.index("1,111,111 bytes of output not kept")
    err_gap = out.index("2,222,222 bytes of output not kept")
    # Each gap sits between its own stream's head and tail, and stdout comes first.
    assert out.index("OUT-HEAD") < out_gap < out.index("oooo")
    assert out.index("oooo") < out.index("ERR-HEAD") < err_gap < out.index("eeee")


def test_a_timeout_with_only_stderr_dropped_does_not_mark_stdout(tmp_path):
    result = _timed_out(stdout=b"short stdout\n", stderr=b"e" * 1_000, stderr_omitted_bytes=3_333)
    out = _tool_with(result, tmp_path)._run_foreground("slow", tmp_path, 1)
    note = out.index("3,333 bytes of output not kept")
    assert out.index("short stdout") < note < out.index("eeee")


# ------------------------------------------- every path out of ``execute``
#
# The foreground formatting was capped, but ``execute`` appended a sandbox hint after
# it (40,295 measured), the background start message echoed the command whole, and a
# refusal reason carries its own length. Each of those reached the result layer's
# threshold the same way.


class _Profile:
    name = "workspace-write"

    def as_args(self):
        return ["sandbox-exec", "-f", "/nonexistent.sb"]


def test_the_sandbox_hint_fits_inside_the_cap(tmp_path):
    from agentao.tools.shell import _sandbox_hint

    denial = b"touch: /etc/x: Operation not permitted\n"
    tool = _tool_with(ShellResult(returncode=1, stdout=b"o" * 200_000, stderr=denial), tmp_path)
    out = tool.execute(command="find /", _sandbox_profile=_Profile())
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert out.endswith(_sandbox_hint(_Profile()))
    # Reserved up front, so the output was cut once, by the formatter, not again after.
    assert out.count("chars omitted") == 1


def test_the_sandbox_hint_fits_on_the_timeout_path(tmp_path):
    from agentao.tools.shell import _sandbox_hint

    result = _timed_out(stdout=b"o" * 200_000, stderr=b"Operation not permitted\n")
    out = _tool_with(result, tmp_path).execute(command="find /", _sandbox_profile=_Profile())
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert out.endswith(_sandbox_hint(_Profile()))


def test_the_last_check_keeps_the_hint_whole_and_cuts_the_body():
    from agentao.tools.shell import _annotate_sandbox_denial, _sandbox_hint

    body = "Operation not permitted\n" + "x" * 39_990
    out = _annotate_sandbox_denial(body, _Profile())
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert out.endswith(_sandbox_hint(_Profile()))
    assert out.startswith("Operation not permitted")
    assert "chars omitted" in out


def test_a_background_start_does_not_echo_a_huge_command(tmp_path):
    command = "cat <<'EOF' > big.txt\n" + "x" * 100_000 + "\nEOF"
    out = _tool_with(None, tmp_path).execute(command=command, is_background=True)
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert "Background process started." in out
    assert "more chars of the command not shown" in out
    assert "PID: 4242" in out


def test_a_long_refusal_reason_is_capped(tmp_path):
    from agentao.capabilities.shell_spec import LaunchRefused

    class Refusing:
        def run(self, request):
            deny = type("Deny", (), {"reason": "hardline: " + "r" * 60_000})()
            raise LaunchRefused(deny)

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = Refusing()
    tool.working_directory = str(tmp_path)
    out = tool.execute(command="echo hi")
    assert len(out) <= _MAX_OUTPUT_CHARS
    assert out.startswith("Error: hardline:")
    assert "chars omitted" in out


# ------------------------------------------------- an escape sequence cut by the gap


@pytest.mark.parametrize("severed", [b"\x1b", b"\x1b[", b"\x1b[3", b"\x1b[38;5;1"])
def test_an_escape_severed_at_the_gap_does_not_reach_the_model(severed):
    """``_strip_ansi`` removes whole sequences only; a cut can leave half of one.

    Left in, the ``[`` of the gap notice that follows completes it for a terminal.
    """
    head = b"before the cut " + severed
    out = ShellTool()._format_result(
        0, head + b"1mafter the cut\n", b"", stdout_omitted=4_096, stdout_omitted_at=len(head),
    )
    assert "\x1b" not in out
    assert out.index("before the cut") < out.index("4,096 bytes of output not kept")


def test_the_real_buffer_cutting_inside_an_escape_leaves_no_esc():
    data = b"a" * 7 + b"\x1b[31mRED\x1b[0m" + b"z" * 200
    buf = _HeadTailBuffer(20)
    buf.append(data)
    kept, omitted, at = buf.snapshot()
    assert kept[:at].endswith(b"\x1b[3")  # the case this guards: the cut is inside one
    out = ShellTool()._format_result(0, kept, b"", stdout_omitted=omitted, stdout_omitted_at=at)
    assert "\x1b" not in out


def test_an_early_error_before_anything_runs_is_capped(tmp_path):
    """``execute`` returns before running anything when a host's spec provider raises.

    That early return echoes the exception, so it is capped like every other path.
    """

    class RaisingSpec:
        @property
        def shell_spec(self):
            raise RuntimeError("provider broke: " + "x" * 60_000)

        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = RaisingSpec()
    tool.working_directory = str(tmp_path)
    out = tool.execute(command="echo hi")
    assert out.startswith("Error: shell spec provider raised: provider broke:")
    assert len(out) <= _MAX_OUTPUT_CHARS
