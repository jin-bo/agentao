"""Main agent logic for ChatAgent."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .llm import LLMClient
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
    CLIHelpAgentTool,
    CodebaseInvestigatorTool,
    ActivateSkillTool,
)
from .skills import SkillManager
from .context_manager import ContextManager, is_context_too_long_error


MAX_TOOL_RESULT_CHARS = 80_000  # ~20K tokens per tool result


class ChatAgent:
    """Main chat agent with tool and skill support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        confirmation_callback: Optional[Callable[[str, str, Dict[str, Any]], bool]] = None,
        recall_callback: Optional[Callable[[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]]]] = None,
        max_context_tokens: int = 200_000,
        step_callback: Optional[Callable[[Optional[str], Dict[str, Any]], None]] = None,
        thinking_callback: Optional[Callable[[str], None]] = None,
    ):
        """Initialize chat agent.

        Args:
            api_key: API key for LLM service
            base_url: Base URL for API endpoint
            model: Model name to use
            confirmation_callback: Optional callback for tool confirmation.
                                   Takes (tool_name, tool_description, tool_args) and returns bool
            recall_callback: Optional callback for memory recall confirmation.
                             Takes (recalled_memories) and returns confirmed list or None to skip
            max_context_tokens: Maximum context window tokens (default 200K)
            step_callback: Optional callback called before/after each tool execution.
                           Takes (tool_name, tool_args); tool_name=None means reset to Thinking...
            thinking_callback: Optional callback called when LLM produces reasoning text
                               before tool calls. Takes the reasoning string.
        """
        self.llm = LLMClient(api_key=api_key, base_url=base_url, model=model)
        self.skill_manager = SkillManager()
        self.memory_tool = SaveMemoryTool()
        self.confirmation_callback = confirmation_callback
        self.recall_callback = recall_callback
        self.step_callback = step_callback
        self.thinking_callback = thinking_callback

        # Initialize context manager
        self.context_manager = ContextManager(
            llm_client=self.llm,
            memory_tool=self.memory_tool,
            max_tokens=max_context_tokens,
        )

        # Initialize tool registry
        self.tools = ToolRegistry()
        self._register_tools()

        # Conversation history
        self.messages: List[Dict[str, Any]] = []

        # Load project instructions if available
        self.project_instructions = self._load_project_instructions()

    def _load_project_instructions(self) -> Optional[str]:
        """Load project-specific instructions from CHATAGENT.md.

        Returns:
            Project instructions content or None if file doesn't exist
        """
        try:
            # Look for CHATAGENT.md in current directory
            chatagent_md = Path.cwd() / "CHATAGENT.md"
            if chatagent_md.exists():
                content = chatagent_md.read_text(encoding='utf-8')
                self.llm.logger.info(f"Loaded project instructions from {chatagent_md}")
                return content
        except Exception as e:
            self.llm.logger.warning(f"Could not load CHATAGENT.md: {e}")

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
            CLIHelpAgentTool(),
            CodebaseInvestigatorTool(),
            ActivateSkillTool(self.skill_manager),
        ]

        for tool in tools_to_register:
            self.tools.register(tool)

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

    def _build_system_prompt(self, recalled_context: Optional[str] = None) -> str:
        """Build system prompt for the agent.

        Args:
            recalled_context: Optional recalled memory context to inject

        Returns:
            System prompt string
        """
        # Get current date and time
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M:%S")
        current_datetime = now.strftime("%Y-%m-%d %H:%M:%S")
        day_of_week = now.strftime("%A")

        # Start with project-specific instructions if available
        if self.project_instructions:
            prompt = f"""=== Project Instructions ===

{self.project_instructions}

=== Agent Instructions ===

You are ChatAgent, a helpful AI assistant with access to various tools and skills.

Current Date and Time: {current_datetime} ({day_of_week})"""
        else:
            prompt = f"""You are ChatAgent, a helpful AI assistant with access to various tools and skills.

Current Date and Time: {current_datetime} ({day_of_week})

You can help users with:
- Reading, writing, and editing files
- Searching for files and text content
- Executing shell commands
- Fetching web content and searching the web
- Saving important information to memory
- Activating specialized skills for specific tasks
- Investigating codebases and project structures

When users ask you to do something:
1. Identify which tools are needed and what you expect to find
2. Use tools; treat results as ground truth, not as confirmation of prior assumptions
3. If results are unexpected, re-evaluate your understanding before proceeding
4. Provide responses grounded in tool output, not in prior assumptions
5. If you need more information, ask the user

Be proactive in using tools when they would be helpful. For example:
- If asked about a file, use read_file to view it
- If asked to search for something in code, use search_file_content
- If asked to create or modify files, use write_file or replace
- If asked to fetch web content, use web_fetch
- For specialized tasks, check if there's an appropriate skill to activate

Always be helpful, accurate, and efficient."""

        # Add available skills section
        available_skills = self.skill_manager.list_available_skills()
        if available_skills:
            prompt += "\n\n=== Available Skills ===\n"
            prompt += "You have access to specialized skills. Use the 'activate_skill' tool to activate them when needed.\n\n"

            for skill_name in sorted(available_skills):
                skill_info = self.skill_manager.get_skill_info(skill_name)
                if skill_info:
                    description = skill_info.get('description', 'No description available')
                    prompt += f"• {skill_name}: {description}\n"

            prompt += "\nWhen the user's request matches a skill's description, use the activate_skill tool before proceeding with the task."

        # Add active skills context if any
        skills_context = self.skill_manager.get_skills_context()
        if skills_context:
            prompt += "\n\n" + skills_context

        # Inject reliability principles unconditionally
        prompt += self._build_reliability_section()

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

        # Inject recalled memories if provided
        if recalled_context:
            prompt += "\n\n=== Relevant Memories (recalled for this message) ===\n"
            prompt += recalled_context

        return prompt

    def add_message(self, role: str, content: str):
        """Add a message to conversation history.

        Args:
            role: Message role (user/assistant/system)
            content: Message content
        """
        self.messages.append({"role": role, "content": content})

    def _try_recall_memories(self, user_message: str) -> Optional[str]:
        """Attempt to recall relevant memories for the current user message (Agentic RAG).

        Args:
            user_message: The user's current message

        Returns:
            Formatted string of relevant memories for injection, or None if none recalled
        """
        try:
            all_memories = self.memory_tool.get_all_memories()
            if not all_memories:
                return None

            relevant = self.context_manager.recall_relevant_memories(
                user_message, all_memories
            )

            if not relevant:
                return None

            # If recall_callback is set, ask user to confirm injection
            if self.recall_callback:
                confirmed = self.recall_callback(relevant)
                if confirmed is None:
                    return None
                relevant = confirmed

            if not relevant:
                return None

            lines = ["[Recalled Memories]"]
            for mem in relevant:
                lines.append(f"• {mem['key']}: {mem['value']}")
            return "\n".join(lines)

        except Exception as e:
            self.llm.logger.warning(f"Memory recall failed: {e}")
            return None

    def _try_save_memories(self, user_message: str, assistant_response: str) -> None:
        """Analyze the conversation turn and auto-save important memories.

        Called after each complete chat() turn. Uses LLM to extract
        valuable long-term information. Saves silently; logs to chatagent.log.
        """
        # Skip trivial responses
        if not assistant_response or len(assistant_response.strip()) < 20:
            return

        try:
            all_memories = self.memory_tool.get_all_memories()
            memories_to_save = self.context_manager.extract_memories_to_save(
                user_message, assistant_response, all_memories
            )

            for mem in memories_to_save:
                self.memory_tool.execute(
                    key=mem["key"],
                    value=mem["value"],
                    tags=mem.get("tags", ["auto"]),
                )
                self.llm.logger.info(f"[Auto-Memory] Saved: {mem['key']} = {mem['value'][:80]}")

        except Exception as e:
            self.llm.logger.warning(f"Auto-memory save failed: {e}")

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

        # Step 1: Dynamic memory recall (Agentic RAG)
        recalled_context = self._try_recall_memories(user_message)

        # Step 2: Build system prompt with optional recalled context
        system_prompt = self._build_system_prompt(recalled_context=recalled_context)

        # Prepare messages with system prompt
        messages_with_system = [
            {"role": "system", "content": system_prompt}
        ] + self.messages

        # Get tools in OpenAI format
        tools = self.tools.to_openai_format()

        # Call LLM and handle multiple rounds of tool calls
        iteration = 0
        while iteration < max_iterations:
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
                response = self.llm.chat(messages=messages_with_system, tools=tools)
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
                    response = self.llm.chat(messages=messages_with_system, tools=tools)
                except Exception as e2:
                    if is_context_too_long_error(e2):
                        # System prompt alone may be too large; keep only the last 2 messages
                        self.llm.logger.warning("Context still too long after compression, keeping minimal history")
                        self.messages = self.messages[-2:]
                        messages_with_system = [
                            {"role": "system", "content": system_prompt}
                        ] + self.messages
                        response = self.llm.chat(messages=messages_with_system, tools=tools)
                    else:
                        raise

            # Process response
            assistant_message = response.choices[0].message

            # Check if tool calls are needed
            if assistant_message.tool_calls:
                self.llm.logger.info(f"Processing {len(assistant_message.tool_calls)} tool call(s) in iteration {iteration}")

                # Show LLM reasoning text (content before tool calls) if present
                reasoning = (assistant_message.content or "").strip()
                if reasoning and self.thinking_callback:
                    self.thinking_callback(reasoning)

                # Add assistant message with tool calls
                self.messages.append({
                    "role": "assistant",
                    "content": assistant_message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in assistant_message.tool_calls
                    ],
                })

                # Execute tool calls
                for tool_call in assistant_message.tool_calls:
                    function_name = tool_call.function.name
                    function_args = json.loads(tool_call.function.arguments)

                    # Execute tool
                    try:
                        tool = self.tools.get(function_name)

                        # Notify step callback (for live thinking display)
                        if self.step_callback:
                            self.step_callback(function_name, function_args)

                        # Check if tool requires confirmation
                        if tool.requires_confirmation and self.confirmation_callback:
                            self.llm.logger.info(f"Tool {function_name} requires confirmation")
                            confirmed = self.confirmation_callback(
                                function_name,
                                tool.description,
                                function_args
                            )

                            if not confirmed:
                                self.llm.logger.info(f"Tool {function_name} execution cancelled by user")
                                result = f"Tool execution cancelled by user. The user declined to execute {function_name}."
                            else:
                                self.llm.logger.info(f"Tool {function_name} execution confirmed by user")
                                result = tool.execute(**function_args)
                        else:
                            # No confirmation needed or no callback provided
                            result = tool.execute(**function_args)
                    except Exception as e:
                        result = f"Error executing {function_name}: {str(e)}"

                    # Truncate oversized tool results to avoid context overflow
                    if isinstance(result, str) and len(result) > MAX_TOOL_RESULT_CHARS:
                        truncated = len(result) - MAX_TOOL_RESULT_CHARS
                        result = (
                            result[:MAX_TOOL_RESULT_CHARS]
                            + f"\n\n[... {truncated} characters truncated to fit context window ...]"
                        )
                        self.llm.logger.warning(
                            f"Tool result from {function_name} truncated: {truncated} chars removed"
                        )

                    # Add tool result to messages
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": result,
                    })

                # Update messages for next iteration
                # Rebuild system prompt in case skills were activated
                system_prompt = self._build_system_prompt()
                messages_with_system = [
                    {"role": "system", "content": system_prompt}
                ] + self.messages

                # Continue loop to check if more tool calls are needed
            else:
                # No more tool calls, we have the final response
                self.llm.logger.info(f"Reached final response in iteration {iteration}")
                assistant_content = assistant_message.content or ""
                self.add_message("assistant", assistant_content)
                self._try_save_memories(user_message, assistant_content)
                return assistant_content

        # If we hit max iterations, return what we have
        self.llm.logger.warning(f"Maximum tool call iterations ({max_iterations}) reached")
        assistant_content = assistant_message.content or "Maximum tool call iterations reached."
        self.add_message("assistant", assistant_content)
        self._try_save_memories(user_message, assistant_content)
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
        """List commonly available models.

        Returns:
            List of model names
        """
        return [
            # Claude models
            "claude-opus-4",
            "claude-sonnet-4-5",
            "claude-sonnet-4",
            "claude-haiku-4",
            # OpenAI models
            "gpt-4-turbo-preview",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-4-32k",
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-16k",
            # Other common models
            "deepseek-chat",
            "deepseek-coder",
        ]
