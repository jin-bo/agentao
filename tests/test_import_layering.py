"""Structural enforcement: first-party import *direction* inside ``agentao/``.

This is the missing third leg of the boundary suite. The two existing
guards both police import **weight**, and both are about *third-party*
names:

- ``test_no_cli_deps_in_core.py`` — static: no top-level ``rich`` /
  ``prompt_toolkit`` / ``readchar`` / ``filelock`` outside ``agentao/cli/``.
- ``test_import_cost.py`` — runtime: none of ``bs4`` / ``jieba`` /
  ``openai`` / … appear in the import graph of bare ``import agentao``.

Neither says anything about ``agentao`` importing ``agentao``. The proof
that the gap was real: ``agentao/tools/agents.py`` imports
``agentao.cli.help_text`` — a core tool reaching into the reference host —
and both guards stayed green, because the name is first-party and the
import sits in a function body. ``test_plugin_boundary_contract.py`` is
first-party but checks one point invariant (``agentao.plugins`` must not
drag in the embedding loader), not a direction rule.

Five rules, each independently justified rather than derived from an
invented global tier lattice:

1. :func:`test_no_cli_imports_outside_cli` — the CLI is a *host*. Core
   must stay shippable without it.
2. :func:`test_acp_stays_behind_its_boundary` — ACP is a surface, not a
   dependency of the runtime.
3. :func:`test_leaf_utilities_do_not_import_upward` — the shared leaves
   (``paths``, ``security``, ``cancellation``, …) are imported by most of
   the tree; an upward edge from one of them is how a cycle starts.
4. :func:`test_no_cross_package_eager_import_cycles` — self-maintaining,
   no allowlist to go stale.
5. :func:`test_import_agentao_host_stays_off_the_runtime_stack` — the
   stability boundary must remain importable without the LLM stack, the
   property ``redact.py``'s docstring already claims in prose.

**Eager vs deferred.** Rules 3 and 4 apply to *eager* imports only —
module top level and class bodies. A first-party import inside a function
body or an ``if TYPE_CHECKING:`` block is the sanctioned cycle-breaker in
this tree (15 of the 16 cross-package back-edges use it), so forbidding it
would fail the whole package on day one. Rules 1 and 2 apply at **any**
timing: importing the host from core is a layering claim about ownership,
and deferring it does not make core shippable without ``cli``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "agentao"


# --------------------------------------------------------------------------
# AST plumbing
# --------------------------------------------------------------------------


def _iter_source_files() -> Iterator[Path]:
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _module_name(path: Path) -> str:
    """``agentao/a/b.py`` -> ``agentao.a.b``; ``a/b/__init__.py`` -> ``agentao.a.b``."""
    parts = list(path.relative_to(PACKAGE_ROOT).parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(["agentao", *parts])


def _package_name(path: Path) -> str:
    """The package a relative import in ``path`` resolves against."""
    module = _module_name(path)
    if path.name == "__init__.py":
        return module
    return module.rsplit(".", 1)[0]


def _subpackage(module: str) -> str:
    """``agentao.tools.web`` -> ``tools``; ``agentao.paths`` -> ``<root>``."""
    parts = module.split(".")
    if len(parts) < 2:
        return "<root>"
    return parts[1] if len(parts) > 2 or _is_package(parts[1]) else "<root>"


def _is_package(name: str) -> bool:
    return (PACKAGE_ROOT / name).is_dir()


class _ImportCollector(ast.NodeVisitor):
    """Collect ``(node, is_eager)`` for every import statement.

    ``is_eager`` is False inside a function/method body or an
    ``if TYPE_CHECKING:`` block. Class bodies stay eager — they execute at
    import time.
    """

    def __init__(self) -> None:
        self._eager = True
        self.found: List[Tuple[ast.stmt, bool]] = []

    def _deferred(self, node: ast.AST) -> None:
        outer, self._eager = self._eager, False
        self.generic_visit(node)
        self._eager = outer

    visit_FunctionDef = _deferred
    visit_AsyncFunctionDef = _deferred
    visit_Lambda = _deferred

    def visit_If(self, node: ast.If) -> None:
        if "TYPE_CHECKING" in ast.dump(node.test):
            self._deferred(node)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.found.append((node, self._eager))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.found.append((node, self._eager))


def _resolve_targets(path: Path, node: ast.stmt) -> List[str]:
    """Absolute dotted first-party targets of one import statement.

    ``from .x import Y`` yields both ``pkg.x`` and ``pkg.x.Y`` — the caller
    narrows to whichever actually exists on disk. Non-``agentao`` imports
    yield nothing.
    """
    targets: List[str] = []
    if isinstance(node, ast.Import):
        targets += [a.name for a in node.names if a.name.split(".")[0] == "agentao"]
    elif isinstance(node, ast.ImportFrom):
        if node.level == 0:
            if node.module and node.module.split(".")[0] == "agentao":
                targets.append(node.module)
        else:
            base = _package_name(path).split(".")
            climb = node.level - 1
            if climb:
                base = base[: len(base) - climb] or ["agentao"]
            root = ".".join(base)
            head = f"{root}.{node.module}" if node.module else root
            targets.append(head)
            targets += [f"{head}.{a.name}" for a in node.names]
    return targets


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
        return None


def _imports_of(path: Path) -> List[Tuple[str, int, bool]]:
    """``[(target_module, lineno, is_eager), ...]`` for one file."""
    tree = _parse(path)
    if tree is None:
        return []
    collector = _ImportCollector()
    collector.visit(tree)
    out: List[Tuple[str, int, bool]] = []
    for node, eager in collector.found:
        for target in _resolve_targets(path, node):
            out.append((target, node.lineno, eager))
    return out


def _rel(path: Path) -> str:
    """Repo-relative, always with ``/``.

    The exemption tables are written with forward slashes, so on Windows a native
    ``agentao\\__main__.py`` matched none of them and every exemption silently stopped
    applying — the rule reported violations it had been told about years ago.
    """
    return path.relative_to(REPO_ROOT).as_posix()


def _under(path: Path, *names: str) -> bool:
    parts = path.relative_to(PACKAGE_ROOT).parts
    return bool(parts) and parts[0] in names


# --------------------------------------------------------------------------
# Rule 1 — nothing outside agentao/cli/ may import agentao.cli
# --------------------------------------------------------------------------

# Path -> why this file is allowed to reach into the reference host.
# Every entry is asserted still-live by ``test_cli_exemptions_are_not_stale``:
# an exemption list nobody re-checks silently certifies the defect it was
# written to contain (see docs/design/refactor-audit-2026-07.md, the
# ``session_ended`` exemption that certified its own bug).
CLI_IMPORT_EXEMPTIONS: Dict[str, str] = {
    "agentao/__main__.py": (
        "The console entrypoint. Launching the CLI is the whole job of this "
        "module — it is on the host side of the boundary, not in core."
    ),
    "agentao/tools/agents.py": (
        "KNOWN INVERSION, deliberately left visible rather than silently "
        "allowed. ``CLIHelpAgentTool.execute`` returns ``cli.help_text."
        "CLI_HELP_TEXT``, so a core tool carries host product knowledge. It "
        "costs nothing at import (function-local) and the tool is not "
        "registered by default (``tooling/registry.py`` scopes ``cli_help`` "
        "out), but it does mean ``agentao.tools`` cannot ship without "
        "``agentao.cli``. Resolving it means moving the tool to the host via "
        "the injection contract in docs/design/host-tool-injection.md; until "
        "that call is made, this entry is the record."
    ),
}


def test_no_cli_imports_outside_cli() -> None:
    """``agentao.cli`` is a host. Core must not depend on it, ever — at any
    import timing, eager or deferred."""
    offenders: List[str] = []
    for path in _iter_source_files():
        if _under(path, "cli"):
            continue
        rel = _rel(path)
        if rel in CLI_IMPORT_EXEMPTIONS:
            continue
        for target, lineno, _eager in _imports_of(path):
            if target == "agentao.cli" or target.startswith("agentao.cli."):
                offenders.append(f"{rel}:{lineno}: imports `{target}`")

    assert not offenders, (
        "Import-direction violation — these non-CLI modules import the "
        "reference host. Core has to stay shippable without `agentao.cli`.\n"
        "Fix by moving the shared value down into core (or the whole tool up "
        "into the host, see docs/design/host-tool-injection.md). If the "
        "dependency is genuinely correct, add the file to "
        "CLI_IMPORT_EXEMPTIONS *with a reason*.\n  " + "\n  ".join(offenders)
    )


def test_cli_exemptions_are_not_stale() -> None:
    """Every exemption must still describe a real import.

    A stale entry is worse than no list: it pre-authorizes an inversion that
    someone already removed.
    """
    stale: List[str] = []
    for rel in CLI_IMPORT_EXEMPTIONS:
        path = REPO_ROOT / rel
        if not path.exists():
            stale.append(f"{rel}: file no longer exists")
            continue
        has_cli_import = any(
            target == "agentao.cli" or target.startswith("agentao.cli.")
            for target, _lineno, _eager in _imports_of(path)
        )
        if not has_cli_import:
            stale.append(f"{rel}: no longer imports agentao.cli — drop the entry")

    assert not stale, (
        "CLI_IMPORT_EXEMPTIONS has entries that no longer apply:\n  "
        + "\n  ".join(stale)
    )


# --------------------------------------------------------------------------
# Rule 2 — ACP is a surface, not a runtime dependency
# --------------------------------------------------------------------------

# Files outside agentao/acp{,_client}/ permitted to import them. ``cli`` is a
# host and drives both; ``host/schema.py`` re-exports the ACP JSON-Schema
# snapshot and defers the import to call time (which is what keeps
# ``import agentao.host`` off the ACP stack — see Rule 5).
ACP_IMPORT_ALLOWED_PREFIXES: Tuple[str, ...] = (
    "agentao/cli/",
    "agentao/host/schema.py",
)


def test_acp_stays_behind_its_boundary() -> None:
    """``agentao.acp`` / ``agentao.acp_client`` are entry surfaces.

    Core importing a *surface* inverts the relationship — the surface
    adapts to core, not the other way round. This is the invariant
    CLAUDE.md records as "core no longer eagerly imports ``agentao.acp``",
    generalized to both packages and to deferred imports.
    """
    offenders: List[str] = []
    for path in _iter_source_files():
        if _under(path, "acp", "acp_client"):
            continue
        rel = _rel(path)
        if rel.startswith(ACP_IMPORT_ALLOWED_PREFIXES):
            continue
        for target, lineno, _eager in _imports_of(path):
            head = target.split(".")[:2]
            if head in (["agentao", "acp"], ["agentao", "acp_client"]):
                offenders.append(f"{rel}:{lineno}: imports `{target}`")

    assert not offenders, (
        "ACP boundary violation — these modules reach into an entry surface:"
        "\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------
# Rule 3 — the shared leaves stay leaves
# --------------------------------------------------------------------------

# Modules/packages with high fan-in and (today) no eager first-party imports
# outside this set. They are the bottom of the graph; an upward edge from one
# of them puts a cycle within reach of most of the tree at once.
LEAF_MODULES: Set[str] = {
    "_env",
    "cancellation",
    "frontmatter",
    "logging_utils",
    "media_limits",
    "paths",
    "redact",
    "sandbox",
    "security",
}


def test_leaf_utilities_do_not_import_upward() -> None:
    """A leaf may eagerly import stdlib, itself, and other leaves. Nothing else."""
    offenders: List[str] = []
    for path in _iter_source_files():
        own = _subpackage(_module_name(path))
        stem = path.stem if own == "<root>" else own
        if stem not in LEAF_MODULES:
            continue
        for target, lineno, eager in _imports_of(path):
            if not eager:
                continue
            head = _subpackage(target)
            name = target.split(".")[1] if head == "<root>" else head
            if name == stem or name in LEAF_MODULES:
                continue
            offenders.append(f"{_rel(path)}:{lineno}: leaf imports `{target}`")

    assert not offenders, (
        "Leaf utility imports upward — these modules sit at the bottom of the "
        "graph and most of agentao depends on them, so an upward eager edge "
        "makes a cycle reachable from everywhere.\n"
        "Defer the import into the function body, or move the shared value "
        "down.\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------
# Rule 4 — no eager import cycle spanning two subpackages
# --------------------------------------------------------------------------


def _eager_module_graph() -> Dict[str, Set[str]]:
    """Module-level eager first-party edges, resolved to real modules on disk."""
    known = {_module_name(p): p for p in _iter_source_files()}
    graph: Dict[str, Set[str]] = {name: set() for name in known}
    for name, path in known.items():
        for target, _lineno, eager in _imports_of(path):
            if not eager:
                continue
            # ``from .x import Y`` may name a submodule or an attribute;
            # walk up to the longest prefix that is a real module.
            while target and target not in known:
                target = target.rsplit(".", 1)[0] if "." in target else ""
            if target and target != name:
                graph[name].add(target)
    return graph


def _strongly_connected(graph: Dict[str, Set[str]]) -> List[List[str]]:
    """Tarjan's SCC, iterative — the graph is ~270 nodes but recursion depth
    is bounded by path length, and CI should not depend on the limit."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    counter = 0
    components: List[List[str]] = []

    for root in sorted(graph):
        if root in index:
            continue
        work: List[Tuple[str, Iterator[str]]] = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: List[str] = []
                while True:
                    popped = stack.pop()
                    on_stack.discard(popped)
                    component.append(popped)
                    if popped == node:
                        break
                if len(component) > 1:
                    components.append(sorted(component))
    return components


def test_no_cross_package_eager_import_cycles() -> None:
    """Eager import cycles must stay inside one subpackage.

    A package ``__init__`` importing its own submodules while those
    submodules import siblings through the package is ordinary Python and
    the only shape present today. A cycle spanning two subpackages means
    neither can be imported, tested, or reasoned about alone — and it is
    load-bearing here, because embedders import subpackages directly.

    Self-maintaining by construction: there is no allowlist to go stale.
    """
    offenders: List[str] = []
    for component in _strongly_connected(_eager_module_graph()):
        packages = {_subpackage(m) for m in component}
        if len(packages) > 1:
            offenders.append(
                f"{' <-> '.join(component)}  (spans {', '.join(sorted(packages))})"
            )

    assert not offenders, (
        "Eager import cycle spanning subpackages — break it by deferring one "
        "edge into a function body or an `if TYPE_CHECKING:` block.\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------
# Rule 5 — the stability boundary stays importable on its own
# --------------------------------------------------------------------------

# ``agentao.host.projection`` eagerly imports ``agentao.runtime.identity``,
# which is fine *only* because ``host/__init__.py`` does not re-export it.
# That is an invisible load-bearing detail; this test makes it visible.
HOST_FORBIDDEN_IN_CLOSURE: Tuple[str, ...] = (
    "agentao.runtime",
    "agentao.llm",
    "agentao.cli",
    "agentao.acp",
    "agentao.acp_client",
    "agentao.mcp",
    "agentao.agent",
    "openai",
    "httpx",
)


def test_import_agentao_host_stays_off_the_runtime_stack() -> None:
    """``import agentao.host`` must not drag in the runtime or the LLM stack.

    ``agentao.host`` is the compatibility boundary: a host embedding
    Agentao reads these models to render events and may want them in a
    process that never constructs an agent (``redact.py``'s docstring makes
    the same claim for ``mask_secret``). The guard is a subprocess probe
    because the static rule cannot see transitive closure — ``host``
    legitimately imports ``runtime`` in a module ``__init__`` does not load.
    """
    probe = (
        "import sys, json; import agentao.host; "
        "print(json.dumps(sorted(sys.modules)))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"probe failed ({proc.returncode})\nstderr:\n{proc.stderr[:2000]}"
    )

    import json

    loaded = set(json.loads(proc.stdout))
    leaked = sorted(
        name
        for name in HOST_FORBIDDEN_IN_CLOSURE
        if name in loaded or any(m.startswith(name + ".") for m in loaded)
    )

    assert not leaked, (
        "`import agentao.host` now pulls in "
        + ", ".join(leaked)
        + ".\nThe host contract must stay importable without the runtime/LLM "
        "stack. Defer the offending import into the function that needs it "
        "(agentao/host/schema.py is the pattern)."
    )
