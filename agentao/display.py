"""DisplayController — semantic, low-noise tool execution display for the CLI.

Consumes structured AgentEvents and renders:
  - Semantic tool headers:  → read src/agent.py  /  $ pytest tests/  /  ← write cli.py
  - Buffered output with tail-biased truncation and fold indicator
  - Completion status:      ✓ read  0.03s   or   ✗ shell  1.2s  Permission denied
  - Sub-agent lifecycle:    ▶ [codebase-investigator]: task…  /  ◀ … 3 turns · 5 tools

Design rules (from spec):
  - All output in this file must not raise; exceptions are swallowed.
  - Does not participate in tool execution logic.
  - Concurrent tools are tracked by call_id; outputs never cross-contaminate.
"""

import re
import sys
import threading
from dataclasses import dataclass, field
from time import monotonic
from typing import Callable, Optional

from rich.console import Console

from .transport import AgentEvent, EventType

# ── Tool semantics table ──────────────────────────────────────────────────────
# (icon, verb, primary_arg_key, secondary_arg_key | None)
_SEMANTICS: dict[str, tuple[str, str, Optional[str], Optional[str]]] = {
    "read_file":              ("→", "read",     "path",        None),
    "list_directory":         ("→", "list",     "path",        None),
    "glob":                   ("✱", "glob",     "pattern",     None),
    "search_file_content":    ("✱", "search",   "query",       "path"),
    "write_file":             ("←", "write",    "path",        None),
    "replace":                ("←", "edit",     "path",        None),
    "run_shell_command":      ("$", "",         "command",     None),
    "web_fetch":              ("↗", "fetch",    "url",         None),
    "google_web_search":      ("↗", "search",   "query",       None),
    "save_memory":            ("◈", "remember", "key",         None),
    "search_memory":          ("◈", "recall",   "query",       None),
    "delete_memory":          ("◈", "forget",   "key",         None),
    "clear_all_memories":     ("◈", "clear memories", None,   None),
    "filter_memory_by_tag":   ("◈", "filter",   "tag",         None),
    "list_memories":          ("◈", "memories", None,          None),
    "activate_skill":         ("◉", "skill",    "name",        None),
    "ask_user":               ("?", "ask",      "question",    None),
    "todo_write":             ("✔", "todos",    None,          None),
    "check_background_agent": ("⟳", "agent status", "agent_id", None),
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mKHJABCDEF]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _shorten(val: str, max_len: int = 60) -> str:
    """Shorten a string; for paths keep head…tail; for others truncate tail."""
    if len(val) <= max_len:
        return val
    # Path heuristic: contains / or \
    if "/" in val or "\\" in val:
        half = (max_len - 3) // 2
        return val[:half] + "…" + val[-half:]
    return val[:max_len - 1] + "…"


def _fmt_arg(val: object) -> str:
    """Format an argument value for display in the header line."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    s = _shorten(s)
    # Quote strings that aren't paths/commands (contain spaces or look textual)
    if " " in s and "/" not in s and "\\" not in s:
        return f'"{s}"'
    return s


def _build_header(tool: str, args: dict) -> str:
    """Compose the one-line display header for a tool call.

    Returns a plain string (no Rich markup) of the form:
        → read src/agent.py
        $ pytest tests/ -q
        ✱ search "def chat"  agentao/agent.py
        ▶ codebase-investigator
        ⬡ filesystem.read_file
    """
    # Sub-agent tool family
    if tool.startswith("agent_"):
        agent_name = tool[len("agent_"):].replace("_", "-")
        task = _fmt_arg(args.get("task", args.get("description", "")))
        return f"▶ {agent_name}" + (f"  {task}" if task else "")

    # MCP tool family: mcp_server_tool → server.tool
    if tool.startswith("mcp_"):
        rest = tool[4:]
        # server name is everything up to the second underscore segment
        parts = rest.split("_", 1)
        if len(parts) == 2:
            label = f"{parts[0]}.{parts[1]}"
        else:
            label = rest
        primary_key = next(
            (k for k in ("path", "file_path", "query", "command", "url") if k in args), None
        )
        arg_str = _fmt_arg(args.get(primary_key)) if primary_key else ""
        return f"⬡ {label}" + (f"  {arg_str}" if arg_str else "")

    sem = _SEMANTICS.get(tool)
    if sem is None:
        # Unknown tool — neutral fallback
        first_val = next(iter(args.values()), None) if args else None
        arg_str = _fmt_arg(first_val) if first_val is not None else ""
        return f"⚙ {tool}" + (f"  {arg_str}" if arg_str else "")

    icon, verb, pk, sk = sem
    # Primary arg
    pval = args.get(pk, "") if pk else ""
    arg_str = _fmt_arg(pval)
    # Secondary arg (only if primary present and secondary key differs)
    if sk and sk != pk and arg_str:
        sval = args.get(sk, "")
        if sval:
            arg_str += "  " + _fmt_arg(sval)

    parts = [icon]
    if verb:
        parts.append(verb)
    if arg_str:
        parts.append(arg_str)
    return " ".join(parts)


def _fmt_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


# ── Output buffer ─────────────────────────────────────────────────────────────

_MAX_DISPLAY_LINES = 8    # tail lines shown inline
_MAX_DISPLAY_CHARS = 1200  # safety cap


@dataclass
class _OutputBuffer:
    """Accumulate streaming output chunks; produce a display-ready tail."""

    _lines: list[str] = field(default_factory=list)
    _current: str = field(default="")    # partial last line (no newline yet)
    _total_lines: int = field(default=0)  # total completed lines ever received

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        combined = self._current + chunk
        # Handle \r (carriage return without newline): overwrite current line
        # Split on \n first, then handle \r within each segment
        parts = combined.split("\n")
        for i, part in enumerate(parts[:-1]):
            # This segment ends with \n — it's a complete line
            # Handle \r within the segment (take only last \r-separated piece)
            line = part.split("\r")[-1]
            self._lines.append(line)
            self._total_lines += 1
        # Last part has no trailing \n yet — handle \r to get current line state
        self._current = parts[-1].split("\r")[-1]

    def flush(self) -> None:
        """Treat any pending partial line as a complete line."""
        if self._current:
            self._lines.append(self._current)
            self._total_lines += 1
            self._current = ""

    def render(self) -> tuple[str, int]:
        """Return (visible_text, hidden_line_count).

        Shows the last _MAX_DISPLAY_LINES lines (tail-biased, per spec §9).
        hidden_line_count > 0 means output was folded.
        """
        all_lines = list(self._lines)
        if self._current:
            all_lines.append(self._current)

        total = len(all_lines)
        if total == 0:
            return "", 0

        if total <= _MAX_DISPLAY_LINES:
            visible = all_lines
            hidden = 0
        else:
            visible = all_lines[-_MAX_DISPLAY_LINES:]
            hidden = total - _MAX_DISPLAY_LINES

        text = "\n".join(visible)
        # Safety cap on total characters
        if len(text) > _MAX_DISPLAY_CHARS:
            text = "…" + text[-((_MAX_DISPLAY_CHARS - 1)):]
        return text, hidden


# ── Per-call state ────────────────────────────────────────────────────────────

@dataclass
class _ToolState:
    tool: str
    call_id: str
    header: str                           # rendered header line (no Rich markup)
    buffer: _OutputBuffer = field(default_factory=_OutputBuffer)
    output_opened: bool = False            # True once we've opened the output section
    start_ts: float = field(default_factory=monotonic)


# ── DisplayController ─────────────────────────────────────────────────────────

class DisplayController:
    """Consume AgentEvents and render a semantic, low-noise tool display.

    Thread-safe: concurrent tool executions are isolated by call_id.
    """

    def __init__(
        self,
        console: Console,
        get_status: Callable[[], object],  # returns current rich.Status or None
    ) -> None:
        self._console = console
        self._get_status = get_status
        self._states: dict[str, _ToolState] = {}  # call_id → _ToolState
        self._lock = threading.Lock()

    # ── Public entry point ────────────────────────────────────────────────────

    def on_event(self, event: AgentEvent) -> None:
        try:
            t = event.type
            d = event.data
            if t == EventType.TOOL_START:
                self._on_tool_start(
                    d.get("tool", ""),
                    d.get("args", {}),
                    d.get("call_id") or d.get("tool", ""),
                )
            elif t == EventType.TOOL_OUTPUT:
                self._on_tool_output(
                    d.get("chunk", ""),
                    d.get("call_id") or d.get("tool", ""),
                )
            elif t == EventType.TOOL_COMPLETE:
                self._on_tool_complete(
                    d.get("call_id") or d.get("tool", ""),
                    d.get("status", "ok"),
                    d.get("duration_ms", 0),
                    d.get("error"),
                )
            elif t == EventType.AGENT_START:
                self._on_agent_start(d)
            elif t == EventType.AGENT_END:
                self._on_agent_end(d)
        except Exception:
            pass  # never let display errors crash the runtime

    # ── Spinner helpers ───────────────────────────────────────────────────────

    def _stop_spinner(self) -> None:
        st = self._get_status()
        if st is not None:
            try:
                st.stop()
            except Exception:
                pass

    def _start_spinner(self, text: Optional[str] = None) -> None:
        st = self._get_status()
        if st is not None:
            try:
                if text:
                    st.update(text)
                st.start()
            except Exception:
                pass

    def _update_spinner(self, text: str) -> None:
        st = self._get_status()
        if st is not None:
            try:
                st.update(text)
            except Exception:
                pass

    # ── Tool lifecycle ────────────────────────────────────────────────────────

    def _on_tool_start(self, tool: str, args: dict, call_id: str) -> None:
        header = _build_header(tool, args)
        state = _ToolState(tool=tool, call_id=call_id, header=header)

        with self._lock:
            self._states[call_id] = state

        self._stop_spinner()
        self._console.print(f"[bold yellow]{header}[/bold yellow]")
        self._start_spinner()

    def _on_tool_output(self, chunk: str, call_id: str) -> None:
        with self._lock:
            state = self._states.get(call_id)
        if state is None:
            # Unknown call_id — write directly as fallback
            sys.stdout.write(chunk)
            sys.stdout.flush()
            return

        state.buffer.feed(chunk)

        if not state.output_opened:
            state.output_opened = True
            self._stop_spinner()
            self._console.print()  # blank line before output

        # Write new lines from buffer directly to stdout so \r progress bars work
        visible, _ = state.buffer.render()
        if visible:
            # Only write the chunk directly; let the buffer track state
            sys.stdout.write(chunk)
            sys.stdout.flush()

    def _on_tool_complete(
        self,
        call_id: str,
        status: str,
        duration_ms: int,
        error: Optional[str],
    ) -> None:
        with self._lock:
            state = self._states.pop(call_id, None)

        if state is None:
            self._start_spinner("[bold yellow]Thinking...[/bold yellow]")
            return

        if state.output_opened:
            # Flush any remaining partial line
            state.buffer.flush()
            _, hidden = state.buffer.render()
            if hidden > 0:
                self._console.print(f"\n[dim]   … +{hidden} lines[/dim]")
            else:
                self._console.print()  # ensure newline after raw stdout

        dur_str = f"  [dim]{_fmt_duration(duration_ms)}[/dim]" if duration_ms > 0 else ""

        if status == "ok":
            self._console.print(
                f"[green]✓[/green] [dim]{state.header}[/dim]{dur_str}"
            )
        elif status == "cancelled":
            reason = f"  [dim]{error}[/dim]" if error else ""
            self._console.print(
                f"[yellow]✗[/yellow] [dim]{state.header}[/dim]{dur_str}{reason}"
            )
        else:  # error
            err_str = f"  [dim red]{_shorten(error or '', 80)}[/dim red]" if error else ""
            self._console.print(
                f"[red]✗[/red] [dim]{state.header}[/dim]{dur_str}{err_str}"
            )

        self._start_spinner("[bold yellow]Thinking...[/bold yellow]")

    # ── Sub-agent lifecycle ───────────────────────────────────────────────────

    def _on_agent_start(self, d: dict) -> None:
        agent = d.get("agent", "agent")
        task = d.get("task", "")
        task_preview = f": [dim]{_shorten(task, 80)}[/dim]" if task else ""
        self._stop_spinner()
        self._console.rule(
            f"[bold cyan]▶ [{agent}]{task_preview}[/bold cyan]", style="cyan"
        )
        self._start_spinner(f"[bold cyan][{agent}] Thinking...[/bold cyan]")

    def _on_agent_end(self, d: dict) -> None:
        agent = d.get("agent", "agent")
        state = d.get("state", "completed")
        turns = d.get("turns", 0)
        tool_calls = d.get("tool_calls", 0)
        tokens = d.get("tokens", 0)
        ms = d.get("duration_ms", 0)
        error = d.get("error")

        stats = f"{turns} turns · {tool_calls} tool calls · ~{tokens:,} tokens · {_fmt_duration(ms)}"
        if error:
            stats += f" · {_shorten(error, 50)}"

        style = "cyan" if state in ("completed",) else "red"
        self._stop_spinner()
        self._console.rule(
            f"[bold {style}]◀ [{agent}]  {stats}[/bold {style}]", style=style
        )
        self._start_spinner("[bold yellow]Thinking...[/bold yellow]")
