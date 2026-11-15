"""Host tool injection: ``extra_tools`` / ``disable_tools``.

Covers the four behavioral contracts from
``docs/design/host-tool-injection.md``:

1. ``extra_tools`` register last and override built-in / agent tools.
2. ``extra_tools`` names using the reserved ``mcp_`` prefix raise.
3. Unknown ``disable_tools`` names raise (typo guard).
4. ``WebSearchTool`` explicit constructor args take precedence over env.

Plus the supporting invariants: capability binding on injected tools,
``disable_tools`` only skipping built-ins, and the ``BUILTIN_TOOL_NAMES``
constant staying in sync with what ``register_builtin_tools`` produces.
"""

from __future__ import annotations

import pytest

from agentao.host import AsyncToolBase, RegistrableTool, Tool
from agentao.tooling import BUILTIN_TOOL_NAMES
from agentao.tools.web import WebSearchTool

from tests.support.tools import NamedTool, make_dummy_agent


# ── Contract 1: extra_tools register last & override ──────────────────────


def test_extra_tools_add_new_tool(tmp_path):
    agent = make_dummy_agent(tmp_path, extra_tools=[NamedTool("my_retrieval")])
    try:
        assert "my_retrieval" in agent.tools.tools
    finally:
        agent.close()


def test_extra_tools_override_builtin(tmp_path):
    """A same-named extra replaces the built-in (last-write-wins, silent).

    Targets ``read_file`` — an *unconditional* built-in — so this exercises
    the genuine override path even on a bare (no-``[web]``) install. Using a
    conditional tool like ``web_search`` would silently degrade to an "add"
    when ``bs4`` is absent.
    """
    custom = NamedTool("read_file", description="custom-override")
    agent = make_dummy_agent(tmp_path, extra_tools=[custom])
    try:
        assert agent.tools.tools["read_file"] is custom
        assert agent.tools.tools["read_file"].description == "custom-override"
    finally:
        agent.close()


def test_extra_tools_inherit_capability_binding(tmp_path):
    """Injected tools get the same wd/filesystem/shell binding as built-ins."""
    tool = NamedTool("my_retrieval")
    agent = make_dummy_agent(tmp_path, extra_tools=[tool])
    try:
        registered = agent.tools.tools["my_retrieval"]
        assert registered.working_directory == agent._working_directory
        # Bound to the agent's *own* capability objects (identity), not just
        # "some attribute exists" — a regression that bound the wrong object
        # would still leave the attrs present.
        assert registered.filesystem is agent.filesystem
        assert registered.shell is agent.shell
    finally:
        agent.close()


# ── Contract 2: mcp_ prefix is rejected ───────────────────────────────────


def test_extra_tools_mcp_prefix_rejected(tmp_path):
    with pytest.raises(ValueError, match="reserved 'mcp_' prefix"):
        make_dummy_agent(tmp_path, extra_tools=[NamedTool("mcp_alpha_ping")])


def test_extra_tools_duplicate_name_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate tool name"):
        make_dummy_agent(
            tmp_path,
            extra_tools=[NamedTool("dup"), NamedTool("dup")],
        )


# ── Contract 3: disable_tools typo guard ──────────────────────────────────


def test_disable_unknown_tool_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown built-in tool name"):
        make_dummy_agent(tmp_path, disable_tools={"web_serach"})


def test_disable_known_tool_skips_registration(tmp_path):
    agent = make_dummy_agent(tmp_path, disable_tools={"read_file"})
    try:
        assert "read_file" not in agent.tools.tools
        # Other built-ins remain.
        assert "write_file" in agent.tools.tools
    finally:
        agent.close()


def test_disable_tool_without_extra_dep_is_noop_not_error(tmp_path):
    """Disabling a name is legal even if its optional dep is absent.

    ``disable_tools`` validates against static registration eligibility,
    not live availability — so ``web_search`` is always a valid name even
    when ``[web]`` isn't installed; disabling it is simply a no-op then.
    """
    agent = make_dummy_agent(tmp_path, disable_tools={"web_search", "web_fetch"})
    try:
        assert "web_search" not in agent.tools.tools
        assert "web_fetch" not in agent.tools.tools
    finally:
        agent.close()


def test_disable_plus_extra_replacement(tmp_path):
    """disable built-in + add own same-named tool = host's tool wins.

    Uses ``read_file`` (unconditional) so the disable+replace path is
    exercised on any install, not only when ``[web]`` is present.
    """
    custom = NamedTool("read_file", description="host-owned")
    agent = make_dummy_agent(
        tmp_path, disable_tools={"read_file"}, extra_tools=[custom]
    )
    try:
        assert agent.tools.tools["read_file"] is custom
    finally:
        agent.close()


# ── Contract 4: WebSearchTool explicit args > env ─────────────────────────


def test_web_search_explicit_args_beat_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "from-env")
    tool = WebSearchTool(backend="tavily", tavily_api_key="explicit")
    assert tool._tavily_api_key == "explicit"
    assert tool._provider == "tavily"


def test_web_search_env_is_fallback(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)  # else deepseek precedes tavily
    monkeypatch.setenv("TAVILY_API_KEY", "from-env")
    tool = WebSearchTool()
    assert tool._tavily_api_key == "from-env"
    assert tool._provider == "tavily"


def test_web_search_no_key_has_no_backend(monkeypatch):
    for var in ("DEEPSEEK_API_KEY", "TAVILY_API_KEY", "JINA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    tool = WebSearchTool()
    assert tool._provider is None
    assert tool.backends == ()


def test_web_search_empty_api_key_overrides_env(monkeypatch):
    """Explicit tavily_api_key='' (force-unset) must beat the env var, not fall back."""
    for var in ("DEEPSEEK_API_KEY", "JINA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "from-env")
    tool = WebSearchTool(tavily_api_key="")
    assert tool._tavily_api_key == ""
    assert tool.backends == ()


def test_web_search_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    with pytest.raises(ValueError, match="unknown backend"):
        WebSearchTool(backend="brave")


def test_web_search_tavily_backend_without_key_raises(monkeypatch):
    """Forcing backend='tavily' with no key fails loudly, not as a 'Bearer None' 401."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="requires an API key"):
        WebSearchTool(backend="tavily")


# ── Validation edge cases ─────────────────────────────────────────────────


def test_extra_tools_empty_name_rejected(tmp_path):
    with pytest.raises(ValueError, match="non-empty string"):
        make_dummy_agent(tmp_path, extra_tools=[NamedTool("")])


def test_extra_tools_override_is_logged_and_unwarned(tmp_path, caplog):
    """An override emits an auditable INFO line but NOT the collision WARNING.

    ``register(replace=True)`` is silent by contract; the visibility instead
    comes from ``register_extra_tools`` logging at INFO. Pins both halves so
    a regression (spurious warning, or a silent clobber with no trace) fails.
    """
    import logging

    # ``read_file`` is unconditional — without it (e.g. overriding
    # web_search, which needs a search key) replace would be False and the
    # asserted INFO line would never fire.
    custom = NamedTool("read_file", description="host-owned")
    with caplog.at_level(logging.INFO):
        agent = make_dummy_agent(tmp_path, extra_tools=[custom])
    try:
        infos = [r for r in caplog.records if r.levelno == logging.INFO
                 and "overrides an already-registered tool" in r.getMessage()]
        assert infos, "expected an INFO audit line for the override"
        warns = [r for r in caplog.records if r.levelno == logging.WARNING
                 and "already registered" in r.getMessage()]
        assert not warns, "override must not emit the accidental-collision warning"
    finally:
        agent.close()


# ── Supporting invariant: BUILTIN_TOOL_NAMES stays in sync ────────────────


def test_builtin_tool_names_constant_in_sync(tmp_path, monkeypatch):
    """The static name set must equal what registration actually produces.

    Pins the hand-maintained constant to reality so a tool rename can't
    silently desync the ``disable_tools`` validator. bg_store is forced
    live so the conditional bg-agent tools register.

    ``web_fetch`` is conditional on the ``[web]`` extra, so instead of
    *skipping* without ``bs4`` (which would leave the constant un-guarded on
    a no-[web] CI lane and let a rename slip through), we subtract it from
    the expected set when ``bs4`` is absent — the invariant is still enforced
    in both environments. ``web_search`` needs a search key, so one is set.
    """
    import importlib.util
    from unittest.mock import Mock
    from agentao.tooling.registry import register_builtin_tools

    # Minimal stand-in agent exposing only what register_builtin_tools reads.
    fake = Mock()
    fake._working_directory = tmp_path
    fake.filesystem = None
    fake.shell = None
    fake._disable_tools = frozenset()
    fake.bg_store = Mock()  # truthy → bg tools register
    fake.skill_manager = Mock()
    fake.todo_tool = NamedTool("todo_write")
    fake.memory_tool = NamedTool("save_memory")
    fake.transport = Mock()

    from agentao.tools.base import ToolRegistry

    fake.tools = ToolRegistry()
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    register_builtin_tools(fake)

    expected = set(BUILTIN_TOOL_NAMES)
    if importlib.util.find_spec("bs4") is None:
        expected -= {"web_fetch"}  # not registered without [web]

    assert set(fake.tools.tools) == expected


def _no_search_keys(monkeypatch):
    for var in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "TAVILY_API_KEY", "JINA_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _omitted_notes(caplog):
    return [r for r in caplog.records if "web_search omitted" in r.getMessage()]


def test_web_search_is_omitted_without_a_search_key(tmp_path, monkeypatch, caplog):
    """Every backend needs a key; with none, the tool would only ever return a
    configuration error after a confirmation prompt, so it is not offered."""
    import logging

    _no_search_keys(monkeypatch)
    with caplog.at_level(logging.INFO):
        agent = make_dummy_agent(tmp_path)
    try:
        assert "web_search" not in agent.tools.tools
        assert len(_omitted_notes(caplog)) == 1
    finally:
        agent.close()


def test_web_search_registers_with_a_search_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    agent = make_dummy_agent(tmp_path)
    try:
        assert agent.tools.tools["web_search"].backends == ("tavily",)
    finally:
        agent.close()


def test_web_search_does_not_need_the_web_extra(tmp_path, monkeypatch, caplog):
    """No backend parses HTML; only web_fetch needs beautifulsoup4."""
    import importlib.util
    import logging

    real_find_spec = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name == "bs4":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    with caplog.at_level(logging.INFO):
        agent = make_dummy_agent(tmp_path)
    try:
        assert "web_search" in agent.tools.tools
        assert "web_fetch" not in agent.tools.tools
        notes = [r.getMessage() for r in caplog.records if "beautifulsoup4" in r.getMessage()]
        assert notes and all("web_search" not in n for n in notes)
    finally:
        agent.close()


def test_omitted_note_is_logged_once_per_process(tmp_path, monkeypatch, caplog):
    """A sub-agent spawn builds the registry again; the note must not repeat."""
    import logging

    _no_search_keys(monkeypatch)
    with caplog.at_level(logging.INFO):
        first = make_dummy_agent(tmp_path / "a")
        second = make_dummy_agent(tmp_path / "b")
    try:
        assert "web_search" not in second.tools.tools
        assert len(_omitted_notes(caplog)) == 1
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"disable_tools": {"web_search"}},
        {"enabled_tools": {"read_file"}},
        {"extra_tools": [NamedTool("web_search", description="host-owned")]},
    ],
    ids=["disabled", "not-enabled", "replaced"],
)
def test_host_owned_web_search_skips_the_default(tmp_path, monkeypatch, caplog, kwargs):
    """The default is not even built, so no "omitted" note claims otherwise."""
    import logging

    import agentao.tooling.registry as registry

    built = []

    class _Counting(registry.WebSearchTool):
        def __init__(self, *a, **k):
            built.append(1)
            super().__init__(*a, **k)

    monkeypatch.setattr(registry, "WebSearchTool", _Counting)
    _no_search_keys(monkeypatch)
    with caplog.at_level(logging.INFO):
        agent = make_dummy_agent(tmp_path, **kwargs)
    try:
        assert built == []
        assert _omitted_notes(caplog) == []
        if "extra_tools" in kwargs:
            assert agent.tools.tools["web_search"].description == "host-owned"
    finally:
        agent.close()


def test_registrable_tool_reexport_identity():
    """host re-export must BE the canonical types, not copies."""
    from agentao.tools import base

    assert Tool is base.Tool
    assert AsyncToolBase is base.AsyncToolBase
    assert RegistrableTool is base.RegistrableTool
