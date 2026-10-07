"""Host-facing permission posture: string modes and ``Agentao(permission_mode=)``.

The public models spell a mode as a string (``ActivePermissions.mode`` is a
``Literal``), so a host should not need the internal ``PermissionMode`` enum
to set one. ``docs/design/host-api-ergonomics-review.md`` (F3) records the
contract these tests pin:

- ``set_permission_mode`` accepts the string; its return value stays the enum;
- ``permission_mode=`` builds ``PermissionEngine(rules=[])`` — never the
  file loader — and starts the agent in that mode silently (no event: a
  starting state is not a switch), with the engine and the runner's
  read-only flag agreeing; ``"plan"`` is refused;
- ``permission_mode=None`` builds nothing, and passing both it and
  ``permission_engine=`` is refused rather than given a precedence rule.
"""

from __future__ import annotations

import logging
import subprocess
import sys

import pytest

from agentao import Agentao
from agentao.permissions import PermissionEngine, PermissionMode, _parse_permission_mode
from agentao.transport import EventType, SdkTransport

_LOGGER = logging.getLogger("test.permission_mode_host_api")


def _make(tmp_path, **kwargs):
    return Agentao(
        api_key="k", base_url="https://test.local/v1", model="m",
        working_directory=tmp_path, logger=_LOGGER, **kwargs,
    )


def _mode_events(events):
    return [
        (e.data.get("previous"), e.data.get("current"), e.data.get("cause"))
        for e in events
        if e.type is EventType.PERMISSION_MODE_CHANGED
    ]


# ── _parse_permission_mode ────────────────────────────────────────────────


@pytest.mark.parametrize("mode", list(PermissionMode))
def test_every_mode_parses_from_its_string_value(mode):
    assert _parse_permission_mode(mode.value) is mode
    assert _parse_permission_mode(mode) is mode


def test_an_unknown_string_is_refused():
    with pytest.raises(ValueError, match="unknown permission mode 'readonly'"):
        _parse_permission_mode("readonly")


@pytest.mark.parametrize("value", [None, 1, True, ["read-only"]])
def test_a_non_string_is_a_type_error(value):
    with pytest.raises(TypeError):
        _parse_permission_mode(value)


# ── Agentao(permission_mode=) ────────────────────────────────────────────


def test_no_permission_mode_builds_no_engine(tmp_path):
    agent = _make(tmp_path)
    try:
        assert agent.permission_engine is None
        assert agent.active_permissions().loaded_sources == ["default:no-engine"]
    finally:
        agent.close()


def test_read_only_moves_both_switches(tmp_path):
    agent = _make(tmp_path, permission_mode="read-only")
    try:
        assert isinstance(agent.permission_engine, PermissionEngine)
        assert agent.permission_engine.active_mode is PermissionMode.READ_ONLY
        # The runner's flag, not only the engine's preset: a half switch is
        # what apply_permission_mode exists to prevent.
        assert agent.tool_runner.readonly_mode is True
        assert agent.tool_runner.readonly_active() is True
        assert agent.active_permissions().mode == "read-only"
    finally:
        agent.close()


def test_the_engine_has_no_rules_and_never_runs_the_file_loader(tmp_path, monkeypatch):
    # rules=None would run the loader, which also stats a project-scope
    # permissions.json and warns about it. permission_mode= must do neither.
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "permissions.json").write_text(
        '{"rules": [{"tool": "*", "action": "allow"}]}', encoding="utf-8",
    )

    def _loader_called(**_kwargs):
        raise AssertionError("permission_mode= must not run the permission-file loader")

    monkeypatch.setattr(
        "agentao.embedding.permission_loader.load_permission_rules", _loader_called,
    )
    agent = _make(tmp_path, permission_mode="workspace-write")
    try:
        assert agent.permission_engine.rules == []
        assert agent.active_permissions().loaded_sources == ["preset:workspace-write"]
    finally:
        agent.close()


def test_a_workspace_write_start_emits_nothing(tmp_path):
    events = []
    agent = _make(tmp_path, permission_mode="workspace-write",
                  transport=SdkTransport(on_event=events.append))
    try:
        assert _mode_events(events) == []
        assert not [e for e in events if e.type is EventType.READONLY_MODE_CHANGED]
    finally:
        agent.close()


def test_a_read_only_start_is_silent(tmp_path):
    # A starting state is not a switch: no invented workspace-write ->
    # read-only transition, and nothing delivered to the host's transport
    # before the constructor has returned the agent.
    events = []
    agent = _make(tmp_path, permission_mode="read-only",
                  transport=SdkTransport(on_event=events.append))
    try:
        assert _mode_events(events) == []
        assert not [e for e in events if e.type is EventType.READONLY_MODE_CHANGED]
        assert agent.tool_runner.readonly_mode is True
    finally:
        agent.close()


def test_a_switch_after_construction_is_still_recorded(tmp_path):
    events = []
    agent = _make(tmp_path, permission_mode="read-only",
                  transport=SdkTransport(on_event=events.append))
    try:
        agent.set_permission_mode("workspace-write")
        assert _mode_events(events) == [("read-only", "workspace-write", "host")]
        assert [e.data for e in events if e.type is EventType.READONLY_MODE_CHANGED] == [
            {"previous": True, "current": False},
        ]
    finally:
        agent.close()


@pytest.mark.parametrize("mode", ["plan", PermissionMode.PLAN])
def test_plan_is_refused_at_construction(tmp_path, mode):
    # The PLAN preset without a PlanSession would deny without telling the
    # model it is planning; plan mode has its own entry points.
    with pytest.raises(ValueError, match="not accepted at construction"):
        _make(tmp_path, permission_mode=mode)


def test_plan_is_still_accepted_by_set_permission_mode(tmp_path):
    # Existing behaviour, unchanged: only the new construction parameter
    # refuses it.
    agent = _make(tmp_path, permission_mode="workspace-write")
    try:
        agent.set_permission_mode("plan")
        assert agent.permission_engine.active_mode is PermissionMode.PLAN
    finally:
        agent.close()


def test_the_enum_is_still_accepted(tmp_path):
    agent = _make(tmp_path, permission_mode=PermissionMode.FULL_ACCESS)
    try:
        assert agent.permission_engine.active_mode is PermissionMode.FULL_ACCESS
    finally:
        agent.close()


def test_both_permission_mode_and_permission_engine_is_refused(tmp_path):
    with pytest.raises(ValueError, match="permission_engine= or permission_mode="):
        _make(tmp_path, permission_mode="read-only",
              permission_engine=PermissionEngine(project_root=tmp_path, rules=[]))


def test_a_bad_mode_fails_at_construction(tmp_path):
    with pytest.raises(ValueError, match="unknown permission mode"):
        _make(tmp_path, permission_mode="read_only")


# ── set_permission_mode(str) ─────────────────────────────────────────────


def test_set_permission_mode_takes_a_string_and_still_returns_the_enum(tmp_path):
    agent = _make(tmp_path, permission_mode="workspace-write")
    try:
        previous = agent.set_permission_mode("read-only")
        # The return value is deliberately unchanged: the enum, not a string.
        assert previous is PermissionMode.WORKSPACE_WRITE
        assert agent.tool_runner.readonly_active() is True
        assert agent.set_permission_mode("full-access") is PermissionMode.READ_ONLY
        assert agent.tool_runner.readonly_mode is False
    finally:
        agent.close()


def test_set_permission_mode_refuses_an_unknown_string_and_changes_nothing(tmp_path):
    agent = _make(tmp_path, permission_mode="workspace-write")
    try:
        with pytest.raises(ValueError, match="unknown permission mode"):
            agent.set_permission_mode("readonly")
        assert agent.permission_engine.active_mode is PermissionMode.WORKSPACE_WRITE
    finally:
        agent.close()


def test_set_permission_mode_without_an_engine_still_raises(tmp_path):
    agent = _make(tmp_path)
    try:
        with pytest.raises(ValueError, match="no permission engine"):
            agent.set_permission_mode("read-only")
    finally:
        agent.close()


# ── agentao.host exports ─────────────────────────────────────────────────


def test_cancellation_token_is_the_same_class_from_agentao_host():
    import agentao.host
    from agentao.cancellation import CancellationToken

    assert agentao.host.CancellationToken is CancellationToken
    assert "CancellationToken" in agentao.host.__all__


def test_dir_lists_lazy_exports_without_importing_them():
    # A fresh interpreter: the suite has long since imported agentao.tools.
    code = (
        "import sys, agentao.host as h\n"
        "names = dir(h)\n"
        "missing = [n for n in h.__all__ if n not in names]\n"
        "assert not missing, missing\n"
        "assert 'agentao.tools.base' not in sys.modules, 'dir() imported the tools package'\n"
        "assert 'agentao.runtime.chat_loop' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr


# ── build_from_environment(permission_mode=) ─────────────────────────────


def test_build_from_environment_applies_the_mode_to_its_own_engine(tmp_path, monkeypatch):
    # The factory always builds an engine from the permission files, so
    # forwarding ``permission_mode=`` unchanged would trip the constructor's
    # either-or check for a caller who passed only the mode.
    from agentao.embedding import build_from_environment

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    # User scope: project-scope permission rules are not honoured.
    (tmp_path / "home" / ".agentao").mkdir(parents=True)
    (tmp_path / "home" / ".agentao" / "permissions.json").write_text(
        '{"rules": [{"tool": "web_fetch", "action": "deny"}]}', encoding="utf-8",
    )
    events = []
    agent = build_from_environment(
        tmp_path, resolved_llm={"api_key": "k", "base_url": "https://test.local/v1", "model": "m"},
        permission_mode="read-only", logger=_LOGGER,
        transport=SdkTransport(on_event=events.append),
        mcp_registry=None, bg_store=None,
    )
    try:
        assert agent.permission_engine.active_mode is PermissionMode.READ_ONLY
        assert agent.tool_runner.readonly_active() is True
        # The file's rules survive: the mode does not swap in a rule-less engine.
        assert {"tool": "web_fetch", "action": "deny"} in agent.permission_engine.rules
        assert agent.tool_runner.readonly_mode is True
        # Silent, as with Agentao(permission_mode=).
        assert _mode_events(events) == []
        assert not [e for e in events if e.type is EventType.READONLY_MODE_CHANGED]
    finally:
        agent.close()


def test_build_from_environment_still_refuses_mode_with_an_explicit_engine(tmp_path):
    from agentao.embedding import build_from_environment

    with pytest.raises(ValueError, match="permission_engine= or permission_mode="):
        build_from_environment(
            tmp_path, resolved_llm={"api_key": "k", "base_url": "https://test.local/v1", "model": "m"},
            permission_mode="read-only", logger=_LOGGER,
            permission_engine=PermissionEngine(project_root=tmp_path, rules=[]),
            mcp_registry=None, bg_store=None,
        )
    # Refused by the factory itself, before the memory store was opened —
    # not by ``Agentao`` after it.
    assert not (tmp_path / ".agentao" / "memory.db").exists()


def test_build_from_environment_refuses_plan_before_opening_anything(tmp_path):
    from agentao.embedding import build_from_environment

    with pytest.raises(ValueError, match="not accepted at construction"):
        build_from_environment(
            tmp_path, resolved_llm={"api_key": "k", "base_url": "https://test.local/v1", "model": "m"},
            permission_mode="plan", logger=_LOGGER,
            mcp_registry=None, bg_store=None,
        )
    # Refused before the memory store was opened.
    assert not (tmp_path / ".agentao" / "memory.db").exists()


def test_set_initial_permission_mode_refuses_plan_itself(tmp_path):
    # Not only via its two callers' pre-filtering: the helper enforces the
    # construction vocabulary, so a future caller cannot reach PLAN without
    # a PlanSession through it.
    from agentao.runtime.permission_mode import _set_initial_permission_mode

    agent = _make(tmp_path, permission_mode="workspace-write")
    try:
        with pytest.raises(ValueError, match="not accepted at construction"):
            _set_initial_permission_mode(agent, PermissionMode.PLAN)
        assert agent.permission_engine.active_mode is PermissionMode.WORKSPACE_WRITE
    finally:
        agent.close()
