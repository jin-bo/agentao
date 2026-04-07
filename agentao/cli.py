"""CLI interface for Agentao."""

import warnings
warnings.filterwarnings("ignore", message="urllib3.*or chardet.*doesn't match")

import atexit
import json
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
    '/agent', '/agent bg', '/agent dashboard', '/agent list', '/agent status',
    '/agents',
    '/clear', '/copy', '/new',
    '/plan', '/plan clear', '/plan history', '/plan implement', '/plan show',
    '/context', '/context limit', '/exit', '/help',
    '/mcp', '/mcp add', '/mcp list', '/mcp remove',
    '/markdown',
    '/memory', '/memory clear', '/memory delete', '/memory list',
    '/memory search', '/memory tag', '/mode', '/model', '/permission', '/provider', '/quit',
    '/sessions', '/sessions delete', '/sessions delete all', '/sessions list', '/sessions resume',
    '/skills', '/skills activate', '/skills deactivate',
    '/skills disable', '/skills enable', '/skills reload', '/status', '/temperature',
    '/todos', '/tools',
]


_SLASH_COMMAND_HINTS = {
    '/agent bg': '<agent-name> <task>',
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


class AgentaoCLI:
    """CLI interface for Agentao."""

    def __init__(self):
        """Initialize CLI."""
        load_dotenv()

        self.current_session_id: Optional[str] = None  # Stable UUID of active session
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
- `activate_skill` - Activate Claude skills
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

    def show_memories(self, subcommand: str = "", arg: str = ""):
        """Show saved memories.

        Args:
            subcommand: Subcommand (list, search, tag, delete, clear)
            arg: Argument for the subcommand
        """
        memory_tool = self.agent.memory_tool

        # Handle subcommands
        if subcommand in ["", "list"]:
            # List all memories
            memories = memory_tool.get_all_memories()

            if not memories:
                console.print("\n[warning]No memories saved yet.[/warning]\n")
                return

            console.print(f"\n[info]Saved Memories ({len(memories)} total):[/info]\n")
            for memory in memories:
                console.print(f"  • [cyan]{memory['key']}[/cyan]: {memory['value']}")
                if memory.get('tags'):
                    console.print(f"    Tags: {', '.join(memory['tags'])}")
                console.print(f"    Saved: {memory['timestamp']}")
                console.print()

            # Tag summary
            all_tags: dict = {}
            for mem in memories:
                for tag in mem.get("tags", []):
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

            results = memory_tool.search_memories(arg)

            if not results:
                console.print(f"\n[warning]No memories found matching '{arg}'[/warning]\n")
                return

            console.print(f"\n[info]Found {len(results)} memory(ies) matching '{arg}':[/info]\n")
            for memory in results:
                console.print(f"  • [cyan]{memory['key']}[/cyan]: {memory['value']}")
                if memory.get('tags'):
                    console.print(f"    Tags: {', '.join(memory['tags'])}")
                console.print(f"    Saved: {memory['timestamp']}")
                console.print()

        elif subcommand == "tag":
            if not arg:
                console.print("\n[error]Usage: /memory tag <tag_name>[/error]\n")
                return

            results = memory_tool.filter_by_tag(arg)

            if not results:
                console.print(f"\n[warning]No memories found with tag '{arg}'[/warning]\n")
                return

            console.print(f"\n[info]Found {len(results)} memory(ies) with tag '{arg}':[/info]\n")
            for memory in results:
                console.print(f"  • [cyan]{memory['key']}[/cyan]: {memory['value']}")
                if memory.get('tags'):
                    console.print(f"    Tags: {', '.join(memory['tags'])}")
                console.print(f"    Saved: {memory['timestamp']}")
                console.print()

        elif subcommand == "delete":
            if not arg:
                console.print("\n[error]Usage: /memory delete <key>[/error]\n")
                return

            if memory_tool.delete_memory(arg):
                console.print(f"\n[success]Successfully deleted memory: {arg}[/success]\n")
            else:
                console.print(f"\n[warning]Memory not found: {arg}[/warning]\n")

        elif subcommand == "clear":
            # Confirm before clearing
            if Confirm.ask("\n[warning]Are you sure you want to delete ALL memories? This cannot be undone.[/warning]", default=False):
                count = memory_tool.clear_all_memories()
                console.print(f"\n[success]Successfully cleared {count} memory(ies)[/success]\n")
            else:
                console.print("\n[info]Cancelled.[/info]\n")

        else:
            console.print(f"\n[error]Unknown subcommand: {subcommand}[/error]")
            console.print("[info]Available subcommands: list, search, tag, delete, clear[/info]\n")

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
            if status == "running":
                elapsed = _time.time() - t.get("started_at", _time.time())
                return Text(f"○  {elapsed:.0f}s", style="yellow")
            if status == "completed":
                ms = t.get("duration_ms", 0)
                turns = t.get("turns", 0)
                calls = t.get("tool_calls", 0)
                tok = t.get("tokens", 0)
                tok_s = f"~{tok // 1000}k" if tok >= 1000 else str(tok)
                dur_s = f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"
                return Text(f"✓  {turns}t {calls}c {tok_s}  {dur_s}", style="green")
            # failed
            return Text("✗  failed", style="red")

        def _make_panel() -> Panel:
            tasks = list_bg_tasks()

            n_run = sum(1 for t in tasks if t["status"] == "running")
            n_ok  = sum(1 for t in tasks if t["status"] == "completed")
            n_err = sum(1 for t in tasks if t["status"] == "failed")

            tbl = Table(box=rich_box.SIMPLE, show_header=True, pad_edge=False,
                        header_style="bold dim")
            tbl.add_column("ID",     style="cyan",   width=9)
            tbl.add_column("Agent",  style="bold",   min_width=22, no_wrap=True)
            tbl.add_column("Status", min_width=22)
            tbl.add_column("Task",   style="dim",    ratio=1)

            for t in sorted(tasks, key=lambda x: x.get("started_at", 0), reverse=True):
                status_cell = _fmt_status(t)
                err_hint = ""
                if t["status"] == "failed" and t.get("error"):
                    err_hint = f"  [dim red]{str(t['error'])[:60]}[/dim red]"
                task_cell = (t.get("task", "")[:55] or "") + err_hint
                tbl.add_row(t["id"], t["agent_name"], status_cell, task_cell)

            summary = (
                f"[yellow]○ {n_run} running[/yellow]  "
                f"[green]✓ {n_ok} completed[/green]  "
                f"{'[red]' if n_err else '[dim]'}✗ {n_err} failed{'[/red]' if n_err else '[/dim]'}"
            )
            footer = "[dim]Press Ctrl+C to exit[/dim]" if n_run else ""
            title = f"Background Agents  ·  {summary}"
            return Panel(tbl, title=title, subtitle=footer, border_style="cyan")

        tasks = list_bg_tasks()
        if not tasks:
            console.print("\n[dim]No background agents in this session.[/dim]\n")
            return

        has_running = any(t["status"] == "running" for t in tasks)
        if not has_running:
            console.print()
            console.print(_make_panel())
            console.print()
            return

        # Live view — auto-refreshes while agents are running
        try:
            with Live(_make_panel(), console=console, refresh_per_second=2,
                      vertical_overflow="visible") as live:
                while True:
                    _time.sleep(0.5)
                    live.update(_make_panel())
                    if not any(t["status"] == "running" for t in list_bg_tasks()):
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
                    color = "yellow" if status == "running" else "green" if status == "completed" else "red"
                    if t.get("finished_at"):
                        elapsed = f"{t['finished_at'] - t['started_at']:.1f}s"
                    else:
                        elapsed = f"{_time.time() - t['started_at']:.0f}s"
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
                color = "yellow" if status == "running" else "green" if status == "completed" else "red"
                console.print(f"\n[info]Agent:[/info] [bold]{rec['agent_name']}[/bold]  ID: [cyan]{agent_id}[/cyan]")
                console.print(f"[info]Status:[/info] [{color}]{status}[/{color}]")
                console.print(f"[info]Task:[/info]   {rec['task']}")
                if rec.get("finished_at"):
                    elapsed = rec["finished_at"] - rec["started_at"]
                    console.print(f"[info]Time:[/info]   {elapsed:.1f}s")
                if status == "completed" and rec.get("result"):
                    console.print("\n[info]Result:[/info]")
                    console.print(Markdown(rec["result"]))
                elif status == "failed" and rec.get("error"):
                    console.print(f"\n[error]Error:[/error] {rec['error']}")
                console.print()
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
        """Hook called at the start of every session.

        Override or extend in a subclass to add custom session-start behavior.
        Default implementation does nothing.
        """
        pass

    def on_session_end(self) -> None:
        """Hook called at the end of every session (before /clear, /new, or exit).

        Override or extend in a subclass to add custom session-end behavior.
        Default implementation saves the current session to disk.
        """
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
                    if st == "running":
                        elapsed = int(_time.time() - t.get("started_at", _time.time()))
                        tokens.append(f"\x1b[93m⚙ {name} {elapsed}s\x1b[0m")
                    elif st == "completed":
                        tokens.append(f"\x1b[32m✓ {name}\x1b[0m")
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

                    elif command in ("clear", "new"):
                        self.on_session_end()
                        self.current_session_id = None
                        if self._plan_session.is_active:
                            self._plan_controller.exit_plan_mode()
                        self.agent.clear_history()
                        self.agent.memory_tool.clear_all_memories()
                        self.last_response = None
                        self._cached_ctx_pct = 0.0
                        from .permissions import PermissionMode
                        self._apply_mode(PermissionMode.WORKSPACE_WRITE)
                        self.on_session_start()
                        console.print("\n[success]Session ended and new session started.[/success]")
                        console.print("[info]Conversation history and memories cleared.[/info]")
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
    try:
        response = agent.chat(prompt)
        print(response)
        return 2 if max_iterations_reached[0] else 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


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


def entrypoint():
    """Unified entry point: -p for print mode, --resume for session restore, otherwise interactive."""
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
    args, _ = parser.parse_known_args()

    if args.prompt is not None:
        stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
        parts = [p for p in [args.prompt.strip(), stdin_text.strip()] if p]
        full_prompt = "\n".join(parts)
        sys.exit(run_print_mode(full_prompt))
    else:
        main(resume_session=args.resume)


if __name__ == "__main__":
    entrypoint()
