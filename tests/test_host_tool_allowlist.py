"""Host tool allowlist: ``enabled_tools``.

Covers the behavioral contract from
``docs/design/host-tool-allowlist.md``:

* ``enabled_tools=None`` is the status quo (allowlist disabled).
* A non-empty allowlist keeps only the named built-in / agent-path tools.
* The empty set is a legal "enabled" config (``is not None`` semantics):
  it prunes all built-in + agent tools, not a no-op.
* ``extra_tools`` are always kept, even when absent from the allowlist.
* Mutual exclusion with ``disable_tools``.
* Reserved names (``mcp_`` prefix, plan-only) raise at construction.
* Unknown names raise after registration (typo guard against the live
  registry).
"""

from __future__ import annotations

import pytest

from agentao import Agentao
from agentao.host import Tool
from agentao.tooling import BUILTIN_TOOL_NAMES


def _make_agent(tmp_path, **kwargs) -> Agentao:
    """Construct an Agentao with a dummy LLM config (no network at init)."""
    return Agentao(
        working_directory=tmp_path,
        api_key="x",
        base_url="http://localhost:0",
        model="dummy",
        **kwargs,
    )


class _NamedTool(Tool):
    """Minimal concrete Tool with a configurable name."""

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


# A small set of unconditional built-ins (no [web]/bg_store dependency).
_CORE = {"read_file", "write_file", "replace",
         "list_directory", "glob", "search_file_content", "run_shell_command"}


# ── Status quo: None disables the allowlist ───────────────────────────────


def test_enabled_none_is_status_quo(tmp_path):
    """``enabled_tools=None`` registers built-ins as before."""
    agent = _make_agent(tmp_path)
    try:
        # Sanity: unconditional built-ins all present without an allowlist.
        assert _CORE <= set(agent.tools.tools)
    finally:
        agent.close()


# ── Non-empty allowlist keeps only named built-ins ────────────────────────


def test_allowlist_keeps_only_named_builtins(tmp_path):
    keep = {"read_file", "run_shell_command"}
    agent = _make_agent(tmp_path, enabled_tools=keep)
    try:
        present = set(agent.tools.tools)
        assert keep <= present
        # Other unconditional built-ins are pruned.
        assert "write_file" not in present
        assert "search_file_content" not in present
    finally:
        agent.close()


def test_allowlist_prunes_optional_builtins(tmp_path):
    """A built-in left out of the allowlist is gone even if normally present."""
    agent = _make_agent(tmp_path, enabled_tools={"read_file"})
    try:
        present = set(agent.tools.tools)
        for name in ("todo_write", "save_memory", "activate_skill", "ask_user"):
            assert name not in present
    finally:
        agent.close()


# ── Empty set is "enabled", not a no-op (is-not-None semantics) ────────────


def test_empty_set_prunes_all_builtins(tmp_path):
    """``enabled_tools=set()`` removes every built-in / agent tool."""
    agent = _make_agent(tmp_path, enabled_tools=set())
    try:
        present = set(agent.tools.tools)
        # No built-in survives an empty allowlist.
        assert not (BUILTIN_TOOL_NAMES & present)
    finally:
        agent.close()


# ── extra_tools are always kept ───────────────────────────────────────────


def test_extra_tools_kept_despite_allowlist(tmp_path):
    """An injected extra survives even when not named in the allowlist."""
    agent = _make_agent(
        tmp_path,
        extra_tools=[_NamedTool("my_retrieval")],
        enabled_tools={"read_file"},
    )
    try:
        present = set(agent.tools.tools)
        assert "my_retrieval" in present   # kept (extra), not in allowlist
        assert "read_file" in present
        assert "write_file" not in present
    finally:
        agent.close()


def test_extra_name_in_allowlist_is_not_required_but_harmless(tmp_path):
    """Naming the extra in the allowlist too is allowed (no typo error)."""
    agent = _make_agent(
        tmp_path,
        extra_tools=[_NamedTool("my_retrieval")],
        enabled_tools={"read_file", "my_retrieval"},
    )
    try:
        assert "my_retrieval" in agent.tools.tools
    finally:
        agent.close()


# ── Mutual exclusion with disable_tools ───────────────────────────────────


def test_enabled_and_disable_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        _make_agent(
            tmp_path,
            enabled_tools={"read_file"},
            disable_tools={"web_search"},
        )


def test_empty_allowlist_still_excludes_disable(tmp_path):
    """Even an empty allowlist is 'enabled', so it clashes with disable_tools."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        _make_agent(tmp_path, enabled_tools=set(), disable_tools={"read_file"})


# ── Reserved names raise at construction ──────────────────────────────────


def test_allowlist_mcp_prefix_rejected(tmp_path):
    with pytest.raises(ValueError, match="reserved 'mcp_' prefix"):
        _make_agent(tmp_path, enabled_tools={"read_file", "mcp_alpha_ping"})


def test_allowlist_plan_only_rejected(tmp_path):
    with pytest.raises(ValueError, match="reserved for plan mode"):
        _make_agent(tmp_path, enabled_tools={"plan_save"})


# ── Unknown name typo guard (apply-time, against live registry) ────────────


def test_allowlist_unknown_name_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown tool name"):
        _make_agent(tmp_path, enabled_tools={"read_fil"})


def test_allowlist_known_builtin_without_extra_is_legal(tmp_path):
    """A valid built-in name is accepted even if its optional dep is absent.

    ``web_search`` only registers with the ``[web]`` extra, but it's a legal
    built-in name, so allowlisting it must not be flagged as a typo.
    """
    agent = _make_agent(tmp_path, enabled_tools={"read_file", "web_search"})
    try:
        assert "read_file" in agent.tools.tools
    finally:
        agent.close()
