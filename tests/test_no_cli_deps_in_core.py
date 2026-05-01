"""P0.5 enforcement: CLI-only deps must not appear in non-CLI core modules.

The lazy-import contract is that ``rich`` / ``prompt_toolkit`` / ``readchar``
/ ``filelock`` only load when CLI / persistence paths actually run.
``agentao/cli/*`` is allowed to import them eagerly (it is the CLI by
definition); every other module under ``agentao/`` must defer or stay
pure-stdlib.

This test walks the package source with ``ast`` and fails on any
top-level import of those names from a non-CLI file. It does not run the
modules — it is a static check, intentionally cheap. ``filelock`` is
slightly more permissive: tests/registry/recovery code paths may need it,
but ``agentao/skills/registry.py`` defers it inside ``save()`` rather than
at module top.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "agentao"
CLI_DIR = PACKAGE_ROOT / "cli"

# Names whose top-level import in a non-CLI file would defeat the P0.5 budget.
# `httpx` is core-allowed (in the §9.9 dep split) but is also lazy in
# ``tools/web.py``; we still permit any non-CLI top-level ``httpx`` import
# because some wrappers legitimately need it.
FORBIDDEN_TOP_LEVEL = {
    "rich",
    "prompt_toolkit",
    "readchar",
    "filelock",
}


def _iter_python_files() -> list[Path]:
    return [
        p
        for p in PACKAGE_ROOT.rglob("*.py")
        if CLI_DIR not in p.parents and p != CLI_DIR / "__init__.py"
    ]


def _top_level_imports(source: str) -> list[tuple[str, int]]:
    """Return ``[(top_level_pkg, lineno), ...]`` for module-top imports only.

    ``import x`` inside a function body / class body / ``if TYPE_CHECKING``
    is excluded — only top-level statements count.
    """
    tree = ast.parse(source)
    out: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.level == 0:
                out.append((node.module.split(".")[0], node.lineno))
        elif isinstance(node, ast.If):
            # ``if TYPE_CHECKING:`` blocks are static-only — ignore.
            test = node.test
            if (
                isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
            ) or (
                isinstance(test, ast.Attribute)
                and test.attr == "TYPE_CHECKING"
            ):
                continue
            # Other ``if`` blocks at module top: be conservative and
            # walk their bodies for imports too.
            for inner in ast.walk(node):
                if isinstance(inner, ast.Import):
                    for alias in inner.names:
                        out.append((alias.name.split(".")[0], inner.lineno))
                elif isinstance(inner, ast.ImportFrom):
                    if inner.module is not None and inner.level == 0:
                        out.append((inner.module.split(".")[0], inner.lineno))
    return out


def test_no_cli_deps_in_core_modules() -> None:
    """Walk every non-CLI .py and assert no forbidden top-level import."""
    offenders: list[str] = []
    for path in _iter_python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pkg, lineno in _top_level_imports(source):
            if pkg in FORBIDDEN_TOP_LEVEL:
                rel = path.relative_to(PACKAGE_ROOT.parent)
                offenders.append(f"{rel}:{lineno}: top-level `{pkg}`")
    assert not offenders, (
        "P0.5 lazy-import contract broken — these non-CLI modules import a "
        "CLI-only dep at top level. Defer to a function body, or move the "
        "module under agentao/cli/.\n  " + "\n  ".join(offenders)
    )
