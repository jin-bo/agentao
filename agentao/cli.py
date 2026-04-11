"""CLI interface for Agentao."""

import warnings
warnings.filterwarnings("ignore", message="urllib3.*or chardet.*doesn't match")

import atexit
import json
import logging
import os
import sys
import uuid as _uuid_mod
try:
    import termios
    _HAS_TERMIOS = True
except ImportError:
    _HAS_TERMIOS = False
from pathlib import Path
from typing import Optional

import readchar
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.theme import Theme
from dotenv import load_dotenv

from .agent import Agentao
from .display import DisplayController
from .transport import AgentEvent, EventType

# Custom theme for the CLI
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
})

console = Console(theme=custom_theme)
logger = logging.getLogger(__name__)

# Plugin inline dirs set from --plugin-dir in entrypoint(), consumed by
# AgentaoCLI and run_print_mode to wire plugins into sessions.
_plugin_inline_dirs: list[Path] = []

# Tool argument keys to display in the thinking step (priority order)
_TOOL_SUMMARY_KEYS = ("path", "file_path", "query", "description", "command", "url", "key", "pattern", "tag")


def _tool_args_summary(tool_name: str, args: dict) -> str:
    """Build a short human-readable summary of tool arguments for display."""
    if not args:
        return ""
    # Try priority keys first
    for key in _TOOL_SUMMARY_KEYS:
        if key in args:
            val = str(args[key])
            if len(val) > 50:
                val = val[:47] + "..."
            return f"({val})"
    # Fall back to first value
    first_val = str(next(iter(args.values())))
    if len(first_val) > 50:
        first_val = first_val[:47] + "..."
    return f"({first_val})"




_SLASH_COMMANDS = [
    '/agent', '/agent bg', '/agent cancel', '/agent dashboard', '/agent delete', '/agent list', '/agent status',
    '/agents',
    '/clear', '/copy', '/new',
    '/crystallize', '/crystallize create', '/crystallize suggest',
    '/plan', '/plan clear', '/plan history', '/plan implement', '/plan show',
    '/context', '/context limit', '/exit', '/help',
    '/mcp', '/mcp add', '/mcp list', '/mcp remove',
    '/markdown',
    '/memory', '/memory clear', '/memory delete', '/memory list',
    '/memory project', '/memory search', '/memory session', '/memory status',
    '/memory tag', '/memory user', '/mode', '/model', '/permission', '/provider', '/quit',
    '/sessions', '/sessions delete', '/sessions delete all', '/sessions list', '/sessions resume',
    '/plugins', '/plugins list',
    '/skills', '/skills activate', '/skills deactivate',
    '/skills disable', '/skills enable', '/skills reload', '/status', '/temperature',
    '/todos', '/tools',
]


_SLASH_COMMAND_HINTS = {
    '/crystallize create': '[skill-name]',
    '/agent bg': '<agent-name> <task>',
    '/agent cancel': '<agent-id>',
    '/agent delete': '<agent-id>',
    '/agent status': '[agent-id]',
    '/mode': '[read-only|workspace-write|full-access]',
    '/model': '<model-name>',
    '/provider': '<provider-name>',
    '/memory search': '<keyword>',
    '/memory delete': '<key>',
    '/memory tag': '<tag>',
    '/skills activate': '<skill-name>',
    '/skills deactivate': '<skill-name>',
    '/skills enable': '<skill-name>',
    '/skills disable': '<skill-name>',
    '/context limit': '<tokens>',
    '/temperature': '<value>',
    '/sessions resume': '<session-id>',
    '/sessions delete': '<session-id>',
    '/mcp add': '<name> <command|url>',
    '/mcp remove': '<name>',
}


class _SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith('/'):
            return
        # If the typed text exactly matches a command, show its argument hint
        stripped = text.rstrip()
        if stripped in _SLASH_COMMAND_HINTS:
            hint = _SLASH_COMMAND_HINTS[stripped]
            yield Completion(f' {hint}', start_position=0, display_meta='arg')
            return
        # Prefix completion for command names
        for cmd in _SLASH_COMMANDS:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text))


def _display_layered_entries(entries, header: str, console) -> None:
    """Display MemoryRecord list in a readable format."""
    if not entries:
        console.print(f"\n[warning]{header}: no entries.[/warning]\n")
        return
    console.print(f"\n[info]{header} ({len(entries)} total):[/info]\n")
    for e in entries:
        excerpt = e.content[:120] + "..." if len(e.content) > 120 else e.content
        console.print(f"  [dim]{e.id}[/dim] • [cyan]{e.title}[/cyan]: {excerpt}")
        if e.tags:
            console.print(f"    Tags: {', '.join(e.tags)}")
    console.print()


class AgentaoCLI:
    """CLI interface for Agentao."""

    def __init__(self):
        """Initialize CLI."""
        load_dotenv()

        self.current_session_id: Optional[str] = str(_uuid_mod.uuid4())  # Stable UUID of active session
        self.current_status = None  # Track active status context
        self._streaming_output = False  # unused; kept for any external callers
        self.markdown_mode = True  # Render responses as Markdown (toggle with /markdown)
        self.last_response: str | None = None  # Last assistant response for /copy
        # Plan mode: shared session + controller (replaces old _plan_mode booleans)
        from .plan import PlanSession, PlanController
        self._plan_session = PlanSession()
        self._plan_controller: Optional[object] = None  # set after agent construction
        provider = os.getenv("LLM_PROVIDER", "OPENAI").strip().upper()
        self.current_provider = provider  # Track active provider name

        context_limit = int(os.getenv("AGENTAO_CONTEXT_TOKENS", "200000"))

        from .permissions import PermissionEngine, PermissionMode
        self.permission_engine = PermissionEngine()

        # Derived state — kept for internal use; always in sync with current_mode
        self.allow_all_tools = False  # unused after mode refactor; kept for safety
        self.readonly_mode = False    # synced from current_mode via _apply_mode()
        self._cached_ctx_pct: float = 0.0  # updated after each agent.chat()
        self._streaming_started: bool = False

        # Load persisted mode (defaults to workspace-write on first run)
        from .permissions import PermissionMode as _PM
        _saved = self._load_settings().get("mode", "workspace-write")
        try:
            self.current_mode: PermissionMode = _PM(_saved)
        except ValueError:
            self.current_mode = _PM.WORKSPACE_WRITE

        self.agent = Agentao(
            api_key=os.getenv(f"{provider}_API_KEY"),
            base_url=os.getenv(f"{provider}_BASE_URL"),
            model=os.getenv(f"{provider}_MODEL"),
            transport=self,
            max_context_tokens=context_limit,
            permission_engine=self.permission_engine,
            plan_session=self._plan_session,
        )

        # Initialize plan controller and register plan tools on agent
        from .plan import PlanController
        self._plan_controller = PlanController(
            session=self._plan_session,
            permission_engine=self.permission_engine,
            apply_mode_fn=self._apply_mode,
            load_settings_fn=self._load_settings,
        )
        from .tools.plan import PlanSaveTool, PlanFinalizeTool
        self.agent.tools.register(PlanSaveTool(self._plan_controller))
        self.agent.tools.register(PlanFinalizeTool(self._plan_controller))

        # Propagate session ID to agent so plugin hooks can identify the session.
        self.agent._session_id = self.current_session_id
        self.agent.tool_runner._session_id = self.current_session_id

        # Load plugins and register their skills/agents/MCP servers.
        _load_and_register_plugins(self.agent)

        # prompt_toolkit session: multiline=True captures full paste; Enter submits
        _kb = KeyBindings()

        @_kb.add('enter')
        def _pt_submit(event):
            event.current_buffer.validate_and_handle()

        @_kb.add('escape', 'enter')  # Meta/Alt+Enter → insert newline
        def _pt_newline(event):
            event.current_buffer.insert_text('\n')

        _history_file = os.path.expanduser("~/.agentao/history")
        os.makedirs(os.path.dirname(_history_file), exist_ok=True)
        self._prompt_session = PromptSession(
            history=FileHistory(_history_file),
            key_bindings=_kb,
            multiline=True,
            prompt_continuation='',
            completer=_SlashCompleter(),
            bottom_toolbar=self._get_status_toolbar,
            style=Style.from_dict({"bottom-toolbar": "noreverse bg:default"}),
        )

        self.display = DisplayController(console, lambda: self.current_status)

        # Apply the loaded mode (engine + ToolRunner) without saving again
        self.permission_engine.set_mode(self.current_mode)
        from .permissions import PermissionMode as _PM2
        self.readonly_mode = (self.current_mode == _PM2.READ_ONLY)
        self._apply_readonly_mode()

    def _apply_readonly_mode(self) -> None:
        """Sync self.readonly_mode into the ToolRunner."""
        self.agent.tool_runner.set_readonly_mode(self.readonly_mode)

    def _load_settings(self) -> dict:
        """Load persisted settings from .agentao/settings.json."""
        path = Path(".agentao") / "settings.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _save_settings(self) -> None:
        """Persist current settings to .agentao/settings.json."""
        path = Path(".agentao") / "settings.json"
        path.parent.mkdir(exist_ok=True)
        data = self._load_settings()
        data["mode"] = self.current_mode.value
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _apply_mode(self, mode) -> None:
        """Switch permission mode, sync engine + ToolRunner, and persist."""
        self.current_mode = mode
        self.permission_engine.set_mode(mode)
        from .permissions import PermissionMode
        self.readonly_mode = (mode == PermissionMode.READ_ONLY)
        self._apply_readonly_mode()
        self.allow_all_tools = False
        self._save_settings()

    # ── Transport protocol implementation ────────────────────────────────────

    def emit(self, event: AgentEvent) -> None:
        """Dispatch a runtime event to the appropriate handler."""
        try:
            t = event.type
            if t == EventType.TURN_START:
                if self.current_status:
                    self.current_status.update("[bold yellow]Thinking…[/bold yellow]")
            elif t == EventType.TOOL_CONFIRMATION:
                if self.current_status:
                    tool_name = event.data.get("tool", "")
                    self.current_status.update(
                        f"[yellow]Waiting for confirmation…  [dim]{tool_name}[/dim][/yellow]"
                    )
            elif t == EventType.THINKING:
                self.on_llm_thinking(event.data.get("text", ""))
            elif t == EventType.LLM_TEXT:
                self.on_llm_text(event.data.get("chunk", ""))
            else:
                self.display.on_event(event)
        except Exception:
            pass  # never let a UI error crash the runtime

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        """Transport protocol method — delegates to confirm_tool_execution."""
        return self.confirm_tool_execution(tool_name, description, args)

    def confirm_tool_execution(self, tool_name: str, tool_description: str, tool_args: dict) -> bool:
        """Prompt user to confirm tool execution with menu options.

        Args:
            tool_name: Name of the tool to execute
            tool_description: Description of the tool
            tool_args: Arguments to pass to the tool

        Returns:
            True if user confirms, False otherwise
        """
        # full-access mode: the engine returns ALLOW before this is ever called, but
        # guard here too for callers that invoke confirm_tool_execution directly.
        # Also honour the legacy allow_all_tools flag so external callers/tests work.
        from .permissions import PermissionMode
        if (self.current_mode == PermissionMode.FULL_ACCESS or self.allow_all_tools) and not self._plan_session.is_active:
            return True

        # Pause the "Thinking..." spinner during user confirmation
        if self.current_status:
            self.current_status.stop()

        try:
            # Display tool information
            console.print(f"\n[yellow]⚠️  Tool Confirmation Required[/yellow]")
            console.print(f"[info]Tool:[/info] [cyan]{tool_name}[/cyan]")
            console.print(f"[info]Arguments:[/info]")

            # Format arguments nicely
            for key, value in tool_args.items():
                console.print(f"  • {key}: {value}")

            # Display menu with better formatting
            console.print("\n[bold]Choose an option:[/bold]")
            console.print(" [green]1[/green]. Yes")
            console.print(" [green]2[/green]. Yes, allow all tools during this session")
            console.print(" [red]3[/red]. No")
            console.print("\n[dim]Press 1, 2, or 3 (single key, no Enter needed) · Esc to cancel[/dim]", end=" ")

            # Get single-key input using readchar
            while True:
                try:
                    key = readchar.readkey()

                    # Handle number keys
                    if key == "1":
                        console.print("\n[green]✓ Executing tool[/green]")
                        return True
                    elif key == "2":
                        # Session-only escalation: switch engine to full-access in
                        # memory and set allow_all_tools for legacy callers, but do
                        # NOT persist so the next launch keeps the saved mode.
                        from .permissions import PermissionMode
                        self.allow_all_tools = True
                        self.current_mode = PermissionMode.FULL_ACCESS
                        self.permission_engine.set_mode(PermissionMode.FULL_ACCESS)
                        # Clear readonly_mode so ToolRunner stops blocking writes.
                        self.readonly_mode = False
                        self._apply_readonly_mode()
                        console.print("\n[green]✓ Executing tool (full-access mode enabled for this session)[/green]")
                        return True
                    elif key == "3":
                        console.print("\n[red]✗ Cancelled[/red]")
                        return False
                    # Handle Esc key
                    elif key == readchar.key.ESC:
                        console.print("\n[red]✗ Cancelled[/red]")
                        return False
                    # Handle Ctrl+C
                    elif key == readchar.key.CTRL_C:
                        console.print("\n[red]✗ Cancelled[/red]")
                        return False
                    # Ignore other keys
                    else:
                        continue

                except KeyboardInterrupt:
                    console.print("\n[red]✗ Cancelled[/red]")
                    return False
                except Exception as e:
                    # Fallback to cancelled on any error
                    console.print(f"\n[red]✗ Cancelled (error: {e})[/red]")
                    return False

        finally:
            # Resume the "Thinking..." spinner after user makes a choice
            if self.current_status:
                self.current_status.start()

    def on_llm_thinking(self, reasoning: str) -> None:
        """Display LLM reasoning text produced before tool calls.

        Args:
            reasoning: The LLM's reasoning / thinking text
        """
        if not reasoning.strip():
            return

        # Pause the spinner while displaying reasoning
        if self.current_status:
            self.current_status.stop()

        console.rule("[dim]Thinking[/dim]", style="dim blue")
        for line in reasoning.strip().splitlines():
            console.print(f"  [dim italic]{line}[/dim italic]")
        console.print()

        # Resume spinner
        if self.current_status:
            self.current_status.start()

    def on_max_iterations(self, max_iterations: int, pending_tools: list) -> dict:
        """Called when tool call loop reaches max iterations. Asks user how to proceed.

        Args:
            max_iterations: The iteration limit that was reached
            pending_tools: List of dicts with "name" and "args" for pending tool calls

        Returns:
            dict with "action": "continue"|"stop"|"new_instruction" and optional "message"
        """
        if self.current_status:
            self.current_status.stop()
        try:
            console.print(f"\n[bold yellow]⚠️  已达到最大工具调用次数 ({max_iterations})[/bold yellow]")

            if pending_tools:
                console.print("[dim]待执行的工具调用：[/dim]")
                for tc in pending_tools:
                    try:
                        args = json.loads(tc["args"]) if isinstance(tc["args"], str) else tc["args"]
                        args_str = ", ".join(f"{k}={repr(v)}" for k, v in list(args.items())[:3])
                    except Exception:
                        args_str = str(tc["args"])[:80]
                    console.print(f"  • [cyan]{tc['name']}[/cyan]({args_str})")
            else:
                console.print("[dim]无待执行的工具调用。[/dim]")

            console.print("\n[bold]选择操作：[/bold]")
            console.print(" [green]1[/green]. 继续（重置计数器，再执行 100 次）")
            console.print(" [red]2[/red]. 停止")
            console.print(" [yellow]3[/yellow]. 输入新的工作指令后继续")
            console.print("\n[dim]按 1、2 或 3（单键，无需回车）· Esc 停止[/dim]", end=" ")

            while True:
                try:
                    key = readchar.readkey()
                    if key == "1":
                        console.print("\n[green]✓ 继续执行[/green]")
                        return {"action": "continue"}
                    elif key == "2" or key in (readchar.key.ESC, readchar.key.CTRL_C):
                        console.print("\n[red]✗ 停止[/red]")
                        return {"action": "stop"}
                    elif key == "3":
                        console.print()
                        new_msg = console.input("[bold yellow]▶ 新指令：[/bold yellow]").strip()
                        if not new_msg:
                            new_msg = "继续"
                        return {"action": "new_instruction", "message": new_msg}
                    else:
                        continue
                except KeyboardInterrupt:
                    console.print("\n[red]✗ 停止[/red]")
                    return {"action": "stop"}
        finally:
            if self.current_status:
                self.current_status.start()

    def on_llm_text(self, chunk: str) -> None:
        if self.markdown_mode:
            return  # batch-render at end; streaming raw markup is unreadable
        import sys
        if not self._streaming_started:
            if self.current_status:
                self.current_status.stop()
                self.current_status = None
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._streaming_started = True
        sys.stdout.write(chunk)
        sys.stdout.flush()

    def ask_user(self, question: str) -> str:
        """Pause spinner, display question, read free-form user response, resume spinner.

        Args:
            question: The question from the LLM to show the user

        Returns:
            User's text response, or fallback string on interrupt/EOF
        """
        if self.current_status:
            self.current_status.stop()
        try:
            console.print(f"\n[bold yellow]🤔 Agent Question[/bold yellow]")
            console.print(f"[yellow]{question}[/yellow]")
            response = console.input("[bold yellow]▶ [/bold yellow]").strip()
            return response if response else "(no response)"
        except (EOFError, KeyboardInterrupt):
            return "(user interrupted)"
        finally:
            if self.current_status:
                self.current_status.start()

    def print_welcome(self):
        """Print welcome message."""
        current_model = self.agent.get_current_model()

        logo = [
            "   ___                      _                ",
            "  / _ \\ ___ _ ___  ___  ___| |_  ___  ___  ",
            " /  _  // _` / -_)| _ \\/ _ \\  _|/ _` / _ \\ ",
            "/_/ |_| \\__, \\___||_// \\___/\\__|\\__,_\\___/ ",
        ]

        console.print()
        for line in logo:
            console.print(f"[bold cyan]{line}[/bold cyan]")
        console.print("[bold cyan]        |___/        [/bold cyan][bold yellow](The Way of Agents)[/bold yellow]")
        console.print()
        console.print(f"  [dim]Model:[/dim] [green]{current_model}[/green]  [dim]|[/dim]  [dim]Type[/dim] [cyan]/help[/cyan] [dim]for commands[/dim]")
        console.print()

    def print_help(self):
        """Print help message."""
        help_text = """
# Agentao Help

**Available Commands:**
All commands start with `/`:

- `/help` - Show this help message
- `/model` - List available models or switch model
  - `/model` - Show current model and available models
  - `/model <name>` - Switch to specified model
- `/provider` - List or switch API providers
  - `/provider` - Show current provider and available providers
  - `/provider <NAME>` - Switch to provider (reads XXXX_API_KEY, XXXX_BASE_URL, XXXX_MODEL from env)
- `/clear` - End current session (saves it) and start a new one
  - Clears conversation history, all memories, and resets permission mode to workspace-write
  - `/clear all` - Alias for `/clear` (backward compatible)
- `/new` - Alias for `/clear`; start a fresh session
- `/status` - Show conversation status
- `/temperature [value]` - Show or set LLM temperature (0.0-2.0)
- `/mode [read-only|workspace-write|full-access]` - Set permission mode
  - `/mode` - Show current mode
  - `/mode read-only` - Block all write & shell tools
  - `/mode workspace-write` - Allow file writes & safe shell; ask for web (default)
  - `/mode full-access` - Allow all tools without prompting
- `/plan` - Plan mode workflow (read-only; LLM plans, not executes)
  - `/plan` - Enter plan mode; if already on, shows current saved plan
  - `/plan show` - Display the saved plan file
  - `/plan implement` - Exit plan mode, restore prior permissions, show plan
  - `/plan clear` - Archive and clear the current plan
  - `/plan history` - List recent archived plans
- `/skills` - List available skills
- `/memory [subcommand] [arg]` - Manage saved memories
  - `/memory` or `/memory list` - Show all saved memories (with tag summary)
  - `/memory search <query>` - Search memories by keyword (key, value, tags)
  - `/memory tag <tag>` - Filter memories by tag
  - `/memory delete <key>` - Delete a specific memory
  - `/memory clear` - Clear all memories (requires confirmation)
- `/context` - Show context window token usage and limit
  - `/context limit <n>` - Set max context tokens (default: 200,000)
- `/plugins` - List loaded plugins with diagnostics
- `/mcp [subcommand]` - Manage MCP servers
  - `/mcp` or `/mcp list` - List MCP servers with status and tools
  - `/mcp add <name> <command|url>` - Add an MCP server
  - `/mcp remove <name>` - Remove an MCP server
- `/copy` - Copy the last assistant response (Markdown) to clipboard
- `/markdown` - Toggle Markdown rendering ON/OFF (default: ON)
- `/exit` or `/quit` - Exit the program

**Available Tools:**
The agent has access to the following tools:
- `read_file` - Read file contents with line numbers (supports offset/limit)
- `write_file` - Write/append content to files
- `replace` - Edit files by replacing text (supports replace_all)
- `list_directory` - List directory contents
- `glob` - Find files matching patterns
- `search_file_content` - Search text in files
- `run_shell_command` - Execute shell commands
- `web_fetch` - Fetch web content
- `google_web_search` - Search the web
- `activate_skill` - Activate skills
- `cli_help` - Get CLI help
- `codebase_investigator` - Investigate codebases

**Skills:**
Type `/skills` to see available skills, or ask the agent to activate a specific skill.

**Examples:**
- "Read the file main.py"
- "Search for function definitions in Python files"
- "Fetch content from https://example.com"
- "Activate the pdf skill to help me work with PDF files"

**Note:** Regular messages (without `/`) are sent to the AI agent.
"""
        console.print(Markdown(help_text))

    def list_skills(self):
        """List available, disabled, and active skills."""
        sm = self.agent.skill_manager
        available = sm.list_available_skills()
        disabled = sorted(sm.disabled_skills & set(sm.available_skills.keys()))

        console.print(f"\n[info]Available Skills ({len(available)}):[/info]\n")
        for skill_name in sorted(available):
            skill_info = sm.get_skill_info(skill_name)
            title = skill_info.get('title', skill_name) if skill_info else skill_name
            desc = skill_info.get('description', 'No description')[:100] if skill_info else 'No description'
            console.print(f"  • [cyan]{skill_name}[/cyan] - {title}")
            if desc:
                console.print(f"    {desc}...")

        if disabled:
            console.print(f"\n[info]Disabled Skills ({len(disabled)}):[/info]\n")
            for skill_name in disabled:
                skill_info = sm.get_skill_info(skill_name)
                title = skill_info.get('title', skill_name) if skill_info else skill_name
                console.print(f"  • [dim]{skill_name}[/dim] - {title}")

        console.print("\n[info]Active Skills:[/info]")
        active = sm.get_active_skills()
        if active:
            for skill, info in active.items():
                console.print(f"  • [success]{skill}[/success]: {info['task']}")
        else:
            console.print("  None")
        console.print()

    def show_status(self):
        """Show conversation status."""
        summary = self.agent.get_conversation_summary()
        console.print(f"\n[info]Status:[/info]\n{summary}")

        # Show permission mode
        from .permissions import PermissionMode
        _mode_labels = {
            PermissionMode.READ_ONLY:       ("[red]read-only[/red]",       "write & shell tools are blocked"),
            PermissionMode.WORKSPACE_WRITE: ("[green]workspace-write[/green]", "file writes & safe shell allowed, web asks"),
            PermissionMode.FULL_ACCESS:     ("[yellow]full-access[/yellow]",   "all tools allowed without prompting"),
        }
        _label, _desc = _mode_labels.get(self.current_mode, (self.current_mode.value, ""))
        console.print(f"[info]Permission Mode:[/info] {_label}  [dim]({_desc})[/dim]")

        # Show markdown mode
        md_state = "[green]ON[/green]" if self.markdown_mode else "[yellow]OFF[/yellow]"
        console.print(f"[info]Markdown Rendering:[/info] {md_state}")

        # Show task list summary if any todos exist
        todos = self.agent.todo_tool.get_todos()
        if todos:
            done = sum(1 for t in todos if t["status"] == "completed")
            console.print(f"[info]Task List:[/info] {done}/{len(todos)} completed (use /todos for details)")
        console.print()

    def handle_todos_command(self, args: str = "") -> None:
        """Display the current task list."""
        todos = self.agent.todo_tool.get_todos()
        if not todos:
            console.print(
                "\n[info]No tasks.[/info] [dim]The LLM will create tasks automatically "
                "when handling complex multi-step requests.[/dim]\n"
            )
            return

        done = sum(1 for t in todos if t["status"] == "completed")
        console.print(f"\n[info]Task List ({done}/{len(todos)} completed):[/info]\n")
        _icons = {"pending": "○", "in_progress": "◉", "completed": "✓"}
        _colors = {"pending": "white", "in_progress": "yellow", "completed": "green"}
        for todo in todos:
            status = todo["status"]
            icon = _icons.get(status, "○")
            color = _colors.get(status, "white")
            console.print(f"  [{color}]{icon}[/{color}] {todo['content']} [dim]{status}[/dim]")
        console.print()

    def handle_plan_command(self, args: str) -> None:
        """Handle /plan command and subcommands — thin dispatch to PlanController."""
        _plan_file = self._plan_session.current_plan_path
        args = args.strip()

        if args == "show":
            content = self._plan_controller.show_draft()
            if content is None:
                console.print("\n[warning]No plan file found. The agent saves it automatically when in plan mode.[/warning]\n")
                return
            console.print(f"\n[dim]{_plan_file}[/dim]\n")
            console.print(Markdown(content) if self.markdown_mode else content)
            console.print()
            return

        if args == "clear":
            if not _plan_file.exists() and not self._plan_session.is_active:
                console.print("\n[info]No plan file to clear.[/info]\n")
                return
            was_active = self._plan_session.is_active
            restored, restore_allow_all = self._plan_controller.archive_and_clear()
            if was_active:
                self.allow_all_tools = restore_allow_all
                console.print("\n[success]Plan archived and cleared. Plan mode OFF.[/success]\n")
            else:
                console.print("\n[success]Plan archived and cleared.[/success]\n")
            return

        if args == "implement":
            if not self._plan_session.is_active:
                console.print("\n[info]Not in plan mode.[/info]\n")
                return
            restored, restore_allow_all = self._plan_controller.exit_plan_mode()
            self.allow_all_tools = restore_allow_all
            console.print(f"\n[success]Plan mode OFF. Permission mode: {restored.value}[/success]")
            if _plan_file.exists():
                content = _plan_file.read_text(encoding="utf-8")
                console.print(f"\n[dim]Current plan ({_plan_file}):[/dim]\n")
                console.print(Markdown(content) if self.markdown_mode else content)
                console.print("\n[dim]Ask the agent to implement the plan above.[/dim]\n")
            else:
                console.print("\n[warning]No saved plan file. Describe the plan in your next message.[/warning]\n")
            return

        if args == "":
            if self._plan_session.is_active:
                console.print("\n[bold magenta][plan mode is ON][/bold magenta]")
                content = self._plan_controller.show_draft()
                if content:
                    console.print(f"[dim]Saved plan: {_plan_file}[/dim]\n")
                    console.print(Markdown(content) if self.markdown_mode else content)
                else:
                    console.print("[dim]No plan saved yet.[/dim]")
                console.print("\n[dim]/plan show · /plan implement · /plan clear[/dim]\n")
                return
            # Enter plan mode
            self._plan_controller.enter(self.current_mode, self.allow_all_tools)
            self.allow_all_tools = False
            # Do NOT set readonly_mode=True: the PLAN preset handles enforcement via the
            # engine so that shell commands with "allow" rules are not short-circuited.
            self.readonly_mode = False
            self._apply_readonly_mode()
            console.print("\n[bold magenta]Plan mode ON[/bold magenta]  [dim](read-only; LLM will plan, not execute)[/dim]")
            console.print("[dim]Ask what to plan. When done: /plan implement · /plan clear[/dim]\n")
            return

        if args == "history":
            entries = self._plan_controller.list_history()
            if not entries:
                console.print("\n[info]No plan history yet.[/info]\n")
                return
            console.print("\n[bold]Plan history[/bold] [dim](most recent first)[/dim]\n")
            for entry in entries:
                context_snippet = ""
                try:
                    text = entry.read_text(encoding="utf-8")
                    import re as _re
                    m = _re.search(r"##\s+Context\s*\n(.*?)(?=\n##|\Z)", text, _re.DOTALL)
                    if m:
                        snippet = " ".join(m.group(1).split())
                        if len(snippet) > 160:
                            snippet = snippet[:159] + "..."
                        context_snippet = f"  [dim]{snippet}[/dim]"
                except Exception:
                    pass
                console.print(f"  [bold]{entry.stem}[/bold]")
                if context_snippet:
                    console.print(context_snippet)
            console.print()
            return

        console.print(f"\n[error]Unknown: /plan {args}[/error]\n[info]Usage: /plan | /plan show | /plan implement | /plan clear | /plan history[/info]\n")

    # =========================================================================
    # /crystallize — Skill Crystallization (Phase 4)
    # =========================================================================

    def handle_crystallize_command(self, args: str = "") -> None:
        """Handle /crystallize [suggest|create [name]] commands."""
        from rich.panel import Panel
        from .memory.crystallizer import SkillCrystallizer, SUGGEST_SYSTEM_PROMPT, suggest_prompt, _extract_text

        parts = args.split(maxsplit=1)
        subcommand = parts[0].lower() if parts else "suggest"
        sub_arg = parts[1].strip() if len(parts) > 1 else ""

        if subcommand not in ("suggest", "create"):
            console.print("\n[error]Usage: /crystallize suggest | /crystallize create [name][/error]\n")
            return

        # Read session content: merge compacted summary + live turns after last compaction
        session_content = ""
        summaries = self.agent.memory_manager.get_recent_session_summaries(limit=5)
        if summaries:
            session_content = "\n\n---\n\n".join(s.summary_text for s in reversed(summaries))

        # Always append live user/assistant turns (may postdate last compaction)
        live_parts = []
        for msg in self.agent.messages:
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if content:
                live_parts.append(f"{role.capitalize()}: {content}")
        if live_parts:
            live_section = "\n".join(live_parts)
            session_content = (session_content + "\n\n" + live_section).strip()

        if not session_content:
            console.print("\n[warning]No session content found. Start a conversation first.[/warning]\n")
            return

        console.print("\n[dim]Analyzing session to generate skill draft...[/dim]")

        # Call LLM to generate skill draft
        try:
            response = self.agent.llm.chat(
                messages=[
                    {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
                    {"role": "user", "content": suggest_prompt(session_content)},
                ],
                max_tokens=800,
            )
            draft = _extract_text(response).strip()
        except Exception as e:
            console.print(f"\n[error]LLM call failed: {e}[/error]\n")
            return

        if not draft or draft == "NO_PATTERN_FOUND":
            console.print("\n[warning]No clear repeatable pattern found in this session.[/warning]\n")
            return

        # Display draft in a panel
        console.print()
        console.print(Panel(draft, title="[cyan]Skill Draft[/cyan]", border_style="cyan", padding=(1, 2)))

        if subcommand == "suggest":
            # Just display — let user decide what to do next
            console.print("[dim]Use /crystallize create [name] to save this skill.[/dim]\n")
            return

        # /crystallize create — prompt for name and scope, then write
        name = sub_arg
        if not name:
            name = console.input("[cyan]Skill directory name[/cyan] (e.g. python-testing): ").strip()
            if not name:
                console.print("[warning]Cancelled — no name provided.[/warning]\n")
                return

        # Sanitize name: lowercase, only alphanumeric and hyphens
        import re as _re
        name = _re.sub(r'[^a-z0-9-]', '-', name.lower()).strip('-')
        if not name:
            console.print("[warning]Invalid skill name.[/warning]\n")
            return

        console.print("\n[dim]Scope: [cyan]g[/cyan]lobal (~/.agentao/skills/) or [cyan]p[/cyan]roject (.agentao/skills/)?[/dim]")
        console.print("[dim]Press g or p[/dim]", end=" ")
        while True:
            key = readchar.readkey()
            if key == "g":
                scope = "global"
                console.print("\n")
                break
            elif key == "p":
                scope = "project"
                console.print("\n")
                break
            elif key in (readchar.key.ESC, "\x03"):
                console.print("\n[warning]Cancelled.[/warning]\n")
                return

        crystallizer = SkillCrystallizer()
        try:
            target = crystallizer.create(name, scope, draft)
        except Exception as e:
            console.print(f"\n[error]Failed to write skill: {e}[/error]\n")
            return

        # Reload skills so the new one is immediately available
        try:
            count = self.agent.skill_manager.reload_skills()
        except Exception:
            count = None

        console.print(f"\n[success]Skill saved to:[/success] [cyan]{target}[/cyan]")
        if count is not None:
            console.print(f"[dim]Skills reloaded ({count} available). Activate with /skills activate {name}[/dim]\n")
        else:
            console.print(f"[dim]Activate with /skills activate {name}[/dim]\n")

    def show_memories(self, subcommand: str = "", arg: str = ""):
        """Show saved memories."""
        mgr = self.agent.memory_manager

        def _print_entry(e) -> None:
            console.print(f"  • [cyan]{e.title}[/cyan] [{e.scope}]: {e.content[:120]}")
            if e.tags:
                console.print(f"    Tags: {', '.join(e.tags)}")
            console.print(f"    Updated: {e.updated_at}")
            console.print()

        if subcommand in ["", "list"]:
            entries = mgr.get_all_entries()
            if not entries:
                console.print("\n[warning]No memories saved yet.[/warning]\n")
                return
            console.print(f"\n[info]Saved Memories ({len(entries)} total):[/info]\n")
            for e in entries:
                _print_entry(e)
            all_tags: dict = {}
            for e in entries:
                for tag in e.tags:
                    all_tags[tag] = all_tags.get(tag, 0) + 1
            if all_tags:
                console.print("[info]Tag Summary:[/info]")
                for tag, count in sorted(all_tags.items(), key=lambda x: -x[1]):
                    console.print(f"  [dim]#{tag}[/dim] ({count})")
                console.print()

        elif subcommand == "search":
            if not arg:
                console.print("\n[error]Usage: /memory search <query>[/error]\n")
                return
            results = mgr.search(arg)
            if not results:
                console.print(f"\n[warning]No memories found matching '{arg}'[/warning]\n")
                return
            console.print(f"\n[info]Found {len(results)} memory(ies) matching '{arg}':[/info]\n")
            for e in results:
                _print_entry(e)

        elif subcommand == "tag":
            if not arg:
                console.print("\n[error]Usage: /memory tag <tag_name>[/error]\n")
                return
            results = mgr.filter_by_tag(arg)
            if not results:
                console.print(f"\n[warning]No memories found with tag '{arg}'[/warning]\n")
                return
            console.print(f"\n[info]Found {len(results)} memory(ies) with tag '{arg}':[/info]\n")
            for e in results:
                _print_entry(e)

        elif subcommand == "delete":
            if not arg:
                console.print("\n[error]Usage: /memory delete <key>[/error]\n")
                return
            count = mgr.delete_by_title(arg)
            if not count:
                # Also try matching by normalized key (e.g. "user_preference" → "User Preference")
                for e in mgr.get_all_entries():
                    if e.key_normalized == arg or e.key_normalized == arg.lower().replace(" ", "_"):
                        if mgr.delete(e.id):
                            count += 1
            if count:
                console.print(f"\n[success]Successfully deleted memory: {arg}[/success]\n")
            else:
                console.print(f"\n[warning]Memory not found: {arg}[/warning]\n")

        elif subcommand == "clear":
            if Confirm.ask("\n[warning]Are you sure you want to delete ALL memories? This cannot be undone.[/warning]", default=False):
                count = mgr.clear()
                mgr.clear_all_session_summaries()
                console.print(f"\n[success]Successfully cleared {count} memory(ies)[/success]\n")
            else:
                console.print("\n[info]Cancelled.[/info]\n")

        elif subcommand == "user":
            entries = mgr.get_all_entries(scope="user")
            _display_layered_entries(entries, "[Profile Memory]", console)

        elif subcommand == "project":
            entries = mgr.get_all_entries(scope="project")
            _display_layered_entries(entries, "[Project Memory]", console)

        elif subcommand == "session":
            summaries = mgr.get_recent_session_summaries(limit=10)
            if summaries:
                combined = "\n\n---\n\n".join(s.summary_text for s in reversed(summaries))
                console.print(f"\n[info]Session Memory ({len(combined)} chars, {len(summaries)} summaries):[/info]\n")
                console.print(combined[-2000:] if len(combined) > 2000 else combined)
            else:
                console.print("\n[warning]No active session summary.[/warning]\n")

        elif subcommand == "crystallize":
            items = mgr.crystallize_user_messages(self.agent.messages)
            if not items:
                console.print("\n[warning]No crystallization candidates found in current conversation.[/warning]\n")
                return
            console.print(f"\n[info]Added/updated {len(items)} review queue item(s):[/info]\n")
            for it in items:
                console.print(f"  • [cyan]{it.title}[/cyan] [{it.type}, {it.scope}] occ={it.occurrences}")
                if it.evidence:
                    console.print(f"    [dim]Evidence:[/dim] {it.evidence[:120]}")
            console.print()

        elif subcommand == "review":
            parts = arg.split(maxsplit=1) if arg else [""]
            action = parts[0]
            target = parts[1] if len(parts) > 1 else ""
            if not action:
                items = mgr.list_review_items()
                if not items:
                    console.print("\n[warning]Review queue is empty.[/warning]\n")
                    return
                console.print(f"\n[info]Pending review items ({len(items)}):[/info]\n")
                for it in items:
                    console.print(f"  [{it.id}] [cyan]{it.title}[/cyan] {it.type}/{it.scope} occ={it.occurrences}")
                    if it.evidence:
                        console.print(f"      [dim]{it.evidence[:120]}[/dim]")
                console.print("\n  Approve: /memory review approve <id>")
                console.print("  Reject:  /memory review reject <id>\n")
            elif action == "approve" and target:
                rec = mgr.approve_review_item(target)
                if rec:
                    console.print(f"\n[success]Approved → memory '{rec.title}' (source=crystallized)[/success]\n")
                else:
                    console.print(f"\n[warning]No pending review item with id '{target}'[/warning]\n")
            elif action == "reject" and target:
                ok = mgr.reject_review_item(target)
                if ok:
                    console.print(f"\n[success]Rejected[/success]\n")
                else:
                    console.print(f"\n[warning]No pending review item with id '{target}'[/warning]\n")
            else:
                console.print("\n[error]Usage: /memory review [approve|reject <id>][/error]\n")

        elif subcommand == "status":
            user_entries = mgr.get_all_entries(scope="user")
            proj_entries = mgr.get_all_entries(scope="project")
            session_summaries = mgr.get_recent_session_summaries(limit=100)
            retriever = getattr(self.agent, 'memory_retriever', None)
            recall_count = retriever._recall_count if retriever else 0
            error_count = retriever._error_count if retriever else 0
            last_error = retriever._last_error if retriever else ""
            stable_chars = getattr(self.agent, '_stable_block_chars', 0)
            latest_summary = session_summaries[0].summary_text if session_summaries else ""
            session_chars = len(latest_summary)
            console.print("\n[info]Memory Status:[/info]")
            console.print(f"  Profile  (user):        {len(user_entries)} entries")
            console.print(f"  Project:                {len(proj_entries)} entries")
            console.print(f"  Session summaries:      {len(session_summaries)}")
            console.print(f"  Recall hits (session):  {recall_count}")
            console.print(f"  Recall errors (session):{error_count}")
            if last_error:
                console.print(f"  Last recall error:      {last_error}")
            console.print(f"  Stable block size:      {stable_chars} chars")
            console.print(f"  Latest session summary: {session_chars} chars\n")

        else:
            console.print(f"\n[error]Unknown subcommand: {subcommand}[/error]")
            console.print("[info]Available subcommands: list, search, tag, delete, clear, user, project, session, status, crystallize, review[/info]\n")

    def _list_providers_from_env(self) -> list:
        """Return sorted list of provider names that have an API key in environment."""
        providers = []
        for key, value in os.environ.items():
            if key.endswith("_API_KEY") and value:
                provider = key[: -len("_API_KEY")]
                providers.append(provider)
        return sorted(providers)

    def handle_provider_command(self, args: str):
        """Handle /provider command.

        Args:
            args: Provider name to switch to, or empty to list providers
        """
        args = args.strip().upper()

        if not args:
            # Show current provider and list all available
            current_model = self.agent.get_current_model()
            console.print(f"\n[info]Current Provider:[/info] [cyan]{self.current_provider}[/cyan]  "
                          f"[dim](model: {current_model})[/dim]\n")

            providers = self._list_providers_from_env()
            if not providers:
                console.print("[warning]No providers found in .env (looking for XXXX_API_KEY entries)[/warning]\n")
                return

            console.print("[info]Available Providers:[/info]")
            for p in providers:
                marker = " [green]✓[/green]" if p == self.current_provider else ""
                console.print(f"  • {p}{marker}")
            console.print("\n[info]Usage:[/info] /provider <NAME>  (e.g. /provider GEMINI)\n")

        else:
            # Switch to specified provider
            api_key = os.getenv(f"{args}_API_KEY")
            if not api_key:
                console.print(f"\n[error]No API key found for provider '{args}' "
                               f"(expected env var: {args}_API_KEY)[/error]\n")
                return

            base_url = os.getenv(f"{args}_BASE_URL") or None
            model = os.getenv(f"{args}_MODEL") or None

            self.agent.set_provider(api_key=api_key, base_url=base_url, model=model)
            self.current_provider = args

            current_model = self.agent.get_current_model()
            console.print(f"\n[success]Switched to provider: {args}[/success]")
            console.print(f"[info]Model:[/info] [cyan]{current_model}[/cyan]\n")

    def handle_model_command(self, args: str):
        """Handle model command.

        Args:
            args: Command arguments (model name or empty for list)
        """
        args = args.strip()

        if not args:
            # Show current model and available models
            current = self.agent.get_current_model()
            console.print(f"\n[info]Current Model:[/info] [cyan]{current}[/cyan]\n")
            try:
                with console.status("[dim]Fetching available models…[/dim]"):
                    available = self.agent.list_available_models()
            except RuntimeError as e:
                console.print(f"[error]Failed to list models: {e}[/error]\n")
                return

            console.print("[info]Available Models:[/info]\n")

            # Group by provider
            claude_models = [m for m in available if m.startswith("claude-")]
            gpt_models = [m for m in available if m.startswith("gpt-")]
            other_models = [m for m in available if not m.startswith(("claude-", "gpt-"))]

            if claude_models:
                console.print("  [bold]Claude:[/bold]")
                for model in claude_models:
                    marker = " [green]✓[/green]" if model == current else ""
                    console.print(f"    • {model}{marker}")

            if gpt_models:
                console.print("\n  [bold]OpenAI GPT:[/bold]")
                for model in gpt_models:
                    marker = " [green]✓[/green]" if model == current else ""
                    console.print(f"    • {model}{marker}")

            if other_models:
                console.print("\n  [bold]Other:[/bold]")
                for model in other_models:
                    marker = " [green]✓[/green]" if model == current else ""
                    console.print(f"    • {model}{marker}")

            console.print("\n[info]Usage:[/info] /model <model_name>")
            console.print("Example: /model claude-sonnet-4-5\n")

        else:
            # Switch to specified model
            result = self.agent.set_model(args)
            console.print(f"\n[success]{result}[/success]\n")

    def handle_temperature_command(self, args: str):
        """Handle /temperature command — show or set LLM temperature."""
        args = args.strip()
        if not args:
            console.print(f"\n[info]Temperature:[/info] [cyan]{self.agent.llm.temperature}[/cyan]")
            console.print("[dim]Usage: /temperature <value>  (0.0 - 2.0)[/dim]\n")
            return
        try:
            value = float(args)
        except ValueError:
            console.print(f"\n[error]Invalid temperature value: {args}[/error]\n")
            return
        if not 0.0 <= value <= 2.0:
            console.print("\n[error]Temperature must be between 0.0 and 2.0[/error]\n")
            return
        old = self.agent.llm.temperature
        self.agent.llm.temperature = value
        console.print(f"\n[success]Temperature changed from {old} to {value}[/success]\n")

    def _show_agents_dashboard(self) -> None:
        """Render a live auto-refreshing table of all background agents."""
        import time as _time
        from rich.live import Live
        from rich.table import Table
        from rich import box as rich_box
        from rich.text import Text
        from rich.panel import Panel
        from .agents.tools import list_bg_tasks

        def _fmt_status(t: dict) -> Text:
            status = t["status"]
            if status == "pending":
                return Text("◌  queued", style="dim")
            if status == "running":
                started = t.get("started_at")
                elapsed = _time.time() - started if started else 0
                return Text(f"○  {elapsed:.0f}s", style="yellow")
            if status == "completed":
                ms = t.get("duration_ms", 0)
                turns = t.get("turns", 0)
                calls = t.get("tool_calls", 0)
                tok = t.get("tokens", 0)
                tok_s = f"~{tok // 1000}k" if tok >= 1000 else str(tok)
                dur_s = f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"
                return Text(f"✓  {turns}t {calls}c {tok_s}  {dur_s}", style="green")
            if status == "cancelled":
                return Text("⊘  cancelled", style="dim")
            # failed
            return Text("✗  failed", style="red")

        def _make_panel() -> Panel:
            tasks = list_bg_tasks()

            n_run    = sum(1 for t in tasks if t["status"] == "running")
            n_ok     = sum(1 for t in tasks if t["status"] == "completed")
            n_err    = sum(1 for t in tasks if t["status"] == "failed")
            n_cancel = sum(1 for t in tasks if t["status"] == "cancelled")

            tbl = Table(box=rich_box.SIMPLE, show_header=True, pad_edge=False,
                        header_style="bold dim")
            tbl.add_column("ID",     style="cyan",   width=9)
            tbl.add_column("Agent",  style="bold",   min_width=22, no_wrap=True)
            tbl.add_column("Status", min_width=22)
            tbl.add_column("Task",   style="dim",    ratio=1)

            for t in sorted(tasks, key=lambda x: x.get("created_at", 0), reverse=True):
                status_cell = _fmt_status(t)
                err_hint = ""
                if t["status"] == "failed" and t.get("error"):
                    err_hint = f"  [dim red]{str(t['error'])[:60]}[/dim red]"
                task_cell = (t.get("task", "")[:55] or "") + err_hint
                tbl.add_row(t["id"], t["agent_name"], status_cell, task_cell)

            summary = (
                f"[yellow]○ {n_run} running[/yellow]  "
                f"[green]✓ {n_ok} completed[/green]  "
                f"{'[red]' if n_err else '[dim]'}✗ {n_err} failed{'[/red]' if n_err else '[/dim]'}  "
                f"[dim]⊘ {n_cancel} cancelled[/dim]"
            )
            footer = "[dim]Press Ctrl+C to exit[/dim]" if n_run else ""
            title = f"Background Agents  ·  {summary}"
            return Panel(tbl, title=title, subtitle=footer, border_style="cyan")

        tasks = list_bg_tasks()
        if not tasks:
            console.print("\n[dim]No background agents in this session.[/dim]\n")
            return

        active_statuses = {"pending", "running"}
        has_active = any(t["status"] in active_statuses for t in tasks)
        if not has_active:
            console.print()
            console.print(_make_panel())
            console.print()
            return

        # Live view — auto-refreshes while agents are queued or running
        try:
            with Live(_make_panel(), console=console, refresh_per_second=2,
                      vertical_overflow="visible") as live:
                while True:
                    _time.sleep(0.5)
                    live.update(_make_panel())
                    if not any(t["status"] in active_statuses for t in list_bg_tasks()):
                        _time.sleep(0.3)   # final render
                        live.update(_make_panel())
                        break
        except KeyboardInterrupt:
            pass
        console.print()

    def handle_agent_command(self, args: str):
        """Handle /agent command.

        Subcommands:
          /agent                      — list available agents
          /agent list                 — list available agents
          /agent dashboard            — live background-agent dashboard
          /agent status [id]          — show background agent status (all or specific)
          /agent cancel <id>          — cancel a pending or running background agent
          /agent delete <id>          — delete a finished background agent from history
          /agent <name> <task>        — run agent in foreground
          /agent bg <name> <task>     — run agent in background
        """
        from .agents.tools import list_bg_tasks, get_bg_task
        import time as _time

        args = args.strip()
        parts = args.split(None, 1)
        sub = parts[0] if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        # ── /agent  or  /agent list ─────────────────────────────────────────
        if not sub or sub == "list":
            if not self.agent.agent_manager:
                console.print("\n[warning]No agent manager available.[/warning]\n")
                return
            agents = self.agent.agent_manager.list_agents()
            if not agents:
                console.print("\n[warning]No agents defined.[/warning]\n")
                return
            console.print(f"\n[info]Available Agents ({len(agents)}):[/info]\n")
            for name, desc in agents.items():
                console.print(f"  [cyan]{name}[/cyan]  [dim]{desc}[/dim]")
            console.print(
                "\n[dim]Usage: /agent <name> <task>  |  /agent bg <name> <task>"
                "  |  /agent dashboard[/dim]\n"
            )
            return

        # ── /agent dashboard  (or /agents) ──────────────────────────────────
        if sub in ("dashboard", "dash"):
            self._show_agents_dashboard()
            return

        # ── /agent status [id] ───────────────────────────────────────────────
        if sub == "status":
            agent_id = rest
            if not agent_id:
                tasks = list_bg_tasks()
                if not tasks:
                    console.print("\n[dim]No background agents in this session.[/dim]\n")
                    return
                console.print(f"\n[info]Background Agents ({len(tasks)}):[/info]\n")
                for t in tasks:
                    status = t["status"]
                    color = (
                        "dim" if status in ("pending", "cancelled")
                        else "yellow" if status == "running"
                        else "green" if status == "completed"
                        else "red"
                    )
                    started = t.get("started_at")
                    finished = t.get("finished_at")
                    if finished and started:
                        elapsed = f"{finished - started:.1f}s"
                    elif started:
                        elapsed = f"{_time.time() - started:.0f}s"
                    elif status == "cancelled" and finished:
                        elapsed = "cancelled before start"
                    else:
                        elapsed = "queued"
                    console.print(
                        f"  [{color}]{status:<10}[/{color}]  [cyan]{t['id']}[/cyan]"
                        f"  [bold]{t['agent_name']}[/bold]  ({elapsed})"
                        f"  [dim]{t['task'][:60]}[/dim]"
                    )
                console.print()
            else:
                rec = get_bg_task(agent_id)
                if rec is None:
                    console.print(f"\n[error]No background agent with ID: {agent_id}[/error]\n")
                    return
                status = rec["status"]
                color = (
                    "dim" if status in ("pending", "cancelled")
                    else "yellow" if status == "running"
                    else "green" if status == "completed"
                    else "red"
                )
                console.print(f"\n[info]Agent:[/info] [bold]{rec['agent_name']}[/bold]  ID: [cyan]{agent_id}[/cyan]")
                console.print(f"[info]Status:[/info] [{color}]{status}[/{color}]")
                console.print(f"[info]Task:[/info]   {rec['task']}")
                if rec.get("finished_at") and rec.get("started_at"):
                    elapsed = rec["finished_at"] - rec["started_at"]
                    console.print(f"[info]Time:[/info]   {elapsed:.1f}s")
                elif status == "cancelled" and rec.get("finished_at") and rec.get("started_at") is None:
                    console.print("[info]Time:[/info]   cancelled before start")
                elif rec.get("started_at") is None:
                    console.print("[info]Time:[/info]   not started yet")
                if status == "completed" and rec.get("result"):
                    console.print("\n[info]Result:[/info]")
                    console.print(Markdown(rec["result"]))
                elif status == "failed" and rec.get("error"):
                    console.print(f"\n[error]Error:[/error] {rec['error']}")
                elif status == "cancelled":
                    console.print("\n[dim]Agent was cancelled.[/dim]")
                console.print()
            return

        # ── /agent cancel <id> ───────────────────────────────────────────────
        if sub == "cancel":
            agent_id = rest.strip()
            if not agent_id:
                console.print("\n[error]Usage: /agent cancel <agent-id>[/error]\n")
                return
            from .agents.tools import _cancel_bg_task
            msg = _cancel_bg_task(agent_id)
            console.print(f"\n{msg}\n")
            return

        # ── /agent delete <id> ───────────────────────────────────────────────
        if sub == "delete":
            agent_id = rest.strip()
            if not agent_id:
                console.print("\n[error]Usage: /agent delete <agent-id>[/error]\n")
                return
            from .agents.tools import _delete_bg_task
            msg = _delete_bg_task(agent_id)
            console.print(f"\n{msg}\n")
            return

        # ── /agent bg <name> <task> ──────────────────────────────────────────
        if sub == "bg":
            bg_parts = rest.split(None, 1)
            if len(bg_parts) < 2:
                console.print("\n[error]Usage: /agent bg <agent-name> <task>[/error]\n")
                return
            agent_name, task = bg_parts[0], bg_parts[1]
            tool_name = f"agent_{agent_name.replace('-', '_')}"
            try:
                tool = self.agent.tools.get(tool_name)
            except KeyError:
                console.print(f"\n[error]Unknown agent: {agent_name}[/error]\n")
                return
            msg = tool.execute(task=task, run_in_background=True)
            console.print(f"\n[cyan]{msg}[/cyan]\n")
            return

        # ── /agent <name> <task>  (foreground) ──────────────────────────────
        agent_name = sub
        if not rest:
            console.print(f"\n[error]Usage: /agent {agent_name} <task description>[/error]\n")
            return

        tool_name = f"agent_{agent_name.replace('-', '_')}"
        try:
            tool = self.agent.tools.get(tool_name)
        except KeyError:
            console.print(f"\n[error]Unknown agent: {agent_name}[/error]")
            available = ", ".join(self.agent.agent_manager.list_agents().keys()) if self.agent.agent_manager else ""
            console.print(f"[info]Available: {available}[/info]\n")
            return

        self.current_status = console.status(
            f"[bold cyan][{agent_name}] Thinking...[/bold cyan]", spinner="dots"
        )
        with self.current_status:
            result = tool.execute(task=rest)

        console.print(Markdown(result))

    def handle_context_command(self, args: str):
        """Handle /context command.

        Args:
            args: Empty for status, 'limit <n>' to set token limit
        """
        args = args.strip()
        cm = self.agent.context_manager

        if not args:
            stats = cm.get_usage_stats(self.agent.messages)
            console.print("\n[info]Context Window Status:[/info]")
            console.print(f"  Estimated tokens: [cyan]{stats['estimated_tokens']:,}[/cyan]")
            console.print(f"  Max tokens:       [cyan]{stats['max_tokens']:,}[/cyan]")

            pct = stats["usage_percent"]
            color = "green" if pct < 55 else "yellow" if pct < 65 else "red"
            console.print(f"  Usage:            [{color}]{pct:.1f}%[/{color}]")
            console.print(f"  Messages:         {stats['message_count']}")

            # Circuit breaker warning
            failures = stats.get("circuit_breaker_failures", 0)
            if failures > 0:
                fb_color = "yellow" if failures < cm.CIRCUIT_BREAKER_LIMIT else "red"
                console.print(
                    f"  Compact failures: [{fb_color}]{failures}/{cm.CIRCUIT_BREAKER_LIMIT}[/{fb_color}]"
                    + (" [dim](circuit open — auto-compact disabled)[/dim]"
                       if failures >= cm.CIRCUIT_BREAKER_LIMIT else "")
                )

            # Last compact metadata
            lc = stats.get("last_compact")
            if lc:
                pre = lc.get("pre_compact_tokens", 0)
                post = lc.get("post_compact_tokens", 0)
                summarized = lc.get("messages_summarized", 0)
                kept = lc.get("messages_kept", 0)
                ts = lc.get("timestamp", "")[:19]
                console.print(
                    f"  Last compact:     {ts}  "
                    f"[dim]{pre:,} → {post:,} tokens | "
                    f"{summarized} summarized, {kept} kept[/dim]"
                )
                files = lc.get("recently_read_files", [])
                if files:
                    console.print(f"  Re-injected files: [dim]{', '.join(files[:5])}[/dim]")
            console.print()

        elif args.startswith("limit "):
            limit_str = args[6:].strip()
            try:
                new_limit = int(limit_str)
                if new_limit < 1000:
                    console.print("\n[error]Context limit must be at least 1,000 tokens[/error]\n")
                    return
                cm.max_tokens = new_limit
                console.print(f"\n[success]Context limit set to {new_limit:,} tokens[/success]\n")
            except ValueError:
                console.print(f"\n[error]Invalid number: {limit_str}[/error]\n")
        else:
            console.print("\n[error]Usage: /context  OR  /context limit <n>[/error]\n")

    def handle_mcp_command(self, args: str):
        """Handle /mcp command for MCP server management.

        Args:
            args: Subcommand and arguments
        """
        from .mcp.config import load_mcp_config, save_mcp_config, _load_json_file
        from pathlib import Path

        args = args.strip()
        parts = args.split(None, 1) if args else []
        sub = parts[0] if parts else "list"
        sub_args = parts[1] if len(parts) > 1 else ""

        if sub == "list":
            manager = self.agent.mcp_manager
            if not manager or not manager.clients:
                console.print("\n[warning]No MCP servers configured.[/warning]")
                console.print("[info]Add servers to .agentao/mcp.json or use /mcp add[/info]\n")
                return

            statuses = manager.get_server_status()
            console.print(f"\n[info]MCP Servers ({len(statuses)}):[/info]\n")
            for s in statuses:
                color = "green" if s["status"] == "connected" else "red"
                trust_marker = " [dim](trusted)[/dim]" if s["trusted"] else ""
                console.print(
                    f"  [{color}]●[/{color}] [cyan]{s['name']}[/cyan] "
                    f"[dim]{s['transport']}[/dim] — "
                    f"[{color}]{s['status']}[/{color}], "
                    f"{s['tools']} tool(s){trust_marker}"
                )
                if s["error"]:
                    console.print(f"    [red]{s['error']}[/red]")
            console.print()

        elif sub == "add":
            # /mcp add <name> <command|url> [args...]
            add_parts = sub_args.split(None, 1) if sub_args else []
            if len(add_parts) < 2:
                console.print("\n[error]Usage: /mcp add <name> <command|url> [args...][/error]")
                console.print("[info]Examples:[/info]")
                console.print("  /mcp add github npx -y @modelcontextprotocol/server-github")
                console.print("  /mcp add remote https://api.example.com/sse\n")
                return

            name = add_parts[0]
            endpoint = add_parts[1]

            # Determine transport from endpoint
            if endpoint.startswith("http://") or endpoint.startswith("https://"):
                server_cfg = {"url": endpoint}
            else:
                # Stdio: split into command + args
                cmd_parts = endpoint.split()
                server_cfg = {"command": cmd_parts[0]}
                if len(cmd_parts) > 1:
                    server_cfg["args"] = cmd_parts[1:]

            # Load current project config and add
            project_path = Path.cwd() / ".agentao" / "mcp.json"
            existing = _load_json_file(project_path)
            servers = existing.get("mcpServers", {})
            servers[name] = server_cfg
            saved_path = save_mcp_config(servers)

            console.print(f"\n[success]Added MCP server '{name}' to {saved_path}[/success]")
            console.print("[info]Restart agentao to connect to the new server.[/info]\n")

        elif sub == "remove":
            name = sub_args.strip()
            if not name:
                console.print("\n[error]Usage: /mcp remove <name>[/error]\n")
                return

            project_path = Path.cwd() / ".agentao" / "mcp.json"
            existing = _load_json_file(project_path)
            servers = existing.get("mcpServers", {})
            if name not in servers:
                console.print(f"\n[warning]Server '{name}' not found in config.[/warning]\n")
                return

            del servers[name]
            save_mcp_config(servers)
            console.print(f"\n[success]Removed MCP server '{name}'.[/success]")
            console.print("[info]Restart agentao to apply changes.[/info]\n")

        else:
            console.print(f"\n[error]Unknown subcommand: {sub}[/error]")
            console.print("[info]Available: /mcp list, /mcp add, /mcp remove[/info]\n")

    def handle_permission_command(self, args: str):
        """Handle /permission command — show active permission rules."""
        console.print(f"\n{self.permission_engine.get_rules_display()}\n")

    def handle_sessions_command(self, args: str):
        """Handle /sessions command.

        Args:
            args: Subcommand: list | resume <id> | delete <id>
        """
        from .session import list_sessions, delete_session, delete_all_sessions

        args = args.strip()
        parts = args.split(None, 1) if args else []
        sub = parts[0] if parts else "list"
        sub_arg = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("", "list"):
            sessions = list_sessions()
            if not sessions:
                console.print("\n[warning]No saved sessions found.[/warning]\n")
                return
            console.print(f"\n[info]Saved Sessions ({len(sessions)}):[/info]\n")
            for s in sessions:
                sid = s.get("session_id")
                short_id = sid[:8] if sid else s["id"]
                console.print(f"  • [cyan]{short_id}[/cyan]")
                if s.get("title"):
                    console.print(f"    [bold]{s['title']}[/bold]")
                console.print(f"    Model: [dim]{s['model']}[/dim]  Messages: {s['message_count']}")
                if s.get("created_at"):
                    console.print(f"    Created: {s['created_at'][:19]}  Updated: {s.get('updated_at', '')[:19]}")
                else:
                    console.print(f"    Saved: {s['timestamp']}")
                if s["active_skills"]:
                    console.print(f"    Skills: {', '.join(s['active_skills'])}")
                console.print()
            console.print("[info]Usage:[/info] /sessions resume <id>  or  /sessions delete <id>  or  /sessions delete all\n")

        elif sub == "resume":
            self.resume_session(sub_arg or None)

        elif sub == "delete":
            if sub_arg == "all":
                sessions = list_sessions()
                if not sessions:
                    console.print("\n[warning]No saved sessions to delete.[/warning]\n")
                    return
                console.print(f"\n[warning]Delete all {len(sessions)} session(s)? Press 1 to confirm, any other key to cancel.[/warning]")
                import readchar
                key = readchar.readkey()
                if key == "1":
                    count = delete_all_sessions()
                    console.print(f"\n[success]Deleted {count} session(s).[/success]\n")
                else:
                    console.print("\n[info]Cancelled.[/info]\n")
                return
            if not sub_arg:
                console.print("\n[error]Usage: /sessions delete <session-id>  or  /sessions delete all[/error]\n")
                return
            if delete_session(sub_arg):
                console.print(f"\n[success]Session '{sub_arg}' deleted.[/success]\n")
            else:
                console.print(f"\n[warning]Session '{sub_arg}' not found.[/warning]\n")

        else:
            console.print(f"\n[error]Unknown subcommand: {sub}[/error]")
            console.print("[info]Available: /sessions list | /sessions resume <id> | /sessions delete <id> | /sessions delete all[/info]\n")

    def resume_session(self, session_id: Optional[str] = None):
        """Load a previously saved session into the current agent.

        Args:
            session_id: UUID (or prefix), timestamp prefix, or None for latest.
        """
        from .session import list_sessions, load_session

        # Resolve metadata first so we can display title and capture the stable UUID.
        sessions = list_sessions()
        if not sessions:
            console.print("\n[error]No saved sessions found.[/error]\n")
            return

        if session_id:
            match = next(
                (s for s in sessions
                 if (s.get("session_id") or "").startswith(session_id)
                 or s["id"].startswith(session_id)),
                None,
            )
            if not match:
                console.print(f"\n[error]Session '{session_id}' not found.[/error]\n")
                return
        else:
            match = sessions[0]  # newest

        try:
            messages, model, active_skills = load_session(match["id"])
        except FileNotFoundError as e:
            console.print(f"\n[error]Could not resume session: {e}[/error]\n")
            return

        self.agent.messages = messages
        if model:
            try:
                self.agent.set_model(model)
            except Exception:
                pass
        for skill_name in active_skills:
            try:
                self.agent.skill_manager.activate_skill(skill_name, "Restored from session")
            except Exception:
                pass

        # For legacy sessions without a session_id, mint a new UUID so the resumed
        # conversation gets a stable identity for subsequent saves.
        self.current_session_id = match.get("session_id") or str(_uuid_mod.uuid4())
        self.agent._session_id = self.current_session_id
        self.agent.tool_runner._session_id = self.current_session_id
        sid_display = self.current_session_id[:8]
        title_display = f": {match['title']}" if match.get("title") else ""
        msg_count = len(messages)
        console.print(f"\n[success]↩ Resuming session {sid_display}{title_display}[/success]")
        console.print(f"[dim]{msg_count} messages loaded.[/dim]")
        if model:
            console.print(f"[dim]Model: {model}[/dim]")
        if active_skills:
            console.print(f"[dim]Active skills: {', '.join(active_skills)}[/dim]")
        console.print()

    def handle_tools_command(self, args: str):
        """Handle /tools command.

        Args:
            args: Optional tool name to inspect. Empty to list all tools.
        """
        import json

        args = args.strip()
        all_tools = self.agent.tools.list_tools()

        if not args:
            console.print(f"\n[info]Registered Tools ({len(all_tools)}):[/info]\n")
            for tool in sorted(all_tools, key=lambda t: t.name):
                confirm = "  [warning]⚠ confirm[/warning]" if tool.requires_confirmation else ""
                console.print(f"  [cyan]{tool.name}[/cyan]{confirm}")
                console.print(f"    [dim]{tool.description}[/dim]")
            console.print()
            console.print("[dim]Use /tools <name> to see parameter schema.[/dim]\n")
        else:
            try:
                tool = self.agent.tools.get(args)
            except KeyError:
                console.print(f"\n[error]Tool '{args}' not found.[/error]\n")
                return
            console.print(f"\n[info]{tool.name}[/info]")
            console.print(f"[dim]{tool.description}[/dim]")
            if tool.requires_confirmation:
                console.print("[warning]Requires user confirmation before execution[/warning]")
            console.print("\n[dim]Parameters schema:[/dim]")
            console.print(json.dumps(tool.parameters, indent=2, ensure_ascii=False))
            console.print()

    def on_session_start(self) -> None:
        """Hook called at the start of every session."""
        # Ensure a session ID exists (e.g. after /new or /clear reset it to None).
        if self.current_session_id is None:
            self.current_session_id = str(_uuid_mod.uuid4())
        self.agent._session_id = self.current_session_id
        self.agent.tool_runner._session_id = self.current_session_id

        try:
            self.agent.memory_manager.archive_session()
        except Exception:
            pass

        # Dispatch SessionStart plugin hooks now that the session ID is final.
        self._dispatch_session_start_hooks()

    def on_session_end(self) -> None:
        """Hook called at the end of every session (before /clear, /new, or exit).

        Override or extend in a subclass to add custom session-end behavior.
        Default implementation saves the current session to disk and dispatches
        SessionEnd plugin hooks.
        """
        # Dispatch SessionEnd hooks before saving so plugins can export/clean up.
        self._dispatch_session_end_hooks()

        if not self.agent.messages:
            return
        from .session import save_session
        try:
            active_skills = list(self.agent.skill_manager.get_active_skills().keys())
            session_file, sid = save_session(
                messages=self.agent.messages,
                model=self.agent.get_current_model(),
                active_skills=active_skills,
                session_id=self.current_session_id,
            )
            self.current_session_id = sid
            console.print(f"[dim]Session saved → {sid[:8]} ({session_file.name})[/dim]")
        except Exception:
            pass  # Non-critical

    def _dispatch_session_start_hooks(self) -> None:
        """Fire SessionStart plugin hooks with the current (final) session ID."""
        if not self.agent._plugin_hook_rules:
            return
        try:
            from .plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
            _cwd = self.agent.working_directory
            adapter = ClaudeHookPayloadAdapter()
            payload = adapter.build_session_start(
                session_id=self.current_session_id, cwd=_cwd,
            )
            dispatcher = PluginHookDispatcher(cwd=_cwd)
            dispatcher.dispatch_session_start(
                payload=payload, rules=self.agent._plugin_hook_rules,
            )
        except Exception:
            pass  # Best-effort

    def _dispatch_session_end_hooks(self) -> None:
        """Fire SessionEnd plugin hooks with the current session ID."""
        if not self.agent._plugin_hook_rules:
            return
        try:
            from .plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
            _cwd = self.agent.working_directory
            adapter = ClaudeHookPayloadAdapter()
            payload = adapter.build_session_end(
                session_id=self.current_session_id, cwd=_cwd,
            )
            dispatcher = PluginHookDispatcher(cwd=_cwd)
            dispatcher.dispatch_session_end(
                payload=payload, rules=self.agent._plugin_hook_rules,
            )
        except Exception:
            pass  # Best-effort

    def _save_session_on_exit(self):
        """Internal helper; delegates to on_session_end()."""
        self.on_session_end()

    def _get_user_input(self) -> str:
        """Read user input using prompt_toolkit.

        multiline=True captures pasted multi-line text in one shot.
        Enter submits; Meta/Alt+Enter inserts a literal newline.
        prompt_toolkit's wcwidth support correctly handles CJK characters on macOS.

        A background thread fires app.invalidate() every second so the
        bottom_toolbar (elapsed time, agent count) refreshes in real time.
        """
        import threading
        from prompt_toolkit.application.current import get_app_or_none
        from .permissions import PermissionMode

        if self._plan_session.is_active:
            prompt = ANSI("\n\033[1;35m[plan]\033[0m \033[1;36m❯\033[0m ")
        elif self.current_mode == PermissionMode.READ_ONLY:
            prompt = ANSI("\n\033[1;31m[read-only]\033[0m \033[1;36m❯\033[0m ")
        else:
            prompt = ANSI("\n\033[1;36m❯\033[0m ")

        stop = threading.Event()
        app_ref: list = []  # set by pre_run (main thread); read by ticker thread

        def _pre_run() -> None:
            _app = get_app_or_none()
            if _app:
                app_ref.append(_app)

        def _ticker() -> None:
            while not stop.wait(1.0):
                if app_ref:
                    app_ref[0].invalidate()

        ticker = threading.Thread(target=_ticker, daemon=True)
        ticker.start()
        try:
            return self._prompt_session.prompt(prompt, pre_run=_pre_run)
        finally:
            stop.set()

    def _get_status_toolbar(self) -> ANSI:
        """Bottom status bar: a blue separator line above dim status text (like Claude Code)."""
        from .agents.tools import list_bg_tasks
        from .permissions import PermissionMode

        RST = "\x1b[0m"
        DIM = "\x1b[2m"

        try:
            model = self.agent.get_current_model().split("/")[-1]
        except Exception:
            model = "—"

        if self._plan_session.is_active:
            mode_col, mode_text = "\x1b[95m", "plan"
        elif self.current_mode == PermissionMode.READ_ONLY:
            mode_col, mode_text = "\x1b[91m", "read-only"
        elif self.current_mode == PermissionMode.FULL_ACCESS:
            mode_col, mode_text = "\x1b[92m", "full-access"
        else:
            mode_col, mode_text = "\x1b[96m", "workspace-write"

        pct = self._cached_ctx_pct
        if pct >= 80:
            ctx_col = "\x1b[91m"
        elif pct >= 50:
            ctx_col = "\x1b[93m"
        else:
            ctx_col = "\x1b[37m"

        try:
            tasks = list_bg_tasks()
            if tasks:
                import time as _time
                tokens = []
                for t in tasks:
                    name = t.get("agent_name", "agent").replace("_", "-")
                    st = t.get("status", "running")
                    if st == "pending":
                        tokens.append(f"\x1b[2m⏳ {name} queued\x1b[0m")
                    elif st == "running":
                        started = t.get("started_at")
                        elapsed = int(_time.time() - started) if started else 0
                        tokens.append(f"\x1b[93m⚙ {name} {elapsed}s\x1b[0m")
                    elif st == "completed":
                        tokens.append(f"\x1b[32m✓ {name}\x1b[0m")
                    elif st == "cancelled":
                        tokens.append(f"\x1b[2m⊘ {name}\x1b[0m")
                    else:  # failed
                        tokens.append(f"\x1b[91m✗ {name}\x1b[0m")
                agents_part = f"  {DIM}│{RST}  " + f"  {DIM}·{RST}  ".join(tokens)
            else:
                agents_part = ""
        except Exception:
            agents_part = ""

        # Row 1: blue horizontal rule (long enough to fill any terminal width)
        rule = "\x1b[34m" + "─" * 300 + RST
        # Row 2: status items on default background
        sep = f"  {DIM}│{RST}  "
        cwd = Path.cwd().name or str(Path.cwd())
        status = (
            f" {DIM}{model}{RST}"
            f"{sep}{mode_col}{mode_text}{RST}"
            f"{sep}{ctx_col}ctx {pct:.0f}%{RST}"
            f"{sep}{DIM}{cwd}{RST}"
            f"{agents_part}"
        )
        return ANSI(f"{rule}\n{status}")

    def run(self):
        """Run the CLI."""
        self.print_welcome()

        try:
            self._run_loop()
        finally:
            self.agent.close()

    def _run_loop(self):
        """Main input loop."""
        self.on_session_start()
        while True:
            try:
                # Get user input
                user_input = self._get_user_input()

                if not user_input.strip():
                    continue

                # Handle commands (all start with /)
                input_text = user_input.strip()

                # Check if it's a command
                if input_text.startswith('/'):
                    # Split command and arguments
                    parts = input_text[1:].split(maxsplit=1)
                    command = parts[0].lower()
                    args = parts[1] if len(parts) > 1 else ""

                    if command in ["exit", "quit"]:
                        self._save_session_on_exit()
                        console.print("\n[success]Goodbye![/success]\n")
                        break

                    elif command == "help":
                        self.print_help()
                        continue

                    elif command == "clear":
                        self.on_session_end()
                        self.current_session_id = None
                        if self._plan_session.is_active:
                            self._plan_controller.exit_plan_mode()
                        self.agent.clear_history()
                        self.agent.memory_manager.clear()
                        self.agent.memory_manager.clear_all_session_summaries()
                        self.last_response = None
                        self._cached_ctx_pct = 0.0
                        from .permissions import PermissionMode
                        self._apply_mode(PermissionMode.WORKSPACE_WRITE)
                        self.on_session_start()
                        console.print("\n[success]Session and all memories cleared.[/success]")
                        console.print("[info]Permission mode reset to workspace-write.[/info]\n")
                        continue

                    elif command == "new":
                        self.on_session_end()
                        self.current_session_id = None
                        if self._plan_session.is_active:
                            self._plan_controller.exit_plan_mode()
                        self.agent.clear_history()
                        # NOTE: do NOT call clear_session() here. It would
                        # delete the just-finished session's summaries before
                        # on_session_start() advances the session id, which
                        # would prevent them from surfacing via
                        # get_cross_session_tail() in the new session. The
                        # archive_session() inside on_session_start() is the
                        # correct primitive for /new — it advances _session_id
                        # without touching old rows.
                        self.last_response = None
                        self._cached_ctx_pct = 0.0
                        from .permissions import PermissionMode
                        self._apply_mode(PermissionMode.WORKSPACE_WRITE)
                        self.on_session_start()
                        console.print("\n[success]New session started. Long-term memories preserved.[/success]")
                        console.print("[info]Permission mode reset to workspace-write.[/info]\n")
                        continue

                    elif command == "status":
                        self.show_status()
                        continue

                    elif command == "skills":
                        if not args:
                            self.list_skills()
                        else:
                            sub_parts = args.split(maxsplit=1)
                            sub_cmd = sub_parts[0]
                            sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""
                            if sub_cmd == "activate":
                                if not sub_arg:
                                    console.print("[warning]Usage: /skills activate <skill_name>[/warning]")
                                else:
                                    result = self.agent.skill_manager.activate_skill(
                                        sub_arg, "Manually activated via /skills activate"
                                    )
                                    if result.startswith("Error"):
                                        console.print(f"\n[warning]{result}[/warning]\n")
                                    else:
                                        console.print(f"\n[success]Skill '{sub_arg}' activated.[/success]\n")
                            elif sub_cmd == "deactivate":
                                if not sub_arg:
                                    console.print("[warning]Usage: /skills deactivate <skill_name>[/warning]")
                                elif sub_arg not in self.agent.skill_manager.available_skills:
                                    available = ", ".join(sorted(self.agent.skill_manager.list_available_skills()))
                                    console.print(f"[warning]Unknown skill '{sub_arg}'. Available: {available}[/warning]")
                                else:
                                    deactivated = self.agent.skill_manager.deactivate_skill(sub_arg)
                                    if deactivated:
                                        console.print(f"\n[success]Skill '{sub_arg}' deactivated.[/success]\n")
                                    else:
                                        console.print(f"\n[info]Skill '{sub_arg}' is not currently active.[/info]\n")
                            elif sub_cmd == "disable":
                                if not sub_arg:
                                    console.print("[warning]Usage: /skills disable <skill_name>[/warning]")
                                else:
                                    result = self.agent.skill_manager.disable_skill(sub_arg)
                                    console.print(f"\n{result}\n")
                            elif sub_cmd == "enable":
                                if not sub_arg:
                                    console.print("[warning]Usage: /skills enable <skill_name>[/warning]")
                                else:
                                    result = self.agent.skill_manager.enable_skill(sub_arg)
                                    console.print(f"\n{result}\n")
                            elif sub_cmd == "reload":
                                self.agent.skill_manager.reload_skills()
                                count = len(self.agent.skill_manager.list_available_skills())
                                console.print(f"\n[success]Skills reloaded. {count} available.[/success]\n")
                            else:
                                console.print(f"[warning]Unknown subcommand '{sub_cmd}'. Use: activate, deactivate, disable, enable, reload[/warning]")
                        continue

                    elif command == "crystallize":
                        self.handle_crystallize_command(args)
                        continue

                    elif command == "memory":
                        # Parse subcommand and arguments
                        if args:
                            subcommand_parts = args.split(maxsplit=1)
                            subcommand = subcommand_parts[0]
                            subcommand_arg = subcommand_parts[1] if len(subcommand_parts) > 1 else ""
                            self.show_memories(subcommand, subcommand_arg)
                        else:
                            self.show_memories()
                        continue

                    elif command == "model":
                        self.handle_model_command(args)
                        continue

                    elif command == "provider":
                        self.handle_provider_command(args)
                        continue

                    elif command == "context":
                        self.handle_context_command(args)
                        continue

                    elif command == "mcp":
                        self.handle_mcp_command(args)
                        continue

                    elif command in ("plugins", "plugin"):
                        _handle_plugins_interactive()
                        continue

                    elif command == "agent":
                        self.handle_agent_command(args)
                        continue

                    elif command == "agents":
                        # /agents → shorthand for /agent dashboard
                        self._show_agents_dashboard()
                        continue

                    elif command == "mode":
                        from .permissions import PermissionMode
                        # Exclude PLAN — it is an internal mode managed by /plan, not user-settable.
                        _valid = {m.value: m for m in PermissionMode if m != PermissionMode.PLAN}
                        if args == "":
                            console.print(f"\n[info]Permission mode:[/info] {self.current_mode.value}\n")
                        elif args in _valid:
                            if self._plan_session.is_active:
                                console.print("\n[warning]Cannot change permission mode while in plan mode.[/warning]")
                                console.print("[dim]Exit plan mode first with /plan implement or /plan clear.[/dim]\n")
                            else:
                                self._apply_mode(_valid[args])
                                _descriptions = {
                                    "read-only":       "write & shell tools are blocked",
                                    "workspace-write": "file writes & safe shell allowed, web asks",
                                    "full-access":     "all tools allowed without prompting",
                                }
                                console.print(f"\n[green]✓ Permission mode: {args}[/green]  [dim]({_descriptions.get(args, '')})[/dim]\n")
                        else:
                            console.print("\n[warning]Usage: /mode [read-only|workspace-write|full-access][/warning]\n")
                        continue

                    elif command == "plan":
                        self.handle_plan_command(args)
                        continue

                    elif command == "copy":
                        if self.last_response is None:
                            console.print("\n[warning]No response to copy yet.[/warning]\n")
                        else:
                            import subprocess
                            try:
                                proc = subprocess.run(
                                    ["pbcopy"], input=self.last_response.encode(), check=True
                                )
                                console.print("\n[cyan]Copied to clipboard.[/cyan]\n")
                            except FileNotFoundError:
                                # pbcopy not available (non-macOS), try xclip/xsel
                                try:
                                    subprocess.run(
                                        ["xclip", "-selection", "clipboard"],
                                        input=self.last_response.encode(), check=True
                                    )
                                    console.print("\n[cyan]Copied to clipboard.[/cyan]\n")
                                except (FileNotFoundError, subprocess.CalledProcessError):
                                    try:
                                        subprocess.run(
                                            ["xsel", "--clipboard", "--input"],
                                            input=self.last_response.encode(), check=True
                                        )
                                        console.print("\n[cyan]Copied to clipboard.[/cyan]\n")
                                    except (FileNotFoundError, subprocess.CalledProcessError):
                                        console.print("\n[error]No clipboard utility found (pbcopy/xclip/xsel).[/error]\n")
                            except subprocess.CalledProcessError as e:
                                console.print(f"\n[error]Copy failed: {e}[/error]\n")
                        continue

                    elif command == "markdown":
                        self.markdown_mode = not self.markdown_mode
                        state = "ON" if self.markdown_mode else "OFF"
                        console.print(f"\n[cyan]Markdown rendering: {state}[/cyan]\n")
                        continue

                    elif command == "permission":
                        self.handle_permission_command(args)
                        continue

                    elif command == "sessions":
                        self.handle_sessions_command(args)
                        continue

                    elif command == "temperature":
                        self.handle_temperature_command(args)
                        continue

                    elif command == "todos":
                        self.handle_todos_command(args)
                        continue

                    elif command == "tools":
                        self.handle_tools_command(args)
                        continue

                    else:
                        console.print(f"\n[error]Unknown command: /{command}[/error]")
                        console.print("Type [cyan]/help[/cyan] for available commands.\n")
                        continue

                # Process with agent
                console.rule("[bold green]Assistant[/bold green]", style="green")
                self.current_status = console.status("[bold yellow]Thinking…", spinner="dots")
                self.current_status.start()
                try:
                    response = self.agent.chat(user_input)
                    self.last_response = response
                    try:
                        stats = self.agent.context_manager.get_usage_stats(self.agent.messages)
                        self._cached_ctx_pct = stats.get("usage_percent", 0.0)
                    except Exception:
                        pass
                except Exception:
                    self._streaming_started = False
                    raise
                finally:
                    # Explicitly stop the spinner — Rich's Status.__exit__ can lose
                    # track of the live display when start()/stop() are called manually
                    # inside the block (e.g. by on_tool_step for tool/agent display).
                    if self.current_status:
                        self.current_status.stop()
                    self.current_status = None

                # Render response based on markdown_mode setting
                if self._streaming_started:
                    import sys
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    self._streaming_started = False
                else:
                    console.print()
                    if self.markdown_mode:
                        console.print(Markdown(response))
                    else:
                        console.print(response)

                # Plan mode post-response handling
                if self._plan_session.is_active:
                    # Fallback auto-save: only when the session has not yet been
                    # finalized.  Once APPROVAL_PENDING, the draft is frozen —
                    # any trailing model text must not overwrite it.
                    from .plan.session import PlanPhase as _PlanPhase
                    if self._plan_session.phase != _PlanPhase.APPROVAL_PENDING:
                        if self._plan_session.draft_id is None or (
                            response and self._plan_session.draft != response.strip()
                        ):
                            auto_saved = self._plan_controller.auto_save_response(response)
                            if auto_saved:
                                console.print(
                                    f"[dim]Plan auto-saved → {self._plan_session.current_plan_path}[/dim]"
                                )

                # Plan mode: check one-shot approval flag (set by plan_finalize tool)
                if self._plan_session.consume_approval_request():
                    _plan_draft = self._plan_controller.show_draft()
                    if _plan_draft:
                        console.print(f"\n[dim]{self._plan_session.current_plan_path}[/dim]\n")
                        console.print(Markdown(_plan_draft) if self.markdown_mode else _plan_draft)
                        console.print()
                    console.print("[bold magenta]Execute this plan?[/bold magenta] [dim][y/N][/dim] ", end="")
                    try:
                        _key = readchar.readkey()
                        console.print()
                        if _key in ("y", "Y"):
                            restored, restore_allow_all = self._plan_controller.exit_plan_mode()
                            self.allow_all_tools = restore_allow_all
                            # Escalate read-only → workspace-write so implementation can write.
                            from .permissions import PermissionMode as _PM
                            if self.current_mode == _PM.READ_ONLY:
                                self.current_mode = _PM.WORKSPACE_WRITE
                                self.permission_engine.set_mode(_PM.WORKSPACE_WRITE)
                                self.readonly_mode = False
                                self._apply_readonly_mode()
                            # Execute the plan
                            console.rule("[bold green]Assistant[/bold green]", style="green")
                            self.current_status = console.status("[bold yellow]Thinking...", spinner="dots")
                            self.current_status.start()
                            try:
                                _exec_response = self.agent.chat("Now implement the plan. Follow the steps you outlined above.")
                                self.last_response = _exec_response
                            finally:
                                if self.current_status:
                                    self.current_status.stop()
                                self.current_status = None
                            console.print()
                            if self.markdown_mode:
                                console.print(Markdown(_exec_response))
                            else:
                                console.print(_exec_response)
                            # Archive and remove the executed plan
                            _pf = self._plan_session.current_plan_path
                            if _pf.exists():
                                self._plan_controller._archive_plan()
                                _pf.unlink()
                                console.print(f"[dim]Plan executed and archived.[/dim]\n")
                        else:
                            self._plan_controller.reject_approval()
                            console.print("[dim]Plan not approved. Continue refining or /plan implement when ready.[/dim]\n")
                    except (KeyboardInterrupt, EOFError):
                        self._plan_controller.reject_approval()
                        console.print()

            except KeyboardInterrupt:
                if self.current_status:
                    self.current_status.stop()
                    self.current_status = None
                console.print("\n\n[warning]Interrupted. Type '/exit' to quit.[/warning]")
                continue

            except Exception as e:
                import traceback
                error_msg = str(e)
                cause = e.__cause__
                if cause:
                    error_msg += f"\n  Caused by: {type(cause).__name__}: {cause}"
                console.print(f"\n[error]Error: {error_msg}[/error]")
                console.print("[dim]See agentao.log for full traceback.[/dim]\n")
                self.agent.llm.logger.error(f"Unhandled error in chat loop:\n{traceback.format_exc()}")
                continue


def run_print_mode(prompt: str) -> int:
    """Non-interactive print mode: send prompt, print response, exit. Returns exit code."""
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", "OPENAI").strip().upper()
    max_iterations_reached = [False]

    def _on_max_iterations(max_iterations: int, pending_tools: list) -> dict:
        max_iterations_reached[0] = True
        print(
            f"Warning: reached max tool call iterations ({max_iterations}), "
            "stopping. Response may be incomplete.",
            file=sys.stderr,
        )
        return {"action": "stop"}

    agent = Agentao(
        api_key=os.getenv(f"{provider}_API_KEY"),
        base_url=os.getenv(f"{provider}_BASE_URL"),
        model=os.getenv(f"{provider}_MODEL"),
        on_max_iterations_callback=_on_max_iterations,
    )
    # Generate a session ID for print mode so hook payloads are identifiable.
    agent._session_id = str(_uuid_mod.uuid4())
    agent.tool_runner._session_id = agent._session_id
    _load_and_register_plugins(agent)

    # Dispatch SessionStart hooks (after plugin loading so rules are available).
    if agent._plugin_hook_rules:
        try:
            from .plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
            _cwd = agent.working_directory
            adapter = ClaudeHookPayloadAdapter()
            payload = adapter.build_session_start(
                session_id=agent._session_id, cwd=_cwd,
            )
            PluginHookDispatcher(cwd=_cwd).dispatch_session_start(
                payload=payload, rules=agent._plugin_hook_rules,
            )
        except Exception:
            pass

    try:
        response = agent.chat(prompt)
        print(response)
        return 2 if max_iterations_reached[0] else 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        # Dispatch SessionEnd hooks before closing (matching interactive path).
        if agent._plugin_hook_rules:
            try:
                from .plugins.hooks import ClaudeHookPayloadAdapter, PluginHookDispatcher
                _cwd = agent.working_directory
                adapter = ClaudeHookPayloadAdapter()
                payload = adapter.build_session_end(
                    session_id=agent._session_id, cwd=_cwd,
                )
                PluginHookDispatcher(cwd=_cwd).dispatch_session_end(
                    payload=payload, rules=agent._plugin_hook_rules,
                )
            except Exception:
                pass
        agent.close()


def main(resume_session: Optional[str] = None):
    """Main entry point."""
    # Save terminal state before prompt_toolkit/readchar alter it.
    # Restored on every exit path via atexit (normal, exception, sys.exit).
    _saved_tc = None
    _tty_fd = None
    if _HAS_TERMIOS:
        try:
            # Open /dev/tty directly — more reliable than sys.stdin.fileno() in
            # atexit handlers when stdin may already be partially torn down.
            _tty_fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
            _saved_tc = termios.tcgetattr(_tty_fd)
        except Exception:
            # Fallback: try sys.stdin
            if _tty_fd is not None:
                try:
                    os.close(_tty_fd)
                except Exception:
                    pass
                _tty_fd = None
            try:
                if sys.stdin.isatty():
                    _saved_tc = termios.tcgetattr(sys.stdin.fileno())
            except Exception:
                pass

    def _restore_terminal():
        if _saved_tc is None:
            return
        # Use TCSANOW so the change is applied immediately without waiting for
        # output to drain — TCSADRAIN can block or silently fail in atexit.
        fd = _tty_fd if _tty_fd is not None else (
            sys.stdin.fileno() if sys.stdin.isatty() else None
        )
        if fd is None:
            return
        if _HAS_TERMIOS:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, _saved_tc)
            except Exception:
                pass

    atexit.register(_restore_terminal)

    try:
        cli = AgentaoCLI()
        if resume_session is not None:
            # Empty string means "latest session"; non-empty is a session ID prefix
            cli.resume_session(resume_session if resume_session else None)
        cli.run()
    except KeyboardInterrupt:
        console.print("\n\n[success]Goodbye![/success]\n")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[error]Fatal error: {str(e)}[/error]\n")
        sys.exit(1)


_PROVIDER_DEFAULTS = {
    "OPENAI":     {"base_url": "https://api.openai.com/v1",                                          "model": "gpt-4o"},
    "DEEPSEEK":   {"base_url": "https://api.deepseek.com/v1",                                        "model": "deepseek-chat"},
    "GEMINI":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",             "model": "gemini-2.0-flash"},
    "ANTHROPIC":  {"base_url": "https://api.anthropic.com/v1",                                       "model": "claude-opus-4-6"},
}


def run_init_wizard() -> None:
    """Interactive first-run setup wizard."""
    from rich.rule import Rule

    console.print()
    console.print(Panel.fit(
        "[bold cyan]Agentao[/bold cyan] — setup wizard\n"
        "[dim]Configure your LLM provider and create the local .env file.[/dim]",
        border_style="cyan",
    ))
    console.print()

    env_path = Path(".env")
    if env_path.exists():
        console.print("[warning]A .env file already exists in this directory.[/warning]")
        if not Confirm.ask("Overwrite it?", default=False):
            console.print("[dim]Aborted. No changes made.[/dim]")
            return
        console.print()

    # --- Provider ---
    provider_choices = list(_PROVIDER_DEFAULTS.keys()) + ["CUSTOM"]
    console.print("[bold]Step 1 of 3 — LLM Provider[/bold]")
    for i, name in enumerate(provider_choices, 1):
        console.print(f"  [cyan]{i}[/cyan]  {name}")
    console.print()

    while True:
        raw = Prompt.ask(
            "Choose provider",
            default="1",
        ).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(provider_choices):
            provider = provider_choices[int(raw) - 1]
            break
        upper = raw.upper()
        if upper in provider_choices:
            provider = upper
            break
        console.print("[error]Invalid choice — enter a number or provider name.[/error]")

    if provider == "CUSTOM":
        provider = Prompt.ask("Custom provider name (used as env var prefix, e.g. MYAPI)").strip().upper()

    defaults = _PROVIDER_DEFAULTS.get(provider, {"base_url": "", "model": ""})
    console.print()

    # --- API key ---
    console.print("[bold]Step 2 of 3 — API Key[/bold]")
    while True:
        api_key = Prompt.ask(f"{provider}_API_KEY").strip()
        if api_key:
            break
        console.print("[error]API key is required.[/error]")
    console.print()

    # --- Base URL & Model ---
    console.print("[bold]Step 3 of 3 — Endpoint & Model[/bold]  [dim](press Enter to accept defaults)[/dim]")
    default_url = defaults["base_url"]
    default_model = defaults["model"]

    base_url = Prompt.ask(
        f"{provider}_BASE_URL",
        default=default_url if default_url else "",
    ).strip()

    model = Prompt.ask(
        f"{provider}_MODEL",
        default=default_model if default_model else "",
    ).strip()
    console.print()

    # --- Write .env ---
    lines = [
        "# Agentao configuration — generated by `agentao init`\n",
        "\n",
        f"LLM_PROVIDER={provider}\n",
        f"{provider}_API_KEY={api_key}\n",
    ]
    if base_url:
        lines.append(f"{provider}_BASE_URL={base_url}\n")
    if model:
        lines.append(f"{provider}_MODEL={model}\n")
    lines += [
        "\n",
        "# LLM Temperature (0.0-2.0, default: 0.2)\n",
        "# LLM_TEMPERATURE=0.2\n",
    ]

    env_path.write_text("".join(lines), encoding="utf-8")

    # --- Create .agentao/ dir ---
    dot_dir = Path(".agentao")
    dot_dir.mkdir(exist_ok=True)

    # --- Done ---
    console.print(Rule(style="green"))
    console.print(
        f"[success]Done![/success]  "
        f"[dim].env written with [bold]{provider}[/bold] configuration.[/dim]"
    )
    console.print()
    console.print("  Run [bold cyan]agentao[/bold cyan] to start.\n")


def run_acp_mode() -> None:
    """Launch Agentao as an ACP stdio JSON-RPC server (Issue 12).

    Delegates to :func:`agentao.acp.__main__.main`, which constructs an
    :class:`~agentao.acp.server.AcpServer` attached to the real
    ``sys.stdin``/``sys.stdout`` and registers every handler shipped so
    far (initialize, session/new, session/prompt, session/cancel,
    session/load).

    Stdout hygiene: :class:`AcpServer` installs a stdout guard that
    redirects ``sys.stdout`` to ``sys.stderr`` so any stray ``print``
    anywhere in the process lands on stderr instead of corrupting the
    NDJSON wire. JSON-RPC responses go through a captured handle to
    the *original* stdout. Logs are routed to stderr by the same
    guard's :func:`logging.StreamHandler` install.

    Shutdown: :meth:`AcpServer.run` exits cleanly on stdin EOF, and
    its ``finally`` clause calls
    :meth:`AcpSessionManager.close_all` (Issue 03) which disconnects
    every session-owned MCP runtime. Issue 08's executor drain runs
    before that, so any in-flight handler completes before teardown.

    This function never returns to the caller — it blocks inside
    ``server.run()`` until the client disconnects, at which point the
    process exits with code 0.
    """
    # Local import keeps the ACP package optional for non-ACP entry
    # paths (CLI, print mode). Importing it here also defers any ACP
    # module-level side effects (the stdout guard runs in the AcpServer
    # constructor, not at import time, so this is safe).
    from agentao.acp.__main__ import main as acp_main

    acp_main()


def _build_parser():
    """Build the top-level argument parser with subcommands."""
    import argparse

    parser = argparse.ArgumentParser(prog="agentao", add_help=False)
    parser.add_argument("-p", "--print", dest="prompt", nargs="?", const="", default=None)
    parser.add_argument(
        "--resume",
        dest="resume",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION_ID",
        help="Resume a saved session. Omit SESSION_ID to resume the latest.",
    )
    parser.add_argument(
        "--acp",
        dest="acp",
        action="store_true",
        default=False,
        help="Launch Agentao as an Agent Client Protocol (ACP) server.",
    )
    parser.add_argument(
        "--stdio",
        dest="stdio",
        action="store_true",
        default=False,
        help=(
            "Use stdio transport for ACP mode (currently the only supported "
            "transport — implied by --acp)."
        ),
    )
    parser.add_argument(
        "--plugin-dir",
        dest="plugin_dirs",
        action="append",
        default=[],
        metavar="DIR",
        help="Load a plugin from DIR (repeatable).",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    # agentao init
    subparsers.add_parser("init")

    # Subcommand parsers also accept --plugin-dir so the flag works both
    # before and after the subcommand name.  We use default=None (not [])
    # so the subparser default doesn't clobber top-level values; entrypoint()
    # merges both sources.
    _sub_plugin_dir_kwargs = dict(
        dest="sub_plugin_dirs", action="append", default=None,
        metavar="DIR", help="Load a plugin from DIR (repeatable).",
    )

    # agentao plugin ...
    plugin_parser = subparsers.add_parser("plugin")
    plugin_parser.add_argument("--plugin-dir", **_sub_plugin_dir_kwargs)
    plugin_sub = plugin_parser.add_subparsers(dest="plugin_action")
    plugin_list_p = plugin_sub.add_parser("list", help="List loaded plugins")
    plugin_list_p.add_argument("--plugin-dir", **_sub_plugin_dir_kwargs)
    plugin_list_p.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )

    # agentao skill ...
    skill_parser = subparsers.add_parser("skill")
    skill_parser.add_argument("--plugin-dir", **_sub_plugin_dir_kwargs)
    skill_sub = skill_parser.add_subparsers(dest="skill_action")

    # agentao skill install <ref>
    install_p = skill_sub.add_parser("install", help="Install a skill from GitHub")
    install_p.add_argument("ref", help="GitHub ref: owner/repo")
    install_p.add_argument(
        "--scope", choices=["global", "project"], default=None,
        help="Install scope (default: auto-detect)",
    )
    install_p.add_argument(
        "--force", action="store_true",
        help="Overwrite existing skill",
    )

    # agentao skill remove <name>
    remove_p = skill_sub.add_parser("remove", help="Remove an installed skill")
    remove_p.add_argument("name", help="Skill name to remove")
    remove_p.add_argument(
        "--scope", choices=["global", "project"], default=None,
        help="Scope to remove from (default: auto-detect)",
    )

    # agentao skill list
    list_p = skill_sub.add_parser("list", help="List installed skills")
    list_p.add_argument(
        "--installed", action="store_true",
        help="Show only managed installs",
    )
    list_p.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )

    # agentao skill update [name]
    update_p = skill_sub.add_parser("update", help="Update installed skill(s)")
    update_p.add_argument("name", nargs="?", default=None, help="Skill name to update")
    update_p.add_argument(
        "--all", dest="update_all", action="store_true",
        help="Update all managed skills",
    )
    update_p.add_argument(
        "--scope", choices=["global", "project"], default=None,
        help="Scope to update (default: auto-detect)",
    )

    return parser


# ------------------------------------------------------------------
# Skill subcommand handlers
# ------------------------------------------------------------------

def _skill_list(args) -> None:
    """List skills.

    By default lists all discoverable skills (managed + unmanaged).
    With ``--installed`` shows only managed registry entries.
    """
    from rich.table import Table as RichTable

    from .skills.registry import SkillRegistry, registry_path_for_scope

    installed_only = getattr(args, "installed", False)

    # Always collect managed records from both registries.
    managed_records = []
    managed_names: set[str] = set()
    for scope in ("global", "project"):
        reg_path = registry_path_for_scope(scope)
        if reg_path.exists():
            reg = SkillRegistry(reg_path)
            for rec in reg.list_all():
                managed_records.append(rec)
                managed_names.add(rec.name)

    if installed_only:
        # --installed: show only managed installs.
        if getattr(args, "json_output", False):
            import dataclasses as _dc
            print(json.dumps([_dc.asdict(r) for r in managed_records], indent=2))
            return

        if not managed_records:
            console.print("[dim]No managed skills installed.[/dim]")
            return

        table = RichTable(title="Managed Skills")
        table.add_column("Name", style="cyan")
        table.add_column("Version")
        table.add_column("Source")
        table.add_column("Scope", style="green")
        table.add_column("Status")

        for rec in managed_records:
            repo_skill = Path.cwd() / "skills" / rec.name
            status = "shadowed" if repo_skill.exists() else "ok"
            table.add_row(
                rec.name,
                rec.version or "-",
                f"{rec.source_type}:{rec.source_ref}",
                rec.install_scope,
                status,
            )
        console.print(table)
        return

    # Default: show all discoverable skills (managed + unmanaged).
    # Resolve the project root upward so subdirectory invocations still
    # find skills installed in <project>/.agentao/skills and <project>/skills.
    from .skills.manager import SkillManager
    from .skills.registry import _find_project_root
    project_root = _find_project_root() or Path.cwd()
    sm = SkillManager(working_directory=project_root)
    all_skills = sm.available_skills

    if getattr(args, "json_output", False):
        entries = []
        for name, info in sorted(all_skills.items()):
            entries.append({
                "name": name,
                "description": info.get("description", ""),
                "managed": name in managed_names,
            })
        print(json.dumps(entries, indent=2))
        return

    if not all_skills:
        console.print("[dim]No skills found.[/dim]")
        return

    table = RichTable(title="Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Managed", style="green")

    for name, info in sorted(all_skills.items()):
        managed_tag = "yes" if name in managed_names else "-"
        table.add_row(name, info.get("description", "")[:60], managed_tag)
    console.print(table)


def _skill_remove(args, scope: str) -> None:
    """Remove a managed skill installation."""
    import shutil

    from .skills.registry import SkillRegistry, registry_path_for_scope

    reg_path = registry_path_for_scope(scope)
    registry = SkillRegistry(reg_path)
    record = registry.get(args.name)

    if record is None:
        # Try the other scope
        other_scope = "global" if scope == "project" else "project"
        other_path = registry_path_for_scope(other_scope)
        if other_path.exists():
            other_reg = SkillRegistry(other_path)
            if other_reg.get(args.name):
                console.print(
                    f"[yellow]Skill '{args.name}' not found in {scope} scope, "
                    f"but exists in {other_scope} scope. "
                    f"Use --scope {other_scope} to remove it.[/yellow]"
                )
                sys.exit(1)
        console.print(f"[red]Skill '{args.name}' not found in any registry.[/red]")
        sys.exit(1)

    install_dir = Path(record.install_dir)
    if install_dir.exists():
        shutil.rmtree(install_dir)

    registry.remove(args.name)
    registry.save()
    console.print(f"[green]Removed skill '{args.name}' from {scope} scope.[/green]")


def _skill_install(args, scope: str) -> None:
    """Install a skill from a remote source."""
    from .skills.installer import SkillInstallError, SkillInstaller
    from .skills.registry import SkillRegistry, registry_path_for_scope
    from .skills.sources import GitHubSkillSource

    registry = SkillRegistry(registry_path_for_scope(scope))
    source = GitHubSkillSource()
    installer = SkillInstaller(registry=registry, source=source, scope=scope)

    try:
        record = installer.install(args.ref, force=args.force)
        console.print(
            f"[green]Installed skill '{record.name}' "
            f"({record.source_ref}) into {scope} scope.[/green]"
        )
    except SkillInstallError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        sys.exit(1)


def _skill_update(args, scope: str, *, explicit_scope: str | None = None) -> None:
    """Update one or all managed skills."""
    from .skills.installer import SkillInstallError, SkillInstaller
    from .skills.registry import SkillRegistry, registry_path_for_scope
    from .skills.sources import GitHubSkillSource

    if args.update_all:
        # Honor --scope: when the user explicitly passes --scope, only iterate
        # that scope.  Without --scope, update across both scopes.
        scopes_to_update = [explicit_scope] if explicit_scope else ["global", "project"]
        updated, up_to_date, failed = [], [], []
        for update_scope in scopes_to_update:
            reg_path = registry_path_for_scope(update_scope)
            if not reg_path.exists():
                continue
            registry = SkillRegistry(reg_path)
            source = GitHubSkillSource()
            installer = SkillInstaller(registry=registry, source=source, scope=update_scope)
            for rec in registry.list_all():
                if rec.source_type == "manual":
                    continue
                try:
                    result = installer.update(rec.name)
                    if result:
                        updated.append(rec.name)
                    else:
                        up_to_date.append(rec.name)
                except SkillInstallError as exc:
                    failed.append((rec.name, str(exc)))
        if not updated and not up_to_date and not failed:
            console.print("[dim]No managed skills to update.[/dim]")
            return
        if updated:
            console.print(f"[green]Updated: {', '.join(updated)}[/green]")
        if up_to_date:
            console.print(f"[dim]Up-to-date: {', '.join(up_to_date)}[/dim]")
        if failed:
            for name, err in failed:
                console.print(f"[red]Failed {name}: {err}[/red]")
        return

    if not args.name:
        console.print("[red]Specify a skill name or use --all.[/red]")
        sys.exit(2)

    registry = SkillRegistry(registry_path_for_scope(scope))
    record = registry.get(args.name)

    # Fall back to the other scope if the skill isn't in the auto-resolved one.
    if not record:
        other_scope = "global" if scope == "project" else "project"
        other_path = registry_path_for_scope(other_scope)
        if other_path.exists():
            other_reg = SkillRegistry(other_path)
            if other_reg.get(args.name):
                scope = other_scope
                registry = other_reg
                record = other_reg.get(args.name)
    if not record:
        console.print(f"[red]Skill '{args.name}' not found in any registry.[/red]")
        sys.exit(1)

    source = GitHubSkillSource()
    installer = SkillInstaller(registry=registry, source=source, scope=scope)

    try:
        result = installer.update(args.name)
        if result:
            console.print(
                f"[green]Updated '{args.name}' to revision "
                f"{result.revision[:12]}.[/green]"
            )
        else:
            console.print(f"Skill '{args.name}' is already up-to-date.")
    except SkillInstallError as exc:
        console.print(f"[red]Error updating '{args.name}': {exc}[/red]")
        sys.exit(1)


def handle_plugin_subcommand(args) -> None:
    """Dispatch plugin subcommands (``agentao plugin list``)."""
    action = getattr(args, "plugin_action", None)

    if action == "list":
        _plugin_list_cli(args)
    else:
        sys.stderr.write("Usage: agentao plugin {list}\n")
        sys.exit(2)


def _plugin_list_cli(args) -> None:
    """``agentao plugin list`` — show loaded plugins with diagnostics."""
    from pathlib import Path

    from .plugins.diagnostics import build_diagnostics
    from .plugins.manager import PluginManager

    _top = getattr(args, "plugin_dirs", []) or []
    _sub = getattr(args, "sub_plugin_dirs", None) or []
    inline_dirs = [Path(d) for d in _top + _sub]
    mgr = PluginManager(inline_dirs=inline_dirs)
    loaded = mgr.load_plugins()

    # Simulate registration checks so the listing reflects post-load
    # failures (e.g. skill/agent name collisions) that would cause
    # _load_and_register_plugins() to reject a plugin at runtime.
    from .plugins.skills import resolve_plugin_entries
    from .plugins.agents import resolve_plugin_agents

    all_warnings = list(mgr.get_warnings())
    all_errors = list(mgr.get_errors())
    failed_plugins: set[str] = set()

    for plugin in loaded:
        entries, pw, pe = resolve_plugin_entries(plugin)
        all_warnings.extend(pw)
        all_errors.extend(pe)
        if pe:
            failed_plugins.add(plugin.name)

    for plugin in loaded:
        if plugin.name in failed_plugins:
            continue
        defs, aw, ae = resolve_plugin_agents(plugin)
        all_warnings.extend(aw)
        all_errors.extend(ae)
        if ae:
            failed_plugins.add(plugin.name)

    # Separate healthy plugins from failed ones in the diagnostics.
    healthy = [p for p in loaded if p.name not in failed_plugins]
    diag = build_diagnostics(healthy, all_warnings, all_errors)

    if getattr(args, "json_output", False):
        import json as _json
        data = {
            "plugins": [
                {
                    "name": p.name,
                    "version": p.version,
                    "source": p.source,
                    "marketplace": p.marketplace,
                    "qualified_name": p.qualified_name,
                    "root_path": str(p.root_path),
                    "status": "ok" if p.name not in failed_plugins else "failed",
                }
                for p in loaded
            ],
            "warnings": [str(w) for w in diag.warnings],
            "errors": [str(e) for e in diag.errors],
        }
        print(_json.dumps(data, indent=2))
        return

    console.print(diag.format_report())


def _load_and_register_plugins(agent: "Agentao") -> None:
    """Load plugins and register their skills, agents, and MCP servers on *agent*.

    Called during normal startup (interactive and print mode) so that
    plugin-provided capabilities are available in every session.
    """
    from .plugins.diagnostics import build_diagnostics
    from .plugins.manager import PluginManager
    from .plugins.skills import resolve_plugin_entries
    from .plugins.agents import resolve_plugin_agents
    from .plugins.mcp import merge_plugin_mcp_servers

    mgr = PluginManager(inline_dirs=_plugin_inline_dirs or None)
    loaded = mgr.load_plugins()
    if not loaded:
        return

    # Register skills and commands.  Track plugins that fail so we can
    # skip them in later registration phases (hooks, MCP) to avoid a
    # confusing partial-load state.
    failed_plugins: set = set()
    for plugin in loaded:
        entries, warnings, errors = resolve_plugin_entries(plugin)
        if not errors and entries:
            try:
                reg_errors = agent.skill_manager.register_plugin_skills(entries)
                for err in reg_errors:
                    logger.warning("Plugin skill registration failed: %s", err)
                if reg_errors:
                    failed_plugins.add(plugin.name)
            except Exception as exc:
                logger.warning("Plugin skill registration error for '%s': %s", plugin.name, exc)
                failed_plugins.add(plugin.name)
        if errors:
            failed_plugins.add(plugin.name)
        for err in errors:
            logger.warning("Plugin skill resolution error: %s", err)

    # Register agents.
    _agents_added = False
    for plugin in loaded:
        if plugin.name in failed_plugins:
            continue
        defs, warnings, errors = resolve_plugin_agents(plugin)
        if not errors and defs:
            try:
                reg_errors = agent.agent_manager.register_plugin_agents(defs)
                for err in reg_errors:
                    logger.warning("Plugin agent registration failed: %s", err)
                if reg_errors:
                    failed_plugins.add(plugin.name)
                else:
                    _agents_added = True
            except Exception as exc:
                logger.warning("Plugin agent registration error for '%s': %s", plugin.name, exc)
                failed_plugins.add(plugin.name)
        if errors:
            failed_plugins.add(plugin.name)
        for err in errors:
            logger.warning("Plugin agent resolution error: %s", err)

    # Re-register agent tools so new plugin agents get callable tool wrappers.
    if _agents_added:
        agent._register_agent_tools()

    # Filter out failed plugins before MCP merge and hooks.
    active_plugins = [p for p in loaded if p.name not in failed_plugins]

    # Merge plugin MCP servers and apply to the running agent.
    from .mcp.config import load_mcp_config
    base_mcp = load_mcp_config(project_root=agent.working_directory)
    merge_result = merge_plugin_mcp_servers(base_mcp, active_plugins)
    for err in merge_result.errors:
        logger.warning("Plugin MCP merge error: %s", err)

    # Compute new servers contributed by plugins (not already in base).
    plugin_servers = {k: v for k, v in merge_result.servers.items() if k not in base_mcp}
    if plugin_servers:
        # Inject plugin MCP servers and re-initialise MCP so they connect.
        agent._extra_mcp_servers.update(plugin_servers)
        if agent.mcp_manager is not None:
            try:
                agent.mcp_manager.disconnect_all()
            except Exception:
                pass
        agent.mcp_manager = agent._init_mcp()

    # Resolve and register plugin hooks on the agent so they fire at runtime.
    from .plugins.hooks import (
        ClaudeHookPayloadAdapter,
        PluginHookDispatcher,
        resolve_all_hook_rules,
    )
    hook_rules, hook_warnings = resolve_all_hook_rules(active_plugins)
    for w in hook_warnings:
        logger.warning("Plugin hook warning: %s", w.message)
    agent._plugin_hook_rules = hook_rules
    agent._loaded_plugins = list(active_plugins)
    agent.tool_runner._plugin_hook_rules = hook_rules
    agent.tool_runner._working_directory = agent.working_directory

    # NOTE: SessionStart hooks are NOT dispatched here — they are fired from
    # on_session_start() (interactive) or run_print_mode() (print mode) after
    # the session ID is finalized.  This avoids sending a stale/temporary
    # session ID when resuming a session.

    # Log summary.
    diag = build_diagnostics(loaded, mgr.get_warnings(), mgr.get_errors())
    if diag.plugin_count:
        logger.info("Plugins: %s", diag.summary())


def _handle_plugins_interactive() -> None:
    """Handle the interactive ``/plugins`` command."""
    from .plugins.diagnostics import build_diagnostics
    from .plugins.manager import PluginManager

    mgr = PluginManager(inline_dirs=_plugin_inline_dirs or None)
    loaded = mgr.load_plugins()
    diag = build_diagnostics(loaded, mgr.get_warnings(), mgr.get_errors())
    console.print(diag.format_report())


def handle_skill_subcommand(args) -> None:
    """Dispatch skill subcommands."""
    from .skills.registry import resolve_default_scope

    explicit_scope = getattr(args, "scope", None)
    scope = explicit_scope or resolve_default_scope()
    action = args.skill_action

    if action == "list":
        _skill_list(args)
    elif action == "remove":
        _skill_remove(args, scope)
    elif action == "install":
        _skill_install(args, scope)
    elif action == "update":
        _skill_update(args, scope, explicit_scope=explicit_scope)
    else:
        sys.stderr.write("Usage: agentao skill {install|remove|list|update}\n")
        sys.exit(2)


def entrypoint():
    """Unified entry point: -p for print mode, --resume for session restore,
    --acp --stdio for ACP server mode, skill management, or interactive."""
    global _plugin_inline_dirs
    parser = _build_parser()
    args, _ = parser.parse_known_args()

    # Propagate --plugin-dir to the module-level list so AgentaoCLI and
    # run_print_mode can pick it up.  Merge top-level and subcommand-level
    # values so the flag works in either position.
    _top_dirs = getattr(args, "plugin_dirs", []) or []
    _sub_dirs = getattr(args, "sub_plugin_dirs", None) or []
    _plugin_inline_dirs = [Path(d) for d in _top_dirs + _sub_dirs]

    # ACP mode takes priority over every other entry path. We bypass the
    # interactive Rich UI entirely so no terminal output, prompts, or
    # color codes can ever land on stdout — that would corrupt the
    # NDJSON JSON-RPC wire. Stdout hygiene is enforced inside
    # :class:`AcpServer.__init__` via its stdout guard; this branch
    # simply ensures we never reach the CLI's print/main paths.
    #
    # ``--stdio`` without ``--acp`` is rejected so a typo doesn't
    # silently fall through to interactive mode. ``--acp`` without
    # ``--stdio`` defaults to stdio (the only supported transport in
    # v1) — no other flag combination has meaning yet.
    if args.acp:
        # Future-proof: if we ever add a non-stdio ACP transport (sse,
        # websocket, ...) it would be selected by a different flag and
        # this branch would dispatch on it.
        run_acp_mode()
        return
    if args.stdio:
        sys.stderr.write(
            "agentao: --stdio requires --acp (no other transport mode uses stdio)\n"
        )
        sys.exit(2)

    if args.subcommand == "init":
        run_init_wizard()
    elif args.subcommand == "plugin":
        handle_plugin_subcommand(args)
    elif args.subcommand == "skill":
        handle_skill_subcommand(args)
    elif args.prompt is not None:
        stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
        parts = [p for p in [args.prompt.strip(), stdin_text.strip()] if p]
        full_prompt = "\n".join(parts)
        sys.exit(run_print_mode(full_prompt))
    else:
        main(resume_session=args.resume)


if __name__ == "__main__":
    entrypoint()
