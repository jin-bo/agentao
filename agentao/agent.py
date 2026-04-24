"""Main agent logic for Agentao."""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .llm import LLMClient
from .permissions import PermissionEngine
from .runtime import ChatLoopRunner, ToolRunner, run_llm_call, run_turn
from .runtime import model as _runtime_model
from .tools import ToolRegistry, SaveMemoryTool, TodoWriteTool
from .tooling import init_mcp, register_agent_tools, register_builtin_tools
from .agents import AgentManager
from .cancellation import CancellationToken
from .plan import PlanSession
from .prompts import (
    SystemPromptBuilder,
    extract_context_hints,
    load_project_instructions,
)
from .skills import SkillManager
from .context_manager import ContextManager
from .mcp import McpClientManager
from .replay import (
    ReplayAdapter,
    ReplayConfig,
    ReplayRecorder,
    load_replay_config,
)
from .replay.lifecycle import (
    end_replay as _end_replay_impl,
    reload_replay_config as _reload_replay_config_impl,
    start_replay as _start_replay_impl,
)
from .replay.observability import (
    emit_context_compressed as _emit_context_compressed_impl,
    emit_session_summary_if_new as _emit_session_summary_if_new_impl,
    latest_session_summary_id as _latest_session_summary_id_impl,
)
from .sandbox import SandboxPolicy
from .transport import NullTransport, build_compat_transport


class Agentao:
    """Agentao agent with tool, skill, and MCP support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        # ── Deprecated callbacks — kept for backward compatibility ────────────
        confirmation_callback: Optional[Callable[[str, str, Dict[str, Any]], bool]] = None,
        max_context_tokens: int = 200_000,
        step_callback: Optional[Callable[[Optional[str], Dict[str, Any]], None]] = None,
        thinking_callback: Optional[Callable[[str], None]] = None,
        ask_user_callback: Optional[Callable[[str], str]] = None,
        output_callback: Optional[Callable[[str, str], None]] = None,
        tool_complete_callback: Optional[Callable[[str], None]] = None,
        llm_text_callback: Optional[Callable[[str], None]] = None,
        permission_engine: Optional[PermissionEngine] = None,
        on_max_iterations_callback: Optional[Callable[[int, list], dict]] = None,
        transport=None,                   # Transport protocol instance (preferred)
        plan_session: Optional[PlanSession] = None,
        *,
        working_directory: Optional[Path] = None,
        extra_mcp_servers: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """Initialize Agentao agent.

        Args:
            api_key: API key for LLM service.
            base_url: Base URL for API endpoint.
            model: Model name to use.
            transport: A Transport instance that receives all runtime events and
                       handles interactive requests (confirm_tool, ask_user, etc.).
                       If omitted and no legacy callbacks are provided, a NullTransport
                       is used (silent / headless mode).
            max_context_tokens: Maximum context window tokens (default 200K).
            permission_engine: Optional PermissionEngine for rule-based tool access.
            working_directory: Per-runtime working directory (Issue 05). When
                ``None`` (the default, CLI behavior), the runtime lazily reads
                ``Path.cwd()`` at every access so a user ``cd`` in the process
                remains visible. When set to a concrete ``Path``, the runtime
                is frozen to that directory: memory/permissions/MCP config/
                AGENTAO.md/system-prompt rendering/file tools/shell tool all
                resolve against it, isolating multiple ACP sessions that run
                in the same process.
            extra_mcp_servers: Optional in-memory MCP server configs to merge
                **on top of** the file-loaded ``.agentao/mcp.json``. Used by
                ACP ``session/new`` (Issue 11) to inject session-scoped
                servers without writing to the project's config files.
                Already in Agentao's internal dict shape — translation from
                ACP wire format lives in
                :func:`agentao.acp.mcp_translate.translate_acp_mcp_servers`.
                Per-name override semantics: an entry here replaces a
                file-loaded entry with the same name. ``None`` means "no
                extras", which is the CLI default and produces the legacy
                file-only behavior.

        Deprecated args (still accepted for backward compatibility):
            confirmation_callback, step_callback, thinking_callback, ask_user_callback,
            output_callback, tool_complete_callback, llm_text_callback,
            on_max_iterations_callback.
        """
        # Freeze working directory to an absolute path if one was supplied.
        # Resolve once so subsequent accesses are cheap and consistent.
        self._explicit_working_directory: Optional[Path] = (
            Path(working_directory).expanduser().resolve()
            if working_directory is not None
            else None
        )

        # Snapshot of session-scoped MCP server configs (Issue 11). Stored
        # privately so a caller can't mutate it after construction. ``None``
        # means "no extras", preserving the legacy CLI behavior of
        # file-only MCP loading. We deep-copy at the dict level so a
        # subsequent mutation by the caller cannot leak into _init_mcp.
        self._extra_mcp_servers: Dict[str, Dict[str, Any]] = (
            {name: dict(cfg) for name, cfg in extra_mcp_servers.items()}
            if extra_mcp_servers
            else {}
        )

        # Anchor the LLM debug log to the agent's effective working directory
        # so it always resolves to an absolute, writable path. CLI runs land it
        # at <project>/agentao.log (unchanged behavior, since working_directory
        # falls back to Path.cwd()); ACP sessions land it under the frozen,
        # client-supplied project cwd instead of the subprocess's cwd — which
        # for ACP launches is often "/" and read-only.
        self.llm = LLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=temperature,
            log_file=str(self.working_directory / "agentao.log"),
        )
        # Pass the explicit working_directory through (or None for the CLI
        # default — SkillManager will fall back to Path.cwd() at construction
        # time, matching the legacy behavior). ACP sessions targeting different
        # repos must see independent project skills + disabled-skill state.
        self.skill_manager = SkillManager(
            working_directory=self._explicit_working_directory,
        )
        from .memory import MemoryManager, MemoryRetriever
        from .memory.render import MemoryPromptRenderer
        self._memory_manager = MemoryManager(
            project_root=self.working_directory / ".agentao",
            global_root=Path.home() / ".agentao",
        )
        self.memory_tool = SaveMemoryTool(memory_manager=self._memory_manager)
        self.memory_retriever = MemoryRetriever(self._memory_manager)
        self.memory_renderer = MemoryPromptRenderer()
        self._last_user_message: str = ""
        self._stable_block_chars: int = 0  # size of last rendered <memory-stable> block
        self.todo_tool = TodoWriteTool()
        self.permission_engine = permission_engine

        # Resolve transport: explicit > compat shim from old callbacks > NullTransport
        _has_legacy = any([
            confirmation_callback, step_callback, thinking_callback, ask_user_callback,
            output_callback, tool_complete_callback, llm_text_callback,
            on_max_iterations_callback,
        ])
        if transport is not None:
            self.transport = transport
        elif _has_legacy:
            self.transport = build_compat_transport(
                confirmation_callback=confirmation_callback,
                step_callback=step_callback,
                thinking_callback=thinking_callback,
                ask_user_callback=ask_user_callback,
                output_callback=output_callback,
                tool_complete_callback=tool_complete_callback,
                llm_text_callback=llm_text_callback,
                on_max_iterations_callback=on_max_iterations_callback,
            )
        else:
            self.transport = NullTransport()

        # Store legacy callback attrs for backward compat (read-only; transport is the live wire)
        self.confirmation_callback = confirmation_callback
        self.step_callback = step_callback
        self.thinking_callback = thinking_callback
        self.ask_user_callback = ask_user_callback
        self.output_callback = output_callback
        self.tool_complete_callback = tool_complete_callback
        self.llm_text_callback = llm_text_callback
        self.on_max_iterations_callback = on_max_iterations_callback

        # Reasoning prompt is shown only when a dedicated thinking callback is registered
        self._has_thinking_handler = thinking_callback is not None

        # Save LLM config for sub-agent creation
        self._llm_config = {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "temperature": self.llm.temperature,  # resolved value (explicit or from env)
        }

        # Initialize context manager
        self.context_manager = ContextManager(
            llm_client=self.llm,
            memory_tool=self.memory_tool,
            max_tokens=max_context_tokens,
            memory_manager=self._memory_manager,
        )

        # Initialize tool registry
        self.tools = ToolRegistry()
        self._register_tools()

        # Initialize MCP (Model Context Protocol) support
        self.mcp_manager = self._init_mcp()

        # Initialize agent manager and register agent tools
        self.agent_manager = AgentManager()
        self._register_agent_tools()

        # Restore persisted background tasks and reclassify any interrupted work.
        # This belongs in core startup so direct Agentao() and print mode behave
        # the same as the interactive CLI after a restart.
        from .agents.store import recover_bg_task_store_once
        from .agents.tools import _bg_lock, _bg_tasks
        recover_bg_task_store_once(_bg_tasks, _bg_lock)

        # Plugin hook rules — populated by _load_and_register_plugins() in cli.py.
        self._plugin_hook_rules: list = []
        self._loaded_plugins: list = []
        # Session ID for plugin hook payloads — set by CLI after session start.
        self._session_id: Optional[str] = None

        # Per-turn cancellation token (set at the start of each chat() call)
        self._current_token: Optional[CancellationToken] = None

        # Conversation history
        self.messages: List[Dict[str, Any]] = []

        # Plan session (shared with CLI; Agent reads via _plan_mode property)
        self._plan_session: PlanSession = plan_session or PlanSession()

        # Load project instructions if available
        self.project_instructions = self._load_project_instructions()

        # Initialize sandbox policy (macOS sandbox-exec wrapper for shell
        # commands). Silently disabled on non-macOS or when config absent.
        # The policy must track the same working directory as the rest of
        # the runtime — freezing Path.cwd() here would apply the wrong
        # project's .agentao/sandbox.json after a chdir (ACP/embedded).
        if self._explicit_working_directory is not None:
            self.sandbox_policy = SandboxPolicy(
                project_root=self._explicit_working_directory,
            )
        else:
            self.sandbox_policy = SandboxPolicy(
                project_root_provider=Path.cwd,
            )

        # Replay state — recorder + adapter are created lazily in
        # ``start_replay()``. When recording is disabled (the default),
        # ``_replay_adapter`` stays ``None`` and the transport stack is
        # the original transport with zero replay overhead.
        self._replay_recorder: Optional[ReplayRecorder] = None
        self._replay_adapter: Optional[ReplayAdapter] = None
        try:
            self._replay_config: ReplayConfig = load_replay_config(self.working_directory)
        except Exception:
            self._replay_config = ReplayConfig()

        # Initialize tool runner (encapsulates 4-phase tool execution pipeline)
        self.tool_runner = ToolRunner(
            tools=self.tools,
            permission_engine=self.permission_engine,
            transport=self.transport,
            logger=self.llm.logger,
            sandbox_policy=self.sandbox_policy,
        )

    @property
    def working_directory(self) -> Path:
        """Effective working directory for this runtime (Issue 05).

        - When the agent was constructed without ``working_directory``
          (the default, CLI behavior), returns the *current* process cwd
          lazily at each access. This preserves the legacy semantics where
          a ``cd`` in the surrounding shell is immediately visible.
        - When ``working_directory`` was supplied (ACP session path),
          returns the frozen, resolved ``Path`` captured at construction.
          Two Agentao instances created with different ``working_directory``
          values will report independent paths even in the same process.
        """
        if self._explicit_working_directory is not None:
            return self._explicit_working_directory
        return Path.cwd()

    @property
    def memory_manager(self):
        return self._memory_manager

    @memory_manager.setter
    def memory_manager(self, manager):
        """Replace the memory manager and keep all dependent helpers in sync."""
        self._memory_manager = manager
        self.memory_tool.memory_manager = manager
        self.memory_retriever._manager = manager
        self.context_manager.memory_manager = manager

    def _load_project_instructions(self) -> Optional[str]:
        # Implementation lives in :mod:`agentao.prompts.helpers`. Kept as a
        # thin facade so tests and external callers that patch the agent
        # method keep working.
        return load_project_instructions(self.working_directory, self.llm.logger)

    def _register_tools(self):
        # Implementation lives in ``agentao.tooling.registry`` — see that
        # module for the tool list and working-directory binding logic.
        register_builtin_tools(self)

    def _init_mcp(self) -> Optional[McpClientManager]:
        # Implementation lives in ``agentao.tooling.mcp_tools`` — see that
        # module for config merge semantics and error handling.
        return init_mcp(self)

    def close(self) -> None:
        """Clean up resources (MCP connections, event loops).

        NOTE: SessionEnd hooks are dispatched by the CLI layer
        (on_session_end / _dispatch_session_end_hooks) which runs before
        close() on every exit path.  We intentionally do NOT duplicate
        the dispatch here to avoid double-firing.
        """
        try:
            self.end_replay()
        except Exception:
            pass
        if self.mcp_manager is not None:
            try:
                self.mcp_manager.disconnect_all()
            except Exception as e:
                self.llm.logger.warning(f"Error disconnecting MCP: {e}")
            self.mcp_manager = None

    # ------------------------------------------------------------------
    # Session Replay lifecycle
    # ------------------------------------------------------------------

    def start_replay(self, session_id: Optional[str] = None) -> Optional[Path]:
        # Implementation lives in :mod:`agentao.replay.lifecycle`. Kept as
        # a thin facade so the CLI, ACP session/new & session/load, and
        # the test suite can continue to call this as an agent method.
        return _start_replay_impl(self, session_id)

    def end_replay(self) -> None:
        # Implementation lives in :mod:`agentao.replay.lifecycle`.
        _end_replay_impl(self)

    def reload_replay_config(self) -> ReplayConfig:
        # Implementation lives in :mod:`agentao.replay.lifecycle`.
        return _reload_replay_config_impl(self)

    def _register_agent_tools(self):
        # Implementation lives in ``agentao.tooling.agent_tools`` — see
        # that module for event wiring and callback bridging.
        register_agent_tools(self)

    def _build_system_prompt(self) -> str:
        """Build the system prompt for one turn.

        Composition lives in :class:`agentao.prompts.SystemPromptBuilder`;
        this method stays as a thin entry point so existing callers and
        tests keep working unchanged.
        """
        return SystemPromptBuilder(self).build()

    def _extract_context_hints(self) -> List[str]:
        # Implementation lives in :mod:`agentao.prompts.helpers`. Kept as a
        # thin facade so ``SystemPromptBuilder`` and tests that call this
        # as an agent method keep working.
        return extract_context_hints(self.messages)

    # ------------------------------------------------------------------
    # Replay observability helpers (v1.1)
    # Implementation lives in :mod:`agentao.replay.observability`; these
    # stay as thin facades so :mod:`agentao.runtime.chat_loop` and any
    # test patches continue to invoke them as agent methods.
    # ------------------------------------------------------------------

    def _latest_session_summary_id(self) -> Optional[str]:
        return _latest_session_summary_id_impl(self)

    def _emit_context_compressed(
        self,
        *,
        compression_type: str,
        reason: str,
        pre_msgs: int,
        post_msgs: int,
        pre_tokens: Optional[int] = None,
        post_tokens: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        _emit_context_compressed_impl(
            self,
            compression_type=compression_type,
            reason=reason,
            pre_msgs=pre_msgs,
            post_msgs=post_msgs,
            pre_tokens=pre_tokens,
            post_tokens=post_tokens,
            duration_ms=duration_ms,
        )

    def _emit_session_summary_if_new(self, previous_summary_id: Optional[str]) -> Optional[str]:
        return _emit_session_summary_if_new_impl(self, previous_summary_id)

    def _llm_call(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
                  cancellation_token: Optional[CancellationToken] = None) -> Any:
        # Implementation lives in :mod:`agentao.runtime.llm_call`. Kept as
        # a thin facade because ``ChatLoopRunner`` calls this as
        # ``agent._llm_call(...)`` and external tests patch it by name.
        return run_llm_call(self, messages, tools, cancellation_token)

    def add_message(self, role: str, content: str):
        """Add a message to conversation history.

        Args:
            role: Message role (user/assistant/system)
            content: Message content
        """
        self.messages.append({"role": role, "content": content})

    def clear_history(self):
        """Clear conversation history, deactivate all skills, and reset todos."""
        self.messages = []
        self.skill_manager.clear_active_skills()
        self.todo_tool.clear()
        # Reset context and session token counters for the fresh session
        self.context_manager._last_api_prompt_tokens = None
        self.llm.total_prompt_tokens = 0
        self.llm.total_completion_tokens = 0

    @property
    def _plan_mode(self) -> bool:
        """Whether plan mode is active (reads from shared PlanSession)."""
        return self._plan_session.is_active

    def chat(self, user_message: str, max_iterations: int = 100,
             cancellation_token: Optional[CancellationToken] = None) -> str:
        """Process user message and generate response.

        Args:
            user_message: User's message
            max_iterations: Maximum number of tool call iterations to prevent infinite loops
            cancellation_token: Optional token to cancel this chat() call. If not provided,
                                 a fresh token is created. Pass a shared token to propagate
                                 cancellation from a parent agent (Gemini CLI pattern).

        Returns:
            Assistant's response
        """
        # Per-turn lifecycle (cancellation + counters + replay begin/end_turn +
        # KeyboardInterrupt/AgentCancelledError mapping) lives in
        # :mod:`agentao.runtime.turn`; this method stays as a thin facade
        # so external callers and tests keep using ``Agentao.chat``.
        return run_turn(self, user_message, max_iterations, cancellation_token)

    def _chat_inner(self, user_message: str, max_iterations: int,
                    token: CancellationToken) -> str:
        """Inner chat loop — called by chat(). Raises AgentCancelledError on cancellation.

        Body lives in :class:`agentao.runtime.chat_loop.ChatLoopRunner`; this
        method stays as the entry point so subclasses or test patches
        targeting ``_chat_inner`` keep working.
        """
        return ChatLoopRunner(self).run(user_message, max_iterations, token)

    def get_conversation_summary(self) -> str:
        """Get a summary of the conversation.

        Returns:
            Conversation summary
        """
        tools_schema = self.tools.to_openai_format()
        # Headline count: self.messages only so a fresh session shows 0.
        # When Tier 1 API count is present it already reflects all overhead.
        stats = self.context_manager.get_usage_stats(self.messages)
        # Breakdown: include system prompt + tools only when there are messages,
        # so that /new resets all three components to 0.
        if self.messages:
            messages_with_system = [
                {"role": "system", "content": self._build_system_prompt()}
            ] + self.messages
            bd_full = self.context_manager.estimate_tokens_breakdown(
                messages_with_system, tools=tools_schema
            )
        else:
            bd_full = {"system": 0, "messages": 0, "tools": 0, "total": 0}
        stats["token_breakdown"] = bd_full
        memory_count = len(self.memory_manager.get_all_entries())

        if not self.messages:
            summary = "No conversation history\n"
        else:
            summary = f"Messages: {len(self.messages)}\n"

        summary += f"Model: {self.llm.model}\n"
        summary += f"Temperature: {self.llm.temperature}\n"
        summary += f"Active skills: {len(self.skill_manager.get_active_skills())}\n"
        summary += f"Saved memories: {memory_count}\n"
        todos = self.todo_tool.get_todos()
        if todos:
            done = sum(1 for t in todos if t["status"] == "completed")
            summary += f"Task list: {done}/{len(todos)} completed\n"

        # MCP server info
        if self.mcp_manager:
            statuses = self.mcp_manager.get_server_status()
            connected = sum(1 for s in statuses if s["status"] == "connected")
            total_tools = sum(s["tools"] for s in statuses)
            summary += f"MCP servers: {connected}/{len(statuses)} connected, {total_tools} tools\n"
        bd = stats.get("token_breakdown", {})
        source_label = " (api)" if stats.get("token_count_source") == "api" else ""
        summary += (
            f"Context: ~{stats['estimated_tokens']:,}{source_label} / {stats['max_tokens']:,} tokens "
            f"({stats['usage_percent']:.1f}%)\n"
            f"  system: {bd.get('system', 0):,}  "
            f"messages: {bd.get('messages', 0):,}  "
            f"tools: {bd.get('tools', 0):,}\n"
            f"Session: {self.llm.total_prompt_tokens:,} prompt / "
            f"{self.llm.total_completion_tokens:,} completion tokens"
        )

        if self.skill_manager.get_active_skills():
            summary += "\nActive: " + ", ".join(self.skill_manager.get_active_skills().keys())

        return summary

    def get_current_model(self) -> str:
        """Get current model name.

        Returns:
            Current model name
        """
        return self.llm.model

    def set_provider(self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None) -> None:
        # Implementation lives in ``agentao.runtime.model``.
        _runtime_model.set_provider(self, api_key, base_url=base_url, model=model)

    def set_model(self, model: str) -> str:
        # Implementation lives in ``agentao.runtime.model``.
        return _runtime_model.set_model(self, model)

    def list_available_models(self) -> List[str]:
        # Implementation lives in ``agentao.runtime.model``.
        return _runtime_model.list_available_models(self)
