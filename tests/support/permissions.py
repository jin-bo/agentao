"""Shared helpers for PermissionEngine tests.

Extracted from the original ``tests/test_permissions.py`` when that file
was split into focused modules (modes, hardline, sensitive-write).
"""

import json

from agentao.permissions import PermissionEngine


def make_engine(tmp_path, monkeypatch, project_rules=None, user_rules=None):
    """Build a PermissionEngine with optional user JSON rules in tmp_path.

    ``project_rules`` is accepted for legacy parity but written to a
    file the engine deliberately ignores (see ``permissions.py``); it
    is preserved here so collision/precedence tests can still assert
    that a stray project file does not leak into the rule set.
    """
    user_root = tmp_path / "home" / ".agentao"

    if project_rules is not None:
        cfg = tmp_path / ".agentao"
        cfg.mkdir(exist_ok=True)
        (cfg / "permissions.json").write_text(
            json.dumps({"rules": project_rules}), encoding="utf-8",
        )
    if user_rules is not None:
        user_root.mkdir(parents=True, exist_ok=True)
        (user_root / "permissions.json").write_text(
            json.dumps({"rules": user_rules}), encoding="utf-8",
        )

    return PermissionEngine(project_root=tmp_path, user_root=user_root)


def allow(tool, **kwargs):
    return {"tool": tool, "action": "allow", **kwargs}


def deny(tool, **kwargs):
    return {"tool": tool, "action": "deny", **kwargs}


def ask(tool, **kwargs):
    return {"tool": tool, "action": "ask", **kwargs}
