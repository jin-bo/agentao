"""SubAgent tool wrappers — core components for the agent-as-tool pattern."""

import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..tools.base import Tool, ToolRegistry


# ---------------------------------------------------------------------------
# TaskComplete — sub-agent completion signal
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Background agent registry
# ---------------------------------------------------------------------------

_bg_tasks: Dict[str, Dict[str, Any]] = {}   # agent_id → task record
_bg_lock = threading.Lock()


def _register_bg_task(agent_id: str, agent_name: str, task_summary: str) -> None:
    with _bg_lock:
        _bg_tasks[agent_id] = {
            "agent_name": agent_name,
            "task": task_summary,
            "status": "running",   # running | completed | failed
            "result": None,
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        }


def _update_bg_task(agent_id: str, *, status: str, result: Optional[str] = None,
                    error: Optional[str] = None) -> None:
    with _bg_lock:
        rec = _bg_tasks.get(agent_id)
        if rec:
            rec["status"] = status
            rec["result"] = result
            rec["error"] = error
            rec["finished_at"] = time.time()


def get_bg_task(agent_id: str) -> Optional[Dict[str, Any]]:
    with _bg_lock:
        return dict(_bg_tasks[agent_id]) if agent_id in _bg_tasks else None


def list_bg_tasks() -> List[Dict[str, Any]]:
    with _bg_lock:
        return [dict(v) | {"id": k} for k, v in _bg_tasks.items()]


# ---------------------------------------------------------------------------
# CheckBackgroundAgentTool
# ---------------------------------------------------------------------------

class CheckBackgroundAgentTool(Tool):
    """Poll the status of a background sub-agent and retrieve its result."""

    @property
    def name(self) -> str:
        return "check_background_agent"

    @property
    def description(self) -> str:
        return (
            "Check the status of a background sub-agent previously launched with "
            "run_in_background=true. Returns 'running', 'completed' (with result), "
            "or 'failed' (with error). Pass agent_id='' to list all background agents."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": (
                        "The agent ID returned when the background agent was launched. "
                        "Pass empty string to list all background agents."
                    ),
                }
            },
            "required": ["agent_id"],
        }

    def execute(self, agent_id: str) -> str:
        if not agent_id:
            tasks = list_bg_tasks()
            if not tasks:
                return "No background agents have been launched in this session."
            lines = ["Background agents:"]
            for t in tasks:
                elapsed = ""
                if t.get("finished_at"):
                    elapsed = f"{t['finished_at'] - t['started_at']:.1f}s"
                elif t.get("started_at"):
                    elapsed = f"{time.time() - t['started_at']:.0f}s running"
                lines.append(
                    f"  [{t['id']}] {t['agent_name']} — {t['status']} ({elapsed}): "
                    f"{t['task'][:60]}"
                )
            return "\n".join(lines)

        rec = get_bg_task(agent_id)
        if rec is None:
            return f"No background agent found with ID: {agent_id}"

        status = rec["status"]
        name = rec["agent_name"]
        if status == "running":
            elapsed = time.time() - rec["started_at"]
            return f"Agent '{name}' ({agent_id}) is still running… ({elapsed:.0f}s elapsed)"
        elif status == "completed":
            return (
                f"Agent '{name}' ({agent_id}) completed "
                f"({rec['finished_at'] - rec['started_at']:.1f}s):\n\n{rec['result']}"
            )
        else:
            return f"Agent '{name}' ({agent_id}) failed: {rec['error']}"


# ---------------------------------------------------------------------------
# AgentToolWrapper
# ---------------------------------------------------------------------------

class AgentToolWrapper(Tool):
    """Wraps an agent definition as a callable Tool for the parent LLM."""

    # How many recent parent messages to inject as context for sub-agents
    PARENT_CONTEXT_MESSAGES = 10

    def __init__(
        self,
        definition: Dict[str, Any],
        all_tools: Dict[str, Tool],
        llm_config: Dict[str, Any],
        confirmation_callback: Optional[Callable] = None,
        step_callback: Optional[Callable] = None,
        output_callback: Optional[Callable] = None,
        tool_complete_callback: Optional[Callable] = None,
        ask_user_callback: Optional[Callable] = None,
        max_context_tokens: Optional[int] = None,
        parent_messages_getter: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ):
        self._definition = definition
        self._all_tools = all_tools
        self._llm_config = llm_config
        self._confirmation_callback = confirmation_callback
        self._step_callback = step_callback
        self._output_callback = output_callback
        self._tool_complete_callback = tool_complete_callback
        self._ask_user_callback = ask_user_callback
        self._max_context_tokens = max_context_tokens
        self._parent_messages_getter = parent_messages_getter

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
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "Run the agent asynchronously (fire-and-forget). "
                        "Returns immediately with an agent_id. "
                        "Use check_background_agent to poll for the result. "
                        "Useful for long-running tasks that should not block."
                    ),
                },
            },
            "required": ["task"],
        }

    # ------------------------------------------------------------------
    # Public execute — dispatches sync vs background
    # ------------------------------------------------------------------

    # Sentinel tool names used to signal sub-agent lifecycle to the step callback
    _AGENT_START = "__agent_start__"
    _AGENT_END   = "__agent_end__"

    def execute(self, task: str, run_in_background: bool = False) -> str:
        parent_context = self._build_parent_context()

        if run_in_background:
            return self._launch_background(task, parent_context)

        agent_name = self._definition["name"]
        max_turns  = self._definition.get("max_turns", 15)

        # Signal sub-agent start to the CLI
        if self._step_callback:
            self._step_callback(
                self._AGENT_START,
                {"name": agent_name, "task": task[:80]},
            )

        result, stats = self._run_sync(task, parent_context)

        # Signal sub-agent end to the CLI
        if self._step_callback:
            self._step_callback(
                self._AGENT_END,
                {
                    "name": agent_name,
                    "turns": stats["turns"],
                    "tool_calls": stats["tool_calls"],
                    "tokens": stats["tokens"],
                    "duration_ms": stats["duration_ms"],
                    "max_turns": max_turns,
                },
            )

        return self._format_result(result, stats)

    # ------------------------------------------------------------------
    # Parent context injection
    # ------------------------------------------------------------------

    def _build_parent_context(self) -> str:
        """Summarise the last N parent messages as a context block."""
        if not self._parent_messages_getter:
            return ""
        try:
            msgs = self._parent_messages_getter()
        except Exception:
            return ""
        if not msgs:
            return ""

        recent = msgs[-self.PARENT_CONTEXT_MESSAGES:]
        lines: List[str] = []
        for m in recent:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if role == "tool":
                name = m.get("name", "tool")
                lines.append(f"[tool/{name}]: {str(content)[:300]}")
            elif role in ("user", "assistant") and content:
                lines.append(f"[{role}]: {str(content)[:400]}")
            elif role == "assistant" and m.get("tool_calls"):
                tc_names = []
                for tc in m["tool_calls"]:
                    if isinstance(tc, dict):
                        tc_names.append(tc.get("function", {}).get("name", "?"))
                    else:
                        tc_names.append(getattr(getattr(tc, "function", None), "name", "?"))
                lines.append(f"[assistant called: {', '.join(tc_names)}]")

        if not lines:
            return ""
        return "[Parent conversation context (last {} messages)]\n{}\n".format(
            len(recent), "\n".join(lines)
        )

    # ------------------------------------------------------------------
    # Synchronous execution core
    # ------------------------------------------------------------------

    def _run_sync(
        self, task: str, parent_context: str = "", suppress_output: bool = False
    ) -> Tuple[str, Dict[str, Any]]:
        """Create and run a sub-agent. Returns (result, stats).

        Args:
            suppress_output: When True (used for background agents), all live
                display callbacks are suppressed so the background thread does
                not interleave output with the foreground session.
        """
        from ..agent import Agentao
        from ..skills import SkillManager

        # Build scoped ToolRegistry
        scoped_registry = ToolRegistry()
        tool_whitelist = self._definition.get("tools")
        for tname, tool in self._all_tools.items():
            if tool_whitelist is None or tname in tool_whitelist:
                scoped_registry.register(tool)
        scoped_registry.register(CompleteTaskTool())

        # Resolve LLM credentials
        defn_model: Optional[str] = self._definition.get("model")
        defn_temperature: Optional[float] = self._definition.get("temperature")

        if defn_model and "/" in defn_model:
            provider, model_name = defn_model.split("/", 1)
            provider = provider.strip().upper()
            api_key = os.getenv(f"{provider}_API_KEY") or self._llm_config["api_key"]
            base_url = os.getenv(f"{provider}_BASE_URL") or self._llm_config.get("base_url")
        else:
            model_name = defn_model or self._llm_config.get("model")
            api_key = self._llm_config["api_key"]
            base_url = self._llm_config.get("base_url")

        temperature = (
            defn_temperature if defn_temperature is not None
            else self._llm_config.get("temperature")
        )

        max_turns = self._definition.get("max_turns", 15)
        step_cb = None if suppress_output else self._make_prefixed_step_callback(max_turns)

        sub_agent = Agentao(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            temperature=temperature,
            confirmation_callback=self._confirmation_callback,
            step_callback=step_cb,
            output_callback=None if suppress_output else self._output_callback,
            tool_complete_callback=None if suppress_output else self._tool_complete_callback,
            ask_user_callback=None if suppress_output else self._ask_user_callback,
            max_context_tokens=self._max_context_tokens or 200_000,
            # thinking_callback intentionally omitted for sub-agents
        )

        sub_agent.tools = scoped_registry
        sub_agent.project_instructions = self._definition.get("system_instructions")
        sub_agent.skill_manager = SkillManager(skills_dir="/nonexistent")
        sub_agent.agent_manager = None  # prevent recursive spawning

        # Prepend parent context to the task
        if parent_context:
            full_task = f"{parent_context}\n[Your Task]\n{task}"
        else:
            full_task = task

        t0 = time.monotonic()
        try:
            result = sub_agent.chat(full_task, max_iterations=max_turns)
        except TaskComplete as tc:
            result = tc.result

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Collect stats from executed sub-agent
        turns = sum(1 for m in sub_agent.messages if m.get("role") == "assistant")
        tool_calls = sum(1 for m in sub_agent.messages if m.get("role") == "tool")
        approx_tokens = sub_agent.context_manager.estimate_tokens(sub_agent.messages)

        stats = {
            "agent_name": self._definition["name"],
            "turns": turns,
            "tool_calls": tool_calls,
            "tokens": approx_tokens,
            "duration_ms": elapsed_ms,
        }
        return result, stats

    @staticmethod
    def _format_result(result: str, stats: Dict[str, Any]) -> str:
        """Append agent stats footer to result string."""
        name = stats["agent_name"]
        return (
            f"{result}\n\n"
            f"[{name}: {stats['turns']} turns, {stats['tool_calls']} tool calls, "
            f"~{stats['tokens']:,} tokens, {stats['duration_ms']}ms]"
        )

    # ------------------------------------------------------------------
    # Background (async) execution
    # ------------------------------------------------------------------

    def _launch_background(self, task: str, parent_context: str) -> str:
        agent_id = uuid.uuid4().hex[:8]
        agent_name = self._definition["name"]
        _register_bg_task(agent_id, agent_name, task[:80])

        def _run():
            try:
                result, stats = self._run_sync(task, parent_context, suppress_output=True)
                formatted = self._format_result(result, stats)
                _update_bg_task(agent_id, status="completed", result=formatted)
            except Exception as exc:
                _update_bg_task(agent_id, status="failed", error=str(exc))

        # Background agents run silently: suppress_output=True ensures no callbacks
        # fire on the background thread, preventing interleaving with foreground output.
        t = threading.Thread(target=_run, daemon=True, name=f"bg-agent-{agent_id}")
        t.start()

        return (
            f"Background agent '{agent_name}' started (ID: {agent_id}). "
            f"Task: {task[:80]}{'…' if len(task) > 80 else ''}. "
            f"Use check_background_agent(agent_id='{agent_id}') to get the result."
        )

    # ------------------------------------------------------------------
    # Progress callback with turn counter
    # ------------------------------------------------------------------

    def _make_prefixed_step_callback(
        self, max_turns: int
    ) -> Optional[Callable]:
        parent_cb = self._step_callback
        if not parent_cb:
            return None
        agent_name = self._definition["name"]
        turn_counter = [0]  # mutable cell

        def prefixed(tool_name: Optional[str], tool_args: dict) -> None:
            if tool_name is None:
                # Called before each LLM iteration — increment turn counter
                turn_counter[0] += 1
                parent_cb(None, tool_args)  # keep the "Thinking…" reset
            else:
                label = f"[{agent_name} {turn_counter[0]}/{max_turns}] {tool_name}"
                parent_cb(label, tool_args)

        return prefixed
