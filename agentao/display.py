"""DisplayController — semantic, low-noise tool execution display for the CLI.

v1 features:
  - Semantic tool headers  (→ read / $ shell / ← edit / ✱ search …)
  - Buffered output with tail-biased truncation and fold indicator
  - 2-space indented output section
  - Completion status:  ✓ read 32ms  /  ✗ shell 1.2s  Permission denied
  - Sub-agent lifecycle: ▶ / ◀ rules

v2 features (this file):
  - 工具聚合  — parallel tools shown with `+` prefix; batch summary on completion
  - 展开/折叠 — output collapsed by default for read/search/memory tools;
                shell always expanded; write/edit show diff instead
  - Diff 渲染 — replace: colored unified diff of old→new;
                write_file: syntax-highlighted content preview (first N lines)
  - 进度增强  — live elapsed counter on spinner for tools running > 0.5 s

Design rules:
  - Never raises. All exceptions are swallowed.
  - Does not touch tool execution logic.
  - Thread-safe: concurrent tools isolated by call_id.
"""

import difflib
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Callable, Optional

from rich.console import Console
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text

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

# Tools that stream raw output to the terminal (expanded by default)
_OUTPUT_TOOLS: frozenset[str] = frozenset({"run_shell_command"})

# Tools rendered via diff / preview instead of raw output
_DIFF_TOOLS: frozenset[str] = frozenset({"replace", "write_file"})

# Progress timer thresholds
_PROGRESS_DELAY    = 0.5   # seconds before first elapsed update
_PROGRESS_INTERVAL = 0.3   # seconds between updates

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mKHJABCDEF]")

# Argument display budgets
_ARG_TEXT_MAX  = 100   # regular tool argument text
_TASK_TEXT_MAX = 150   # sub-agent task description
_PATH_TAIL_MAX =  55   # chars kept at tail of a path / URL (tail-preserve)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _shorten(val: str, max_len: int = 60) -> str:
    """General-purpose truncation for error messages / stats (not arg display)."""
    if len(val) <= max_len:
        return val
    return val[:max_len - 1] + "…"


def _shorten_path(val: str) -> str:
    """Tail-preserve truncation for paths and URLs — keeps filename / endpoint."""
    if len(val) <= _PATH_TAIL_MAX:
        return val
    return "…" + val[-_PATH_TAIL_MAX:]


def _fmt_arg(val: object, max_len: int = _ARG_TEXT_MAX) -> str:
    """Format a single tool argument for display in a header line.

    Routing:
    - Paths / URLs → _shorten_path (tail-preserve, not constrained by max_len)
    - Plain text   → head-truncate at max_len; quote if it contains spaces
    """
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    if "/" in s or "\\" in s or s.startswith(("http://", "https://")):
        return _shorten_path(s)
    if len(s) > max_len:
        s = s[:max_len - 1] + "…"
    if " " in s:
        return f'"{s}"'
    return s


def _build_header(tool: str, args: dict) -> str:
    if tool.startswith("agent_"):
        agent_name = tool[len("agent_"):].replace("_", "-")
        task = _fmt_arg(args.get("task", args.get("description", "")), max_len=_TASK_TEXT_MAX)
        return f"▶ {agent_name}" + (f"  {task}" if task else "")

    if tool.startswith("mcp_"):
        rest = tool[4:]
        parts = rest.split("_", 1)
        label = f"{parts[0]}.{parts[1]}" if len(parts) == 2 else rest
        pk = next((k for k in ("path", "file_path", "query", "command", "url") if k in args), None)
        arg_str = _fmt_arg(args.get(pk)) if pk else ""
        return f"⬡ {label}" + (f"  {arg_str}" if arg_str else "")

    sem = _SEMANTICS.get(tool)
    if sem is None:
        first_val = next(iter(args.values()), None) if args else None
        arg_str = _fmt_arg(first_val) if first_val is not None else ""
        return f"⚙ {tool}" + (f"  {arg_str}" if arg_str else "")

    icon, verb, pk, sk = sem
    pval = args.get(pk, "") if pk else ""
    arg_str = _fmt_arg(pval)
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


# ── Diff / preview rendering ──────────────────────────────────────────────────

_DIFF_CONTEXT_LINES = 2
_WRITE_PREVIEW_LINES = 16


def _lexer_for(path: str) -> str:
    """Guess Pygments lexer name from file extension."""
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml",
        ".md": "markdown", ".sh": "bash", ".bash": "bash",
        ".html": "html", ".css": "css", ".toml": "toml",
        ".go": "go", ".rs": "rust", ".java": "java", ".c": "c",
        ".cpp": "cpp", ".h": "c",
    }.get(suffix, "text")


def _render_replace_diff(console: Console, path: str, old: str, new: str) -> None:
    """Print a colored unified diff for a replace operation."""
    old_lines = (old or "").splitlines(keepends=True)
    new_lines = (new or "").splitlines(keepends=True)
    fname = Path(path).name if path else "file"
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{fname}", tofile=f"b/{fname}",
        n=_DIFF_CONTEXT_LINES,
    ))
    if not diff:
        return
    # Color each line manually (no Syntax block needed — diff is plain text)
    for line in diff:
        line_s = line.rstrip("\n")
        if line_s.startswith("+++") or line_s.startswith("---"):
            console.print(f"  [bold]{line_s}[/bold]")
        elif line_s.startswith("+"):
            console.print(f"  [green]{line_s}[/green]")
        elif line_s.startswith("-"):
            console.print(f"  [red]{line_s}[/red]")
        elif line_s.startswith("@@"):
            console.print(f"  [cyan]{line_s}[/cyan]")
        else:
            console.print(f"  [dim]{line_s}[/dim]")


def _render_write_preview(console: Console, path: str, content: str) -> None:
    """Print a syntax-highlighted preview of written content."""
    if not content:
        return
    lines = content.splitlines()
    preview = "\n".join(lines[:_WRITE_PREVIEW_LINES])
    hidden = max(0, len(lines) - _WRITE_PREVIEW_LINES)
    lexer = _lexer_for(path)
    try:
        syn = Syntax(preview, lexer, theme="monokai", line_numbers=False,
                     background_color="default", indent_guides=False)
        console.print(Padding(syn, (0, 0, 0, 2)))
    except Exception:
        for line in lines[:_WRITE_PREVIEW_LINES]:
            console.print(f"  [dim]{line}[/dim]")
    if hidden > 0:
        console.print(f"  [dim]… +{hidden} lines[/dim]")


# ── Output buffer ─────────────────────────────────────────────────────────────

_MAX_DISPLAY_LINES = 8
_MAX_DISPLAY_CHARS = 1200


@dataclass
class _OutputBuffer:
    _lines: list[str] = field(default_factory=list)
    _current: str = field(default="")

    def feed(self, chunk: str) -> None:
        """Accept a raw output chunk.

        Handles: multi-chunk, empty chunks, incomplete newlines, bare \\r
        (progress-bar overwrite), ANSI codes, \\r\\n Windows endings.
        """
        if not chunk:
            return
        # Normalize Windows CRLF → LF before bare-\r processing
        chunk = chunk.replace("\r\n", "\n")
        combined = self._current + chunk
        parts = combined.split("\n")
        for part in parts[:-1]:
            # Bare \r: carriage-return overwrite — keep only last segment
            self._lines.append(part.split("\r")[-1])
        # Partial line: same \r semantics for any in-progress overwrite
        self._current = parts[-1].split("\r")[-1]

    def flush(self) -> None:
        """Commit any partial line to _lines."""
        if self._current:
            self._lines.append(self._current)
            self._current = ""

    def render(self) -> tuple[str, int]:
        """Return (visible_text, hidden_line_count) with tail-biased truncation.

        - ANSI codes stripped for clean display
        - Trailing blank lines removed
        - Last _MAX_DISPLAY_LINES lines kept; earlier lines folded
        - visible_text capped at _MAX_DISPLAY_CHARS chars
        """
        all_lines = list(self._lines)
        if self._current:
            all_lines.append(self._current)
        # Strip ANSI, then drop trailing blank lines
        clean = [_strip_ansi(ln) for ln in all_lines]
        while clean and not clean[-1].strip():
            clean.pop()
        total = len(clean)
        if total == 0:
            return "", 0
        if total <= _MAX_DISPLAY_LINES:
            text = "\n".join(clean)
            hidden = 0
        else:
            # Tail-biased: errors/results typically appear at the end
            visible = clean[-_MAX_DISPLAY_LINES:]
            hidden = total - _MAX_DISPLAY_LINES
            text = "\n".join(visible)
        if len(text) > _MAX_DISPLAY_CHARS:
            text = "…" + text[-(_MAX_DISPLAY_CHARS - 1):]
        return text, hidden


# ── Per-call state ────────────────────────────────────────────────────────────

@dataclass
class _ToolState:
    tool: str
    call_id: str
    header: str
    buffer: _OutputBuffer = field(default_factory=_OutputBuffer)
    start_ts: float = field(default_factory=monotonic)
    show_output: bool = True   # True → render buffered output at completion
    diff_context: dict = field(default_factory=dict)  # populated for replace/write_file
    _timer: object = field(default=None, repr=False)  # threading.Timer | None


# ── DisplayController ─────────────────────────────────────────────────────────

class DisplayController:
    """Consume AgentEvents and render a semantic, low-noise tool display."""

    def __init__(
        self,
        console: Console,
        get_status: Callable[[], object],
    ) -> None:
        self._console = console
        self._get_status = get_status
        self._states: dict[str, _ToolState] = {}
        self._active_calls: set[str] = set()   # for tool aggregation
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
            pass

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

    # ── Progress timer ────────────────────────────────────────────────────────

    def _arm_progress_timer(self, call_id: str, header: str, start_ts: float) -> None:
        """Start a daemon timer that updates the spinner with elapsed time."""
        def _tick() -> None:
            with self._lock:
                if call_id not in self._states:
                    return
            elapsed = monotonic() - start_ts
            self._update_spinner(f"[dim]{header}[/dim]  [dim]{elapsed:.1f}s[/dim]")
            # Schedule next tick
            t = threading.Timer(_PROGRESS_INTERVAL, _tick)
            t.daemon = True
            with self._lock:
                state = self._states.get(call_id)
                if state is not None:
                    state._timer = t
                    t.start()

        t = threading.Timer(_PROGRESS_DELAY, _tick)
        t.daemon = True
        with self._lock:
            state = self._states.get(call_id)
            if state is not None:
                state._timer = t
        t.start()

    def _cancel_progress_timer(self, state: _ToolState) -> None:
        if state._timer is not None:
            try:
                state._timer.cancel()
            except Exception:
                pass
            state._timer = None

    # ── Tool lifecycle ────────────────────────────────────────────────────────

    def _on_tool_start(self, tool: str, args: dict, call_id: str) -> None:
        header = _build_header(tool, args)
        show_output = tool in _OUTPUT_TOOLS

        # Collect diff context for write/edit tools
        diff_ctx: dict = {}
        if tool == "replace":
            diff_ctx = {
                "kind": "replace",
                "path": args.get("file_path", ""),
                "old": args.get("old_text", ""),
                "new": args.get("new_text", ""),
            }
        elif tool == "write_file":
            diff_ctx = {
                "kind": "write",
                "path": args.get("file_path", ""),
                "content": args.get("content", ""),
            }

        state = _ToolState(
            tool=tool, call_id=call_id, header=header,
            show_output=show_output, diff_context=diff_ctx,
        )

        with self._lock:
            is_parallel = len(self._active_calls) > 0
            self._active_calls.add(call_id)
            self._states[call_id] = state

        self._stop_spinner()
        if is_parallel:
            # Secondary tool in a parallel batch — indent to signal grouping
            self._console.print(f"  [dim]+ {header}[/dim]")
        else:
            self._console.print(f"[bold yellow]{header}[/bold yellow]")
        self._start_spinner()

        # Arm progress timer (fires after _PROGRESS_DELAY seconds)
        self._arm_progress_timer(call_id, header, state.start_ts)

    def _on_tool_output(self, chunk: str, call_id: str) -> None:
        # All output — including shell — is buffered and shown at completion.
        # This prevents screen flooding and handles \r progress bars correctly.
        with self._lock:
            state = self._states.get(call_id)
        if state is not None:
            state.buffer.feed(chunk)
        # Orphaned chunks (no matching state) are silently discarded.

    def _on_tool_complete(
        self,
        call_id: str,
        status: str,
        duration_ms: int,
        error: Optional[str],
    ) -> None:
        with self._lock:
            state = self._states.pop(call_id, None)
            self._active_calls.discard(call_id)
            still_active = len(self._active_calls) > 0

        if state is None:
            if not still_active:
                self._start_spinner("[bold yellow]Thinking...[/bold yellow]")
            return

        self._cancel_progress_timer(state)

        # ── Flush buffer and decide what to show ──────────────────────────────
        state.buffer.flush()
        # show_output=True  → expanded tool (shell): always show tail
        # show_output=False → collapsed tool: show tail only on error so the
        #                     cause is visible; on success show diff/preview
        render_buf = state.show_output or (status == "error")
        if render_buf:
            visible, hidden = state.buffer.render()
            if visible:
                for line in visible.splitlines():
                    self._console.print("  " + line, markup=False, highlight=False)
                if hidden > 0:
                    self._console.print(f"  [dim]… +{hidden} lines[/dim]")

        # ── Diff / preview for write tools (success only) ─────────────────────
        if status == "ok" and state.diff_context:
            ctx = state.diff_context
            if ctx.get("kind") == "replace":
                _render_replace_diff(self._console, ctx["path"], ctx["old"], ctx["new"])
            elif ctx.get("kind") == "write":
                _render_write_preview(self._console, ctx["path"], ctx["content"])

        # ── Completion line ───────────────────────────────────────────────────
        dur_str = f"  [dim]{_fmt_duration(duration_ms)}[/dim]" if duration_ms > 0 else ""

        if status == "ok":
            self._console.print(f"[green]✓[/green] [dim]{state.header}[/dim]{dur_str}")
        elif status == "cancelled":
            reason = f"  [dim]{error}[/dim]" if error else ""
            self._console.print(f"[yellow]✗[/yellow] [dim]{state.header}[/dim]{dur_str}{reason}")
        else:  # error
            err_str = f"  [dim red]{_shorten(error or '', 80)}[/dim red]" if error else ""
            self._console.print(f"[red]✗[/red] [dim]{state.header}[/dim]{dur_str}{err_str}")

        # Only restore "Thinking…" once the last tool in a parallel batch is done
        if not still_active:
            self._start_spinner("[bold yellow]Thinking...[/bold yellow]")

    # ── Sub-agent lifecycle ───────────────────────────────────────────────────

    def _on_agent_start(self, d: dict) -> None:
        agent = d.get("agent", "agent")
        task = d.get("task", "")
        _t = (task[:_TASK_TEXT_MAX - 1] + "…") if len(task) > _TASK_TEXT_MAX else task
        task_preview = f": [dim]{_t}[/dim]" if _t else ""
        self._stop_spinner()
        self._console.rule(f"[bold cyan]▶ [{agent}]{task_preview}[/bold cyan]", style="cyan")
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

        style = "cyan" if state == "completed" else "red"
        self._stop_spinner()
        self._console.rule(f"[bold {style}]◀ [{agent}]  {stats}[/bold {style}]", style=style)
        self._start_spinner("[bold yellow]Thinking...[/bold yellow]")
