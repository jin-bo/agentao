"""``CheckBackgroundAgentTool`` and ``CancelBackgroundAgentTool``.

Both tools target the same per-Agentao :class:`BackgroundTaskStore`,
which the parent wires in at construction. ``check`` reads (status +
result), ``cancel`` writes (mutates status). The pair forms the
control surface the LLM uses to coordinate fire-and-forget sub-agents
launched via ``run_in_background=True``.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from ...tools.base import Tool
from ..bg_store import BackgroundTaskStore


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
