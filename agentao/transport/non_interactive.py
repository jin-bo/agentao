"""Non-interactive transport for the ``agentao run`` automation surface.

Converts any runtime request that would normally block on a human
(tool confirmation, free-form ``ask_user``, max-iteration choice) into
a clean abort. The pipeline owns a :class:`CancellationToken` and
passes it in at construction; ``confirm_tool`` / ``ask_user`` record a
rejection and cancel the token so the chat loop unwinds on its next
iteration.

Under ``policy="deny"`` (#565) a tool confirmation is declined back to the
model instead: ``confirm_tool`` records a *denial*, tells the runner why
(:data:`~agentao.transport.confirmation.APPROVAL_UNAVAILABLE`) and leaves the
token alone, so the model sees the refusal and the turn goes on. A denial is
not a :attr:`rejection` — the pipeline fails a run on a rejection, and a denied
call is recoverable. The ``max_denials + 1``-th ASK of the run falls back to the
``reject`` abort, so a model that keeps asking still ends. ``ask_user`` aborts
under both policies: a question nobody answers is a dead end.

The transport never receives the matched permission rule directly —
that travels through the public host event stream
(``permission_decision`` events with ``outcome="deny"``). The pipeline
registers a sync observer that converts those events into
``transport.rejection``. ASK plans push their ``tool_call_id`` onto
:attr:`_ask_queue` so the matching ``confirm_tool`` call can recover
it FIFO.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Literal, Optional, Tuple, TYPE_CHECKING

from . import confirmation as _confirmation
from .sdk import SdkTransport

if TYPE_CHECKING:
    from ..cancellation import CancellationToken


# Rejection dict shape (produced here, consumed by the run pipeline).
# Keys: ``type`` ∈ RunErrorEnvelope.type Literal, ``tool_name``,
# ``tool_call_id``, ``message``, optional ``matched_rule``,
# optional ``question``.
RejectionDict = Dict[str, Any]

# Denial dict shape: ``tool_name``, ``tool_call_id``, ``matched_rule``.
DenialDict = Dict[str, Any]

#: Denials a run returns to the model before the next ASK aborts it.
DEFAULT_MAX_DENIALS = 5


class NonInteractiveTransport(SdkTransport):
    """SdkTransport subclass that aborts cleanly instead of prompting.

    - ``confirm_tool``: sets :attr:`rejection` to a ``permission_required``
      envelope, cancels the bound token, returns ``False``. Under
      ``policy="deny"`` it appends to :attr:`denials` and returns ``False``
      without cancelling, until ``max_denials`` denials have been made.
    - ``ask_user``: sets :attr:`rejection` to ``interaction_required``,
      cancels the token, returns a sentinel string.
    - ``on_max_iterations``: flips :attr:`max_iterations_hit` and
      returns ``{"action": "stop"}``. The pipeline classifier maps
      the flag to exit code 4. Cancellation is intentionally NOT
      triggered here — the runtime's existing stop handling already
      returns the partial response.
    """

    def __init__(
        self,
        token: Optional["CancellationToken"] = None,
        *,
        policy: Literal["reject", "deny"] = "reject",
        max_denials: int = DEFAULT_MAX_DENIALS,
    ) -> None:
        super().__init__()
        if policy not in ("reject", "deny"):
            raise ValueError(f"unknown interaction policy {policy!r}")
        if isinstance(max_denials, bool) or not isinstance(max_denials, int) or max_denials < 0:
            raise ValueError(f"max_denials must be an int >= 0, got {max_denials!r}")
        self.rejection: Optional[RejectionDict] = None
        self.denials: List[DenialDict] = []
        self.max_iterations_hit: bool = False
        self._token = token
        self._policy = policy
        self._max_denials = max_denials
        # A foreground sub-agent confirms through this transport on its own
        # thread, so the count and the queue are shared across threads.
        self._lock = threading.Lock()
        # FIFO populated by the pipeline observer from
        # ``permission_decision`` events with ``outcome=="prompt"``.
        # ``confirm_tool`` consumes it to recover the matching
        # ``tool_call_id`` (and rule) for the rejection or denial.
        self._ask_queue: List[Tuple[str, Optional[str], Optional[Dict[str, Any]]]] = []

    def queue_ask(
        self,
        tool_name: str,
        tool_call_id: Optional[str],
        matched_rule: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Push an ASK ``permission_decision`` event onto the FIFO."""
        with self._lock:
            self._ask_queue.append((tool_name, tool_call_id, matched_rule))

    def _pop_ask(self, tool_name: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        # FIFO match by name is sufficient: the runner emits
        # permission_decision events in plan order before any
        # confirm_tool fires for the same batch (tool_runner.py:193-200).
        with self._lock:
            for i, (name, tcid, rule) in enumerate(self._ask_queue):
                if name == tool_name:
                    del self._ask_queue[i]
                    return tcid, rule
        return None, None

    def record_rejection(self, rejection: RejectionDict) -> bool:
        """Set :attr:`rejection` unless one is set; ``True`` if this one won.

        Every writer goes through here (``confirm_tool``, ``ask_user`` and the
        pipeline's ``permission_decision`` observer), so the first reason the
        run ends for is the one reported. Today the writers cannot overlap: a
        sub-agent runs in phase 3 of a batch that a sub-agent call makes
        serial, after the parent emitted its permission events, and its own
        events go to its own stream. The lock keeps that from mattering.
        """
        with self._lock:
            if self.rejection is not None:
                return False
            self.rejection = rejection
            return True

    def _cancel(self, reason: str) -> None:
        if self._token is not None:
            try:
                self._token.cancel(reason)
            except Exception:
                pass

    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
        tool_call_id, matched_rule = self._pop_ask(tool_name)
        if self._policy == "deny":
            with self._lock:
                deny_back = len(self.denials) < self._max_denials
                if deny_back:
                    self.denials.append({
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "matched_rule": matched_rule,
                    })
            if deny_back:
                _confirmation.record_decline(_confirmation.APPROVAL_UNAVAILABLE)
                return False
            message = (
                f"{tool_name} requires approval in this mode; the run already "
                f"denied {self._max_denials} call(s) (max_denials)"
            )
        else:
            message = f"{tool_name} requires approval in this mode"
        self.record_rejection({
            "type": "permission_required",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "message": message,
        })
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
        self.record_rejection({
            "type": "interaction_required",
            "tool_name": "ask_user",
            "tool_call_id": None,
            "question": question,
            "message": "ask_user requires interaction in non-interactive mode",
        })
        self._cancel("interaction_required: ask_user")
        return "[interaction_required]"

    def on_max_iterations(self, count: int, messages: list) -> dict:
        self.max_iterations_hit = True
        return {"action": "stop"}


__all__ = [
    "DEFAULT_MAX_DENIALS",
    "DenialDict",
    "NonInteractiveTransport",
    "RejectionDict",
]
