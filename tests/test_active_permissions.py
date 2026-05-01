"""Tests for ``PermissionEngine.active_permissions`` (PR 3).

Covers the matrix the harness contract requires:

- preset-only (no project / user files)
- project-only file
- project + user files
- injected source label appended by the host
- mode switch invalidates the cached snapshot
- the snapshot is a deep copy (mutation does not leak back into the engine)
- the getter does not re-read disk on every call
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from agentao.harness.models import ActivePermissions
from agentao.permissions import PermissionEngine, PermissionMode


def _write_perms(path: Path, rules: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rules": rules}))


def test_preset_only(tmp_path):
    engine = PermissionEngine(project_root=tmp_path)
    snap = engine.active_permissions()
    assert isinstance(snap, ActivePermissions)
    assert snap.mode == "workspace-write"
    assert snap.loaded_sources == ["preset:workspace-write"]
    assert snap.rules  # preset rules populate the list


def test_project_file_loaded_source(tmp_path):
    rules = [{"tool": "write_file", "action": "ask"}]
    _write_perms(tmp_path / ".agentao" / "permissions.json", rules)
    engine = PermissionEngine(project_root=tmp_path)
    snap = engine.active_permissions()
    assert any(s.startswith("project:") for s in snap.loaded_sources)
    assert any(
        r.get("tool") == "write_file" and r.get("action") == "ask"
        for r in snap.rules
    ), "project rule must appear in the projected rule list"


def test_user_and_project_files_both_recorded(tmp_path):
    proj_root = tmp_path / "proj"
    user_root = tmp_path / "user"
    _write_perms(proj_root / ".agentao" / "permissions.json", [
        {"tool": "write_file", "action": "deny"},
    ])
    _write_perms(user_root / "permissions.json", [
        {"tool": "run_shell_command", "action": "ask"},
    ])
    engine = PermissionEngine(project_root=proj_root, user_root=user_root)
    snap = engine.active_permissions()
    sources = snap.loaded_sources
    assert sources[0] == "preset:workspace-write"
    # Project listed before user (matches the engine's evaluation order).
    project_idx = next(i for i, s in enumerate(sources) if s.startswith("project:"))
    user_idx = next(i for i, s in enumerate(sources) if s.startswith("user:"))
    assert project_idx < user_idx


def test_user_root_without_file_is_not_recorded(tmp_path):
    engine = PermissionEngine(
        project_root=tmp_path / "proj",
        user_root=tmp_path / "user",
    )
    snap = engine.active_permissions()
    assert all(not s.startswith("user:") for s in snap.loaded_sources)
    assert all(not s.startswith("project:") for s in snap.loaded_sources)


def test_injected_source_appended(tmp_path):
    engine = PermissionEngine(project_root=tmp_path)
    engine.add_loaded_source("injected:host")
    snap = engine.active_permissions()
    assert "injected:host" in snap.loaded_sources
    # Duplicate adds are coalesced.
    engine.add_loaded_source("injected:host")
    snap = engine.active_permissions()
    assert snap.loaded_sources.count("injected:host") == 1


def test_mode_switch_invalidates_cache(tmp_path):
    engine = PermissionEngine(project_root=tmp_path)
    snap1 = engine.active_permissions()
    assert snap1.mode == "workspace-write"
    engine.set_mode(PermissionMode.READ_ONLY)
    snap2 = engine.active_permissions()
    assert snap2.mode == "read-only"
    assert snap2.loaded_sources[0] == "preset:read-only"
    # Distinct objects — cached snapshot was invalidated, not mutated.
    assert snap1 is not snap2


def test_snapshot_rules_are_deep_copied(tmp_path):
    _write_perms(tmp_path / ".agentao" / "permissions.json", [
        {"tool": "write_file", "args": {"path": "."}, "action": "ask"},
    ])
    engine = PermissionEngine(project_root=tmp_path)
    snap = engine.active_permissions()
    # Mutate the projection — the engine must not pick up the change.
    snap.rules[0]["action"] = "allow"
    snap2 = engine.active_permissions()
    # ``snap2`` may be the cached object (same instance) but the
    # important guarantee is that ``engine.rules`` is untouched.
    assert engine.rules[0]["action"] == "ask"
    assert snap2.rules[0]["action"] in ("ask", "allow"), (
        "either the cache returned the mutated snapshot (allow) or a "
        "fresh projection (ask); both keep the engine's rules intact"
    )


def test_active_permissions_does_not_re_read_disk(tmp_path, monkeypatch):
    """Permission decisions may call ``active_permissions`` on the hot path.

    The cache must be hit on the second call without touching the
    filesystem (no extra ``_load_file`` invocations).
    """
    _write_perms(tmp_path / ".agentao" / "permissions.json", [
        {"tool": "write_file", "action": "ask"},
    ])
    engine = PermissionEngine(project_root=tmp_path)

    calls: List[Path] = []
    real_load = engine._load_file

    def spy_load(path: Path):
        calls.append(path)
        return real_load(path)

    monkeypatch.setattr(engine, "_load_file", spy_load)

    engine.active_permissions()
    engine.active_permissions()
    engine.active_permissions()
    assert calls == [], "active_permissions() must not re-read disk"


def test_agent_active_permissions_falls_back_when_no_engine(tmp_path):
    """``Agentao.active_permissions()`` reflects the engine-less runtime
    accurately.

    Without a ``PermissionEngine`` the runtime falls back to per-tool
    ``requires_confirmation`` — write tools are NOT categorically
    blocked. Reporting ``read-only`` would be stricter than reality
    and would mislead status displays / public permission events;
    ``workspace-write`` plus the ``default:no-engine`` source label
    is the closest accurate projection.
    """
    from agentao.harness.models import ActivePermissions

    # Build an Agentao without invoking the LLM stack: stub out the
    # heavy bits so we can assert the active_permissions surface only.
    from agentao import agent as _agent_mod

    class _NoLLMAgent(_agent_mod.Agentao):
        def __init__(self):
            self.permission_engine = None

        def active_permissions(self):  # type: ignore[override]
            return _agent_mod.Agentao.active_permissions(self)

    snap = _NoLLMAgent().active_permissions()
    assert isinstance(snap, ActivePermissions)
    assert snap.mode == "workspace-write"
    assert snap.rules == []
    assert snap.loaded_sources == ["default:no-engine"]


def test_engine_decide_unaffected_by_loaded_sources_tracking(tmp_path):
    """Adding loaded-source labels must not change ``decide`` behavior."""
    _write_perms(tmp_path / ".agentao" / "permissions.json", [
        {"tool": "write_file", "action": "deny"},
    ])
    engine = PermissionEngine(project_root=tmp_path)
    engine.add_loaded_source("injected:host")
    from agentao.permissions import PermissionDecision
    assert engine.decide("write_file", {}) == PermissionDecision.DENY
