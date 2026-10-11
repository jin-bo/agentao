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
    assert "[generalist 2/15] → read a.py" in out


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
    assert "+ [r 1/15] → read b.py" in out


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


def test_control_bytes_in_an_argument_do_not_reach_the_terminal():
    out = _render(_start("run_shell_command", {"command": "echo hi\x1b[2K\x1b]0;x\x07"}))
    assert "\x1b" not in out and "\x07" not in out
    assert "echo hi" in out


def test_a_trailing_run_of_backslashes_keeps_the_closing_tag():
    out = _render(
        _start("run_shell_command", {"command": "echo a\\\\\\"}),
        _complete(status="error", error="bad \\\\\\"),
    )
    (footer,) = [line for line in out.splitlines() if line.startswith("✗")]
    assert "[/dim" not in footer
    assert "echo a\\\\\\" in footer
    assert "bad \\\\\\" in footer


# ── a sub-agent's tool row is the same tool as the parent's ────────────────


def _output(chunk, call_id="c1"):
    return AgentEvent(EventType.TOOL_OUTPUT, {"chunk": chunk, "call_id": call_id})


def test_a_sub_agent_replace_shows_its_diff():
    out = _render(
        _start("[r 1/15] replace", {
            "file_path": "m.py", "old_text": "a = 1\n", "new_text": "a = 2\n",
        }),
        _complete(),
    )
    assert "[r 1/15] ← edit m.py" in out
    assert "-a = 1" in out and "+a = 2" in out
    assert "+1 −1" in out


def test_a_sub_agent_shell_command_shows_its_output():
    out = _render(
        _start("[r 1/15] run_shell_command", {"command": "ls"}),
        _output("file-one\n"),
        _complete(),
    )
    assert "[r 1/15] $ ls" in out
    assert "file-one" in out


def test_only_the_forwarders_label_is_split_off():
    """A name that merely starts with brackets is not a sub-agent row."""
    from agentao.cli.display import _split_subagent_label

    assert _split_subagent_label("[r 1/15] read_file") == ("[r 1/15]", "read_file")
    assert _split_subagent_label("[code-reviewer 12/100] mcp_gh_search") == (
        "[code-reviewer 12/100]", "mcp_gh_search",
    )
    for name in ("read_file", "[r] read_file", "[r 1/15] two words", "[r 1/15]read_file"):
        assert _split_subagent_label(name) == ("", name)


def test_the_display_splits_the_label_the_forwarder_writes():
    """The two halves of the label format, joined: the real forwarder's
    ``TOOL_START`` into the real display."""
    from pathlib import Path

    from rich.console import Console

    from agentao.agents.tools import AgentToolWrapper

    console = Console(record=True, width=200, color_system=None)
    display = DisplayController(console, lambda: None)
    wrapper = AgentToolWrapper(
        definition={"name": "code-reviewer", "description": "d"},
        all_tools={}, llm_config_getter=lambda: {}, working_directory=Path("."),
        event_sink=display.on_event,
    )
    child = wrapper._child_transport(False, 15)
    child.emit(AgentEvent(EventType.TURN_START))
    child.emit(AgentEvent(EventType.TOOL_START, {
        "tool": "read_file", "args": {"file_path": "a.py"}, "call_id": "c1",
    }))
    assert "[code-reviewer 1/15] → read a.py" in console.export_text()
