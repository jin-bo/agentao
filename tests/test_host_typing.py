"""Typing gate for the public ``agentao.host`` surface (P0.4).

Two checks:

1. ``mypy --strict --package agentao.host`` is clean — the package
   itself has no internal typing debt.
2. A throwaway downstream-shaped script that imports every name in
   ``agentao.host.__all__`` and ``agentao.host.protocols.__all__``
   passes ``mypy --strict``. This is the property hosts running
   ``mypy --strict`` against their own code path observe.

Skipped if ``mypy`` is not installed in the local env (kept in the dev
group; CI installs it via ``uv sync --group dev``).
"""

from __future__ import annotations

import functools
import inspect
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MYPY_BIN = shutil.which("mypy")
mypy_required = pytest.mark.skipif(
    MYPY_BIN is None,
    reason="mypy not installed; install with `uv sync --group dev`",
)


@mypy_required
def test_mypy_strict_on_harness_package() -> None:
    """The package itself must be clean under ``--strict``."""
    result = subprocess.run(
        [MYPY_BIN, "--strict", "--package", "agentao.host"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "mypy --strict failed on agentao.host:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}\n"
    )


@mypy_required
def test_mypy_strict_on_downstream_consumer(tmp_path: Path) -> None:
    """A host file that imports every public name passes ``--strict``.

    This catches regressions where ``agentao.host`` is internally clean
    but exposes an ``Any`` (or untyped) into a downstream's strict context.
    """
    consumer = tmp_path / "host_app.py"
    consumer.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            from agentao.host import (
                ActivePermissions,
                CancellationToken,
                EventStream,
                HostEvent,
                PermissionDecisionEvent,
                RFC3339UTCString,
                StreamSubscribeError,
                SubagentLifecycleEvent,
                TextDelta,
                ToolLifecycleEvent,
                TurnOutcome,
                export_host_acp_json_schema,
                export_host_event_json_schema,
            )
            from agentao.host.protocols import (
                BackgroundHandle,
                FileEntry,
                FileStat,
                FileSystem,
                MCPRegistry,
                MemoryStore,
                ShellExecutor,
                ShellRequest,
                ShellResult,
            )


            def use_event(ev: HostEvent) -> str:
                # Discriminated-union narrowing must work in strict mode.
                if isinstance(ev, ToolLifecycleEvent):
                    return ev.tool_name
                if isinstance(ev, SubagentLifecycleEvent):
                    return ev.child_task_id
                if isinstance(ev, PermissionDecisionEvent):
                    return ev.decision_id
                return "unknown"


            def use_perms(ap: ActivePermissions) -> int:
                return len(ap.loaded_sources)


            def stop(token: CancellationToken) -> bool:
                token.cancel("host-stop")
                return token.is_cancelled


            def render(item: TextDelta | TurnOutcome) -> str:
                # ``astream()`` items narrow with ``isinstance`` alone.
                if isinstance(item, TextDelta):
                    return item.text
                return item.text if item.is_answer else (item.error or "")


            def stream_handle(s: EventStream) -> None:
                # Confirm the public method signatures are typed.
                s.bind_loop  # noqa: B018 — attribute access checks typing
                s.publish    # noqa: B018
                s.subscribe  # noqa: B018


            # Re-export probes — names are imported above. Touching them keeps
            # static analyzers from pruning the imports.
            _names: tuple[type, ...] = (
                ActivePermissions,
                EventStream,
                PermissionDecisionEvent,
                StreamSubscribeError,
                SubagentLifecycleEvent,
                TextDelta,
                ToolLifecycleEvent,
                TurnOutcome,
                FileEntry,
                FileStat,
                BackgroundHandle,
                ShellRequest,
                ShellResult,
            )
            _protocols: tuple[type, ...] = (
                FileSystem,
                MCPRegistry,
                MemoryStore,
                ShellExecutor,
            )
            _exporters = (export_host_acp_json_schema, export_host_event_json_schema)
            _ts: type[str] = RFC3339UTCString
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [MYPY_BIN, "--strict", str(consumer)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            # Force resolution against the in-tree package.
            "MYPYPATH": str(REPO_ROOT),
            "PYTHONPATH": str(REPO_ROOT),
        },
    )
    assert result.returncode == 0, (
        "Downstream-strict mypy run failed against the public harness "
        "surface:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}\n"
    )


@mypy_required
def test_mypy_strict_on_host_use_of_agentao(tmp_path: Path) -> None:
    """A strict host sees the types of ``Agentao``'s host-facing methods.

    ``agentao.agent`` is not itself held to ``--strict``, so it is read
    with ``--follow-imports=silent``: its own errors are not reported, but
    the signatures a host calls still are. Every result below leaves
    through a typed ``return``, so a method that hands back ``Any`` fails
    under ``warn_return_any`` and an unannotated one fails under
    ``disallow_untyped_calls`` (F8 in
    ``docs/design/host-api-ergonomics-review.md``).
    """
    consumer = tmp_path / "host_agent.py"
    consumer.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            from contextlib import aclosing
            from pathlib import Path

            from agentao import Agentao
            from agentao.compaction.types import CompactionOutcome
            from agentao.host import (
                ActivePermissions,
                HostEvent,
                TextDelta,
                ToolLifecycleEvent,
                TurnOutcome,
            )


            def perms(agent: Agentao) -> ActivePermissions:
                return agent.active_permissions()


            async def first_tool(agent: Agentao) -> str:
                # ``aclosing`` needs ``aclose()``: closing releases the
                # stream's one-consumer slot when the host leaves early.
                async with aclosing(agent.events(session_id=None)) as events:
                    async for ev in events:
                        if isinstance(ev, ToolLifecycleEvent):
                            return ev.tool_name
                return ""


            async def streamed(agent: Agentao) -> TurnOutcome | None:
                async for item in agent.astream("hi"):
                    if not isinstance(item, TextDelta):
                        return item
                return None


            def on_event(ev: HostEvent) -> None:
                pass


            def on_event_flag(ev: HostEvent) -> bool:
                # An observer's return value is discarded, so any is accepted.
                return True


            def observe(agent: Agentao) -> bool:
                agent.add_host_event_observer(on_event)
                agent.add_event_observer(on_event_flag)
                return agent.remove_host_event_observer(on_event)


            def reset(agent: Agentao) -> TurnOutcome | None:
                agent.add_message("user", "hello")
                agent.clear_history()
                return agent.last_turn


            async def answer(agent: Agentao) -> str:
                return await agent.arun("hi")


            class EmitOnly:
                # The four methods the runtime calls, and no ``subscribe()``:
                # supported for every API but ``astream()``.
                def emit(self, event: object) -> None:
                    pass

                def confirm_tool(
                    self, tool_name: str, description: str, args: dict[str, object]
                ) -> bool:
                    return False

                def ask_user(
                    self,
                    question: str,
                    *,
                    header: str | None = None,
                    options: list[str] | None = None,
                    multiple: bool = False,
                    allow_custom: bool = True,
                ) -> str:
                    return ""

                def on_max_iterations(
                    self, count: int, messages: list[object]
                ) -> dict[str, object]:
                    return {"action": "stop"}


            class CountingObserver:
                def __call__(self, ev: HostEvent) -> None:
                    pass

                def count(self) -> int:
                    return 0


            def kept(agent: Agentao) -> int:
                # The observer comes back with its own type.
                return agent.add_host_event_observer(CountingObserver()).count()


            def compacted(agent: Agentao) -> CompactionOutcome:
                return agent.compact(reason="api_overflow")


            def build() -> Agentao:
                return Agentao(working_directory=Path("."), transport=EmitOnly())


            class MyAgent(Agentao):
                def tag(self) -> str:
                    return "mine"


            def scoped() -> str:
                # ``with`` hands back the subclass, not plain ``Agentao``.
                with MyAgent(working_directory=Path(".")) as agent:
                    return agent.tag()


            async def ascoped() -> str:
                async with MyAgent(working_directory=Path(".")) as agent:
                    return agent.tag()


            async def closed(agent: Agentao) -> None:
                await agent.aclose()
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [MYPY_BIN, "--strict", "--follow-imports=silent", str(consumer)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "MYPYPATH": str(REPO_ROOT),
            "PYTHONPATH": str(REPO_ROOT),
        },
    )
    assert result.returncode == 0, (
        "A strict host's use of Agentao's methods failed mypy:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}\n"
    )


def test_agentao_public_members_are_annotated() -> None:
    """Every public ``Agentao`` method and property carries annotations.

    The mypy check above covers the methods a host is shown; this one
    catches a new public member added without them, which would hand a
    strict host ``Any`` from its first call.
    """
    from agentao import Agentao

    # The suite's autouse fixture (tests/conftest.py) swaps ``__init__`` for
    # a shim; ``functools.wraps`` there is what lets ``inspect`` read the real
    # signature. Fail here, naming the fixture, rather than report agent.py.
    assert Agentao.__init__.__qualname__ == "Agentao.__init__", (
        "Agentao.__init__ is wrapped without functools.wraps "
        f"({Agentao.__init__.__qualname__}); fix the shim in tests/conftest.py"
    )

    # The dunders a host calls through syntax (``with`` / ``async with``).
    host_dunders = {"__init__", "__enter__", "__exit__", "__aenter__", "__aexit__"}
    missing = []
    for name, member in inspect.getmembers(Agentao):
        if name.startswith("_") and name not in host_dunders:
            continue
        if isinstance(member, property):
            # A setter or deleter is part of the public surface too.
            funcs = [f for f in (member.fget, member.fset, member.fdel) if f is not None]
        elif isinstance(member, functools.cached_property):
            funcs = [member.func]
        elif callable(member):
            funcs = [member]
        else:
            # A kind this loop cannot read (another descriptor, a class
            # constant) fails rather than passing unchecked.
            missing.append(f"{name}: unchecked {type(member).__name__}")
            continue
        for func in funcs:
            sig = inspect.signature(func)
            if sig.return_annotation is inspect.Signature.empty:
                missing.append(f"{name}: return")
            missing.extend(
                f"{name}: {param}"
                for param, p in sig.parameters.items()
                if param != "self" and p.annotation is inspect.Parameter.empty
            )
    assert missing == [], f"unannotated Agentao members: {missing}"


def test_manual_compaction_reason_is_a_subset() -> None:
    """``compact()``'s reasons stay spellings ``CompactionReason`` knows.

    ``agent.py`` is not checked with ``--strict`` and ``compact()`` does not
    validate ``reason`` at runtime, so a rename in ``CompactionReason`` would
    otherwise leave a stale spelling here that reaches the coordinator.
    """
    from typing import get_args

    from agentao.compaction.types import CompactionReason, ManualCompactionReason

    assert set(get_args(ManualCompactionReason)) <= set(get_args(CompactionReason))


def test_protocols_module_all_matches_imports() -> None:
    """``agentao.host.protocols.__all__`` must list exactly what is imported.

    Drift here means a maintainer added a re-export but forgot ``__all__``
    (so ``from agentao.host.protocols import *`` silently misses it).
    """
    from agentao.host import protocols

    expected = {
        "AbsPath",
        "BackgroundHandle",
        "Exhausted",
        "FileEntry",
        "FileStat",
        "FileSystem",
        "LaunchRequest",
        "LegacyLaunch",
        "MCPRegistry",
        "MemoryStore",
        "ShellBlock",
        "ShellDialect",
        "ShellExecutor",
        "ShellRequest",
        "ShellResult",
        "ShellSpec",
        "ShellSpecProvider",
        "WindowsLaunch",
    }
    assert set(protocols.__all__) == expected
    for name in expected:
        assert getattr(protocols, name, None) is not None, (
            f"agentao.host.protocols.{name} is in __all__ but not bound"
        )


def test_host_all_matches_documented_set() -> None:
    """``agentao.host.__all__`` must match the surface listed in docs/reference/host-api.md.

    Drift detection: a new public name added to ``__all__`` without a
    docs entry — or removed from docs without a deprecation cycle — fails
    here loudly.
    """
    from agentao import host

    documented = {
        "ActivePermissions",
        "AsyncToolBase",
        "CancellationToken",
        "EventStream",
        "HostEvent",
        "PermissionDecisionEvent",
        "RFC3339UTCString",
        "RegistrableTool",
        "StreamSubscribeError",
        "SubagentLifecycleEvent",
        "SubagentUsage",
        "TextDelta",
        "Tool",
        "ToolLifecycleEvent",
        "TurnOutcome",
        "export_host_acp_json_schema",
        "export_host_event_json_schema",
    }
    assert set(host.__all__) == documented, (
        "agentao.host.__all__ drifted from the documented public surface "
        "in docs/reference/host-api.md. Update both, in the same PR."
    )
