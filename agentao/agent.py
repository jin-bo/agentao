"""Main agent logic for Agentao."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from .llm import LLMClient
from .permissions import PermissionEngine
from .runtime import ChatLoopRunner, ToolRunner, run_llm_call, run_turn
from .runtime import model as _runtime_model
from .tools import ToolRegistry, SaveMemoryTool, TodoWriteTool
from .tooling import init_mcp, register_agent_tools, register_builtin_tools, register_mcp_tools
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

if TYPE_CHECKING:
    from .agents.bg_store import BackgroundTaskStore  # noqa: F401
    from .capabilities import FileSystem, ShellExecutor
    from .memory import MemoryManager  # noqa: F401


class Agentao:
    """Agentao agent with tool, skill, and MCP support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
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
        working_directory: Path,
        extra_mcp_servers: Optional[Dict[str, Dict[str, Any]]] = None,
        # Embedded-harness explicit-injection kwargs.
        llm_client: Optional[LLMClient] = None,
        logger: Optional[logging.Logger] = None,
        memory_manager: Optional["MemoryManager"] = None,
        skill_manager: Optional[SkillManager] = None,
        project_instructions: Optional[str] = None,
        mcp_manager: Optional[McpClientManager] = None,
        filesystem: Optional["FileSystem"] = None,
        shell: Optional["ShellExecutor"] = None,
        # Opt-in subsystems — ``None`` (default) disables. The factory
        # wires CLI defaults from ``<wd>/.agentao/*``.
        bg_store: Optional["BackgroundTaskStore"] = None,
        sandbox_policy: Optional[SandboxPolicy] = None,
        replay_config: Optional[ReplayConfig] = None,
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
            working_directory: Per-runtime working directory (required
                since 0.3.0; was a deprecated optional in 0.2.16).
                Frozen at construction: memory/permissions/MCP config/
                AGENTAO.md/system-prompt rendering/file tools/shell tool
                all resolve against it. Two Agentao instances created
                with different ``working_directory`` values can coexist
                in the same process. Use
                :func:`agentao.embedding.build_from_environment` for
                CLI-style auto-detection from the surrounding cwd /
                ``.env`` / ``.agentao/`` files.
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
        # A fully-constructed object always wins over its raw-config
        # sibling; supplying both is a programmer error.
        if llm_client is not None and any(
            v is not None for v in (api_key, base_url, model, temperature, max_tokens)
        ):
            raise ValueError(
                "Agentao(): pass either llm_client= or "
                "api_key/base_url/model/temperature/max_tokens, not both."
            )
        if mcp_manager is not None and extra_mcp_servers is not None:
            raise ValueError(
                "Agentao(): pass either mcp_manager= or extra_mcp_servers=, "
                "not both."
            )

        # Freeze working directory to an absolute path. Resolved once so
        # subsequent accesses are cheap and consistent. Required since
        # 0.3.0 — calling ``Agentao()`` without ``working_directory=``
        # raises ``TypeError`` from Python's signature dispatch.
        self._working_directory: Path = (
            Path(working_directory).expanduser().resolve()
        )

        # When ``None``, file/search/shell tools fall back to
        # ``LocalFileSystem`` / ``LocalShellExecutor`` at first use.
        self.filesystem = filesystem
        self.shell = shell

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
        if llm_client is not None:
            self.llm = llm_client
        else:
            if not api_key or not base_url or not model:
                raise ValueError(
                    "Agentao(): api_key, base_url, and model are required "
                    "when llm_client is not supplied. Pass them explicitly, "
                    "inject a pre-built llm_client=, or use "
                    "agentao.embedding.build_from_environment() for "
                    "CLI-style env auto-discovery."
                )
            llm_kwargs: Dict[str, Any] = dict(
                api_key=api_key,
                base_url=base_url,
                model=model,
                log_file=str(self.working_directory / "agentao.log"),
                logger=logger,
            )
            if temperature is not None:
                llm_kwargs["temperature"] = temperature
            if max_tokens is not None:
                llm_kwargs["max_tokens"] = max_tokens
            self.llm = LLMClient(**llm_kwargs)
        # When the host has constructed and pre-loaded its own
        # ``SkillManager``, skip the auto-discovery scan entirely.
        if skill_manager is not None:
            self.skill_manager = skill_manager
        else:
            self.skill_manager = SkillManager(
                working_directory=self._working_directory,
            )
        from .memory import MemoryManager, MemoryRetriever, SQLiteMemoryStore
        from .memory.render import MemoryPromptRenderer
        if memory_manager is not None:
            self._memory_manager = memory_manager
        else:
            # Pure-injection / bare-construction path: project scope only.
            # The CLI / ACP factory passes an explicitly-built MemoryManager
            # with both project and user stores resolved from the
            # surrounding environment, so cross-project user memory only
            # surfaces through that path.
            self._memory_manager = MemoryManager(
                project_store=SQLiteMemoryStore.open_or_memory(
                    self.working_directory / ".agentao" / "memory.db"
                ),
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

        # Initialize context manager
        self.context_manager = ContextManager(
            llm_client=self.llm,
            memory_tool=self.memory_tool,
            max_tokens=max_context_tokens,
            memory_manager=self._memory_manager,
        )

        self.bg_store: Optional["BackgroundTaskStore"] = bg_store
        # Must be set before _register_agent_tools(): the sub-agent wrapper
        # captures this via getattr(agent, "sandbox_policy", ...) at
        # registration time, so a late assignment leaves sub-agents
        # unsandboxed.
        self.sandbox_policy: Optional[SandboxPolicy] = sandbox_policy

        # Initialize tool registry
        self.tools = ToolRegistry()
        self._register_tools()

        # When an already-built manager is injected, skip the file
        # discovery pass entirely; the host owns the lifecycle. We still
        # have to wrap and register every tool the manager exposes, or
        # the model can't see any of them.
        if mcp_manager is not None:
            self.mcp_manager = mcp_manager
            register_mcp_tools(self, mcp_manager)
        else:
            self.mcp_manager = self._init_mcp()

        # Initialize agent manager and register agent tools
        self.agent_manager = AgentManager()
        self._register_agent_tools()

        if self.bg_store is not None:
            self.bg_store.recover()

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

        # When the host injects a ``project_instructions`` string,
        # skip the AGENTAO.md disk read and use the override verbatim.
        if project_instructions is not None:
            self.project_instructions = project_instructions
        else:
            self.project_instructions = self._load_project_instructions()

        # Replay recorder + adapter are created lazily in
        # ``start_replay()``; the no-op ``ReplayConfig()`` stays in
        # place when no config is injected so the transport stack
        # carries zero replay overhead.
        self._replay_recorder: Optional[ReplayRecorder] = None
        self._replay_adapter: Optional[ReplayAdapter] = None
        self._replay_config: ReplayConfig = replay_config or ReplayConfig()

        # Initialize tool runner (encapsulates 4-phase tool execution pipeline)
        self.tool_runner = ToolRunner(
            tools=self.tools,
            permission_engine=self.permission_engine,
            transport=self.transport,
            logger=self.llm.logger,
            sandbox_policy=self.sandbox_policy,
        )

    @property
    def _llm_config(self) -> Dict[str, Any]:
        """Live snapshot of the parent's effective provider config.

        Read at every access so sub-agents launched after a runtime
        ``set_model`` / ``maxTokens`` change inherit the active values
        rather than the construction-time snapshot.
        """
        return {
            "api_key": self.llm.api_key,
            "base_url": self.llm.base_url,
            "model": self.llm.model,
            "temperature": self.llm.temperature,
            "max_tokens": self.llm.max_tokens,
        }

    @property
    def working_directory(self) -> Path:
        """Effective working directory for this runtime.

        Frozen at construction (required keyword arg since 0.3.0).
        Two Agentao instances created with different
        ``working_directory`` values report independent paths even in
        the same process. ``os.chdir`` inside the host has no effect on
        an already-constructed Agentao.
        """
        return self._working_directory

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

    async def arun(
        self,
        user_message: str,
        max_iterations: int = 100,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> str:
        """Async wrapper around :meth:`chat` for embedded async hosts.

        Runtime internals stay sync (the chat loop, tool execution,
        permission, and replay surfaces are all sequential I/O). This
        method bridges through ``run_in_executor`` so async hosts can
        ``await agent.arun(...)`` without their own thread bridge while
        the same turn lifecycle from :meth:`chat` runs unchanged.

        Cancellation, replay, and ``max_iterations`` behave identically
        across both surfaces; the executor thread reads the same
        cancellation token. If the awaiting task is cancelled (e.g.
        ``asyncio.wait_for`` timeout, client disconnect) we forward the
        signal to the in-flight ``chat()`` call so the executor thread
        actually winds down instead of running to completion against
        the now-detached host.
        """
        loop = asyncio.get_running_loop()
        token = cancellation_token if cancellation_token is not None else CancellationToken()
        future = loop.run_in_executor(
            None, self.chat, user_message, max_iterations, token
        )
        try:
            return await future
        except asyncio.CancelledError:
            token.cancel("async-cancel")
            raise

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
        tools_schema = self.tools.to_openai_format(plan_mode=self._plan_mode)
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
