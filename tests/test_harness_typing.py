"""Typing gate for the public ``agentao.harness`` surface (P0.4).

Two checks:

1. ``mypy --strict --package agentao.harness`` is clean — the package
   itself has no internal typing debt.
2. A throwaway downstream-shaped script that imports every name in
   ``agentao.harness.__all__`` and ``agentao.harness.protocols.__all__``
   passes ``mypy --strict``. This is the property hosts running
   ``mypy --strict`` against their own code path observe.

Skipped if ``mypy`` is not installed in the local env (kept in the dev
group; CI installs it via ``uv sync --group dev``).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
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
        [MYPY_BIN, "--strict", "--package", "agentao.harness"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "mypy --strict failed on agentao.harness:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}\n"
    )


@mypy_required
def test_mypy_strict_on_downstream_consumer(tmp_path: Path) -> None:
    """A host file that imports every public name passes ``--strict``.

    This catches regressions where ``agentao.harness`` is internally clean
    but exposes an ``Any`` (or untyped) into a downstream's strict context.
    """
    consumer = tmp_path / "host_app.py"
    consumer.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            from agentao.harness import (
                ActivePermissions,
                EventStream,
                HarnessEvent,
                PermissionDecisionEvent,
                RFC3339UTCString,
                StreamSubscribeError,
                SubagentLifecycleEvent,
                ToolLifecycleEvent,
                export_harness_acp_json_schema,
                export_harness_event_json_schema,
            )
            from agentao.harness.protocols import (
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


            def use_event(ev: HarnessEvent) -> str:
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
                ToolLifecycleEvent,
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
            _exporters = (export_harness_acp_json_schema, export_harness_event_json_schema)
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
            **__import__("os").environ,
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


def test_protocols_module_all_matches_imports() -> None:
    """``agentao.harness.protocols.__all__`` must list exactly what is imported.

    Drift here means a maintainer added a re-export but forgot ``__all__``
    (so ``from agentao.harness.protocols import *`` silently misses it).
    """
    from agentao.harness import protocols

    expected = {
        "BackgroundHandle",
        "FileEntry",
        "FileStat",
        "FileSystem",
        "MCPRegistry",
        "MemoryStore",
        "ShellExecutor",
        "ShellRequest",
        "ShellResult",
    }
    assert set(protocols.__all__) == expected
    for name in expected:
        assert getattr(protocols, name, None) is not None, (
            f"agentao.harness.protocols.{name} is in __all__ but not bound"
        )


def test_harness_all_matches_documented_set() -> None:
    """``agentao.harness.__all__`` must match the surface listed in docs/api/harness.md.

    Drift detection: a new public name added to ``__all__`` without a
    docs entry — or removed from docs without a deprecation cycle — fails
    here loudly.
    """
    from agentao import harness

    documented = {
        "ActivePermissions",
        "EventStream",
        "HarnessEvent",
        "PermissionDecisionEvent",
        "RFC3339UTCString",
        "StreamSubscribeError",
        "SubagentLifecycleEvent",
        "ToolLifecycleEvent",
        "export_harness_acp_json_schema",
        "export_harness_event_json_schema",
    }
    assert set(harness.__all__) == documented, (
        "agentao.harness.__all__ drifted from the documented public surface "
        "in docs/api/harness.md. Update both, in the same PR."
    )
