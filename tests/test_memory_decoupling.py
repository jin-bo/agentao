"""Regression tests: agentao.memory must be importable without dragging in
the LLM/tool/MCP stack.

These guard against accidental re-coupling — e.g. someone adding a top-level
eager import of `agentao.agent` (which transitively pulls openai, mcp, tools,
llm.client, …) to `agentao/__init__.py`. Using `agentao.memory` in isolated
test environments or as a library from outside the agent must stay cheap.

Each check runs in a fresh subprocess so it doesn't see modules that other
tests have already imported.
"""

import subprocess
import sys
import textwrap


HEAVY_PREFIXES = (
    "openai",
    "mcp.",
    "tiktoken",
    "agentao.agent",
    "agentao.llm",
    "agentao.tools",
    "agentao.mcp",
    "agentao.skills",
    "agentao.context_manager",
    "agentao.cli",
)


def _run(snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(snippet)],
        capture_output=True,
        text=True,
    )


def test_import_agentao_memory_does_not_leak_llm_stack():
    """`from agentao.memory import MemoryManager` must not pull in any
    LLM/MCP/tool modules."""
    result = _run(f"""
        import sys
        from agentao.memory import MemoryManager  # noqa: F401
        prefixes = {HEAVY_PREFIXES!r}
        leaked = sorted([m for m in sys.modules if any(m.startswith(p) or m == p.rstrip('.') for p in prefixes)])
        if leaked:
            print('LEAKED:', ','.join(leaked[:8]))
            raise SystemExit(1)
        raise SystemExit(0)
    """)
    assert result.returncode == 0, (
        f"agentao.memory leaked heavy modules:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_import_agentao_memory_submodules_stay_clean():
    """Each submodule imported standalone stays clean too."""
    for sub in (
        "agentao.memory.manager",
        "agentao.memory.storage",
        "agentao.memory.retriever",
        "agentao.memory.crystallizer",
        "agentao.memory.render",
        "agentao.memory.guards",
        "agentao.memory.models",
    ):
        result = _run(f"""
            import sys
            import {sub}  # noqa: F401
            prefixes = {HEAVY_PREFIXES!r}
            leaked = sorted([m for m in sys.modules if any(m.startswith(p) or m == p.rstrip('.') for p in prefixes)])
            if leaked:
                print('LEAKED:', ','.join(leaked[:8]))
                raise SystemExit(1)
            raise SystemExit(0)
        """)
        assert result.returncode == 0, (
            f"{sub} leaked heavy modules:\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


def test_agentao_top_level_lazy_attribute_resolves():
    """`import agentao; agentao.Agentao` still works (lazy via __getattr__)."""
    result = _run("""
        import agentao
        cls = agentao.Agentao
        assert cls.__name__ == 'Agentao'
        from agentao import SkillManager
        assert SkillManager.__name__ == 'SkillManager'
    """)
    assert result.returncode == 0, result.stderr


def test_agentao_top_level_unknown_attribute_raises():
    """Unknown attributes on the top-level package still raise AttributeError."""
    result = _run("""
        import agentao
        try:
            agentao.NonExistentSymbol
            raise SystemExit(2)
        except AttributeError:
            raise SystemExit(0)
    """)
    assert result.returncode == 0, result.stderr


def test_bare_import_agentao_does_not_eagerly_load_llm_stack():
    """`import agentao` (without touching .Agentao) should not load the LLM stack.

    This is the core of the decoupling: lazy attribute access means the cost
    is paid only when someone actually uses the agent class.
    """
    result = _run(f"""
        import sys
        import agentao  # noqa: F401
        prefixes = {HEAVY_PREFIXES!r}
        leaked = sorted([m for m in sys.modules if any(m.startswith(p) or m == p.rstrip('.') for p in prefixes)])
        if leaked:
            print('LEAKED:', ','.join(leaked[:8]))
            raise SystemExit(1)
        raise SystemExit(0)
    """)
    assert result.returncode == 0, (
        f"`import agentao` leaked heavy modules:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
