"""Permission-rule file loader (extracted from :class:`PermissionEngine`).

:func:`load_permission_rules` reads ``<user_root>/permissions.json`` and
returns the parsed rule list plus the source labels the engine surfaces
through :meth:`PermissionEngine.active_permissions`. Project-scope
``<project_root>/.agentao/permissions.json`` is intentionally NOT
loaded — see :class:`agentao.permissions.PermissionEngine` for the
reasoning — but its presence triggers a one-line warning so users
discover the policy.

Hosts that pre-load rules and pass them explicitly to
``PermissionEngine(rules=..., loaded_sources=...)`` bypass disk
I/O entirely. The legacy ``PermissionEngine(project_root=..., user_root=...)``
constructor path still auto-loads via this function — the engine no
longer reads files itself; it lazy-imports this helper.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)


def load_permission_rules(
    *,
    project_root: Path,
    user_root: Optional[Path],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load permission rules from the user-scope file.

    Args:
        project_root: Project directory. Used only to warn on a stray
            ``<project_root>/.agentao/permissions.json`` that the engine
            no longer honors.
        user_root: User-scope directory whose ``permissions.json`` is
            the only file-based rule source. ``None`` skips the read
            entirely.

    Returns:
        ``(rules, loaded_sources)``. ``rules`` is the parsed rule list
        (empty when no file was loaded). ``loaded_sources`` contains a
        ``"user:<path>"`` entry for each file that existed and parsed
        cleanly.
    """
    sources: List[str] = []
    rules: List[Dict[str, Any]] = []

    if user_root is not None:
        user_path = user_root / "permissions.json"
        user_rules, user_loaded = _read_rule_file(user_path)
        if user_loaded:
            sources.append(f"user:{user_path}")
            rules = list(user_rules)

    project_path = project_root / ".agentao" / "permissions.json"
    if project_path.exists():
        _logger.warning(
            "Ignoring %s: project-scope permission rules are no longer "
            "honored (a checked-in allow-rule could grant the agent "
            "capabilities the user never approved). Move custom rules to "
            "the user-scope file.",
            project_path,
        )

    return rules, sources


def _read_rule_file(path: Path) -> Tuple[List[Dict[str, Any]], bool]:
    """Return ``(rules, loaded)``.

    ``loaded`` is ``True`` only when the file existed and parsed
    cleanly — even if the rule list inside is empty. Non-existent or
    malformed files return ``loaded=False`` so
    :meth:`active_permissions` reports only sources actually consulted.
    """
    if not path.exists():
        return [], False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return [], False
        return data.get("rules", []), True
    except (IOError, json.JSONDecodeError):
        return [], False
