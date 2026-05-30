"""Non-interactive transport for the ``agentao run`` automation surface.

Converts any runtime request that would normally block on a human
(tool confirmation, free-form ``ask_user``, max-iteration choice) into
a clean abort. The pipeline owns a :class:`CancellationToken` and
passes it in at construction; ``confirm_tool`` / ``ask_user`` record a
rejection and cancel the token so the chat loop unwinds on its next
iteration.

The transport never receives the matched permission rule directly —
that travels through the public host event stream
(``permission_decision`` events with ``outcome="deny"``). The pipeline
registers a sync observer that converts those events into
``transport.rejection``. ASK plans push their ``tool_call_id`` onto
:attr:`_ask_queue` so the matching ``confirm_tool`` call can recover
it FIFO.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .sdk import SdkTransport

if TYPE_CHECKING:
    from ..cancellation import CancellationToken


# Rejection dict shape (produced here, consumed by the run pipeline).
# Keys: ``type`` ∈ RunErrorEnvelope.type Literal, ``tool_name``,
# ``tool_call_id``, ``message``, optional ``matched_rule``,
# optional ``question``.
RejectionDict = Dict[str, Any]


class NonInteractiveTransport(SdkTransport):
    """SdkTransport subclass that aborts cleanly instead of prompting.

    - ``confirm_tool``: sets :attr:`rejection` to a ``permission_required``
      envelope, cancels the bound token, returns ``False``.
    - ``ask_user``: sets :attr:`rejection` to ``interaction_required``,
      cancels the token, returns a sentinel string.
    - ``on_max_iterations``: flips :attr:`max_iterations_hit` and
      returns ``{"action": "stop"}``. The pipeline classifier maps
      the flag to exit code 4. Cancellation is intentionally NOT
      triggered here — the runtime's existing stop handling already
      returns the partial response.
    """

    def __init__(self, token: Optional["CancellationToken"] = None) -> None:
        super().__init__()
        self.rejection: Optional[RejectionDict] = None
        self.max_iterations_hit: bool = False
        self._token = token
        # FIFO populated by the pipeline observer from
        # ``permission_decision`` events with ``outcome=="prompt"``.
        # ``confirm_tool`` consumes it to recover the matching
        # ``tool_call_id`` for the rejection envelope.
        self._ask_queue: List[Tuple[str, Optional[str]]] = []

    def queue_ask(self, tool_name: str, tool_call_id: Optional[str]) -> None:
        """Push an ASK ``permission_decision`` event onto the FIFO."""
        self._ask_queue.append((tool_name, tool_call_id))

    def _pop_ask(self, tool_name: str) -> Optional[str]:
        # FIFO match by name is sufficient: the runner emits
        # permission_decision events in plan order before any
        # confirm_tool fires for the same batch (tool_runner.py:193-200).
        for i, (name, tcid) in enumerate(self._ask_queue):
            if name == tool_name:
                del self._ask_queue[i]
                return tcid
        return None

    def _cancel(self, reason: str) -> None:
        if self._token is not None:
            try:
                self._token.cancel(reason)
            except Exception:
                pass

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        if self.rejection is None:
            self.rejection = {
                "type": "permission_required",
                "tool_name": tool_name,
                "tool_call_id": self._pop_ask(tool_name),
                "message": f"{tool_name} requires approval in this mode",
            }
        self._cancel(f"permission_required: {tool_name}")
        return False

    def ask_user(
        self,
        question: str,
        *,
        header: Optional[str] = None,
        options: Optional[List[str]] = None,
        multiple: bool = False,
        allow_custom: bool = True,
    ) -> str:
        if self.rejection is None:
            self.rejection = {
                "type": "interaction_required",
                "tool_name": "ask_user",
                "tool_call_id": None,
                "question": question,
                "message": "ask_user requires interaction in non-interactive mode",
            }
        self._cancel("interaction_required: ask_user")
        return "[interaction_required]"

    def on_max_iterations(self, count: int, messages: list) -> dict:
        self.max_iterations_hit = True
        return {"action": "stop"}


__all__ = ["NonInteractiveTransport", "RejectionDict"]
