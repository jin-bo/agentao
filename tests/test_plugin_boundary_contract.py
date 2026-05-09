"""Import-boundary contract for the agentao.plugins / agentao.embedding.plugins split.

Phase 5a/5b of `docs/design/core-boundary-review.md` separates runtime-path
plugin code (`agentao.plugins.*`) from the loader path
(`agentao.embedding.plugins.*`). The split is only valuable if it actually
keeps the loader, manifest parser, and YAML off the runtime hot import path.

This test asserts the invariant by importing `agentao.plugins` in a fresh
subprocess (so it is isolated from pytest's already-loaded modules) and
checking that the loader-side modules and YAML are not pulled in.
"""

from __future__ import annotations

import functools
import json
import subprocess
import sys
import textwrap


@functools.cache
def _run_probe() -> dict[str, object]:
    probe = textwrap.dedent(
        """
        import json
        import sys

        import agentao.plugins  # noqa: F401

        loader_prefix = "agentao.embedding.plugins"
        print(json.dumps({
            "embedding_plugin_modules": sorted(
                m for m in sys.modules
                if m == loader_prefix or m.startswith(loader_prefix + ".")
            ),
            "yaml": "yaml" in sys.modules,
            "models": "agentao.plugins.models" in sys.modules,
        }))
        """
    ).strip()

    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_import_agentao_plugins_does_not_load_embedding_loader() -> None:
    state = _run_probe()
    leaked = state["embedding_plugin_modules"]
    assert not leaked, (
        f"agentao.plugins should not transitively import the loader package, "
        f"but these modules were loaded: {leaked}"
    )


def test_import_agentao_plugins_does_not_load_yaml() -> None:
    state = _run_probe()
    assert state["yaml"] is False, (
        "agentao.plugins pulled in PyYAML. yaml is a loader-side dependency "
        "(manifest/SKILL.md frontmatter parsing) and must not be on the "
        "runtime import path."
    )


def test_import_agentao_plugins_loads_runtime_models() -> None:
    state = _run_probe()
    assert state["models"] is True, (
        "agentao.plugins.models should be loaded — it carries the runtime "
        "data classes (LoadedPlugin, PluginSkillEntry, StopHookResult, etc.)."
    )
