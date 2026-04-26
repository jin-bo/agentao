"""SubAgent tool wrappers — core components for the agent-as-tool pattern.

Background-task state (registry, cancellation tokens, notification queue,
persistence) lives on a per-Agentao :class:`BackgroundTaskStore`. The
three tools here take a store reference at construction time and read
or write through it.
"""

import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..cancellation import AgentCancelledError, CancellationToken
from ..tools.base import Tool, ToolRegistry
from .bg_store import BackgroundTaskStore, BgTaskStatus


# ---------------------------------------------------------------------------
# SubagentProgress — structured sub-agent lifecycle event
# ---------------------------------------------------------------------------

@dataclass
class SubagentProgress:
    """Structured sub-agent lifecycle event, passed via step_callback.

    Replaces plain-text sentinel strings (_AGENT_START / _AGENT_END).
    The step_callback receives (sentinel_name, SubagentProgress) where
    sentinel_name is AgentToolWrapper._AGENT_START or _AGENT_END.
    """
    agent_name: str
    state: BgTaskStatus
    task: str = ""
    max_turns: int = 0
    turns: int = 0
    tool_calls: int = 0
    tokens: int = 0
    duration_ms: int = 0
    result: Optional[str] = None
    error: Optional[str] = None


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
    def is_read_only(self) -> bool:
        return True

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
# CheckBackgroundAgentTool
# ---------------------------------------------------------------------------

class CheckBackgroundAgentTool(Tool):
    """Poll the status of a background sub-agent and retrieve its result."""

    def __init__(self, bg_store: BackgroundTaskStore):
        super().__init__()
        self.bg_store = bg_store

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "check_background_agent"

    @property
    def description(self) -> str:
        return (
            "Check the status of a background sub-agent previously launched with "
            "run_in_background=true. Returns 'pending', 'running', 'completed' (with result), "
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
            tasks = self.bg_store.list()
            if not tasks:
                return "No background agents have been launched in this session."
            lines = ["Background agents:"]
            for t in tasks:
                if t.get("finished_at") and t.get("started_at"):
                    elapsed = f"{t['finished_at'] - t['started_at']:.1f}s"
                elif t.get("started_at"):
                    elapsed = f"{time.time() - t['started_at']:.0f}s running"
                elif t.get("status") == "cancelled" and t.get("finished_at"):
                    elapsed = "cancelled before start"
                else:
                    elapsed = "queued"
                lines.append(
                    f"  [{t['id']}] {t['agent_name']} — {t['status']} ({elapsed}): "
                    f"{t['task'][:60]}"
                )
            return "\n".join(lines)

        rec = self.bg_store.get(agent_id)
        if rec is None:
            return f"No background agent found with ID: {agent_id}"

        status = rec["status"]
        name = rec["agent_name"]
        if status == "pending":
            return f"Agent '{name}' ({agent_id}) is queued, not yet started."
        elif status == "running":
            elapsed = time.time() - rec["started_at"]
            return f"Agent '{name}' ({agent_id}) is still running… ({elapsed:.0f}s elapsed)"
        elif status == "completed":
            elapsed = rec["finished_at"] - rec["started_at"]
            return (
                f"Agent '{name}' ({agent_id}) completed "
                f"({elapsed:.1f}s):\n\n{rec['result']}"
            )
        elif status == "cancelled":
            return f"Agent '{name}' ({agent_id}) was cancelled."
        else:
            return f"Agent '{name}' ({agent_id}) failed: {rec['error']}"


# ---------------------------------------------------------------------------
# CancelBackgroundAgentTool
# ---------------------------------------------------------------------------

class CancelBackgroundAgentTool(Tool):
    """Cancel a running or pending background sub-agent."""

    def __init__(self, bg_store: BackgroundTaskStore):
        super().__init__()
        self.bg_store = bg_store

    @property
    def name(self) -> str:
        return "cancel_background_agent"

    @property
    def description(self) -> str:
        return (
            "Cancel a background sub-agent that was launched with run_in_background=true. "
            "Works on both pending (not yet started) and running agents. "
            "Completed or failed agents cannot be cancelled."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The agent ID returned when the background agent was launched.",
                }
            },
            "required": ["agent_id"],
        }

    def execute(self, agent_id: str) -> str:
        return self.bg_store.cancel(agent_id)


# ---------------------------------------------------------------------------
# AgentToolWrapper
# ---------------------------------------------------------------------------

class AgentToolWrapper(Tool):
    """Wraps an agent definition as a callable Tool for the parent LLM."""

    # How many recent parent messages to inject as context for sub-agents
    PARENT_CONTEXT_MESSAGES = 10

    @property
    def is_read_only(self) -> bool:
        # The wrapper itself is always allowed; readonly enforcement is propagated
        # into the sub-agent's own ToolRunner via readonly_mode_getter.
        return True

    def __init__(
        self,
        definition: Dict[str, Any],
        all_tools: Dict[str, Tool],
        llm_config: Dict[str, Any],
        bg_store: BackgroundTaskStore,
        confirmation_callback: Optional[Callable] = None,
        step_callback: Optional[Callable] = None,
        output_callback: Optional[Callable] = None,
        tool_complete_callback: Optional[Callable] = None,
        ask_user_callback: Optional[Callable] = None,
        max_context_tokens: Optional[int] = None,
        parent_messages_getter: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        cancellation_token_getter: Optional[Callable] = None,
        readonly_mode_getter: Callable[[], bool] = lambda: False,
        permission_mode_getter: Optional[Callable] = None,
    ):
        self._definition = definition
        self._all_tools = all_tools
        self._llm_config = llm_config
        self._bg_store = bg_store
        self._confirmation_callback = confirmation_callback
        self._step_callback = step_callback
        self._output_callback = output_callback
        self._tool_complete_callback = tool_complete_callback
        self._ask_user_callback = ask_user_callback
        self._max_context_tokens = max_context_tokens
        self._parent_messages_getter = parent_messages_getter
        self._cancellation_token_getter = cancellation_token_getter
        self._readonly_mode_getter = readonly_mode_getter
        self._permission_mode_getter = permission_mode_getter
        # Set by ToolRunner just before execute() to propagate the per-turn token
        self._cancellation_token: Optional[Any] = None

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

        # Resolve the current cancellation token: prefer the one injected by
        # ToolRunner (set just before this call), fall back to the getter.
        token = self._cancellation_token or (
            self._cancellation_token_getter() if self._cancellation_token_getter else None
        )
        # Reset per-call injected token so it doesn't linger across calls.
        self._cancellation_token = None

        if run_in_background:
            return self._launch_background(task, parent_context)

        agent_name = self._definition["name"]
        max_turns  = self._definition.get("max_turns", 15)

        # Signal sub-agent start to the CLI
        if self._step_callback:
            self._step_callback(
                self._AGENT_START,
                SubagentProgress(agent_name=agent_name, state="running",
                                 task=task[:80], max_turns=max_turns),
            )

        result, stats = self._run_sync(task, parent_context, cancellation_token=token)

        # Signal sub-agent end to the CLI
        if self._step_callback:
            self._step_callback(
                self._AGENT_END,
                SubagentProgress(
                    agent_name=agent_name,
                    state="completed",
                    task=task[:80],
                    max_turns=max_turns,
                    turns=stats["turns"],
                    tool_calls=stats["tool_calls"],
                    tokens=stats["tokens"],
                    duration_ms=stats["duration_ms"],
                ),
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
        self, task: str, parent_context: str = "", suppress_output: bool = False,
        cancellation_token: Optional[Any] = None,
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
        agent_name = self._definition["name"]
        step_cb = None if suppress_output else self._make_prefixed_step_callback(max_turns)

        # Background agents: pass None so tool_runner auto-approves (no stdin reads
        # from background threads, which would corrupt the terminal raw mode).
        # Foreground agents: wrap the callback to prepend "[agent_name]" to the
        # tool_name so the user knows which sub-agent is requesting permission.
        if suppress_output or not self._confirmation_callback:
            confirm_cb = None
        else:
            _parent_cb = self._confirmation_callback
            def confirm_cb(tool_name: str, tool_desc: str, tool_args: dict) -> bool:
                return _parent_cb(f"[{agent_name}] {tool_name}", tool_desc, tool_args)

        sub_agent = Agentao(
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            temperature=temperature,
            confirmation_callback=confirm_cb,
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
        if self._readonly_mode_getter():
            sub_agent.tool_runner.set_readonly_mode(True)
        if self._permission_mode_getter:
            mode = self._permission_mode_getter()
            if mode is not None:
                from ..permissions import PermissionEngine
                engine = PermissionEngine()
                engine.set_mode(mode)
                sub_agent.tool_runner._permission_engine = engine

        # Prepend parent context to the task
        if parent_context:
            full_task = f"{parent_context}\n[Your Task]\n{task}"
        else:
            full_task = task

        t0 = time.monotonic()
        try:
            # Foreground sub-agents share the parent's cancellation token so
            # Ctrl+C propagates into nested chat() loops (Gemini CLI pattern).
            # Background agents always receive None (fire-and-forget).
            result = sub_agent.chat(
                full_task,
                max_iterations=max_turns,
                cancellation_token=cancellation_token,
            )
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
        self._bg_store.register(agent_id, agent_name, task[:80])

        token = CancellationToken()
        self._bg_store.register_token(agent_id, token)

        def _run():
            if not self._bg_store.mark_running(agent_id):
                return
            try:
                result, stats = self._run_sync(
                    task, parent_context,
                    suppress_output=True,
                    cancellation_token=token,
                )
                formatted = self._format_result(result, stats)
                self._bg_store.update(
                    agent_id, status="completed", result=formatted,
                    turns=stats["turns"], tool_calls=stats["tool_calls"],
                    tokens=stats["tokens"], duration_ms=stats["duration_ms"],
                )
            except AgentCancelledError:
                self._bg_store.update(agent_id, status="cancelled")
            except Exception as exc:
                self._bg_store.update(agent_id, status="failed", error=str(exc))
            finally:
                self._bg_store.unregister_token(agent_id)

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
