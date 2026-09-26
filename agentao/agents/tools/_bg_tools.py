"""``CheckBackgroundAgentTool`` and ``CancelBackgroundAgentTool``.

Both tools target the same per-Agentao :class:`BackgroundTaskStore`,
which the parent wires in at construction. ``check`` reads (status +
result), ``cancel`` writes (mutates status). The pair forms the
control surface the LLM uses to coordinate fire-and-forget sub-agents
launched via ``run_in_background=True``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ...tools.base import Tool
from ..bg_store import _TERMINAL_BG_STATUSES, BackgroundTaskStore

#: Upper bound on one ``check_background_agent(wait_seconds=…)`` call. A
#: candidate: an ACP client's own turn timeout may be shorter, and has to be
#: checked against the client before this is treated as settled
#: (``docs/design/background-subagent-wake.md`` §6.2).
MAX_WAIT_SECONDS = 1800

#: How often a long wait reports that it is still waiting. Every ACP
#: ``tool_call_update`` resends all the output so far, so this stays sparse.
_WAIT_PROGRESS_SECONDS = 60


class CheckBackgroundAgentTool(Tool):
    """Poll the status of a background sub-agent and retrieve its result."""

    def __init__(self, bg_store: BackgroundTaskStore):
        super().__init__()
        self.bg_store = bg_store
        # The turn's token, set by the tool executor before each call. A wait
        # polls it, so cancelling the turn ends the wait — never the child.
        self._cancellation_token: Optional[Any] = None

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
            "or 'failed' (with error). Pass agent_id='' to list all background agents. "
            "Only when you need the result before you can continue in this turn, pass "
            f"wait_seconds (up to {MAX_WAIT_SECONDS}) to wait for it once. If the wait "
            "times out, do not repeat it: end the turn or cancel the agent."
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
                },
                "wait_seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_WAIT_SECONDS,
                    "description": (
                        "Seconds to wait for this agent to finish before answering. "
                        "Default 0 answers at once. Ignored when listing."
                    ),
                },
            },
            "required": ["agent_id"],
        }

    def execute(self, agent_id: str, wait_seconds: Any = 0) -> str:
        if agent_id:
            try:
                wait = int(wait_seconds or 0)
            except (TypeError, ValueError):
                return (
                    f"Invalid wait_seconds {wait_seconds!r}: expected an integer "
                    f"from 0 to {MAX_WAIT_SECONDS}."
                )
            wait = min(max(wait, 0), MAX_WAIT_SECONDS)
            if wait:
                return self._wait_and_report(agent_id, wait)
        return self._report(agent_id)

    def _wait_and_report(self, agent_id: str, wait: int) -> str:
        token = self._cancellation_token

        def cancelled() -> bool:
            return token is not None and token.is_cancelled

        started = time.monotonic()
        deadline = started + wait
        while True:
            chunk = min(_WAIT_PROGRESS_SECONDS, deadline - time.monotonic())
            rec = self.bg_store.wait_until_settled(
                agent_id, max(chunk, 0.0), should_stop=cancelled,
            )
            if rec is None or rec["status"] in _TERMINAL_BG_STATUSES:
                return self._report(agent_id, rec)
            if cancelled():
                return (
                    f"Stopped waiting for agent '{rec['agent_name']}' ({agent_id}): "
                    "this turn was cancelled. The agent itself was not cancelled "
                    "and is still " + rec["status"] + "."
                )
            if time.monotonic() >= deadline:
                return (
                    self._report(agent_id, rec)
                    + f"\nWaited {wait}s without it finishing. Do not repeat the same "
                    "wait: end this turn (its update is delivered when this session "
                    "next runs), or stop it with cancel_background_agent."
                )
            callback = self.output_callback
            if callback is not None:
                callback(
                    f"Still waiting for agent '{rec['agent_name']}' ({agent_id}): "
                    f"{time.monotonic() - started:.0f}s of {wait}s\n"
                )

    def _report(self, agent_id: str, rec: Optional[Dict[str, Any]] = None) -> str:
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

        if rec is None:
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
            # A cancel can land on a run that already did work. Same rule as
            # the ``failed`` branch below: the status says what happened and
            # the result is still worth reading. A task cancelled before it
            # started has none, and reads exactly as it used to.
            report = f"Agent '{name}' ({agent_id}) was cancelled."
            if rec.get("result"):
                report += f"\n\n{rec['result']}"
            return report
        else:
            # ``failed`` covers two shapes: a raised exception (no result),
            # and a run that finished without answering — budget exhausted,
            # doom-loop halted — which still carries whatever work it did.
            # Dropping ``result`` here would discard the entire output of a
            # long background task purely because it stopped short.
            if rec.get("incomplete_reason"):
                report = (
                    f"Agent '{name}' ({agent_id}) did not finish "
                    f"({rec['incomplete_reason']}): {rec['error']}"
                )
            else:
                report = f"Agent '{name}' ({agent_id}) failed: {rec['error']}"
            if rec.get("result"):
                report += f"\n\n{rec['result']}"
            return report


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
