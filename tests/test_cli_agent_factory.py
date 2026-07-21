"""Host injection seam for the interactive CLI (`agent_factory`).

Covers the contract in ``docs/design/cli-host-agent-factory.md``:

* §3   — the CLI calls the factory with exactly its three owned kwargs
* §3.1 — post-conditions on the returned runtime
* §3.2 — pre-bound CLI-owned kwargs and unreachable transports are rejected

These tests inject a factory rather than patching module globals. Note that
``patch('agentao.cli.app.Agentao')`` — used by several older CLI tests —
intercepts nothing, because construction goes through
``build_from_environment``, which imports ``Agentao`` locally.

Every test that reaches the real factory pins ``working_directory`` to a
``tmp_path``: ``build_from_environment`` otherwise resolves ``Path.cwd()``,
which under pytest is the repo root, and would create/mutate the developer's
real ``.agentao/memory.db``, background-task store, and ``agentao.log``.
"""

from functools import partial
from unittest.mock import patch

import pytest

from agentao.embedding import build_from_environment


def _make_cli(**kwargs):
    """Build an AgentaoCLI with plugin loading and dotenv reads suppressed."""
    with patch('agentao.cli.app.safe_load_dotenv'), \
            patch('agentao.cli.subcommands._load_and_register_plugins'):
        from agentao.cli import AgentaoCLI
        return AgentaoCLI(**kwargs)


def _isolated(tmp_path, **extra):
    """A factory that builds a real runtime rooted in ``tmp_path``."""
    return partial(build_from_environment, working_directory=tmp_path, **extra)


class _RecordingFactory:
    """Wraps the real factory and records the kwargs the CLI passed."""

    def __init__(self, working_directory, **extra):
        self.calls = []
        self._extra = {"working_directory": working_directory, **extra}

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return build_from_environment(**{**kwargs, **self._extra})


def _host_tool(tool_name):
    """A minimal host-supplied Tool instance."""
    from agentao.tools.base import Tool

    return type(
        "_HostTool", (Tool,),
        {
            "name": tool_name,
            "description": "injected by the host",
            "parameters": {"type": "object", "properties": {}},
            "requires_confirmation": False,
            "execute": lambda self, **kw: "ok",
        },
    )()


# ── §3: the CLI-owned kwarg set ────────────────────────────────────────────

def test_factory_receives_cli_owned_kwargs(tmp_path):
    """Transport is the CLI, plan_session is the CLI's own object."""
    factory = _RecordingFactory(tmp_path)
    cli = _make_cli(agent_factory=factory)

    assert len(factory.calls) == 1, "factory must be invoked exactly once"
    call = factory.calls[0]
    assert set(call) == {"transport", "max_context_tokens", "plan_session"}
    assert call["transport"] is cli
    assert call["plan_session"] is cli._plan_session
    assert isinstance(call["max_context_tokens"], int)


def test_context_limit_comes_from_environment(tmp_path, monkeypatch):
    """The forwarded limit tracks AGENTAO_CONTEXT_TOKENS."""
    monkeypatch.setenv("AGENTAO_CONTEXT_TOKENS", "12345")
    factory = _RecordingFactory(tmp_path)
    _make_cli(agent_factory=factory)
    assert factory.calls[0]["max_context_tokens"] == 12345


def test_extra_tools_visible_before_first_turn(tmp_path):
    """A host tool is registered by construction, not on the first turn."""
    cli = _make_cli(agent_factory=_isolated(tmp_path, extra_tools=[_host_tool("host_probe")]))

    assert "host_probe" in cli.agent.tools.tools


def test_default_path_unchanged(tmp_path, monkeypatch):
    """No factory means the current build_from_environment path."""
    monkeypatch.chdir(tmp_path)
    cli = _make_cli()
    assert cli.agent is not None
    assert cli.agent.transport is cli
    assert cli.agent.permission_engine is not None
    assert cli.agent._plan_session is cli._plan_session


def test_two_instances_do_not_share_host_tools(tmp_path):
    """Host tool sets are per-instance (plugins are out of scope)."""
    cli_a = _make_cli(agent_factory=_isolated(
        tmp_path / "a", extra_tools=[_host_tool("tool_a")]))
    cli_b = _make_cli(agent_factory=_isolated(
        tmp_path / "b", extra_tools=[_host_tool("tool_b")]))

    assert "tool_a" in cli_a.agent.tools.tools
    assert "tool_a" not in cli_b.agent.tools.tools
    assert "tool_b" in cli_b.agent.tools.tools
    assert "tool_b" not in cli_a.agent.tools.tools


def test_reused_tool_instance_is_rebound_by_the_second_cli(tmp_path):
    """Documents a real hazard: one Tool object across two CLIs is shared state.

    ``_bind_and_register`` assigns ``working_directory`` / ``filesystem`` /
    ``shell`` onto the tool *object*, so a host that reuses one factory (and
    therefore one ``extra_tools`` list) for two CLI sessions has the second
    construction silently rebind the first CLI's tool. Asserting the current
    behavior keeps the hazard visible rather than implying isolation that
    ``test_two_instances_do_not_share_host_tools`` — which builds distinct Tool
    objects — cannot detect.
    """
    shared = _host_tool("shared_probe")
    factory_a = _isolated(tmp_path / "a", extra_tools=[shared])
    factory_b = _isolated(tmp_path / "b", extra_tools=[shared])

    cli_a = _make_cli(agent_factory=factory_a)
    assert shared.working_directory == cli_a.agent.working_directory

    _make_cli(agent_factory=factory_b)
    # The *same* object now points at the second CLI's root — the first CLI's
    # registry still holds it, so its relative paths resolve to the wrong root.
    assert shared.working_directory != cli_a.agent.working_directory
    assert cli_a.agent.tools.tools["shared_probe"] is shared


def test_factory_exception_propagates_from_constructor():
    """Direct construction lets the host see its own failure."""
    def _boom(**kwargs):
        raise RuntimeError("host factory failed")

    with pytest.raises(RuntimeError, match="host factory failed"):
        _make_cli(agent_factory=_boom)


# ── §3.2: CLI-owned kwargs cannot be pre-bound ─────────────────────────────

@pytest.mark.parametrize("kwarg", ["transport", "max_context_tokens", "plan_session"])
def test_rejects_partial_prebinding_cli_owned_kwarg(kwarg):
    """A partial's keyword loses to the call-time keyword, silently.

    Nothing downstream can detect this — the runtime ends up correctly bound to
    the CLI and every post-condition passes — so it must be caught before the
    factory runs.
    """
    factory = partial(build_from_environment, **{kwarg: object()})

    with pytest.raises(TypeError, match="pre-binds CLI-owned keyword"):
        _make_cli(agent_factory=factory)


def test_allows_partial_with_non_cli_owned_kwargs(tmp_path):
    """The documented shape — partial carrying extra_tools — still works."""
    cli = _make_cli(agent_factory=_isolated(
        tmp_path, extra_tools=[_host_tool("ok_probe")]))
    assert "ok_probe" in cli.agent.tools.tools


# ── §3.1: post-conditions on the returned runtime ──────────────────────────

def test_rejects_none_return():
    with pytest.raises(TypeError, match="returned None"):
        _make_cli(agent_factory=lambda **kw: None)


def test_rejects_object_missing_required_attrs():
    """A duck-typed stand-in names every attribute it lacks, at once."""
    class _NotAnAgent:
        pass

    with pytest.raises(TypeError) as exc:
        _make_cli(agent_factory=lambda **kw: _NotAnAgent())

    message = str(exc.value)
    assert "_NotAnAgent" in message
    for attr in ("transport", "working_directory", "permission_engine",
                 "tools", "tool_runner", "memory_manager", "clear_history"):
        assert attr in message


def test_rejects_substituted_transport(tmp_path):
    """The silent-hang failure mode becomes a loud error."""
    class _OtherTransport:
        pass

    def _factory(**kwargs):
        kwargs["transport"] = _OtherTransport()
        return build_from_environment(working_directory=tmp_path, **kwargs)

    with pytest.raises(TypeError, match="transport does not reach"):
        _make_cli(agent_factory=_factory)


def test_accepts_wrapped_transport_reaching_the_cli(tmp_path):
    """Reachability, not identity — agentao's own ReplayAdapter wraps this way."""
    class _TeeAdapter:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

    def _factory(**kwargs):
        agent = build_from_environment(working_directory=tmp_path, **kwargs)
        adapter = _TeeAdapter(agent.transport)
        agent.transport = adapter
        agent.tool_runner._transport = adapter
        return agent

    cli = _make_cli(agent_factory=_factory)
    assert cli.agent.transport.inner is cli


def test_rejects_out_of_sync_tool_runner_transport(tmp_path):
    """agent.transport alone is not enough — ToolRunner holds its own copy."""
    class _Stale:
        pass

    def _factory(**kwargs):
        agent = build_from_environment(working_directory=tmp_path, **kwargs)
        agent.tool_runner._transport = _Stale()
        return agent

    with pytest.raises(TypeError, match=r"tool_runner\._transport does not reach"):
        _make_cli(agent_factory=_factory)


def test_rejects_none_permission_engine(tmp_path):
    """The sharpest post-condition: no bare NoneType AttributeError."""
    def _factory(**kwargs):
        agent = build_from_environment(working_directory=tmp_path, **kwargs)
        agent.permission_engine = None
        return agent

    with pytest.raises(TypeError, match="permission_engine=None"):
        _make_cli(agent_factory=_factory)


def test_rejects_substituted_plan_session(tmp_path):
    """Dropping plan_session silently breaks /plan, so it is enforced."""
    def _factory(*, transport, max_context_tokens, plan_session):
        # Drops plan_session — the runtime builds its own.
        return build_from_environment(
            working_directory=tmp_path,
            transport=transport,
            max_context_tokens=max_context_tokens,
        )

    with pytest.raises(TypeError, match="different PlanSession"):
        _make_cli(agent_factory=_factory)


# ── working_directory / settings interaction ───────────────────────────────

def test_saved_mode_is_read_from_the_factory_working_directory(tmp_path, monkeypatch):
    """A factory-supplied root must not be shadowed by the process cwd.

    The mode is read once before the factory runs (the root is not yet known)
    and re-read afterwards if the factory moved it. Without the re-read the
    project's saved posture is ignored at startup and then overwritten.
    """
    import json

    project = tmp_path / "project"
    (project / ".agentao").mkdir(parents=True)
    (project / ".agentao" / "settings.json").write_text(
        json.dumps({"mode": "read-only"}), encoding="utf-8")

    # Process cwd holds a *different*, more permissive posture.
    cwd = tmp_path / "cwd"
    (cwd / ".agentao").mkdir(parents=True)
    (cwd / ".agentao" / "settings.json").write_text(
        json.dumps({"mode": "full-access"}), encoding="utf-8")
    monkeypatch.chdir(cwd)

    from agentao.permissions import PermissionMode

    cli = _make_cli(agent_factory=_isolated(project))

    assert cli._project_root == project.resolve()
    assert cli.current_mode == PermissionMode.READ_ONLY
    assert cli.permission_engine.active_mode == PermissionMode.READ_ONLY


# ── main() forwarding ──────────────────────────────────────────────────────

def _run_main(**kwargs):
    """Invoke main() without leaking /dev/tty fds or atexit handlers.

    ``main()`` opens ``/dev/tty`` and registers a process-wide terminal-restore
    handler; neither is cleaned up on the success path, so an unguarded call
    from a test leaks an fd and leaves a handler that fires at pytest exit.
    """
    from agentao.cli import entrypoints

    seen = {}

    class _FakeCLI:
        def __init__(self, *, agent_factory=None):
            seen["factory"] = agent_factory

        def run(self):
            seen["ran"] = True

    with patch('agentao.cli.app.AgentaoCLI', _FakeCLI), \
            patch('agentao.cli.entrypoints.atexit.register'), \
            patch.dict('sys.modules', {'termios': None}):
        entrypoints.main(**kwargs)

    return seen


def test_main_forwards_factory_identity(tmp_path):
    """main() hands the identical callable to AgentaoCLI."""
    sentinel = _RecordingFactory(tmp_path)
    seen = _run_main(agent_factory=sentinel)

    assert seen["factory"] is sentinel
    assert seen.get("ran") is True


def test_main_defaults_to_none_factory():
    """Console startup is unchanged: no factory reaches AgentaoCLI."""
    seen = _run_main()

    assert seen["factory"] is None
    assert seen.get("ran") is True
