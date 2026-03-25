"""SubAgent tool wrappers — core components for the agent-as-tool pattern."""

from typing import Any, Callable, Dict, Optional

from ..tools.base import Tool, ToolRegistry


class TaskComplete(Exception):
    """Raised by CompleteTaskTool to signal sub-agent task completion."""

    def __init__(self, result: str):
        self.result = result


class CompleteTaskTool(Tool):
    """Tool that sub-agents call to return their result."""

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def description(self) -> str:
        return (
            "Call this tool when you have completed the assigned task. "
            "Pass the final result as a string. You MUST call this tool to finish."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "The final result of the completed task",
                }
            },
            "required": ["result"],
        }

    def execute(self, result: str) -> str:
        raise TaskComplete(result)


class AgentToolWrapper(Tool):
    """Wraps an agent definition as a callable Tool for the parent LLM."""

    def __init__(
        self,
        definition: Dict[str, Any],
        all_tools: Dict[str, Tool],
        llm_config: Dict[str, Any],
        confirmation_callback: Optional[Callable] = None,
        step_callback: Optional[Callable] = None,
    ):
        self._definition = definition
        self._all_tools = all_tools
        self._llm_config = llm_config
        self._confirmation_callback = confirmation_callback
        self._step_callback = step_callback

    @property
    def name(self) -> str:
        return f"agent_{self._definition['name'].replace('-', '_')}"

    @property
    def description(self) -> str:
        return self._definition["description"]

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task description to delegate to this agent",
                }
            },
            "required": ["task"],
        }

    def execute(self, task: str) -> str:
        # Lazy import to avoid circular dependency
        from ..agent import ChatAgent
        from ..skills import SkillManager

        # 1. Build scoped ToolRegistry
        scoped_registry = ToolRegistry()
        tool_whitelist = self._definition.get("tools")  # None = all tools
        for tname, tool in self._all_tools.items():
            if tool_whitelist is None or tname in tool_whitelist:
                scoped_registry.register(tool)
        scoped_registry.register(CompleteTaskTool())

        # 2. Create sub-agent ChatAgent
        sub_agent = ChatAgent(
            api_key=self._llm_config["api_key"],
            base_url=self._llm_config.get("base_url"),
            model=self._llm_config.get("model"),
            confirmation_callback=self._confirmation_callback,
            step_callback=self._make_prefixed_step_callback(),
            # No thinking_callback or ask_user_callback for sub-agents
        )

        # 3. Replace tools and disable skills/agents for the sub-agent
        sub_agent.tools = scoped_registry
        sub_agent.project_instructions = self._definition.get("system_instructions")
        sub_agent.skill_manager = SkillManager(skills_dir="/nonexistent")
        sub_agent.agent_manager = None  # Prevent recursive agent spawning

        # 4. Execute
        max_turns = self._definition.get("max_turns", 15)
        try:
            result = sub_agent.chat(task, max_iterations=max_turns)
        except TaskComplete as tc:
            result = tc.result

        return result

    def _make_prefixed_step_callback(self) -> Optional[Callable]:
        parent_cb = self._step_callback
        if not parent_cb:
            return None
        agent_name = self._definition["name"]

        def prefixed(tool_name: Optional[str], tool_args: dict):
            if tool_name is not None:
                parent_cb(f"[{agent_name}] {tool_name}", tool_args)
            else:
                parent_cb(f"[{agent_name}] Thinking...", tool_args)

        return prefixed
