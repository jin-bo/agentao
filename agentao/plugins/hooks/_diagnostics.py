"""The one-shot diagnostic registry — design gate G10 (§4.2).

An ``ignore``d field earns the author **one** diagnostic naming it, so they learn
the field had no effect without a notice per invocation. "Once" needs an owner,
and the obvious one is wrong: ``PluginHookDispatcher`` is constructed fresh at
six call sites, two of them **inside pool workers**
(``tool_executor.py``), so dispatcher-scoped state would dedup nothing and race
while doing it.

So the registry is **session-scoped and lock-guarded**, keyed by a *stable* rule
key. Get the scope wrong in either direction and the mechanism inverts: a
per-invocation storm, or silence. Both failures look like "it works" from inside
a single test.

**The key is derived from the rule's content**, never from ``id(rule)`` — object
identity changes on every reload, which would silently re-announce everything.
Re-announcing after a reload *is* wanted, and it is arranged explicitly by
:func:`clear_session`, so a corrected hook speaks up again while an unchanged one
stays quiet.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..models import ParsedHookRule


def rule_key(rule: "ParsedHookRule", handler_index: int = 0) -> str:
    """A stable identity for one hook rule.

    Content-derived, so it survives the rule being re-parsed into a new object;
    ``handler_index`` disambiguates two identical handlers in one matcher group,
    which are two rules the author can edit independently.
    """
    material = json.dumps(
        [
            rule.plugin_name or "",
            rule.event,
            rule.hook_type,
            rule.command or "",
            rule.prompt or "",
            sorted((rule.matcher or {}).items()) if isinstance(rule.matcher, dict) else str(rule.matcher),
            handler_index,
        ],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class HookDiagnosticRegistry:
    """Remembers which (rule, field) pairs have already been announced."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str]] = set()

    def announce(self, key: str, field: str) -> bool:
        """Return ``True`` the first time this ``(rule, field)`` is seen.

        Atomic: two tool events firing concurrently must produce one diagnostic,
        not two, and the check and the insert cannot be separate operations.
        """
        with self._lock:
            pair = (key, field)
            if pair in self._seen:
                return False
            self._seen.add(pair)
            return True

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()

    def __len__(self) -> int:  # pragma: no cover - trivial
        with self._lock:
            return len(self._seen)


_REGISTRIES: dict[str, HookDiagnosticRegistry] = {}
_REGISTRY_LOCK = threading.Lock()

#: Used when a caller has no session id — a single process-wide bucket. It keeps
#: the "once" promise for embedders that never create a session, and it is a
#: separate key so a real session cannot inherit its state.
DEFAULT_SESSION = "__default__"


def get_registry(session_id: str | None = None) -> HookDiagnosticRegistry:
    key = session_id or DEFAULT_SESSION
    with _REGISTRY_LOCK:
        registry = _REGISTRIES.get(key)
        if registry is None:
            registry = HookDiagnosticRegistry()
            _REGISTRIES[key] = registry
        return registry


def clear_session(session_id: str | None = None) -> None:
    """Drop a session's registry — on ``/clear``, and on a plugin reload.

    A reload is the case that makes this a function rather than a lifetime: the
    author has just edited the hook, and the fix they made is exactly what they
    want to hear about.
    """
    key = session_id or DEFAULT_SESSION
    with _REGISTRY_LOCK:
        _REGISTRIES.pop(key, None)


def clear_all() -> None:
    with _REGISTRY_LOCK:
        _REGISTRIES.clear()
