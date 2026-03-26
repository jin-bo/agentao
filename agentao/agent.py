"""Main agent logic for Agentao."""

import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .llm import LLMClient
from .permissions import PermissionDecision, PermissionEngine
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
)
from .agents import AgentManager, TaskComplete
from .skills import SkillManager
from .context_manager import ContextManager, is_context_too_long_error
from .mcp import load_mcp_config, McpClientManager, McpTool


MAX_TOOL_RESULT_CHARS = 80_000  # ~20K tokens per tool result
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
        confirmation_callback: Optional[Callable[[str, str, Dict[str, Any]], bool]] = None,
        max_context_tokens: int = 200_000,
        step_callback: Optional[Callable[[Optional[str], Dict[str, Any]], None]] = None,
        thinking_callback: Optional[Callable[[str], None]] = None,
        ask_user_callback: Optional[Callable[[str], str]] = None,
        output_callback: Optional[Callable[[str, str], None]] = None,
        tool_complete_callback: Optional[Callable[[str, int], None]] = None,
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
                                    Takes (tool_name, returncode).
            on_max_iterations_callback: Optional callback when max tool iterations is reached.
                                        Takes (max_iterations, pending_tool_calls) and returns
                                        dict with "action": "continue"|"stop"|"new_instruction"
                                        and optional "message" for new_instruction action.
        """
        self.llm = LLMClient(api_key=api_key, base_url=base_url, model=model)
        self.skill_manager = SkillManager()
        self.memory_tool = SaveMemoryTool()
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

        # Conversation history
        self.messages: List[Dict[str, Any]] = []

        # Load project instructions if available
        self.project_instructions = self._load_project_instructions()

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
            "- Remembering facts: use save_memory only for user-specific facts or preferences "
            "(e.g. preferred coding style, common project paths, personal aliases) "
            "when the user explicitly asks or clearly states something that would help personalize "
            "future interactions. Do NOT use it for general project context. "
            "If unsure whether to save something, ask: 'Should I remember that for you?'\n\n"

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
        # Get current date and time
        now = datetime.now()
        current_datetime = now.strftime("%Y-%m-%d %H:%M:%S")
        day_of_week = now.strftime("%A")

        agent_instructions = f"""You are Agentao, a helpful AI assistant with access to various tools and skills.

Current Date and Time: {current_datetime} ({day_of_week})
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
                    prompt += f"• {skill_name}: {description}\n"

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

        # Inject all saved memories
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
        else:
            prompt += "\n\n=== Memories ===\n"
            prompt += "No memories saved yet. Call save_memory when you learn durable facts about the user or project."

        return prompt

    def _llm_call(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Any:
        """Dispatch to streaming or non-streaming LLM call based on llm_text_callback."""
        if self.llm_text_callback:
            return self.llm.chat_stream(
                messages=messages,
                tools=tools,
                on_text_chunk=self.llm_text_callback,
            )
        return self.llm.chat(messages=messages, tools=tools)

    def add_message(self, role: str, content: str):
        """Add a message to conversation history.

        Args:
            role: Message role (user/assistant/system)
            content: Message content
        """
        self.messages.append({"role": role, "content": content})

    def clear_history(self):
        """Clear conversation history and deactivate all skills."""
        self.messages = []
        self.skill_manager.clear_active_skills()

    def chat(self, user_message: str, max_iterations: int = 100) -> str:
        """Process user message and generate response.

        Args:
            user_message: User's message
            max_iterations: Maximum number of tool call iterations to prevent infinite loops

        Returns:
            Assistant's response
        """
        # Add user message
        self.add_message("user", user_message)

        # Build system prompt (injects all memories)
        system_prompt = self._build_system_prompt()

        # Prepare messages with system prompt
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + self.messages

        # Get tools in OpenAI format
        tools = self.tools.to_openai_format()

        # Doom-loop detection: track (tool_name, raw_args) call counts per chat() invocation
        _doom_loop_counter: Counter = Counter()

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

            # Compress if the full message list (including system prompt) is too large.
            # Check happens every iteration so tool results that bloat context are caught.
            if self.context_manager.needs_compression(messages_with_system):
                self.llm.logger.info("Context compression triggered inside loop")
                self.messages = self.context_manager.compress_messages(self.messages)
                system_prompt = self._build_system_prompt()
                messages_with_system = [
                    {"role": "system", "content": system_prompt}
                ] + self.messages
                self.llm.logger.info(f"Context compressed to {len(self.messages)} messages")

            # Reset step display before each LLM call
            if self.step_callback:
                self.step_callback(None, {})

            # Call LLM — catch context-overflow errors and force-compress once before giving up
            try:
                response = self._llm_call(messages_with_system, tools)
            except Exception as e:
                if not is_context_too_long_error(e):
                    raise
                self.llm.logger.warning(f"Context overflow from API, forcing compression: {e}")
                self.messages = self.context_manager.compress_messages(self.messages)
                system_prompt = self._build_system_prompt()
                messages_with_system = [
                    {"role": "system", "content": system_prompt}
                ] + self.messages
                try:
                    response = self._llm_call(messages_with_system, tools)
                except Exception as e2:
                    if is_context_too_long_error(e2):
                        # System prompt alone may be too large; keep only the last 2 messages
                        self.llm.logger.warning("Context still too long after compression, keeping minimal history")
                        self.messages = self.messages[-2:]
                        messages_with_system = [
                            {"role": "system", "content": system_prompt}
                        ] + self.messages
                        response = self._llm_call(messages_with_system, tools)
                    else:
                        raise

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

                # Execute tool calls — three phases: pre-process, confirm, parallel execute.
                _doom_loop_triggered = False

                # --- Phase 1: Pre-process (sequential) ---
                # Doom-loop detection + permission decisions; no I/O yet.
                _plans: List[Dict[str, Any]] = []
                for tool_call in assistant_message.tool_calls:
                    function_name = tool_call.function.name
                    function_args_raw = tool_call.function.arguments

                    _doom_key = (function_name, function_args_raw)
                    _doom_loop_counter[_doom_key] += 1
                    if _doom_loop_counter[_doom_key] >= 3:
                        self.llm.logger.warning(
                            f"Doom-loop detected: {function_name} called 3+ times with identical args"
                        )
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": function_name,
                            "content": (
                                f"[Doom-loop detected] Tool '{function_name}' was called 3 times "
                                f"with identical arguments. Execution stopped to prevent an infinite loop. "
                                f"Please try a different approach or tool."
                            ),
                        })
                        # Placeholder results for already-queued plans so message
                        # state stays consistent across conversation turns.
                        for _p in _plans:
                            self.messages.append({
                                "role": "tool",
                                "tool_call_id": _p["tool_call"].id,
                                "name": _p["function_name"],
                                "content": "Tool not executed (halted by doom-loop detection).",
                            })
                        _doom_loop_triggered = True
                        break

                    function_args = json.loads(function_args_raw)
                    tool = self.tools.get(function_name)

                    if self.permission_engine:
                        engine_decision = self.permission_engine.decide(function_name, function_args)
                        if engine_decision == PermissionDecision.ALLOW:
                            raw_decision = PermissionDecision.ALLOW
                        elif engine_decision == PermissionDecision.DENY:
                            raw_decision = PermissionDecision.DENY
                        else:
                            # No matching rule — fall back to tool's own setting
                            raw_decision = (
                                PermissionDecision.ASK
                                if tool.requires_confirmation
                                else PermissionDecision.ALLOW
                            )
                    else:
                        raw_decision = (
                            PermissionDecision.ASK
                            if tool.requires_confirmation
                            else PermissionDecision.ALLOW
                        )

                    _plans.append({
                        "tool_call": tool_call,
                        "function_name": function_name,
                        "function_args": function_args,
                        "tool": tool,
                        "decision": raw_decision,
                    })

                # Break outer iteration loop if doom-loop was triggered
                if _doom_loop_triggered:
                    break

                # --- Phase 2: Confirmation (sequential, interactive) ---
                # All user-facing prompts happen here before any execution starts.
                for _plan in _plans:
                    if _plan["decision"] == PermissionDecision.ASK:
                        _fn = _plan["function_name"]
                        if self.confirmation_callback:
                            self.llm.logger.info(f"Tool {_fn} requires confirmation")
                            _confirmed = self.confirmation_callback(
                                _fn,
                                _plan["tool"].description,
                                _plan["function_args"],
                            )
                            if not _confirmed:
                                self.llm.logger.info(f"Tool {_fn} execution cancelled by user")
                                _plan["decision"] = "CANCELLED"
                            else:
                                self.llm.logger.info(f"Tool {_fn} execution confirmed by user")
                                _plan["decision"] = PermissionDecision.ALLOW
                        else:
                            _plan["decision"] = PermissionDecision.ALLOW

                # --- Phase 3: Parallel execution ---
                # Independent tools run concurrently; results collected by tool_call.id.
                _tool_cb_lock = threading.Lock()

                def _execute_one(_plan: Dict[str, Any]) -> tuple:
                    _fn = _plan["function_name"]
                    _args = _plan["function_args"]
                    _tool = _plan["tool"]
                    _tc = _plan["tool_call"]
                    _decision = _plan["decision"]

                    if self.step_callback:
                        self.step_callback(_fn, _args)

                    if _decision == PermissionDecision.DENY:
                        self.llm.logger.info(f"Tool {_fn} denied by permission engine")
                        _result = (
                            f"Tool execution denied: '{_fn}' is not permitted "
                            f"by the current permission rules."
                        )
                    elif _decision == "CANCELLED":
                        _result = (
                            f"Tool execution cancelled by user. "
                            f"The user declined to execute {_fn}."
                        )
                    else:  # ALLOW
                        if self.output_callback and hasattr(_tool, 'output_callback'):
                            with _tool_cb_lock:
                                _tool.output_callback = (
                                    lambda chunk, _name=_fn: self.output_callback(_name, chunk)
                                )
                        try:
                            _result = _tool.execute(**_args)
                        except TaskComplete as _tc_exc:
                            _result = _tc_exc.result
                        except Exception as _e:
                            _result = f"Error executing {_fn}: {str(_e)}"
                        finally:
                            if hasattr(_tool, 'output_callback'):
                                with _tool_cb_lock:
                                    _tool.output_callback = None
                            if self.tool_complete_callback:
                                self.tool_complete_callback(_fn)

                    return _tc.id, _fn, _result

                _exec_results: Dict[str, tuple] = {}
                with ThreadPoolExecutor(max_workers=8) as _executor:
                    _futures = {_executor.submit(_execute_one, p): p for p in _plans}
                    for _future in as_completed(_futures):
                        _call_id, _fn_name, _res = _future.result()
                        _exec_results[_call_id] = (_fn_name, _res)

                # --- Phase 4: Append results in original order ---
                for _plan in _plans:
                    _tc = _plan["tool_call"]
                    _fn_name, _result = _exec_results[_tc.id]

                    # Truncate oversized tool results to avoid context overflow
                    if isinstance(_result, str) and len(_result) > MAX_TOOL_RESULT_CHARS:
                        _truncated = len(_result) - MAX_TOOL_RESULT_CHARS
                        _result = (
                            _result[:MAX_TOOL_RESULT_CHARS]
                            + f"\n\n[... {_truncated} characters truncated to fit context window ...]"
                        )
                        self.llm.logger.warning(
                            f"Tool result from {_fn_name} truncated: {_truncated} chars removed"
                        )

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": _tc.id,
                        "name": _fn_name,
                        "content": _result,
                    })

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
        stats = self.context_manager.get_usage_stats(self.messages)
        memory_count = len(self.memory_tool.get_all_memories())

        if not self.messages:
            summary = "No conversation history\n"
        else:
            summary = f"Messages: {len(self.messages)}\n"

        summary += f"Model: {self.llm.model}\n"
        summary += f"Active skills: {len(self.skill_manager.get_active_skills())}\n"
        summary += f"Saved memories: {memory_count}\n"

        # MCP server info
        if self.mcp_manager:
            statuses = self.mcp_manager.get_server_status()
            connected = sum(1 for s in statuses if s["status"] == "connected")
            total_tools = sum(s["tools"] for s in statuses)
            summary += f"MCP servers: {connected}/{len(statuses)} connected, {total_tools} tools\n"
        summary += (
            f"Context: ~{stats['estimated_tokens']:,} / {stats['max_tokens']:,} tokens "
            f"({stats['usage_percent']:.1f}%)"
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
