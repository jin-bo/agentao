"""Main agent logic for Agentao."""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .llm import LLMClient
from .permissions import PermissionEngine
from .runtime import ChatLoopRunner, ToolRunner
from .runtime import model as _runtime_model
from .tools import ToolRegistry, SaveMemoryTool, TodoWriteTool
from .tooling import init_mcp, register_agent_tools, register_builtin_tools
from .agents import AgentManager
from .cancellation import CancellationToken, AgentCancelledError
from .plan import PlanSession
from .prompts import SystemPromptBuilder
from .skills import SkillManager
from .context_manager import ContextManager
from .mcp import McpClientManager
from .replay import (
    ReplayAdapter,
    ReplayConfig,
    ReplayRecorder,
    ReplayRetentionPolicy,
    load_replay_config,
)
from .replay.events import EventKind as _ReplayKind
from .sandbox import SandboxPolicy
from .transport import AgentEvent, EventType, NullTransport, build_compat_transport


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
        """Load project-specific instructions from AGENTAO.md.

        Returns:
            Project instructions content or None if file doesn't exist
        """
        try:
            # Look for AGENTAO.md in the runtime's working directory (Issue 05).
            agentao_md = self.working_directory / "AGENTAO.md"
            if agentao_md.exists():
                content = agentao_md.read_text(encoding='utf-8')
                self.llm.logger.info(f"Loaded project instructions from {agentao_md}")
                return content
        except Exception as e:
            self.llm.logger.warning(f"Could not load AGENTAO.md: {e}")

        return None

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
        """Begin a new replay instance when ``replay.enabled=true``.

        Called by the CLI on session start and by ACP session/new and
        session/load after the session id is known. The call is
        idempotent — a second call while a recorder is already open is a
        no-op and returns the existing file path.

        Returns the replay file path when recording started, ``None``
        when recording is disabled or the recorder could not be created.
        """
        if session_id:
            self._session_id = session_id
        if not self._replay_config.enabled:
            return None
        if self._replay_recorder is not None:
            return self._replay_recorder.path
        sid = self._session_id or ""
        if not sid:
            return None
        try:
            recorder = ReplayRecorder.create(
                session_id=sid,
                project_root=self.working_directory,
                logger_=self.llm.logger,
                capture_flags=dict(self._replay_config.capture_flags),
            )
        except Exception as exc:
            self.llm.logger.warning("replay: start failed: %s", exc)
            return None
        if self._replay_config.deep_capture_enabled():
            # Deep-capture modes enlarge the replay file and may preserve
            # content that the default scanner can't fully redact (free-
            # form LLM messages, full tool results). Warn in the log so
            # the user sees it in agentao.log for audit purposes.
            on_flags = [
                k for k, v in self._replay_config.capture_flags.items() if v
            ]
            self.llm.logger.warning(
                "replay: deep-capture mode active (%s). File size and "
                "sensitivity may be higher than usual.",
                ", ".join(sorted(on_flags)),
            )
        self._replay_recorder = recorder
        adapter = ReplayAdapter(self.transport, recorder)
        self._replay_adapter = adapter
        # Route every downstream emit/confirm through the adapter. The
        # adapter forwards to the original inner transport, so display
        # and ACP behavior remain unchanged.
        self.transport = adapter
        try:
            self.tool_runner._transport = adapter
        except Exception:
            pass
        recorder.record(
            _ReplayKind.SESSION_STARTED,
            payload={
                "session_id": sid,
                "cwd": str(self.working_directory),
                "model": self.llm.model,
            },
        )
        # Best-effort retention pass: new instance created.
        try:
            ReplayRetentionPolicy(
                max_instances=self._replay_config.max_instances
            ).prune(self.working_directory)
        except Exception:
            pass
        return recorder.path

    def end_replay(self) -> None:
        """Finalize the current replay instance, if any.

        Emits ``session_ended`` and closes the file. Safe to call more
        than once. Restores the original inner transport so a subsequent
        ``start_replay()`` cycle can attach a fresh adapter.
        """
        recorder = self._replay_recorder
        adapter = self._replay_adapter
        if recorder is None and adapter is None:
            return
        if recorder is not None:
            try:
                recorder.record(
                    _ReplayKind.SESSION_ENDED,
                    payload={"session_id": self._session_id or ""},
                )
            except Exception:
                pass
            try:
                recorder.close()
            except Exception:
                pass
        # Detach adapter and restore the inner transport. Otherwise a
        # later ``start_replay()`` would wrap the adapter in a second
        # adapter and double-record every event.
        if adapter is not None:
            try:
                inner = adapter._inner
                if self.transport is adapter:
                    self.transport = inner
                if self.tool_runner._transport is adapter:
                    self.tool_runner._transport = inner
            except Exception:
                pass
        self._replay_recorder = None
        self._replay_adapter = None
        # Best-effort retention pass: instance ended.
        try:
            ReplayRetentionPolicy(
                max_instances=self._replay_config.max_instances
            ).prune(self.working_directory)
        except Exception:
            pass

    def reload_replay_config(self) -> ReplayConfig:
        """Re-read ``replay`` settings from disk.

        Called after ``/replay on`` / ``/replay off`` so a toggle takes
        effect on the next ``start_replay()`` without a CLI restart. The
        currently-open replay instance (if any) is intentionally left
        untouched — per spec, toggling only affects future instances.
        """
        try:
            self._replay_config = load_replay_config(self.working_directory)
        except Exception:
            self._replay_config = ReplayConfig()
        return self._replay_config

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
        """Extract file paths from recent messages to use as recall hints (§10.1).

        Handles both shapes the chat path can produce:

        - Plain string ``content``.
        - List of typed blocks (multimodal/tool-use); the canonical text
          block is ``{"type": "text", "text": "..."}``, matching how
          :meth:`ContextManager._format_for_summary` and
          :meth:`MemoryCrystallizer._user_message_text` consume them.
        """
        path_re = re.compile(r'[\w./\\-]+\.\w{2,6}')
        hints = []
        for msg in self.messages[-10:]:
            content = msg.get("content", "")
            if isinstance(content, str):
                hints.extend(path_re.findall(content))
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        hints.extend(path_re.findall(str(block.get("text", ""))))
        return hints[:20]

    # ------------------------------------------------------------------
    # Replay observability helpers (v1.1)
    # ------------------------------------------------------------------

    def _latest_session_summary_id(self) -> Optional[str]:
        """Return the id of the most recent session summary, or None."""
        if self.memory_manager is None:
            return None
        try:
            rows = self.memory_manager.get_recent_session_summaries(limit=1)
        except Exception:
            return None
        return rows[0].id if rows else None

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
        self.transport.emit(AgentEvent(EventType.CONTEXT_COMPRESSED, {
            "type": compression_type,
            "reason": reason,
            "pre_msgs": pre_msgs,
            "post_msgs": post_msgs,
            "pre_est_tokens": pre_tokens,
            "post_est_tokens": post_tokens,
            "duration_ms": duration_ms,
        }))

    def _emit_session_summary_if_new(self, previous_summary_id: Optional[str]) -> Optional[str]:
        """Emit SESSION_SUMMARY_WRITTEN when the latest summary id changed.

        Returns the (possibly unchanged) latest summary id so a caller
        can keep polling across multiple compression events in one turn.
        """
        if self.memory_manager is None:
            return previous_summary_id
        try:
            rows = self.memory_manager.get_recent_session_summaries(limit=1)
        except Exception:
            return previous_summary_id
        if not rows:
            return previous_summary_id
        current = rows[0]
        if current.id == previous_summary_id:
            return previous_summary_id
        self.transport.emit(AgentEvent(EventType.SESSION_SUMMARY_WRITTEN, {
            "summary_id": current.id,
            "session_id": self._session_id,
            "tokens_before": current.tokens_before,
            "messages_summarized": current.messages_summarized,
            "summary_size": len(current.summary_text or ""),
        }))
        return current.id

    def _llm_call(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
                  cancellation_token: Optional[CancellationToken] = None) -> Any:
        """Call LLM with streaming; emit LLM_TEXT events per chunk via transport.

        Also emits the v1.1 replay-observability events
        ``LLM_CALL_STARTED`` / ``LLM_CALL_DELTA`` / ``LLM_CALL_IO`` /
        ``LLM_CALL_COMPLETED`` so a replay reader can reconstruct what
        was sent to the LLM on each attempt and what came back. Metadata
        only by default; the two deep-capture flags enable full messages.
        """
        self._llm_call_seq = getattr(self, "_llm_call_seq", 0) + 1
        attempt = self._llm_call_seq

        system_text = ""
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_text = content
                break
        system_prompt_hash = hashlib.sha256(
            system_text.encode("utf-8", errors="replace"),
        ).hexdigest()[:16]

        tool_schemas = tools or []
        tool_names = sorted(
            t.get("function", {}).get("name", "") for t in tool_schemas
        )
        tools_hash = hashlib.sha256(
            json.dumps(tool_names).encode("utf-8"),
        ).hexdigest()[:16]

        capture_flags = self._replay_config.capture_flags if self._replay_config else {}

        started_payload: Dict[str, Any] = {
            "attempt": attempt,
            "model": self.llm.model,
            "temperature": self.llm.temperature,
            "max_tokens": self.llm.max_tokens,
            "n_messages": len(messages),
            "n_tool_messages": sum(
                1 for m in messages if isinstance(m, dict) and m.get("role") == "tool"
            ),
            "n_system_reminder_blocks": sum(
                1 for m in messages
                if isinstance(m, dict)
                and m.get("role") == "user"
                and "<system-reminder>" in str(m.get("content", ""))
            ),
            "system_prompt_hash": system_prompt_hash,
            "tools_hash": tools_hash,
            "tool_count": len(tool_names),
        }
        self.transport.emit(AgentEvent(EventType.LLM_CALL_STARTED, started_payload))

        # Delta capture (default on): just-added messages since the last
        # _llm_call in this turn. The first call of the turn reports the
        # full message list (delta_start_index == 0).
        if capture_flags.get("capture_llm_delta", True):
            delta_start = getattr(self, "_llm_call_last_msg_count", 0)
            if delta_start > len(messages):
                # Caller shrank history (compression / retry with fewer
                # messages). Treat it as a reset so the reader sees the
                # post-shrink list rather than negative slicing.
                delta_start = 0
            added = messages[delta_start:]
            self.transport.emit(AgentEvent(EventType.LLM_CALL_DELTA, {
                "attempt": attempt,
                "delta_start_index": delta_start,
                "total_messages": len(messages),
                "added_messages": added,
            }))
            self._llm_call_last_msg_count = len(messages)

        # Full IO capture (opt-in). Cost is large: every call writes the
        # entire messages array. Scanner still runs inside the recorder.
        if capture_flags.get("capture_full_llm_io", False):
            self.transport.emit(AgentEvent(EventType.LLM_CALL_IO, {
                "attempt": attempt,
                "messages": messages,
                "tools": tool_schemas,
            }))

        t0 = time.monotonic()
        try:
            response = self.llm.chat_stream(
                messages=messages,
                tools=tools,
                max_tokens=self.llm.max_tokens,
                on_text_chunk=lambda chunk: self.transport.emit(
                    AgentEvent(EventType.LLM_TEXT, {"chunk": chunk})
                ),
                cancellation_token=cancellation_token,
            )
        except Exception as exc:
            self.transport.emit(AgentEvent(EventType.LLM_CALL_COMPLETED, {
                "attempt": attempt,
                "status": "error",
                "duration_ms": round((time.monotonic() - t0) * 1000),
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:500],
                "finish_reason": None,
                "prompt_tokens": None,
                "completion_tokens": None,
            }))
            raise

        finish_reason: Optional[str] = None
        prompt_tokens: Optional[int] = None
        completion_tokens: Optional[int] = None
        try:
            choices = getattr(response, "choices", None)
            if choices:
                finish_reason = getattr(choices[0], "finish_reason", None)
            usage = getattr(response, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", None)
                completion_tokens = getattr(usage, "completion_tokens", None)
        except Exception:
            pass

        self.transport.emit(AgentEvent(EventType.LLM_CALL_COMPLETED, {
            "attempt": attempt,
            "status": "ok",
            "duration_ms": round((time.monotonic() - t0) * 1000),
            "error_class": None,
            "error_message": None,
            "finish_reason": finish_reason,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }))
        return response

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
        token = cancellation_token or CancellationToken()
        self._current_token = token
        # Reset the per-turn LLM-call counters so ``attempt`` numbers in
        # LLM_CALL_* events restart at 1 and ``delta_start_index`` tracks
        # only messages added in the current chat() invocation.
        #
        # The first ``_llm_call`` of this turn receives ``[system] + self.messages``
        # after the new user message is appended. Seeding the baseline to
        # ``1 + len(self.messages)`` (system + pre-turn history) makes the
        # first LLM_CALL_DELTA emit only the messages added in this turn,
        # instead of replaying the entire accumulated conversation every turn.
        self._llm_call_seq = 0
        self._llm_call_last_msg_count = 1 + len(self.messages)
        # Snapshot the latest session-summary id so the inner loop can
        # fire SESSION_SUMMARY_WRITTEN each time compress_messages writes
        # a new one. Held on the instance so compression paths inside the
        # retry branches can update it without threading it through args.
        self._last_session_summary_id = self._latest_session_summary_id()
        # Snapshot the adapter so the finally block can emit end_turn even if
        # end_replay() is called concurrently (e.g. ACP session teardown) and
        # clears self._replay_adapter before this turn finishes unwinding.
        replay_adapter = self._replay_adapter
        if replay_adapter is not None:
            try:
                replay_adapter.begin_turn(user_message)
            except Exception:
                pass
        final_text = ""
        status = "ok"
        error_detail: Optional[str] = None
        try:
            final_text = self._chat_inner(user_message, max_iterations, token)
            return final_text
        except KeyboardInterrupt:
            token.cancel("user-cancel")
            self.messages.append({"role": "assistant", "content": "[Interrupted]"})
            final_text = "[Interrupted by user]"
            status = "cancelled"
            error_detail = "user-cancel"
            return final_text
        except AgentCancelledError as e:
            self.messages.append({"role": "assistant", "content": f"[Cancelled: {e.reason}]"})
            final_text = f"[Cancelled: {e.reason}]"
            status = "cancelled"
            error_detail = e.reason
            return final_text
        except Exception as e:
            status = "error"
            error_detail = str(e)
            raise
        finally:
            if replay_adapter is not None:
                try:
                    replay_adapter.end_turn(
                        final_text, status=status, error=error_detail,
                    )
                except Exception:
                    pass
            self._current_token = None

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
