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

``confirm_tool`` gets no call identity, so the transport pairs each one with
its call as ACP does (``acp/transport.py``): the runner emits
``TOOL_CONFIRMATION`` with ``call_id`` just before ``confirm_tool``, on the
same thread, and :meth:`emit` keeps that id in a thread-local that the next
``confirm_tool`` consumes. A foreground sub-agent's ``TOOL_CONFIRMATION`` is
forwarded here on the child's thread, so its denials carry its own id (#566).

The matched permission rule travels through the public host event stream
instead. The pipeline registers a sync observer: a ``permission_decision``
with ``outcome="deny"`` becomes ``transport.rejection``, and one with
``outcome="prompt"`` is kept by :meth:`queue_ask`, keyed by
``(tool_name, tool_call_id)``, until its ``confirm_tool`` takes it. Provider
ids are not guaranteed unique, hence the name in the key. A sub-agent's
permission events go to its own stream, so its denials have no rule.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Literal, Optional, Tuple, TYPE_CHECKING

from . import confirmation as _confirmation
from .events import AgentEvent, EventType
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
        # thread, so the count and the rule map are shared across threads.
        self._lock = threading.Lock()
        # The ``call_id`` of the confirmation about to be asked on this
        # thread, from ``TOOL_CONFIRMATION``; consumed by ``confirm_tool``.
        self._confirmation_local = threading.local()
        # Rules of ASK ``permission_decision`` events, by
        # ``(tool_name, tool_call_id)``; an entry is removed when matched.
        self._asked_rules: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}

    def emit(self, event: AgentEvent) -> None:
        if event.type == EventType.TOOL_CONFIRMATION:
            call_id = (event.data or {}).get("call_id")
            # Set even when empty, so an id never outlives its confirmation.
            self._confirmation_local.call_id = str(call_id) if call_id else None
        super().emit(event)

    def queue_ask(
        self,
        tool_name: str,
        tool_call_id: Optional[str],
        matched_rule: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Keep an ASK ``permission_decision`` event's rule for its confirmation."""
        if not tool_call_id:
            return
        with self._lock:
            self._asked_rules[(tool_name, tool_call_id)] = matched_rule

    def _take_call(self, tool_name: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """The call being confirmed on this thread, and its rule; consumes both."""
        call_id = getattr(self._confirmation_local, "call_id", None)
        self._confirmation_local.call_id = None
        if call_id is None:
            return None, None
        with self._lock:
            rule = self._asked_rules.pop((tool_name, call_id), None)
        return call_id, rule

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
        tool_call_id, matched_rule = self._take_call(tool_name)
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
        rejection: RejectionDict = {
            "type": "permission_required",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "message": message,
        }
        if matched_rule is not None:
            rejection["matched_rule"] = matched_rule
        self.record_rejection(rejection)
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
