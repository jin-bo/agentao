"""P0.5 enforcement: ``import agentao`` and ``from agentao import Agentao``
must not pull in heavy CLI / parser / SDK deps at import time.

This is the *runtime* counterpart to ``test_no_cli_deps_in_core.py``. It
runs ``python -X importtime`` in a subprocess and asserts that none of
the deferred third-party packages appear anywhere in the import graph.
The subprocess starts from a clean interpreter so prior tests cannot
pollute state.

The two checks together catch both shapes of regression:

- Static: a developer adds ``from rich.console import Console`` to a
  non-CLI module — ``test_no_cli_deps_in_core`` flags it.
- Transitive: a non-CLI module imports a sibling whose closure pulls in
  one of the forbidden names — only ``test_import_cost`` catches that.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# Names that must not appear in the import graph of bare ``import agentao`` /
# ``from agentao import Agentao``. ``httpx`` is core-allowed, but we still
# expect its load to be deferred to first tool execution.
FORBIDDEN_TOP_LEVEL_PACKAGES = {
    "bs4",
    "jieba",
    "openai",
    "rich",
    "prompt_toolkit",
    "readchar",
    "filelock",
}


def _captured_top_level_packages(stmt: str) -> set[str]:
    """Run ``python -X importtime -c <stmt>`` and collect top-level packages."""
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", stmt],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"importtime probe failed: {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr (truncated):\n{proc.stderr[:2000]}\n"
    )
    pat = re.compile(r"^import time:\s+\d+\s+\|\s+\d+\s+\|\s+(\S+)$", re.MULTILINE)
    seen: set[str] = set()
    for match in pat.finditer(proc.stderr):
        seen.add(match.group(1).split(".")[0])
    return seen


def test_bare_import_agentao_is_lean() -> None:
    """``import agentao`` itself must not load anything heavy.

    Lazy ``__getattr__`` on the package keeps this one fast — broken only
    by introducing eager top-level imports in ``agentao/__init__.py``.
    """
    seen = _captured_top_level_packages("import agentao")
    leaked = seen & FORBIDDEN_TOP_LEVEL_PACKAGES
    assert not leaked, (
        "`import agentao` pulled in deferred packages: "
        f"{sorted(leaked)}. Check agentao/__init__.py for new eager imports."
    )


def test_from_agentao_import_agentao_is_lean() -> None:
    """``from agentao import Agentao`` must keep CLI/parser deps out.

    Embedded hosts pay for what they use — constructing an ``Agentao``
    against an injected ``LLMClient`` should not pay the CLI/web/i18n
    cost. This is the canonical assertion for the §9.5 budget.
    """
    seen = _captured_top_level_packages("from agentao import Agentao")
    leaked = seen & FORBIDDEN_TOP_LEVEL_PACKAGES
    assert not leaked, (
        "`from agentao import Agentao` pulled in deferred packages: "
        f"{sorted(leaked)}. See docs/design/path-a-roadmap.md §9.5 for "
        "the deferral contract; defer the offending import to a function "
        "body or expose it via PEP 562 ``__getattr__``."
    )
