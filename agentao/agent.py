"""Main agent logic for Agentao."""

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
from .agents.tools import drain_bg_notifications
from .cancellation import CancellationToken, AgentCancelledError
from .skills import SkillManager
from .context_manager import ContextManager, is_context_too_long_error, _get_tiktoken_encoding
from .mcp import load_mcp_config, McpClientManager, McpTool


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
    """Main chat agent with tool and skill support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
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
    ):
        """Initialize chat agent.

        Args:
            api_key: API key for LLM service
            base_url: Base URL for API endpoint
            model: Model name to use
            confirmation_callback: Optional callback for tool confirmation.
                                   Takes (tool_name, tool_description, tool_args) and returns bool
            max_context_tokens: Maximum context window tokens (default 200K)
            step_callback: Optional callback called before/after each tool execution.
                           Takes (tool_name, tool_args); tool_name=None means reset to Thinking...
            thinking_callback: Optional callback called when LLM produces reasoning text
                               before tool calls. Takes the reasoning string.
            ask_user_callback: Optional callback for ask_user tool. Takes (question) and returns
                               the user's free-form text response.
            output_callback: Optional callback for streaming tool output.
                             Takes (tool_name, text_chunk).
            tool_complete_callback: Optional callback called after tool execution completes.
                                    Takes (tool_name,).
            on_max_iterations_callback: Optional callback when max tool iterations is reached.
                                        Takes (max_iterations, pending_tool_calls) and returns
                                        dict with "action": "continue"|"stop"|"new_instruction"
                                        and optional "message" for new_instruction action.
        """
        self.llm = LLMClient(api_key=api_key, base_url=base_url, model=model, temperature=temperature)
        self.skill_manager = SkillManager()
        self.memory_tool = SaveMemoryTool()
        self.todo_tool = TodoWriteTool()
        self.confirmation_callback = confirmation_callback
        self.step_callback = step_callback
        self.thinking_callback = thinking_callback
        self.ask_user_callback = ask_user_callback
        self.output_callback = output_callback
        self.tool_complete_callback = tool_complete_callback
        self.llm_text_callback = llm_text_callback
        self.permission_engine = permission_engine
        self.on_max_iterations_callback = on_max_iterations_callback

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
        )

        # Initialize tool registry
        self.tools = ToolRegistry()
        self._register_tools()

        # Initialize MCP (Model Context Protocol) support
        self.mcp_manager = self._init_mcp()

        # Initialize agent manager and register agent tools
        self.agent_manager = AgentManager()
        self._register_agent_tools()

        # Per-turn cancellation token (set at the start of each chat() call)
        self._current_token: Optional[CancellationToken] = None

        # Conversation history
        self.messages: List[Dict[str, Any]] = []

        # Load project instructions if available
        self.project_instructions = self._load_project_instructions()

        # Initialize tool runner (encapsulates 4-phase tool execution pipeline)
        self.tool_runner = ToolRunner(
            tools=self.tools,
            permission_engine=self.permission_engine,
            confirmation_callback=self.confirmation_callback,
            step_callback=self.step_callback,
            output_callback=self.output_callback,
            tool_complete_callback=self.tool_complete_callback,
            logger=self.llm.logger,
        )

    def _load_project_instructions(self) -> Optional[str]:
        """Load project-specific instructions from AGENTAO.md.

        Returns:
            Project instructions content or None if file doesn't exist
        """
        try:
            # Look for AGENTAO.md in current directory
            agentao_md = Path.cwd() / "AGENTAO.md"
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
            AskUserTool(ask_user_callback=self.ask_user_callback),
            self.todo_tool,
        ]

        for tool in tools_to_register:
            self.tools.register(tool)

    def _init_mcp(self) -> Optional[McpClientManager]:
        """Load MCP config, connect servers, and register discovered tools."""
        try:
            configs = load_mcp_config()
        except Exception as e:
            self.llm.logger.warning(f"Failed to load MCP config: {e}")
            return None

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
        """Clean up resources (MCP connections, event loops)."""
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
        agent_tools = self.agent_manager.create_agent_tools(
            all_tools=self.tools.tools,
            llm_config=self._llm_config,
            confirmation_callback=self.confirmation_callback,
            step_callback=self.step_callback,
            output_callback=self.output_callback,
            tool_complete_callback=self.tool_complete_callback,
            ask_user_callback=self.ask_user_callback,
            max_context_tokens=self.context_manager.max_tokens,
            parent_messages_getter=lambda: self.messages,
            cancellation_token_getter=lambda: self._current_token,
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

    def _build_operational_guidelines(self) -> str:
        """Return operational guidelines injected unconditionally into every system prompt."""
        return (
            "\n\n=== Operational Guidelines ===\n\n"

            "## Tone and Style\n"
            "- Concise & Direct: fewer than 3 lines of text per response (excluding tool use/code) when practical.\n"
            "- No Chitchat: omit preambles ('Okay, I will now...') and postambles ('I have finished...') "
            "unless explaining intent before a modifying command.\n"
            "- Tools vs. Text: use tools for actions, text only for communication. "
            "No explanatory comments inside tool calls.\n"
            "- Formatting: GitHub-flavored Markdown; responses render in monospace.\n"
            "- Clarity over Brevity: when a request is ambiguous or explanation is essential, prioritize clarity.\n\n"

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

            "## Task Completion\n"
            "- Work autonomously until the task is fully resolved before yielding back to the user.\n"
            "- If a fix introduces a new error, keep iterating rather than stopping and reporting the error.\n"
            "- Only stop and ask when you are genuinely blocked on missing information "
            "you cannot discover with tools.\n\n"

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
        agent_instructions = f"""You are Agentao, a helpful AI assistant with access to various tools and skills.

Current Working Directory: {Path.cwd()}

Use tools proactively whenever they provide ground truth. If you need clarification, ask the user."""

        # Start with project-specific instructions if available
        if self.project_instructions:
            prompt = f"""=== Project Instructions ===

{self.project_instructions}

=== Agent Instructions ===

{agent_instructions}"""
        else:
            prompt = agent_instructions

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

        # Add available agents section
        if self.agent_manager:
            agent_descriptions = self.agent_manager.list_agents()
            if agent_descriptions:
                prompt += "\n\n=== Available Agents ===\n"
                prompt += "For the following types of tasks, prefer delegating to a specialized agent:\n\n"
                for agent_name, desc in agent_descriptions.items():
                    tool_name = f"agent_{agent_name.replace('-', '_')}"
                    prompt += f"- {agent_name}: {desc} (use tool: {tool_name})\n"
                prompt += "\nCall the corresponding agent tool to delegate a task."

        # Inject reliability principles unconditionally
        prompt += self._build_reliability_section()

        # Inject operational guidelines unconditionally
        prompt += self._build_operational_guidelines()

        # Instruct LLM to show reasoning when thinking_callback is active
        if self.thinking_callback:
            prompt += (
                "\n\n=== Reasoning Requirement ===\n"
                "Before each set of tool calls, write 2-3 sentences in this structure:\n"
                "- Action: What tool you are calling and with what input.\n"
                "- Expectation: What you expect to find or what the result should confirm.\n"
                "- If wrong: What you will do if the result contradicts your expectation.\n"
                "Be specific and falsifiable. This reasoning is shown to the user."
            )

        # Inject current task list if any todos exist
        todos = self.todo_tool.get_todos()
        if todos:
            _icons = {"pending": "○", "in_progress": "◉", "completed": "✓"}
            prompt += "\n\n=== Current Task List ===\n"
            for todo in todos:
                icon = _icons.get(todo["status"], "○")
                prompt += f"- {icon} [{todo['status']}] {todo['content']}\n"
            prompt += "\nUpdate task statuses with todo_write as you complete each step."

        # Inject all saved memories (omit section entirely when empty to save tokens)
        memories = self.memory_tool.get_all_memories()
        if memories:
            prompt += "\n\n=== Memories ===\n"
            prompt += "Important information remembered from past conversations:\n\n"
            for m in memories:
                line = f"• {m['key']}: {m['value']}"
                if m.get('tags'):
                    line += f" [tags: {', '.join(m['tags'])}]"
                prompt += line + "\n"
            prompt += "\nWhen you learn new durable facts, call save_memory to preserve them."

        return prompt

    def _llm_call(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
                  cancellation_token: Optional[CancellationToken] = None) -> Any:
        """Dispatch to streaming or non-streaming LLM call based on llm_text_callback."""
        if self.llm_text_callback:
            return self.llm.chat_stream(
                messages=messages,
                tools=tools,
                max_tokens=self.llm.max_tokens,
                on_text_chunk=self.llm_text_callback,
                cancellation_token=cancellation_token,
            )
        return self.llm.chat(messages=messages, tools=tools, max_tokens=self.llm.max_tokens)

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
        self.add_message("user", system_reminder + user_message)

        # Build system prompt (injects all memories)
        system_prompt = self._build_system_prompt()

        # Prepare messages with system prompt
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + self.messages

        # Get tools in OpenAI format
        tools = self.tools.to_openai_format()

        # Reset doom-loop counter for this chat() invocation
        self.tool_runner.reset()

        # System prompt dirty-flag: only rebuild when skills or memories change
        _current_active_skills = frozenset(self.skill_manager.get_active_skills().keys())
        _current_memory_count = len(self.memory_tool.get_all_memories())

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

                if self.on_max_iterations_callback:
                    result = self.on_max_iterations_callback(max_iterations, pending)
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
                else:
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

            # Reset step display before each LLM call
            if self.step_callback:
                self.step_callback(None, {})

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

                # Show reasoning_content via thinking_callback if present
                if reasoning_content and self.thinking_callback:
                    self.thinking_callback(reasoning_content)

                # Show LLM content text (content before tool calls) if present
                # When reasoning_content is present, content usually lacks thinking text
                reasoning = (assistant_message.content or "").strip()
                if reasoning and self.thinking_callback:
                    self.thinking_callback(reasoning)

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
                new_memory_count = len(self.memory_tool.get_all_memories())
                if new_active_skills != _current_active_skills or new_memory_count != _current_memory_count:
                    _current_active_skills = new_active_skills
                    _current_memory_count = new_memory_count
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
        memory_count = len(self.memory_tool.get_all_memories())

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
