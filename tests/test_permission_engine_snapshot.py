"""``PermissionEngine.snapshot`` decides as its source does, and stays separate.

A sub-agent decides with a snapshot of its parent's engine. It used to rebuild
one from the rule files, which missed every rule that lives only on the engine:
a host's ``rules=``, and an ``agentao run`` spec's allow and deny rules.
"""

from __future__ import annotations

import pytest

from agentao.permissions import PermissionDecision, PermissionEngine, PermissionMode


CALLS = [
    ("write_file", {"file_path": "/tmp/agentao-snapshot/a.txt", "content": "x"}),
    ("run_shell_command", {"command": "ls"}),
    ("run_shell_command", {"command": "rm -rf /"}),
    ("web_fetch", {"url": "https://example.com"}),
    ("web_search", {"query": "x"}),
    ("read_file", {"file_path": "/tmp/agentao-snapshot/a.txt"}),
]


def _source(tmp_path, *, enable_hardline=True):
    engine = PermissionEngine(
        project_root=tmp_path,
        rules=[{"tool": "read_file", "action": "deny"}],
        loaded_sources=["host:in-code"],
        enable_hardline=enable_hardline,
    )
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)
    engine.add_run_rules(
        allow=[{"tool": "web_fetch", "action": "allow"}],
        deny=[{"tool": "write_file", "action": "deny"}],
    )
    engine.add_loaded_source("injected:host")
    return engine


def _decisions(engine):
    return [engine.decide(name, args) for name, args in CALLS]


@pytest.mark.parametrize("enable_hardline", [True, False], ids=["floor", "no-floor"])
def test_a_snapshot_decides_as_its_source_does(tmp_path, enable_hardline):
    source = _source(tmp_path, enable_hardline=enable_hardline)
    snapshot = source.snapshot(project_root=tmp_path)

    assert _decisions(snapshot) == _decisions(source)
    # The rules that exist only on the engine are the point of it.
    assert snapshot.decide("read_file", dict(CALLS[-1][1])) is PermissionDecision.DENY
    assert snapshot.decide(*CALLS[0]) is PermissionDecision.DENY
    assert snapshot.decide(*CALLS[3]) is PermissionDecision.ALLOW
    assert snapshot.hardline_enabled is enable_hardline
    assert snapshot.active_permissions() == source.active_permissions()


def test_a_snapshot_is_not_changed_by_its_source_afterwards(tmp_path):
    source = _source(tmp_path)
    snapshot = source.snapshot(project_root=tmp_path)
    before = _decisions(snapshot)

    source.set_mode(PermissionMode.FULL_ACCESS)
    source.add_run_rules(deny=[{"tool": "web_search", "action": "deny"}])
    source.rules.append({"tool": "run_shell_command", "action": "deny"})

    assert _decisions(snapshot) == before
    assert snapshot.active_mode is PermissionMode.WORKSPACE_WRITE
