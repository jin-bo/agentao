"""Main agent logic for Agentao."""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .llm import LLMClient
from .permissions import PermissionEngine
from .tool_runner import ToolRunner
from .tools import (
    ToolRegistry,
    ReadFileTool,
    WriteFileTool,
    EditTool,
    ReadFolderTool,
    FindFilesTool,
    SearchTextTool,
    ShellTool,
    WebFetchTool,
    GoogleSearchTool,
    SaveMemoryTool,
    ActivateSkillTool,
    AskUserTool,
    TodoWriteTool,
)
from .agents import AgentManager
from .agents.tools import CancelBackgroundAgentTool, CheckBackgroundAgentTool, drain_bg_notifications
from .cancellation import CancellationToken, AgentCancelledError
from .plan import PlanSession, build_plan_prompt
from .skills import SkillManager
from .context_manager import ContextManager, is_context_too_long_error, _get_tiktoken_encoding
from .mcp import load_mcp_config, McpClientManager, McpTool
from .sandbox import SandboxPolicy
from .transport import AgentEvent, EventType, NullTransport, build_compat_transport


MAX_REASONING_HISTORY_CHARS = 500  # Truncate reasoning_content in history to ~125 tokens


def _serialize_tool_call(tc) -> dict:
    """Serialize a tool call object to a dict for conversation history.

    Uses model_dump() to preserve ALL Pydantic extra fields at their correct level.
    This handles Gemini's thought_signature (and similar fields) regardless of
    which level they appear at in the response (tc vs tc.function).
    Falls back to manual construction for non-Pydantic objects.
    """
    if hasattr(tc, "model_dump"):
        return tc.model_dump()
    # Fallback for non-Pydantic objects
    entry: Dict[str, Any] = {
        "id": tc.id,
        "type": "function",
        "function": {
            "name": tc.function.name,
            "arguments": tc.function.arguments,
        },
    }
    thought_sig = getattr(tc.function, "thought_signature", None)
    if thought_sig is None:
        thought_sig = getattr(tc, "thought_signature", None)
    if thought_sig is not None:
        entry["function"]["thought_signature"] = thought_sig
    return entry


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
        """Register all available tools."""
        tools_to_register = [
            ReadFileTool(),
            WriteFileTool(),
            EditTool(),
            ReadFolderTool(),
            FindFilesTool(),
            SearchTextTool(),
            ShellTool(),
            WebFetchTool(),
            GoogleSearchTool(),
            self.memory_tool,
            ActivateSkillTool(self.skill_manager),
            AskUserTool(ask_user_callback=self.transport.ask_user),
            self.todo_tool,
            CheckBackgroundAgentTool(),
            CancelBackgroundAgentTool(),
        ]

        # Bind per-session working directory onto each tool (Issue 05).
        # When the agent was constructed without ``working_directory`` (CLI
        # default), ``self._explicit_working_directory`` is ``None`` and the
        # tools' ``_resolve_path`` helpers fall through to legacy process-cwd
        # behavior, so this loop is a no-op for CLI runs.
        wd = self._explicit_working_directory
        for tool in tools_to_register:
            tool.working_directory = wd
            self.tools.register(tool)

    def _init_mcp(self) -> Optional[McpClientManager]:
        """Load MCP config, connect servers, and register discovered tools.

        Sources merged (later overrides earlier):

          1. ``~/.agentao/mcp.json``  (global, file)
          2. ``<cwd>/.agentao/mcp.json``  (project, file)
          3. ``self._extra_mcp_servers``  (Issue 11: ACP session-scoped)

        Steps 1+2 are loaded by :func:`load_mcp_config`. Step 3 is the
        in-memory dict captured at construction time. Names collide on
        a "last writer wins" basis so an ACP client can override a
        project's mcp.json without touching disk.

        All errors are logged and downgraded to a no-op return so a
        single broken MCP server cannot crash session creation. Tools
        from servers that connected successfully are still registered.
        """
        try:
            configs = load_mcp_config(project_root=self.working_directory)
        except Exception as e:
            self.llm.logger.warning(f"Failed to load MCP config: {e}")
            configs = {}

        # Merge ACP-injected configs on top of file-loaded ones (Issue 11).
        # We treat ``configs`` as a fresh dict and ``self._extra_mcp_servers``
        # as overrides — same per-name semantics as ``load_mcp_config``'s
        # global-then-project merge.
        if self._extra_mcp_servers:
            merged = dict(configs)
            for name, override in self._extra_mcp_servers.items():
                if name in merged:
                    self.llm.logger.info(
                        "MCP: ACP session config overrides file-loaded server %r",
                        name,
                    )
                merged[name] = override
            configs = merged

        if not configs:
            return None

        manager = McpClientManager(configs)
        try:
            manager.connect_all()
        except Exception as e:
            self.llm.logger.warning(f"MCP connection error: {e}")

        # Register discovered tools
        for server_name, mcp_tool_def in manager.get_all_tools():
            client = manager.get_client(server_name)
            trusted = client.is_trusted if client else False
            tool = McpTool(
                server_name=server_name,
                mcp_tool=mcp_tool_def,
                call_fn=manager.call_tool,
                trusted=trusted,
            )
            self.tools.register(tool)
            self.llm.logger.info(f"Registered MCP tool: {tool.name}")

        count = sum(1 for _ in manager.get_all_tools())
        if count:
            self.llm.logger.info(f"MCP: {count} tools from {len(manager.clients)} server(s)")
        return manager

    def close(self) -> None:
        """Clean up resources (MCP connections, event loops).

        NOTE: SessionEnd hooks are dispatched by the CLI layer
        (on_session_end / _dispatch_session_end_hooks) which runs before
        close() on every exit path.  We intentionally do NOT duplicate
        the dispatch here to avoid double-firing.
        """
        if self.mcp_manager is not None:
            try:
                self.mcp_manager.disconnect_all()
            except Exception as e:
                self.llm.logger.warning(f"Error disconnecting MCP: {e}")
            self.mcp_manager = None

    def _register_agent_tools(self):
        """Register agent tools (after base tools are registered)."""
        if self.agent_manager is None:
            return

        # Maps sub-agent tool_name → call_id so TOOL_OUTPUT and TOOL_COMPLETE
        # events can carry the same stable key as their TOOL_START.
        # Keyed by name — works for serial and different-named parallel calls.
        _subagent_call_ids: dict = {}

        def _agent_step_cb(name, args):
            if name is None:
                self.transport.emit(AgentEvent(EventType.TURN_START, {}))
            elif name == "__agent_start__":
                self.transport.emit(AgentEvent(EventType.AGENT_START, {
                    "agent": args.agent_name,
                    "task": args.task,
                    "max_turns": args.max_turns,
                }))
            elif name == "__agent_end__":
                self.transport.emit(AgentEvent(EventType.AGENT_END, {
                    "agent": args.agent_name,
                    "state": args.state,
                    "turns": args.turns,
                    "tool_calls": args.tool_calls,
                    "tokens": args.tokens,
                    "duration_ms": args.duration_ms,
                    "error": args.error,
                }))
            else:
                # Extract call_id injected by build_compat_transport; fall back to name.
                _args = dict(args) if isinstance(args, dict) else {}
                call_id = _args.pop("__call_id__", None) or name
                _subagent_call_ids[name] = call_id
                self.transport.emit(AgentEvent(EventType.TOOL_START, {
                    "tool": name, "args": _args, "call_id": call_id,
                }))

        agent_tools = self.agent_manager.create_agent_tools(
            all_tools=self.tools.tools,
            llm_config=self._llm_config,
            confirmation_callback=self.transport.confirm_tool,
            step_callback=_agent_step_cb,
            output_callback=lambda name, chunk: self.transport.emit(
                AgentEvent(EventType.TOOL_OUTPUT, {
                    "tool": name, "chunk": chunk,
                    "call_id": _subagent_call_ids.get(name, name),
                })
            ),
            tool_complete_callback=lambda name: self.transport.emit(
                AgentEvent(EventType.TOOL_COMPLETE, {
                    "tool": name,
                    "call_id": _subagent_call_ids.pop(name, name),
                    "status": "ok", "duration_ms": 0, "error": None,
                })
            ),
            ask_user_callback=self.transport.ask_user,
            max_context_tokens=self.context_manager.max_tokens,
            parent_messages_getter=lambda: self.messages,
            cancellation_token_getter=lambda: self._current_token,
            readonly_mode_getter=lambda: getattr(self, 'tool_runner', None) is not None and self.tool_runner.readonly_mode,
            permission_mode_getter=lambda: getattr(self.tool_runner, '_permission_engine', None) and self.tool_runner._permission_engine.active_mode,
        )
        for agent_tool in agent_tools:
            self.tools.register(agent_tool)

    def _build_reliability_section(self) -> str:
        """Return reliability principles injected unconditionally into every system prompt."""
        return (
            "\n\n=== Reliability Principles ===\n"
            "1. Only assert facts about files or code after reading them with a tool. "
            "Do not state what a file contains without first using read_file or search_file_content.\n"
            "2. When a tool result differs from what you expected, state the discrepancy "
            "explicitly before continuing.\n"
            "3. When a tool returns an error, reason about the cause before retrying "
            "with a different approach.\n"
            "4. Distinguish verified information (from tool output) from inferences. "
            "Use 'the file shows...' for facts, 'I expect...' for inferences."
        )

    def _build_operational_guidelines(self, plan_mode: bool = False) -> str:
        """Return operational guidelines injected into every system prompt."""
        task_completion_section = (
            "## Task Completion\n"
            "- In plan mode, stop after the research and proposal are complete. Do not "
            "attempt implementation, editing, or execution.\n"
            "- If the plan is blocked by missing requirements, ask the user or list "
            "open questions, then stop.\n"
        ) if plan_mode else (
            "## Task Completion\n"
            "- Work autonomously until the task is fully resolved before yielding back to the user.\n"
            "- If a fix introduces a new error, keep iterating rather than stopping and reporting the error.\n"
            "- Only stop and ask when you are genuinely blocked on missing information "
            "you cannot discover with tools.\n\n"
        )

        return (
            "\n\n=== Operational Guidelines ===\n\n"

            "## Tone and Style\n"
            "- Default to short, direct replies (a few lines). Expand only when the user asks "
            "for detail, when explaining a non-trivial plan, or when the answer genuinely requires it.\n"
            "- No Chitchat: omit preambles ('Okay, I will now...') and postambles ('I have finished...') "
            "unless stating intent before a modifying command.\n"
            "- Tools vs. Text: use tools for actions, text only for communication. "
            "No explanatory comments inside tool calls.\n"
            "- Formatting: GitHub-flavored Markdown; responses render in monospace.\n\n"

            "## Shell Command Efficiency\n"
            "IT IS CRITICAL TO FOLLOW THESE TO AVOID EXCESSIVE TOKEN CONSUMPTION.\n"
            "- Prefer quiet/silent flags: e.g. `npm install --silent`, `pip install -q`, "
            "`git --no-pager`, `PAGER=cat`.\n"
            "- For commands with potentially long or unpredictable output, redirect to temp files:\n"
            "  `command > /tmp/out.log 2> /tmp/err.log`\n"
            "  Then inspect with `grep`/`tail`/`head`. Remove temp files when done.\n"
            "- Exception: if the command's full output is essential for understanding, "
            "avoid aggressive quieting.\n\n"

            "## Tool Usage\n"
            "- Parallelism: execute independent tool calls in parallel in a single response when feasible.\n"
            "- Interactive commands: always prefer non-interactive flags "
            "(e.g. `--ci`, `--no-pager`, `--yes`, `--non-interactive`) "
            "unless a persistent process is specifically required.\n"
            "- Background processes: set `is_background=true` for commands that will not stop on their own "
            "(servers, file watchers).\n"
            "- Respect cancellations: if a user cancels a tool call, do not retry it in the same turn. "
            "Ask if they prefer an alternative approach.\n"
            "- Remembering facts: call save_memory when the user explicitly asks, or when the user "
            "clearly states a durable preference or fact useful across sessions "
            "(e.g. preferred coding style, common project paths, personal aliases). "
            "Do NOT save ephemeral details or general project context. "
            "If unsure, ask first: 'Should I remember that?'\n\n"

            "## Code Conventions\n"
            "- Follow the existing code style, conventions, and file structure of the project.\n"
            "- Minimize comments: only add them where the logic is non-obvious. "
            "Do not add docstrings to unchanged functions.\n"
            "- After making code changes, run the project's linter or type checker if one exists "
            "(e.g. `mypy`, `ruff`, `eslint`).\n"
            "- Use absolute file paths in all file tool calls.\n"
            "- Verify that any library or framework you reference actually exists in the project "
            "before using it.\n\n"

            f"{task_completion_section}"

            "## Security\n"
            "- Before running shell commands that modify the filesystem, codebase, or system state, "
            "briefly state the command's purpose and potential impact.\n"
            "- Never write code that exposes, logs, or commits secrets, API keys, or sensitive information."
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt for the agent.

        Returns:
            System prompt string
        """
        if self._plan_mode:
            agent_instructions = f"""You are Agentao, a helpful AI assistant with access to various tools and skills.

Current Working Directory: {self.working_directory}

In plan mode, use tools only to research, inspect, and verify facts needed for the proposal. Do not use tools to execute changes or simulate implementation. If you need clarification, ask the user."""
        else:
            agent_instructions = f"""You are Agentao, a helpful AI assistant with access to various tools and skills.

Current Working Directory: {self.working_directory}

Use tools proactively only when they materially improve correctness or are needed to verify ground truth. Do not use tools for casual greetings, small talk, or obvious questions. If you need clarification, ask the user."""

        # Start with project-specific instructions if available
        if self.project_instructions:
            prompt = f"""=== Project Instructions ===

{self.project_instructions}

=== Agent Instructions ===

{agent_instructions}"""
        else:
            prompt = agent_instructions

        # --- Stable prefix (cached across turns) ---------------------------
        # Order here is intentional: Reliability → Operational → Reasoning →
        # Agents → <memory-stable>. Keeping volatile content (skills, todos,
        # dynamic recall, plan suffix) below this prefix maximizes
        # prompt-cache reuse across turns.

        # Inject reliability principles unconditionally
        prompt += self._build_reliability_section()

        # Inject operational guidelines unconditionally
        prompt += self._build_operational_guidelines(plan_mode=self._plan_mode)

        # Instruct LLM to show reasoning when a thinking/reasoning sink is active
        if self._has_thinking_handler:
            prompt += (
                "\n\n=== Reasoning Requirement ===\n"
                "Before any tool call that modifies state, runs a shell command, "
                "or is part of a multi-step investigation, write 2-3 sentences:\n"
                "- Action: What tool you are calling and with what input.\n"
                "- Expectation: What you expect to find or what the result should confirm.\n"
                "- If wrong: What you will do if the result contradicts your expectation.\n"
                "Skip this preamble for trivial read-only lookups "
                "(single read_file, list_directory, glob). "
                "Be specific and falsifiable when you do write it."
            )

        # Add available agents section (suppressed in plan mode — delegation contradicts research-only intent)
        if not self._plan_mode and self.agent_manager:
            agent_descriptions = self.agent_manager.list_agents()
            if agent_descriptions:
                prompt += "\n\n=== Available Agents ===\n"
                prompt += "For the following types of tasks, prefer delegating to a specialized agent:\n\n"
                for agent_name, desc in agent_descriptions.items():
                    tool_name = f"agent_{agent_name.replace('-', '_')}"
                    prompt += f"- {agent_name}: {desc} (use tool: {tool_name})\n"
                prompt += "\nCall the corresponding agent tool to delegate a task."

        # Stable memory block (structured, XML-escaped) — last item in the stable prefix
        stable_records = self.memory_manager.get_stable_entries()
        cross_session_tail = self.memory_manager.get_cross_session_tail()
        stable_block = self.memory_renderer.render_stable_block(
            stable_records, session_tail=cross_session_tail,
        )
        self._stable_block_chars = len(stable_block)
        if stable_block:
            prompt += "\n\n" + stable_block

        # --- Volatile suffix (changes within a session) --------------------

        # Add available skills section (excluding already-active skills to save tokens)
        available_skills = self.skill_manager.list_available_skills()
        active_names = set(self.skill_manager.get_active_skills().keys())
        inactive_skills = [s for s in available_skills if s not in active_names]
        if inactive_skills:
            prompt += "\n\n=== Available Skills ===\n"
            prompt += "You have access to specialized skills. Use the 'activate_skill' tool to activate them when needed.\n\n"

            for skill_name in sorted(inactive_skills):
                skill_info = self.skill_manager.get_skill_info(skill_name)
                if skill_info:
                    description = skill_info.get('description', 'No description available')
                    when_to_use = skill_info.get('when_to_use', '')
                    prompt += f"• {skill_name}: {description}\n"
                    if when_to_use:
                        prompt += f"  Activate when: {when_to_use}\n"

            prompt += "\nWhen the user's request matches a skill's description, use the activate_skill tool before proceeding with the task."

        # Add active skills context if any
        skills_context = self.skill_manager.get_skills_context()
        if skills_context:
            prompt += "\n\n" + skills_context

        # Inject current task list if any todos exist
        todos = self.todo_tool.get_todos()
        if todos:
            _icons = {"pending": "○", "in_progress": "◉", "completed": "✓"}
            prompt += "\n\n=== Current Task List ===\n"
            for todo in todos:
                icon = _icons.get(todo["status"], "○")
                prompt += f"- {icon} [{todo['status']}] {todo['content']}\n"
            prompt += "\nUpdate task statuses with todo_write as you complete each step."

        # Dynamic recall (per-turn; query-specific top-k candidates)
        # Exclude entries already shown in the stable block to avoid duplication.
        context_hints = self._extract_context_hints()
        stable_ids = {r.id for r in stable_records}
        candidates = self.memory_retriever.recall_candidates(
            query=self._last_user_message or "",
            context_hints=context_hints,
            exclude_ids=stable_ids,
        )
        if candidates:
            recall_block = self.memory_renderer.render_dynamic_block(candidates)
            if recall_block:
                prompt += "\n\n" + recall_block

        if self._plan_mode:
            prompt += build_plan_prompt(self._plan_session)

        return prompt

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

    def _llm_call(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
                  cancellation_token: Optional[CancellationToken] = None) -> Any:
        """Call LLM with streaming; emit LLM_TEXT events per chunk via transport."""
        return self.llm.chat_stream(
            messages=messages,
            tools=tools,
            max_tokens=self.llm.max_tokens,
            on_text_chunk=lambda chunk: self.transport.emit(
                AgentEvent(EventType.LLM_TEXT, {"chunk": chunk})
            ),
            cancellation_token=cancellation_token,
        )

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
        try:
            return self._chat_inner(user_message, max_iterations, token)
        except KeyboardInterrupt:
            token.cancel("user-cancel")
            self.messages.append({"role": "assistant", "content": "[Interrupted]"})
            return "[Interrupted by user]"
        except AgentCancelledError as e:
            self.messages.append({"role": "assistant", "content": f"[Cancelled: {e.reason}]"})
            return f"[Cancelled: {e.reason}]"
        finally:
            self._current_token = None

    def _chat_inner(self, user_message: str, max_iterations: int,
                    token: CancellationToken) -> str:
        """Inner chat loop — called by chat(). Raises AgentCancelledError on cancellation."""
        # Prepend volatile context (date/time) as <system-reminder> so the system prompt
        # itself stays stable and benefits from prompt cache across turns.
        now = datetime.now()
        system_reminder = (
            f"<system-reminder>\n"
            f"Current Date/Time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({now.strftime('%A')})\n"
            f"</system-reminder>\n"
        )
        self._last_user_message = user_message

        # Dispatch UserPromptSubmit plugin hooks before processing.
        if self._plugin_hook_rules:
            from .plugins.hooks import (
                ClaudeHookPayloadAdapter,
                PluginHookDispatcher,
            )
            _cwd = self.working_directory
            adapter = ClaudeHookPayloadAdapter()
            payload = adapter.build_user_prompt_submit(
                user_message=user_message, session_id=self._session_id, cwd=_cwd,
            )
            dispatcher = PluginHookDispatcher(cwd=_cwd)
            ups_result = dispatcher.dispatch_user_prompt_submit(
                payload=payload, rules=self._plugin_hook_rules,
            )
            if ups_result.blocking_error:
                return f"[Blocked by hook] {ups_result.blocking_error}"
            if ups_result.prevent_continuation:
                return f"[Hook stopped] {ups_result.stop_reason or 'Hook prevented continuation'}"
            # Inject additional context from hooks into the user message.
            if ups_result.additional_contexts:
                extra = "\n".join(
                    f"<user-prompt-submit-hook>\n{ctx}\n</user-prompt-submit-hook>"
                    for ctx in ups_result.additional_contexts
                )
                user_message = extra + "\n" + user_message

        self.add_message("user", system_reminder + user_message)

        # Build system prompt (injects all memories)
        system_prompt = self._build_system_prompt()

        # Prepare messages with system prompt
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + self.messages

        # Get tools in OpenAI format; hide plan tools when not in plan mode
        _plan_tool_names = {"plan_save", "plan_finalize"}
        tools = [
            t for t in self.tools.to_openai_format()
            if self._plan_mode or t["function"]["name"] not in _plan_tool_names
        ]

        # Reset doom-loop counter for this chat() invocation
        self.tool_runner.reset()

        # System prompt dirty-flag: only rebuild when skills or memories change
        _current_active_skills = frozenset(self.skill_manager.get_active_skills().keys())
        _current_memory_version = self.memory_manager.write_version

        # Call LLM and handle multiple rounds of tool calls
        iteration = 0
        assistant_message = None
        while True:
            # Check if max iterations reached; ask user what to do if callback is set
            if iteration >= max_iterations:
                pending = []
                if assistant_message and getattr(assistant_message, "tool_calls", None):
                    for tc in assistant_message.tool_calls:
                        pending.append({"name": tc.function.name, "args": tc.function.arguments})

                _handler = getattr(self.transport, "on_max_iterations", None)
                result = _handler(max_iterations, pending) if callable(_handler) else {"action": "stop"}
                action = result.get("action", "stop")
                if action == "continue":
                    iteration = 0
                elif action == "new_instruction":
                    iteration = 0
                    new_msg = result.get("message", "")
                    if new_msg:
                        self.messages.append({"role": "user", "content": new_msg})
                        messages_with_system = [
                            {"role": "system", "content": system_prompt}
                        ] + self.messages
                else:  # "stop"
                    break

            iteration += 1
            self.llm.logger.info(f"LLM iteration {iteration}/{max_iterations}")

            # Microcompact (55-65%): cheaply strip large tool results, no LLM call.
            if self.context_manager.needs_microcompaction(messages_with_system):
                self.messages = self.context_manager.microcompact_messages(self.messages)
                messages_with_system = [
                    {"role": "system", "content": system_prompt}
                ] + self.messages

            # Full compress (>= 65%): LLM summarization of early messages.
            # Check happens every iteration so tool results that bloat context are caught.
            if self.context_manager.needs_compression(messages_with_system):
                self.llm.logger.info("Context compression triggered inside loop")
                self.messages = self.context_manager.compress_messages(self.messages, is_auto=True)
                self.context_manager._last_api_prompt_tokens = None  # stale after compression
                system_prompt = self._build_system_prompt()
                messages_with_system = [
                    {"role": "system", "content": system_prompt}
                ] + self.messages
                self.llm.logger.info(f"Context compressed to {len(self.messages)} messages")

            # Inject background-agent completion notifications so the LLM is
            # automatically informed without having to poll check_background_agent.
            _bg_notes = drain_bg_notifications()
            if _bg_notes:
                note_content = "\n\n".join(_bg_notes)
                self.messages.append({
                    "role": "user",
                    "content": (
                        f"<system-reminder>\n"
                        f"Background agent update:\n{note_content}\n"
                        f"</system-reminder>"
                    ),
                })
                messages_with_system = [
                    {"role": "system", "content": system_prompt}
                ] + self.messages

            # Check cancellation before each LLM call (e.g. Ctrl+C fired during
            # tool execution of the previous iteration).
            token.check()

            # Signal transport to reset display before each LLM call
            self.transport.emit(AgentEvent(EventType.TURN_START, {}))

            # Call LLM — catch context-overflow errors and force-compress once before giving up
            try:
                response = self._llm_call(messages_with_system, tools, token)
            except Exception as e:
                if not is_context_too_long_error(e):
                    # Non-context errors (content filter, rate limit, streaming error, etc.):
                    # log and return a clean inline error so conversation state stays valid.
                    err_msg = f"[LLM API error: {e}]"
                    self.llm.logger.error(f"LLM call failed: {e}")
                    self.messages.append({"role": "assistant", "content": err_msg})
                    return err_msg
                self.llm.logger.warning(f"Context overflow from API, forcing compression: {e}")
                self.messages = self.context_manager.compress_messages(self.messages)
                self.context_manager._last_api_prompt_tokens = None  # stale after compression
                system_prompt = self._build_system_prompt()
                messages_with_system = [
                    {"role": "system", "content": system_prompt}
                ] + self.messages
                try:
                    response = self._llm_call(messages_with_system, tools, token)
                except Exception as e2:
                    if is_context_too_long_error(e2):
                        # System prompt alone may be too large; keep only the last 2 messages
                        self.llm.logger.warning("Context still too long after compression, keeping minimal history")
                        self.messages = self.messages[-2:]
                        messages_with_system = [
                            {"role": "system", "content": system_prompt}
                        ] + self.messages
                        try:
                            response = self._llm_call(messages_with_system, tools, token)
                        except Exception as e3:
                            err_msg = f"[LLM API error: {e3}]"
                            self.llm.logger.error(f"LLM call failed after compression: {e3}")
                            self.messages.append({"role": "assistant", "content": err_msg})
                            return err_msg
                    else:
                        err_msg = f"[LLM API error: {e2}]"
                        self.llm.logger.error(f"LLM call failed after compression: {e2}")
                        self.messages.append({"role": "assistant", "content": err_msg})
                        return err_msg

            # Tier 1 token count: record real prompt_tokens from API response
            if getattr(response, "usage", None) and getattr(response.usage, "prompt_tokens", None):
                self.context_manager.record_api_usage(response.usage.prompt_tokens)

            # Process response
            assistant_message = response.choices[0].message

            # Check if tool calls are needed
            if assistant_message.tool_calls:
                self.llm.logger.info(f"Processing {len(assistant_message.tool_calls)} tool call(s) in iteration {iteration}")

                # Extract reasoning_content (thinking-enabled APIs like DeepSeek Reasoner)
                reasoning_content = getattr(assistant_message, "reasoning_content", None)

                # Show reasoning_content via transport if present
                if reasoning_content:
                    self.transport.emit(AgentEvent(EventType.THINKING, {"text": reasoning_content}))

                # Show LLM content text (content before tool calls) if present
                reasoning = (assistant_message.content or "").strip()
                if reasoning:
                    self.transport.emit(AgentEvent(EventType.THINKING, {"text": reasoning}))

                # Build assistant message with tool calls
                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_message.content or "",
                    "tool_calls": [
                        _serialize_tool_call(tc)
                        for tc in assistant_message.tool_calls
                    ],
                }

                # Preserve reasoning_content so API accepts this message in subsequent calls.
                # Truncate to avoid context bloat (already shown live via thinking_callback).
                if reasoning_content is not None:
                    stored = reasoning_content[:MAX_REASONING_HISTORY_CHARS]
                    if len(reasoning_content) > MAX_REASONING_HISTORY_CHARS:
                        stored += "..."
                    assistant_msg["reasoning_content"] = stored

                self.messages.append(assistant_msg)

                # Execute tool calls via ToolRunner (4-phase pipeline).
                doom_triggered, tool_results = self.tool_runner.execute(
                    assistant_message.tool_calls,
                    cancellation_token=token,
                )
                self.messages.extend(tool_results)
                if doom_triggered:
                    break
                if token.is_cancelled:
                    raise AgentCancelledError(token.reason)

                # Update messages for next iteration.
                # Only rebuild system prompt if skills or memories changed (dirty flag).
                new_active_skills = frozenset(self.skill_manager.get_active_skills().keys())
                new_memory_version = self.memory_manager.write_version
                if new_active_skills != _current_active_skills or new_memory_version != _current_memory_version:
                    _current_active_skills = new_active_skills
                    _current_memory_version = new_memory_version
                    system_prompt = self._build_system_prompt()
                messages_with_system = [
                    {"role": "system", "content": system_prompt}
                ] + self.messages

                # Continue loop to check if more tool calls are needed
            else:
                # No more tool calls, we have the final response
                self.llm.logger.info(f"Reached final response in iteration {iteration}")
                assistant_content = assistant_message.content or ""
                reasoning_content = getattr(assistant_message, "reasoning_content", None)
                final_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
                if reasoning_content is not None:
                    stored = reasoning_content[:MAX_REASONING_HISTORY_CHARS]
                    if len(reasoning_content) > MAX_REASONING_HISTORY_CHARS:
                        stored += "..."
                    final_msg["reasoning_content"] = stored
                self.messages.append(final_msg)
                return assistant_content

        # If we hit max iterations, return what we have
        self.llm.logger.warning(f"Maximum tool call iterations ({max_iterations}) reached")
        assistant_content = assistant_message.content or "Maximum tool call iterations reached."
        reasoning_content = getattr(assistant_message, "reasoning_content", None)
        final_msg = {"role": "assistant", "content": assistant_content}
        if reasoning_content is not None:
            stored = reasoning_content[:MAX_REASONING_HISTORY_CHARS]
            if len(reasoning_content) > MAX_REASONING_HISTORY_CHARS:
                stored += "..."
            final_msg["reasoning_content"] = stored
        self.messages.append(final_msg)
        return assistant_content

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
        """Reinitialize the LLM client with new provider credentials.

        Args:
            api_key: API key for the new provider
            base_url: Base URL for the new provider's API endpoint
            model: Model name to use with the new provider
        """
        self.llm.reconfigure(api_key=api_key, base_url=base_url, model=model)

    def set_model(self, model: str) -> str:
        """Set the model to use.

        Args:
            model: Model name

        Returns:
            Status message
        """
        old_model = self.llm.model
        self.llm.model = model
        self.context_manager._encoding = _get_tiktoken_encoding(model)
        self.context_manager._last_api_prompt_tokens = None  # stale after model change
        self.llm.logger.info(f"Model changed from {old_model} to {model}")
        return f"Model changed from {old_model} to {model}"

    def list_available_models(self) -> List[str]:
        """List models available via the API.

        Returns:
            Sorted list of model IDs from the configured endpoint

        Raises:
            RuntimeError: If the API call fails
        """
        try:
            models_page = self.llm.client.models.list()
            return sorted([m.id for m in models_page.data])
        except Exception as e:
            self.llm.logger.warning(f"Failed to fetch models from API: {e}")
            raise RuntimeError(f"Could not fetch model list: {e}") from e
