"""AgentaoCLI — the interactive CLI class (slim core).

The class itself holds session state (``current_session_id``,
``current_mode``, ``_acp_manager`` etc.) and wires the agent, plan
controller, permission engine, and prompt session together.

Everything else — display, input loop, status bar, ACP routing, slash
commands, transport and session lifecycle — lives in sibling modules
and is delegated to here.  External callers (``entrypoints``, ``plan
controller``, ``commands``, tests) continue to call ``AgentaoCLI``
methods, which now forward to the extracted helpers.
"""

from __future__ import annotations

import json
import os
import uuid as _uuid_mod
from pathlib import Path
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

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
        self._acp_load_error_shown = False
        self._acp_config_mtime: Optional[float] = None

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
        previous_mode = self.permission_engine.active_mode
        self.current_mode = mode
        self.permission_engine.set_mode(mode)
        from ..permissions import PermissionMode
        self.readonly_mode = (mode == PermissionMode.READ_ONLY)
        self._apply_readonly_mode()
        self.allow_all_tools = False
        self._save_settings()
        # Step 6 replay event — surface the user-visible mode transition.
        if previous_mode != mode:
            try:
                from ..transport import AgentEvent, EventType
                self.agent.transport.emit(AgentEvent(
                    EventType.PERMISSION_MODE_CHANGED,
                    {
                        "previous": getattr(previous_mode, "value", str(previous_mode)),
                        "current": getattr(mode, "value", str(mode)),
                        "cause": "cli",
                    },
                ))
            except Exception:
                pass

    # ── ACP inbox flush (delegated) ─────────────────────────────────────

    def _try_acp_explicit_route(self, user_input: str) -> bool:
        from .acp_inbox import try_acp_explicit_route
        return try_acp_explicit_route(self, user_input)

    def _flush_acp_inbox(self) -> None:
        from .acp_inbox import flush_acp_inbox
        flush_acp_inbox(self)

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

    # ── Display (delegated) ─────────────────────────────────────────────

    def print_welcome(self):
        from .ui import print_welcome
        print_welcome(self)

    def print_help(self):
        from .ui import print_help
        print_help(self)

    def list_skills(self):
        from .ui import list_skills
        list_skills(self)

    def show_status(self):
        from .ui import show_status
        show_status(self)

    # ── Input / status bar (delegated) ──────────────────────────────────

    def _get_user_input(self) -> str:
        from .input_loop import get_user_input
        return get_user_input(self)

    def _get_status_toolbar(self) -> ANSI:
        from .input_loop import get_status_toolbar
        return get_status_toolbar(self)

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
        from .input_loop import run_loop
        run_loop(self)
