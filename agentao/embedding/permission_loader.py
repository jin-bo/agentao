"""Permission-rule file loader.

:func:`load_permission_rules` reads ``<user_root>/permissions.json`` and
returns the parsed rule list plus the source labels the engine surfaces
through :meth:`PermissionEngine.active_permissions`. Project-scope
``<project_root>/.agentao/permissions.json`` is intentionally NOT
loaded — see :class:`agentao.permissions.PermissionEngine` for the
reasoning — but its presence triggers a one-line warning so users
discover the policy.

**This file fails closed.** Every other config reader in the tree warns
and degrades to its default when the file is unreadable, mis-encoded,
malformed, or the wrong shape; this one raises
:class:`PermissionConfigError` and lets session construction abort. The
asymmetry is the point: dropping permission rules is not a neutral
degradation. A user ``deny`` on a shell or web tool degrades to ASK, and
a ``deny`` on an ``mcp_*`` tool degrades to *nothing at all* — the engine
returns ``None``, the runtime falls through to the tool's own
``requires_confirmation``, and a ``trust: true`` server's tool then runs
with no prompt. A log line does not close that; refusing to start does.
The convention already exists in the tree: ``acp_client/config.py``
raises ``AcpConfigError`` for a malformed ``acp.json``.

The ``is_file()`` pre-check is load-bearing rather than cosmetic. Without
it a *missing* file reaches the ``OSError`` branch, which now raises —
i.e. agentao would refuse to start for every user who has never written
a permissions file, which is the common case.

This module owns everything **document**-shaped: that the top level is an
object, the ``rules`` key, and the path in the error message.
:func:`agentao.permissions.validate_permission_rules` owns everything
**rule-list**-shaped and is shared with the two callers that never see a
document (``PermissionEngine(rules=...)`` and ``add_run_rules()``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..permissions import (
    RuleError,
    format_permission_rule_errors,
    validate_permission_rules,
)

_logger = logging.getLogger(__name__)

#: Closed top-level key set for ``permissions.json``. A missing ``rules``
#: key is still legal (an empty policy file is a real, benign state); an
#: *extra* key is not, because it is almost always ``rules`` misspelled.
_LEGAL_DOCUMENT_FIELDS: Tuple[str, ...] = ("rules",)

_ENCODING_HINT = (
    "Re-save it as UTF-8. PowerShell 5.1 — still the default shell on stock "
    "Windows — writes UTF-16LE from `>` and `Out-File`."
)


class PermissionConfigError(ValueError):
    """Raised when ``<user_root>/permissions.json`` exists but cannot be honored.

    Attributes:
        path: The offending file.
        reason: One-line description of the document-level failure.
        errors: ``(index, reason)`` pairs from
            :func:`agentao.permissions.validate_permission_rules`, empty
            for failures that happen before rule validation is reached.
    """

    def __init__(
        self,
        path: Path,
        reason: str,
        *,
        errors: Optional[List[RuleError]] = None,
    ) -> None:
        self.path = Path(path)
        self.reason = reason
        self.errors: List[RuleError] = list(errors or [])
        super().__init__(
            f"Cannot load permission rules from {self.path}: {reason}"
            + format_permission_rule_errors(self.errors)
            + "\nAgentao will not start with a permission file it cannot "
            "honor, because silently dropping rules turns a deny into an "
            "ask. Fix the file, or move it aside to run with defaults."
        )


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

    Raises:
        PermissionConfigError: ``<user_root>/permissions.json`` exists
            but cannot be honored. Callers that must survive a broken
            policy file — ``agentao doctor`` and any future
            ``config validate`` — have to catch this and report it, not
            propagate it: the moment a user most needs diagnostics is
            exactly when their config is broken.
    """
    sources: List[str] = []
    rules: List[Dict[str, Any]] = []

    if user_root is not None:
        user_path = user_root / "permissions.json"
        user_rules, user_loaded = _read_rule_file(user_path)
        if user_loaded:
            sources.append(f"user:{user_path}")
            rules = user_rules

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
    """Return ``(rules, loaded)`` for an existing, valid policy file.

    ``loaded`` is ``True`` only when the file existed and parsed
    cleanly — even if the rule list inside is empty. A *missing* file is
    the one benign case and returns ``([], False)`` so
    :meth:`active_permissions` reports only sources actually consulted.

    Raises:
        PermissionConfigError: The file exists but is unreadable,
            mis-encoded, not valid JSON, not a JSON object, or carries
            rules that fail validation. See the module docstring for why
            this path does not degrade quietly.
    """
    if not path.is_file():
        return [], False
    try:
        # ``utf-8-sig`` strips a leading BOM and is a byte-for-byte no-op
        # without one, so a BOM'd-but-otherwise-valid file loads instead
        # of being rejected. Reads only — on a *write* this codec emits
        # a BOM.
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        # Ordered before OSError only for readability; the two are
        # disjoint. UnicodeDecodeError subclasses ValueError, which is
        # why the original ``except (OSError, json.JSONDecodeError)``
        # let it through.
        raise PermissionConfigError(
            path,
            f"the file is not valid UTF-8 ({exc.reason} at byte {exc.start}). "
            + _ENCODING_HINT,
        ) from exc
    except OSError as exc:
        raise PermissionConfigError(
            path, f"cannot read the file: {type(exc).__name__}: {exc}",
        ) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PermissionConfigError(
            path,
            f"invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
        ) from exc

    if not isinstance(data, dict):
        raise PermissionConfigError(
            path,
            "the top-level value must be a JSON object with a 'rules' key, "
            f"got {type(data).__name__}",
        )

    # The document key set is closed for the same reason the rule key set
    # is: ``data.get("rules", [])`` swallows a typo whole. ``{"rule": [...]}``
    # parses, every rule is dropped, and ``active_permissions()`` still
    # reports the file under ``loaded_sources`` — a silent fail-*open* in
    # the one loader that exists to fail closed.
    unknown = sorted(k for k in data if k not in _LEGAL_DOCUMENT_FIELDS)
    if unknown:
        raise PermissionConfigError(
            path,
            "unknown top-level key(s) "
            + ", ".join(repr(k) for k in unknown)
            + f" (allowed: {', '.join(_LEGAL_DOCUMENT_FIELDS)}). A typo here "
            "is silent: the file parses and every rule is dropped.",
        )

    rules = data.get("rules", [])
    errors = validate_permission_rules(rules)
    if errors:
        raise PermissionConfigError(
            path, "one or more rules are invalid", errors=errors,
        )
    return rules, True
