"""The CLI's tool lines show the model's arguments and file content as typed.

``DisplayController`` builds every tool line as Rich markup. The header
carried the model's arguments and a sub-agent's ``[name n/max]`` label
unescaped, and the replace diff carried file content the same way, so Rich
read ``[r 1/15]`` or ``x[i]`` as tags and dropped them, and a stray closing
tag raised ``MarkupError``, which ``on_event`` swallowed with the whole line.
"""

from __future__ import annotations

from rich.console import Console

from agentao.cli.display import DisplayController
from agentao.transport import AgentEvent, EventType


def _render(*events: AgentEvent) -> str:
    console = Console(record=True, width=200, color_system=None)
    display = DisplayController(console, lambda: None)
    for event in events:
        display.on_event(event)
    return console.export_text()


def _start(tool, args, call_id="c1"):
    return AgentEvent(EventType.TOOL_START, {"tool": tool, "args": args, "call_id": call_id})


def _complete(call_id="c1", status="ok", duration_ms=3000, error=None):
    return AgentEvent(EventType.TOOL_COMPLETE, {
        "call_id": call_id, "status": status, "duration_ms": duration_ms, "error": error,
    })


def test_a_sub_agent_tool_keeps_its_label():
    out = _render(_start("[generalist 2/15] read_file", {"file_path": "a.py"}))
    assert "[generalist 2/15] read_file" in out


def test_markup_in_an_argument_is_shown_as_typed():
    out = _render(_start("run_shell_command", {"command": "echo [red]hi[/red] [/b]"}))
    assert "echo [red]hi[/red] [/b]" in out


def test_the_completion_line_keeps_the_header_and_the_error():
    out = _render(
        _start("run_shell_command", {"command": "ls [x]"}),
        _complete(status="error", error="no such file: [x] [/dim]"),
    )
    (footer,) = [line for line in out.splitlines() if line.startswith("✗")]
    assert "ls [x]" in footer
    assert "no such file: [x] [/dim]" in footer


def test_a_cancelled_tool_keeps_its_reason():
    out = _render(
        _start("read_file", {"file_path": "a.py"}),
        _complete(status="cancelled", error="stopped [/b]"),
    )
    assert "stopped [/b]" in out


def test_a_parallel_tool_keeps_its_label():
    out = _render(
        _start("[r 1/15] read_file", {"file_path": "a.py"}, "c1"),
        _start("[r 1/15] read_file", {"file_path": "b.py"}, "c2"),
    )
    assert "+ ⚙ [r 1/15] read_file" in out


def test_the_replace_diff_shows_code_with_brackets():
    out = _render(
        _start("replace", {
            "file_path": "m.py",
            "old_text": "return x\n",
            "new_text": "return x[i] + y[/b]\n",
        }),
        _complete(),
    )
    assert "+return x[i] + y[/b]" in out
    assert "-return x" in out
