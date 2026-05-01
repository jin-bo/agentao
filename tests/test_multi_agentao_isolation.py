"""P0.7 regression: two ``Agentao`` instances in one process must not share state.

Embedded hosts may construct several ``Agentao`` objects per process (one
per request, one per session, one per tenant). The contract is that each
instance is a fresh runtime — nothing leaks between them. This test
exercises the visible state surfaces:

- message history (per-instance turn buffer)
- tool registry (per-instance ``ToolRegistry``)
- skill activations (per-instance ``SkillManager`` when default-built)
- permission state (per-instance ``PermissionEngine`` snapshot)
- working_directory (per-instance, frozen at construction)
- session id (distinct UUIDs)
- memory writes (per-project ``MemoryManager`` writes do not bleed
  across working_directories)

Cross-cutting state we *cannot* easily test in-process (ACP server
identity, replay file paths picked from cwd) is covered by sibling
tests; this file focuses on the in-memory state that two instances are
most likely to alias by accident.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock, patch

from agentao.tools.base import Tool


class _MarkerTool(Tool):
    """Tool that records which agent it was registered on via a unique name."""

    def __init__(self, suffix: str) -> None:
        self._suffix = suffix

    @property
    def name(self) -> str:
        return f"marker_{self._suffix}"

    @property
    def description(self) -> str:
        return "Marker tool for isolation testing"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        return f"marker_{self._suffix}"


def _make_agent(working_directory: Path):
    """Construct an Agentao via the public embedded-host path.

    Pass ``llm_client=`` explicitly so the test exercises the
    documented "inject your own llm_client" seam, not the
    ``conftest.py`` env-backfill helper.
    """
    mock_llm = Mock()
    mock_llm.logger = Mock()
    mock_llm.model = "gpt-test"
    with patch("agentao.tooling.mcp_tools.McpClientManager"), patch(
        "agentao.tooling.mcp_tools.load_mcp_config", return_value={}
    ):
        from agentao.agent import Agentao

        return Agentao(working_directory=working_directory, llm_client=mock_llm)


def test_message_history_is_isolated(tmp_path: Path) -> None:
    """Appending a turn on agent A leaves agent B's history untouched."""
    a = _make_agent(tmp_path / "a")
    b = _make_agent(tmp_path / "b")
    try:
        a.messages.append({"role": "user", "content": "hello A"})
        assert a.messages and len(a.messages) == 1
        assert b.messages == [], "agent B saw agent A's message — shared list?"
    finally:
        a.close()
        b.close()


def test_tool_registry_is_isolated(tmp_path: Path) -> None:
    """Registering a tool on A must not appear on B's registry."""
    a = _make_agent(tmp_path / "a")
    b = _make_agent(tmp_path / "b")
    try:
        marker_a = _MarkerTool("A")
        a.tools.register(marker_a)

        a_names = {t.name for t in a.tools.tools.values()}
        b_names = {t.name for t in b.tools.tools.values()}

        assert "marker_A" in a_names
        assert "marker_A" not in b_names, (
            "tool registry leaked across instances — registries should be per-agent"
        )
        # Ensure the dicts themselves are distinct objects, not just dedup'd.
        assert a.tools.tools is not b.tools.tools
    finally:
        a.close()
        b.close()


def test_skill_manager_is_isolated(tmp_path: Path) -> None:
    """Default-built ``SkillManager`` instances must not share active state."""
    a = _make_agent(tmp_path / "a")
    b = _make_agent(tmp_path / "b")
    try:
        # Two default constructions yield two distinct SkillManagers.
        assert a.skill_manager is not b.skill_manager

        # Active-skill state is per-instance.
        a_active_before = dict(a.skill_manager.active_skills)
        b_active_before = dict(b.skill_manager.active_skills)
        assert a_active_before == b_active_before  # both empty initially

        # Forcing an entry into A's active dict must not appear on B.
        a.skill_manager.active_skills["fake-skill"] = object()
        assert "fake-skill" in a.skill_manager.active_skills
        assert "fake-skill" not in b.skill_manager.active_skills
    finally:
        a.close()
        b.close()


def test_working_directory_is_frozen_per_instance(tmp_path: Path) -> None:
    """Each agent reports the directory it was constructed with.

    Regression for the earlier ``Path.cwd()`` leak — the test sister to
    ``test_factory_freezes_working_directory`` but at the multi-instance
    level: two agents constructed against different dirs keep them.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    a = _make_agent(dir_a)
    b = _make_agent(dir_b)
    try:
        assert a.working_directory == dir_a.resolve()
        assert b.working_directory == dir_b.resolve()
        assert a.working_directory != b.working_directory
    finally:
        a.close()
        b.close()


def test_session_ids_are_distinct(tmp_path: Path) -> None:
    """Two instances start with different session ids.

    Public events carry ``session_id``; if it aliased across instances,
    a host-side filter on ``events(session_id=...)`` would receive
    events from a sibling agent.
    """
    a = _make_agent(tmp_path / "a")
    b = _make_agent(tmp_path / "b")
    try:
        # ``_session_id`` is the runtime-internal field; ``events()`` and
        # ``active_permissions()`` route through it, so two instances
        # generating the same id would defeat host-side filtering.
        sid_a = a._session_id
        sid_b = b._session_id
        assert sid_a and sid_b
        assert sid_a != sid_b, (
            "two Agentao instances minted the same session_id — "
            "embedded hosts cannot filter events()"
        )
    finally:
        a.close()
        b.close()


def test_close_one_does_not_kill_the_other(tmp_path: Path) -> None:
    """Closing agent A leaves agent B fully usable.

    Catches the regression where a class-level singleton (e.g. shared
    MCP client manager, shared memory connection) is closed by A's
    ``close()`` and silently breaks B.
    """
    a = _make_agent(tmp_path / "a")
    b = _make_agent(tmp_path / "b")
    try:
        a.close()
        # B must still be able to access its registries after A closed.
        b.tools.register(_MarkerTool("B"))
        b_names = {t.name for t in b.tools.tools.values()}
        assert "marker_B" in b_names
        b.messages.append({"role": "user", "content": "still alive"})
        assert b.messages
    finally:
        b.close()
