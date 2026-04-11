"""AgentaoCLI — the interactive CLI class (slim core)."""

from __future__ import annotations

import json
import os
import subprocess
import uuid as _uuid_mod
from pathlib import Path
from typing import Optional

import readchar
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.markdown import Markdown

from dotenv import load_dotenv

from ..agent import Agentao
from ..display import DisplayController
from ..transport import AgentEvent
from ._globals import console
from ._utils import _SlashCompleter


class AgentaoCLI:
    """CLI interface for Agentao."""

    def __init__(self):
        """Initialize CLI."""
        load_dotenv()

        self.current_session_id: Optional[str] = str(_uuid_mod.uuid4())
        self.current_status = None
        self._streaming_output = False
        self.markdown_mode = True
        self.last_response: str | None = None
        from ..plan import PlanSession, PlanController
        self._plan_session = PlanSession()
        self._plan_controller: Optional[object] = None
        provider = os.getenv("LLM_PROVIDER", "OPENAI").strip().upper()
        self.current_provider = provider

        context_limit = int(os.getenv("AGENTAO_CONTEXT_TOKENS", "200000"))

        from ..permissions import PermissionEngine, PermissionMode
        self.permission_engine = PermissionEngine()

        self.allow_all_tools = False
        self.readonly_mode = False
        self._cached_ctx_pct: float = 0.0
        self._streaming_started: bool = False

        from ..permissions import PermissionMode as _PM
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

        from ..plan import PlanController
        self._plan_controller = PlanController(
            session=self._plan_session,
            permission_engine=self.permission_engine,
            apply_mode_fn=self._apply_mode,
            load_settings_fn=self._load_settings,
        )
        from ..tools.plan import PlanSaveTool, PlanFinalizeTool
        self.agent.tools.register(PlanSaveTool(self._plan_controller))
        self.agent.tools.register(PlanFinalizeTool(self._plan_controller))

        self.agent._session_id = self.current_session_id
        self.agent.tool_runner._session_id = self.current_session_id

        from .subcommands import _load_and_register_plugins
        _load_and_register_plugins(self.agent)

        _kb = KeyBindings()

        @_kb.add('enter')
        def _pt_submit(event):
            event.current_buffer.validate_and_handle()

        @_kb.add('escape', 'enter')
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

        # ACP client manager — lazy-initialized on first use or /acp command.
        self._acp_manager = None

        self.permission_engine.set_mode(self.current_mode)
        from ..permissions import PermissionMode as _PM2
        self.readonly_mode = (self.current_mode == _PM2.READ_ONLY)
        self._apply_readonly_mode()

    # ── Settings management ─────────────────────────────────────────────

    def _apply_readonly_mode(self) -> None:
        self.agent.tool_runner.set_readonly_mode(self.readonly_mode)

    def _load_settings(self) -> dict:
        path = Path(".agentao") / "settings.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _save_settings(self) -> None:
        path = Path(".agentao") / "settings.json"
        path.parent.mkdir(exist_ok=True)
        data = self._load_settings()
        data["mode"] = self.current_mode.value
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _apply_mode(self, mode) -> None:
        self.current_mode = mode
        self.permission_engine.set_mode(mode)
        from ..permissions import PermissionMode
        self.readonly_mode = (mode == PermissionMode.READ_ONLY)
        self._apply_readonly_mode()
        self.allow_all_tools = False
        self._save_settings()

    # ── ACP inbox flush ──────────────────────────────────────────────────

    def _flush_acp_inbox(self) -> None:
        """Drain and render ACP inbox messages at a safe idle point.

        Called before the input prompt, after slash command dispatch, and
        after the agent response is printed.  No-op when no ACP manager
        is configured.

        After rendering queued messages, checks for pending interactions
        (permission / input requests from ACP servers) and prints an
        actionable summary so the user knows how to respond.
        """
        if self._acp_manager is None:
            return
        messages = self._acp_manager.flush_inbox()
        if messages:
            from ..acp_client.render import flush_to_console
            flush_to_console(messages, console, markdown_mode=self.markdown_mode)

        # Handle pending interactions inline — same UX as during /acp send.
        pending = self._acp_manager.interactions.list_pending()
        if pending:
            from .commands_ext import _handle_inline_interaction
            for interaction in pending:
                _handle_inline_interaction(
                    self, self._acp_manager, interaction.server, interaction
                )

    # ── Transport protocol delegation ───────────────────────────────────

    def emit(self, event: AgentEvent) -> None:
        from .transport import emit_event
        emit_event(self, event)

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        from .transport import confirm_tool_execution
        return confirm_tool_execution(self, tool_name, description, args)

    def confirm_tool_execution(self, tool_name: str, tool_description: str, tool_args: dict) -> bool:
        from .transport import confirm_tool_execution
        return confirm_tool_execution(self, tool_name, tool_description, tool_args)

    def on_llm_thinking(self, reasoning: str) -> None:
        from .transport import on_llm_thinking
        on_llm_thinking(self, reasoning)

    def on_max_iterations(self, max_iterations: int, pending_tools: list) -> dict:
        from .transport import on_max_iterations
        return on_max_iterations(self, max_iterations, pending_tools)

    def on_llm_text(self, chunk: str) -> None:
        from .transport import on_llm_text
        on_llm_text(self, chunk)

    def ask_user(self, question: str) -> str:
        from .transport import ask_user
        return ask_user(self, question)

    # ── Session lifecycle delegation ────────────────────────────────────

    def on_session_start(self) -> None:
        from .session import on_session_start
        on_session_start(self)

    def on_session_end(self) -> None:
        from .session import on_session_end
        on_session_end(self)

    def _save_session_on_exit(self):
        self.on_session_end()

    # ── Display methods ─────────────────────────────────────────────────

    def print_welcome(self):
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
- `/acp [subcommand]` - Manage ACP servers
  - `/acp` or `/acp list` - List ACP servers with state
  - `/acp start/stop/restart <name>` - Control server lifecycle
  - `/acp send <name> <message>` - Send a prompt (permission/input handled inline)
  - `/acp cancel <name>` - Cancel active turn
  - `/acp status <name>` - Detailed server status
  - `/acp logs <name> [lines]` - View server stderr
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
        summary = self.agent.get_conversation_summary()
        console.print(f"\n[info]Status:[/info]\n{summary}")

        from ..permissions import PermissionMode
        _mode_labels = {
            PermissionMode.READ_ONLY:       ("[red]read-only[/red]",       "write & shell tools are blocked"),
            PermissionMode.WORKSPACE_WRITE: ("[green]workspace-write[/green]", "file writes & safe shell allowed, web asks"),
            PermissionMode.FULL_ACCESS:     ("[yellow]full-access[/yellow]",   "all tools allowed without prompting"),
        }
        _label, _desc = _mode_labels.get(self.current_mode, (self.current_mode.value, ""))
        console.print(f"[info]Permission Mode:[/info] {_label}  [dim]({_desc})[/dim]")

        md_state = "[green]ON[/green]" if self.markdown_mode else "[yellow]OFF[/yellow]"
        console.print(f"[info]Markdown Rendering:[/info] {md_state}")

        todos = self.agent.todo_tool.get_todos()
        if todos:
            done = sum(1 for t in todos if t["status"] == "completed")
            console.print(f"[info]Task List:[/info] {done}/{len(todos)} completed (use /todos for details)")

        # ACP summary
        if self._acp_manager is not None:
            statuses = self._acp_manager.get_status()
            if statuses:
                running = sum(
                    1 for s in statuses
                    if s["state"] not in ("configured", "stopped", "failed")
                )
                inbox_n = self._acp_manager.inbox.pending_count
                interact_n = self._acp_manager.interactions.pending_count
                console.print(
                    f"[info]ACP servers:[/info] {running}/{len(statuses)} running"
                )
                if inbox_n:
                    console.print(f"[info]ACP inbox:[/info] {inbox_n} queued")
                if interact_n:
                    console.print(
                        f"[warning]ACP interactions:[/warning] {interact_n} pending"
                    )
        console.print()

    # ── Input / Status bar ──────────────────────────────────────────────

    def _get_user_input(self) -> str:
        import threading
        from prompt_toolkit.application.current import get_app_or_none
        from ..permissions import PermissionMode

        if self._plan_session.is_active:
            prompt = ANSI("\n\033[1;35m[plan]\033[0m \033[1;36m❯\033[0m ")
        elif self.current_mode == PermissionMode.READ_ONLY:
            prompt = ANSI("\n\033[1;31m[read-only]\033[0m \033[1;36m❯\033[0m ")
        else:
            prompt = ANSI("\n\033[1;36m❯\033[0m ")

        stop = threading.Event()
        app_ref: list = []

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
        from ..agents.tools import list_bg_tasks
        from ..permissions import PermissionMode

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
                    else:
                        tokens.append(f"\x1b[91m✗ {name}\x1b[0m")
                agents_part = f"  {DIM}│{RST}  " + f"  {DIM}·{RST}  ".join(tokens)
            else:
                agents_part = ""
        except Exception:
            agents_part = ""

        # ACP pending interactions indicator.
        acp_part = ""
        try:
            if self._acp_manager is not None:
                n_interact = self._acp_manager.interactions.pending_count
                if n_interact:
                    acp_part = (
                        f"  {DIM}│{RST}  "
                        f"\x1b[93m⏳ {n_interact} ACP interaction(s)\x1b[0m"
                    )
        except Exception:
            pass

        rule = "\x1b[34m" + "─" * 300 + RST
        sep = f"  {DIM}│{RST}  "
        cwd = Path.cwd().name or str(Path.cwd())
        status = (
            f" {DIM}{model}{RST}"
            f"{sep}{mode_col}{mode_text}{RST}"
            f"{sep}{ctx_col}ctx {pct:.0f}%{RST}"
            f"{sep}{DIM}{cwd}{RST}"
            f"{agents_part}"
            f"{acp_part}"
        )
        return ANSI(f"{rule}\n{status}")

    # ── Main loop ───────────────────────────────────────────────────────

    def run(self):
        self.print_welcome()
        try:
            self._run_loop()
        finally:
            # Stop ACP server subprocesses before closing the agent.
            if self._acp_manager is not None:
                try:
                    self._acp_manager.stop_all()
                except Exception:
                    pass
            self.agent.close()

    def _run_loop(self):
        """Main input loop."""
        from .commands import (
            handle_todos_command, handle_plan_command, handle_provider_command,
            handle_model_command, handle_temperature_command, handle_context_command,
            handle_mcp_command, handle_permission_command, handle_sessions_command,
            resume_session, handle_tools_command,
        )
        from .commands_ext import (
            handle_crystallize_command, show_memories, handle_agent_command,
            _show_agents_dashboard, handle_acp_command,
        )
        from .subcommands import _handle_plugins_interactive

        self.on_session_start()
        while True:
            try:
                self._flush_acp_inbox()
                user_input = self._get_user_input()

                if not user_input.strip():
                    continue

                input_text = user_input.strip()

                if input_text.startswith('/'):
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
                        from ..permissions import PermissionMode
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
                        self.last_response = None
                        self._cached_ctx_pct = 0.0
                        from ..permissions import PermissionMode
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
                        handle_crystallize_command(self, args)
                        continue

                    elif command == "memory":
                        if args:
                            subcommand_parts = args.split(maxsplit=1)
                            subcommand = subcommand_parts[0]
                            subcommand_arg = subcommand_parts[1] if len(subcommand_parts) > 1 else ""
                            show_memories(self, subcommand, subcommand_arg)
                        else:
                            show_memories(self)
                        continue

                    elif command == "model":
                        handle_model_command(self, args)
                        continue

                    elif command == "provider":
                        handle_provider_command(self, args)
                        continue

                    elif command == "context":
                        handle_context_command(self, args)
                        continue

                    elif command == "mcp":
                        handle_mcp_command(self, args)
                        continue

                    elif command in ("plugins", "plugin"):
                        _handle_plugins_interactive()
                        continue

                    elif command == "acp":
                        handle_acp_command(self, args)
                        continue

                    elif command == "agent":
                        handle_agent_command(self, args)
                        continue

                    elif command == "agents":
                        _show_agents_dashboard(self)
                        continue

                    elif command == "mode":
                        from ..permissions import PermissionMode
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
                        handle_plan_command(self, args)
                        continue

                    elif command == "copy":
                        if self.last_response is None:
                            console.print("\n[warning]No response to copy yet.[/warning]\n")
                        else:
                            try:
                                subprocess.run(
                                    ["pbcopy"], input=self.last_response.encode(), check=True
                                )
                                console.print("\n[cyan]Copied to clipboard.[/cyan]\n")
                            except FileNotFoundError:
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
                        handle_permission_command(self, args)
                        continue

                    elif command == "sessions":
                        handle_sessions_command(self, args)
                        continue

                    elif command == "temperature":
                        handle_temperature_command(self, args)
                        continue

                    elif command == "todos":
                        handle_todos_command(self, args)
                        continue

                    elif command == "tools":
                        handle_tools_command(self, args)
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
                    if self.current_status:
                        self.current_status.stop()
                    self.current_status = None

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

                self._flush_acp_inbox()

                # Plan mode post-response handling
                if self._plan_session.is_active:
                    from ..plan.session import PlanPhase as _PlanPhase
                    if self._plan_session.phase != _PlanPhase.APPROVAL_PENDING:
                        if self._plan_session.draft_id is None or (
                            response and self._plan_session.draft != response.strip()
                        ):
                            auto_saved = self._plan_controller.auto_save_response(response)
                            if auto_saved:
                                console.print(
                                    f"[dim]Plan auto-saved → {self._plan_session.current_plan_path}[/dim]"
                                )

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
                            from ..permissions import PermissionMode as _PM
                            if self.current_mode == _PM.READ_ONLY:
                                self.current_mode = _PM.WORKSPACE_WRITE
                                self.permission_engine.set_mode(_PM.WORKSPACE_WRITE)
                                self.readonly_mode = False
                                self._apply_readonly_mode()
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
