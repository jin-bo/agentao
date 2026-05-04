"""Skip-list behavior for ``search_file_content``.

The Python fallback path in :class:`SearchTextTool` previously walked
into ``node_modules/``, ``.git/``, language caches, etc. and could lock
the agent up on large trees. The skip-list defined in
:mod:`agentao.tools.search` filters those before any stat/open, with an
escape hatch when the caller explicitly references one of the skipped
names in ``directory`` or ``file_pattern``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentao.tools.search import (
    DEFAULT_SKIP_DIRS,
    SearchTextTool,
    _any_part_in_skip,
    _effective_skip_dirs,
    _path_in_skip_dirs,
)


def _make_tree(root: Path) -> None:
    """Lay out a tree that exercises both kept and skipped dirs."""
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("needle in source\n", encoding="utf-8")

    (root / "node_modules" / "lodash").mkdir(parents=True)
    (root / "node_modules" / "lodash" / "index.js").write_text("needle inside vendored dep\n", encoding="utf-8")

    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("needle inside git internals\n", encoding="utf-8")

    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "main.cpython-313.pyc").write_text("needle inside cache\n", encoding="utf-8")

    (root / "build").mkdir()
    (root / "build" / "out.txt").write_text("needle inside build output\n", encoding="utf-8")


def _bypass_git_grep(tool: SearchTextTool) -> None:
    """Force the Python fallback path. The skip-list is what we care about,
    not whether git happens to be installed in the test environment."""
    tool._is_git_repo = lambda d: False  # type: ignore[assignment]


def _run(tool: SearchTextTool, **kwargs) -> str:
    return tool.execute(pattern="needle", **kwargs)


def test_skip_dirs_are_excluded_by_default(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    tool = SearchTextTool()
    tool.working_directory = tmp_path
    _bypass_git_grep(tool)

    out = _run(tool, directory=str(tmp_path))

    assert "src/main.py" in out
    assert "node_modules" not in out
    assert ".git/config" not in out
    assert "__pycache__" not in out
    assert "build/out.txt" not in out


def test_explicit_directory_opts_back_in(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    tool = SearchTextTool()
    tool.working_directory = tmp_path
    _bypass_git_grep(tool)

    out = _run(tool, directory=str(tmp_path / "node_modules"))

    assert "lodash/index.js" in out


def test_explicit_file_pattern_opts_back_in(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    tool = SearchTextTool()
    tool.working_directory = tmp_path
    _bypass_git_grep(tool)

    # The pattern must include a skip-dir component literally — that is
    # the opt-in signal. Depth is then handled by the existing ``**/``
    # rewrite + ``Path.rglob`` semantics in ``SearchTextTool.execute``.
    out = _run(
        tool,
        directory=str(tmp_path),
        file_pattern="node_modules/lodash/**/*.js",
    )

    assert "node_modules/lodash/index.js" in out
    # Other skipped dirs remain skipped — we only opted in to node_modules.
    assert ".git/config" not in out


def test_effective_skip_dirs_strips_referenced_names() -> None:
    skip = _effective_skip_dirs("node_modules/lodash/**/*.js", ".")
    assert "node_modules" not in skip
    assert ".git" in skip  # untouched

    skip = _effective_skip_dirs("**/*", "build/intermediate")
    assert "build" not in skip
    assert "node_modules" in skip


def test_effective_skip_dirs_default_when_nothing_referenced() -> None:
    assert _effective_skip_dirs("**/*", ".") is DEFAULT_SKIP_DIRS


def test_path_in_skip_dirs_relative_components_only(tmp_path: Path) -> None:
    base = tmp_path / "project"
    base.mkdir()
    inside = base / "node_modules" / "lodash" / "index.js"
    outside_skip = base / "src" / "main.py"

    assert _path_in_skip_dirs(inside, base, DEFAULT_SKIP_DIRS) is True
    assert _path_in_skip_dirs(outside_skip, base, DEFAULT_SKIP_DIRS) is False

    # If the base itself sits inside a skip-named directory, that prefix
    # is invisible to the relative-components check — caller already
    # opted in by pointing `directory` there.
    deep_base = tmp_path / "node_modules" / "consumer"
    deep_base.mkdir(parents=True)
    deep_file = deep_base / "src" / "main.js"
    deep_file.parent.mkdir(parents=True)
    deep_file.write_text("hi", encoding="utf-8")
    assert _path_in_skip_dirs(deep_file, deep_base, DEFAULT_SKIP_DIRS) is False


def test_path_in_skip_dirs_empty_skip_short_circuits(tmp_path: Path) -> None:
    inside = tmp_path / "node_modules" / "x.js"
    inside.parent.mkdir()
    inside.write_text("hi", encoding="utf-8")
    assert _path_in_skip_dirs(inside, tmp_path, frozenset()) is False


@pytest.mark.parametrize(
    "path_str,expected",
    [
        ("src/main.py", False),
        ("node_modules/lodash/index.js", True),
        (".git/config", True),
        ("subdir/__pycache__/m.pyc", True),
        ("packages/build/x.txt", True),
        # Backslash-separated (Windows-style) paths normalize identically.
        (r"node_modules\lodash\index.js", True),
    ],
)
def test_any_part_in_skip(path_str: str, expected: bool) -> None:
    assert _any_part_in_skip(path_str, DEFAULT_SKIP_DIRS) is expected


def test_any_part_in_skip_empty_skip_short_circuits() -> None:
    assert _any_part_in_skip("node_modules/x.js", frozenset()) is False
