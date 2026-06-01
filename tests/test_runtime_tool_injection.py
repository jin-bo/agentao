"""Runtime tool injection: ``Agentao.add_tool`` / ``remove_tool``.

Covers the contract from ``docs/design/runtime-tool-injection.md``:

1. ``add_tool`` registers post-construction, binding capabilities like
   ``extra_tools=`` (never a "bare" tool), and overrides only on ``replace=True``.
2. ``remove_tool`` unregisters and returns existence; unknown name → ``False``.
3. Reserved namespaces (``mcp_`` prefix, ``_PLAN_ONLY_TOOLS``) are rejected by
   *both* add and remove — no ``add_tool(name="plan_save", replace=True)`` loophole.
4. The plan-name reservation also tightens construction-time ``extra_tools=``.
5. Visibility: changes show up in the per-call schema snapshot
   (``to_openai_format``), the same surface ``chat()``/``arun()`` reads.
"""

from __future__ import annotations

import logging

import pytest

from agentao import Agentao
from agentao.host import Tool
from agentao.tools.base import ToolRegistry


def _make_agent(tmp_path, **kwargs) -> Agentao:
    return Agentao(
        working_directory=tmp_path,
        api_key="x",
        base_url="http://localhost:0",
        model="dummy",
        **kwargs,
    )


class _NamedTool(Tool):
    """Minimal concrete Tool with a configurable name + marker description."""

    def __init__(self, name: str, description: str = "marker") -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self):
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return "ran"


# ── ToolRegistry.unregister (the base primitive) ──────────────────────────


def test_registry_unregister_present_and_absent():
    reg = ToolRegistry()
    reg.register(_NamedTool("alpha"))
    assert reg.unregister("alpha") is True
    assert "alpha" not in reg.tools
    # Second removal / unknown name → False, never raises.
    assert reg.unregister("alpha") is False
    assert reg.unregister("never_existed") is False


# ── add_tool: register, bind, override ────────────────────────────────────


def test_add_tool_registers_new(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        agent.add_tool(_NamedTool("my_retrieval"))
        assert "my_retrieval" in agent.tools.tools
    finally:
        agent.close()


def test_add_tool_binds_capabilities(tmp_path):
    """An injected tool inherits the agent's own wd/filesystem/shell (identity).

    Inject non-``None`` filesystem / shell sentinels so the identity check is
    meaningful — with the default ``None`` capabilities the assertions would be
    ``None is None`` and pass even if ``_bind_and_register`` never ran.
    """
    fs_sentinel, shell_sentinel = object(), object()
    agent = _make_agent(tmp_path, filesystem=fs_sentinel, shell=shell_sentinel)
    try:
        tool = _NamedTool("my_retrieval")
        agent.add_tool(tool)
        registered = agent.tools.tools["my_retrieval"]
        assert registered.working_directory == agent._working_directory
        # ``fs_sentinel`` / ``shell_sentinel`` are fresh unique objects, so an
        # identity match can only mean ``_bind_and_register`` assigned the
        # agent's own capabilities (not a default ``None is None`` pass).
        assert registered.filesystem is fs_sentinel
        assert registered.shell is shell_sentinel
    finally:
        agent.close()


def test_add_tool_existing_name_requires_replace(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        with pytest.raises(ValueError, match="already.*registered"):
            agent.add_tool(_NamedTool("read_file"))
    finally:
        agent.close()


def test_add_tool_replace_overrides_and_logs(tmp_path, caplog):
    """``replace=True`` overrides a built-in, silent save for an INFO audit line."""
    agent = _make_agent(tmp_path)
    try:
        custom = _NamedTool("read_file", description="host-owned")
        with caplog.at_level(logging.INFO):
            agent.add_tool(custom, replace=True)
        assert agent.tools.tools["read_file"] is custom
        infos = [r for r in caplog.records if r.levelno == logging.INFO
                 and "overrides an already-registered tool" in r.getMessage()]
        assert infos, "expected an INFO audit line for the override"
        warns = [r for r in caplog.records if r.levelno == logging.WARNING
                 and "already registered" in r.getMessage()]
        assert not warns, "override must not emit the accidental-collision warning"
    finally:
        agent.close()


def test_add_tool_replace_on_absent_name_is_plain_add(tmp_path, caplog):
    """``replace=True`` for a fresh name just adds — no override log, no warning."""
    agent = _make_agent(tmp_path)
    try:
        with caplog.at_level(logging.INFO):
            agent.add_tool(_NamedTool("brand_new"), replace=True)
        assert "brand_new" in agent.tools.tools
        assert not [r for r in caplog.records
                    if "overrides an already-registered tool" in r.getMessage()]
    finally:
        agent.close()


# ── add_tool: reserved-name + shape guards ────────────────────────────────


def test_add_tool_mcp_prefix_rejected(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        with pytest.raises(ValueError, match="reserved 'mcp_' prefix"):
            agent.add_tool(_NamedTool("mcp_alpha_ping"))
    finally:
        agent.close()


@pytest.mark.parametrize("replace", [False, True])
def test_add_tool_plan_name_rejected(tmp_path, replace):
    """Closes the ``add_tool(name='plan_save', replace=True)`` loophole."""
    agent = _make_agent(tmp_path)
    try:
        with pytest.raises(ValueError, match="reserved for plan mode"):
            agent.add_tool(_NamedTool("plan_save"), replace=replace)
    finally:
        agent.close()


def test_add_tool_empty_name_rejected(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        with pytest.raises(ValueError, match="non-empty string"):
            agent.add_tool(_NamedTool(""))
    finally:
        agent.close()


# ── remove_tool ───────────────────────────────────────────────────────────


def test_remove_tool_existing_returns_true(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        assert "read_file" in agent.tools.tools
        assert agent.remove_tool("read_file") is True
        assert "read_file" not in agent.tools.tools
    finally:
        agent.close()


def test_remove_tool_unknown_returns_false(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        assert agent.remove_tool("never_existed") is False
    finally:
        agent.close()


def test_remove_tool_mcp_prefix_rejected(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        with pytest.raises(ValueError, match="reserved 'mcp_' prefix"):
            agent.remove_tool("mcp_alpha_ping")
    finally:
        agent.close()


def test_remove_tool_plan_name_rejected(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        with pytest.raises(ValueError, match="reserved for plan mode"):
            agent.remove_tool("plan_finalize")
    finally:
        agent.close()


def test_remove_tool_non_string_raises_clean_error(tmp_path):
    """A non-string name fails with a clear ValueError, not an AttributeError."""
    agent = _make_agent(tmp_path)
    try:
        with pytest.raises(ValueError, match="must be a string"):
            agent.remove_tool(123)  # type: ignore[arg-type]
    finally:
        agent.close()


def test_add_then_remove_round_trip(tmp_path):
    agent = _make_agent(tmp_path)
    try:
        agent.add_tool(_NamedTool("ephemeral"))
        assert "ephemeral" in agent.tools.tools
        assert agent.remove_tool("ephemeral") is True
        assert "ephemeral" not in agent.tools.tools
    finally:
        agent.close()


# ── Construction-time tightening (the §5.2 spillover) ─────────────────────


def test_extra_tools_plan_name_rejected_at_construction(tmp_path):
    """Reserving plan names also closes 'extra_tools named plan_save'."""
    with pytest.raises(ValueError, match="reserved for plan mode"):
        _make_agent(tmp_path, extra_tools=[_NamedTool("plan_save")])


# ── Visibility: changes reach the per-call schema snapshot ────────────────


def test_add_remove_reflected_in_schema_snapshot(tmp_path):
    """``to_openai_format`` is what ``chat()``/``arun()`` snapshots per call."""
    agent = _make_agent(tmp_path)
    try:
        def names():
            return {t["function"]["name"] for t in agent.tools.to_openai_format()}

        assert "my_retrieval" not in names()
        agent.add_tool(_NamedTool("my_retrieval"))
        assert "my_retrieval" in names()
        agent.remove_tool("my_retrieval")
        assert "my_retrieval" not in names()
    finally:
        agent.close()
