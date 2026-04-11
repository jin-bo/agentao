"""Pending interaction registry for ACP server-user bridge (Issue 10).

When an ACP server requests user permission or free-form input, the request
is registered as a :class:`PendingInteraction`.  The user responds via
``/acp approve``, ``/acp reject``, or ``/acp reply`` CLI commands.

Design constraints:
  - Interactions are queued, not popped up — the user decides when to respond.
  - Each server can have at most a few pending interactions concurrently.
  - Thread-safe: notifications arrive on the reader thread; CLI commands run
    on the main thread.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class InteractionKind(str, Enum):
    """Discriminator for the type of user interaction requested."""

    PERMISSION = "permission"
    INPUT = "input"


@dataclass
class PendingInteraction:
    """A single pending interaction request from an ACP server.

    Attributes:
        request_id: Stable, user-visible identifier for this interaction.
        server: Name of the ACP server that sent the request.
        session_id: ACP session id.
        kind: Whether this is a permission or input request.
        prompt: Short human-readable prompt text.
        details: Optional structured details (e.g., tool call payload).
        created_at: Epoch timestamp of when the interaction was registered.
        deadline_at: Optional epoch timestamp after which a default action
            is taken (reject for permission, cancel for input).
        resolved: Set to ``True`` once the user has responded.
        response: The user's response (populated by resolve methods).
    """

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    server: str = ""
    session_id: str = ""
    kind: InteractionKind = InteractionKind.PERMISSION
    prompt: str = ""
    details: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    deadline_at: Optional[float] = None
    resolved: bool = False
    response: Optional[Dict[str, Any]] = None
    # The JSON-RPC request id from the server, needed to send back a response.
    rpc_request_id: Any = None


class InteractionRegistry:
    """Thread-safe registry of pending ACP user interactions.

    Each ACP server can have multiple pending interactions.  The registry
    is keyed by ``request_id`` (globally unique within a session).
    """

    def __init__(self) -> None:
        self._pending: Dict[str, PendingInteraction] = {}
        self._lock = threading.Lock()

    def register(self, interaction: PendingInteraction) -> str:
        """Add a new pending interaction.  Returns the request_id."""
        with self._lock:
            self._pending[interaction.request_id] = interaction
        return interaction.request_id

    def get(self, request_id: str) -> Optional[PendingInteraction]:
        """Look up a pending interaction by request_id."""
        with self._lock:
            return self._pending.get(request_id)

    def resolve(self, request_id: str, response: Dict[str, Any]) -> Optional[PendingInteraction]:
        """Mark an interaction as resolved with the given response.

        Returns the interaction if found and not yet resolved, else ``None``.
        """
        with self._lock:
            interaction = self._pending.get(request_id)
            if interaction is None or interaction.resolved:
                return None
            interaction.resolved = True
            interaction.response = response
            return interaction

    def remove(self, request_id: str) -> Optional[PendingInteraction]:
        """Remove and return a pending interaction."""
        with self._lock:
            return self._pending.pop(request_id, None)

    def list_pending(self, server: Optional[str] = None) -> List[PendingInteraction]:
        """Return all unresolved interactions, optionally filtered by server."""
        with self._lock:
            items = [
                i for i in self._pending.values()
                if not i.resolved
            ]
        if server is not None:
            items = [i for i in items if i.server == server]
        return sorted(items, key=lambda i: i.created_at)

    def list_all(self, server: Optional[str] = None) -> List[PendingInteraction]:
        """Return all interactions (including resolved), optionally filtered."""
        with self._lock:
            items = list(self._pending.values())
        if server is not None:
            items = [i for i in items if i.server == server]
        return sorted(items, key=lambda i: i.created_at)

    def expire_overdue(self) -> List[PendingInteraction]:
        """Find and return interactions past their deadline.

        Does NOT auto-resolve them — the caller decides what default
        action to take (reject for permission, cancel for input).
        """
        now = time.time()
        overdue: List[PendingInteraction] = []
        with self._lock:
            for interaction in self._pending.values():
                if (
                    not interaction.resolved
                    and interaction.deadline_at is not None
                    and now >= interaction.deadline_at
                ):
                    overdue.append(interaction)
        return overdue

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for i in self._pending.values() if not i.resolved)

    @property
    def is_empty(self) -> bool:
        return self.pending_count == 0
