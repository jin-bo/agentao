"""Search tools."""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional
import fnmatch
import re

from .base import Tool
from ..capabilities import FileSystem, LocalFileSystem


def _find_executable(name: str) -> Optional[str]:
    """Locate an external binary on PATH (absolute path) or return None."""
    return shutil.which(name)

# Files modified within this window are sorted by recency
RECENCY_THRESHOLD = 86400  # 24 hours

# Directories almost no one wants searched: build outputs, vendored deps,
# language caches, VCS internals. Skipping them keeps search_file_content
# from melting on large trees. A caller who explicitly references one of
# these names in `directory` or `file_pattern` opts back in (see
# `_effective_skip_dirs`).
DEFAULT_SKIP_DIRS: FrozenSet[str] = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
})


def _effective_skip_dirs(file_pattern: str, directory: str) -> FrozenSet[str]:
    """Drop any skip-dir name the caller explicitly asks for.

    If the user passes ``file_pattern='node_modules/lodash/**/*.js'`` or
    ``directory='node_modules/foo'`` they clearly want results from there,
    so we remove ``node_modules`` from the skip set for that call.
    """
    referenced: set = set()
    for raw in (file_pattern or "", directory or ""):
        for part in raw.replace("\\", "/").split("/"):
            if part in DEFAULT_SKIP_DIRS:
                referenced.add(part)
    if not referenced:
        return DEFAULT_SKIP_DIRS
    return frozenset(DEFAULT_SKIP_DIRS - referenced)


def _any_part_in_skip(path_str: str, skip: FrozenSet[str]) -> bool:
    """True if any '/'- or '\\'-separated component of ``path_str`` is in ``skip``."""
    if not skip:
        return False
    return any(p in skip for p in path_str.replace("\\", "/").split("/"))


def _path_in_skip_dirs(file_path: Path, base: Path, skip: FrozenSet[str]) -> bool:
    """True if any path component (relative to ``base``) is a skip-dir name."""
    if not skip:
        return False
    try:
        rel = file_path.relative_to(base)
    except ValueError:
        return False
    return any(part in skip for part in rel.parts)


def _format_grep_output(stdout: str, pattern: str, skip: FrozenSet[str]) -> str:
    """Shared formatter for ``git grep -n`` / ``rg --line-number`` output.

    Both engines emit ``path:lineno:content`` lines, so skip-list filtering
    and the 100-match truncation are identical.
    """
    lines = stdout.strip().splitlines()
    if skip:
        lines = [ln for ln in lines if not _any_part_in_skip(ln.split(":", 1)[0], skip)]
    if not lines:
        return f"No matches found for pattern: {pattern}"
    header = f"Found {len(lines)} match(es):\n\n"
    if len(lines) > 100:
        return header + "\n".join(lines[:100]) + f"\n\n... and {len(lines) - 100} more matches"
    return header + "\n".join(lines)


class FindFilesTool(Tool):
    """Tool for finding files using glob patterns."""

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return "Find files matching a glob pattern (e.g., '*.py', '**/*.txt'). Supports recursive search with '**'."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match files (e.g., '*.py', 'src/**/*.js')",
                },
                "directory": {
                    "type": "string",
                    "description": "Base directory to search from (defaults to current directory)",
                    "default": ".",
                },
            },
            "required": ["pattern"],
        }

    def _sort_by_recency(self, base_path: Path, file_paths: List[str], fs: FileSystem) -> List[str]:
        """Sort files: recently modified (24h) by mtime desc, then rest alphabetically."""
        now = time.time()
        recent = []
        older = []
        for f in file_paths:
            try:
                mtime = fs.stat(base_path / f).mtime
                if now - mtime < RECENCY_THRESHOLD:
                    recent.append((f, mtime))
                else:
                    older.append(f)
            except OSError:
                older.append(f)
        recent.sort(key=lambda x: x[1], reverse=True)  # newest first
        older.sort()  # alphabetical
        return [f for f, _ in recent] + older

    def execute(self, pattern: str, directory: str = ".") -> str:
        """Find files matching pattern, with recently modified files listed first."""
        try:
            path = self._resolve_path(directory)
            fs = self._get_fs()
            if not fs.exists(path):
                return f"Error: Directory {directory} does not exist"

            matches = []
            recursive = "**" in pattern
            search_pattern = pattern.replace("**/", "") if recursive else pattern
            for item in fs.glob(path, search_pattern, recursive=recursive):
                if fs.is_file(item):
                    matches.append(str(item.relative_to(path)))

            if not matches:
                return f"No files found matching pattern: {pattern}"

            sorted_matches = self._sort_by_recency(path, matches, fs)
            return f"Found {len(sorted_matches)} file(s):\n\n" + "\n".join(sorted_matches)
        except Exception as e:
            return f"Error finding files: {str(e)}"


class SearchTextTool(Tool):
    """Tool for searching text content in files."""

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "search_file_content"

    @property
    def description(self) -> str:
        return (
            "Search for text patterns in files. Supports regex patterns and "
            "can search across multiple files. Heavyweight directories "
            "(.git, node_modules, .venv, dist, build, __pycache__, language "
            "caches) are skipped by default; reference one explicitly in "
            "`directory` or `file_pattern` to include it."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "File glob pattern to search in (e.g., '*.py', '**/*.txt')",
                    "default": "**/*",
                },
                "directory": {
                    "type": "string",
                    "description": "Base directory to search from",
                    "default": ".",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether to perform case-sensitive search",
                    "default": True,
                },
                "regex": {
                    "type": "boolean",
                    "description": "Whether to treat pattern as regex",
                    "default": False,
                },
            },
            "required": ["pattern"],
        }

    def _is_git_repo(self, directory: Path) -> bool:
        """Check if directory is inside a git repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(directory),
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def _git_grep(
        self,
        directory: Path,
        pattern: str,
        file_pattern: str,
        case_sensitive: bool,
        regex: bool,
        skip: FrozenSet[str] = frozenset(),
    ) -> Optional[str]:
        """Try searching with git grep. Returns formatted result string, or None if git grep fails."""
        try:
            cmd = ["git", "grep", "-n", "--no-color"]
            if not case_sensitive:
                cmd.append("-i")
            if not regex:
                cmd.append("-F")  # fixed string (literal) mode

            # Pattern: use -e to safely accept patterns starting with '-'.
            # For git grep, `--` is the pathspec separator (not an option
            # terminator), so a leading-`-` pattern would still be parsed
            # as a flag without -e.
            cmd.extend(["-e", pattern])

            # File pattern filter (after the pattern, separated by --).
            if file_pattern and file_pattern != "**/*":
                # Convert glob to git pathspec
                # e.g., "*.py" -> "*.py", "**/*.py" -> "*.py"
                clean_pattern = file_pattern.replace("**/", "")
                cmd.extend(["--", clean_pattern])

            result = subprocess.run(
                cmd,
                cwd=str(directory),
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 1:
                return f"No matches found for pattern: {pattern}"
            if result.returncode != 0:
                return None
            return _format_grep_output(result.stdout, pattern, skip)
        except (subprocess.SubprocessError, FileNotFoundError):
            return None

    def _ripgrep(
        self,
        directory: Path,
        pattern: str,
        file_pattern: str,
        case_sensitive: bool,
        regex: bool,
        skip: FrozenSet[str] = frozenset(),
    ) -> Optional[str]:
        """Search using ripgrep. Caller must probe rg via ``_find_executable`` first.

        Primary territory: non-git host trees. ``execute()`` only reaches this
        branch when the directory is not a git repo (or ``git grep`` itself
        failed), so we cannot rely on ``.gitignore`` semantics to prune heavy
        directories — we translate ``skip`` into ``--glob '!<dir>'`` ourselves
        to skip them at the source rather than post-filter rg's output.
        """
        try:
            cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
            if not case_sensitive:
                cmd.append("-i")
            if not regex:
                cmd.append("-F")  # fixed-string (literal) mode

            # ripgrep's --glob handles ``**`` natively — no rewrite needed.
            if file_pattern and file_pattern != "**/*":
                cmd.extend(["--glob", file_pattern])

            # Source-level exclusion of skip dirs: gitignore-style, so a bare
            # name matches at any depth. Cheaper than rg-then-filter, and
            # essential in non-git trees where there's no .gitignore to lean
            # on. ``skip`` already honors caller opt-in (see
            # ``_effective_skip_dirs``), so a user who explicitly references
            # ``node_modules`` in their query won't have it excluded here.
            # Order matters: rg gives *later* matching globs precedence, so
            # the negative skip globs MUST come after the positive
            # ``file_pattern`` glob. Reversed, ``--glob '**/*.js'`` would
            # re-enable matches inside ``node_modules/`` and defeat the
            # skip set.
            for d in sorted(skip):
                cmd.extend(["--glob", f"!{d}"])

            # `--` terminates ripgrep options so a pattern like
            # `--pre=/tmp/payload.sh` is treated as text, not a flag.
            cmd.extend(["--", pattern, "."])

            result = subprocess.run(
                cmd,
                cwd=str(directory),
                capture_output=True,
                text=True,
                timeout=30,
            )

            # rg exit codes: 0 = matches, 1 = no matches, 2 = error.
            if result.returncode == 1:
                return f"No matches found for pattern: {pattern}"
            if result.returncode != 0:
                return None
            return _format_grep_output(result.stdout, pattern, skip)
        except (subprocess.SubprocessError, FileNotFoundError):
            return None

    def execute(
        self,
        pattern: str,
        file_pattern: str = "**/*",
        directory: str = ".",
        case_sensitive: bool = True,
        regex: bool = False,
    ) -> str:
        """Search for text in files. Uses git grep or ripgrep when available for performance."""
        try:
            path = self._resolve_directory(directory)
            fs = self._get_fs()
            if not fs.exists(path):
                return f"Error: Directory {directory} does not exist"

            skip = _effective_skip_dirs(file_pattern, directory)

            # Prefer ``git grep`` in git repos: it constrains the search space
            # to tracked files (which usually matches "search project code"
            # intent and excludes build artifacts / vendored deps for free)
            # and reuses git's universally-available binary. This is a
            # *semantic* preference — rg's raw scan is faster, but searching
            # a smaller, intent-aligned file set wins end-to-end on most
            # repos. Gate on ``LocalFileSystem`` so an injected virtual /
            # remote FS doesn't get bypassed by the on-disk git repo at
            # ``path`` returning unrelated results.
            if isinstance(fs, LocalFileSystem) and self._is_git_repo(path):
                result = self._git_grep(path, pattern, file_pattern, case_sensitive, regex, skip)
                if result is not None:
                    return result

            # Fall through to ripgrep for non-git trees and as a rescue when
            # ``git grep`` errored. Same ``LocalFileSystem`` gate as git grep:
            # a virtual / Docker / remote FS would have rg search the wrong
            # tree.
            if isinstance(fs, LocalFileSystem) and _find_executable("rg") is not None:
                result = self._ripgrep(path, pattern, file_pattern, case_sensitive, regex, skip)
                if result is not None:
                    return result

            # Fallback: Python-based search
            if regex:
                flags = 0 if case_sensitive else re.IGNORECASE
                try:
                    compiled_pattern = re.compile(pattern, flags)
                except re.error as e:
                    return f"Error: Invalid regex pattern: {str(e)}"
            else:
                if not case_sensitive:
                    pattern = pattern.lower()

            recursive = "**" in file_pattern
            search_pattern = file_pattern.replace("**/", "") if recursive else file_pattern
            files_to_search = fs.glob(path, search_pattern, recursive=recursive)

            results = []
            for file_path in files_to_search:
                # Skip heavyweight dirs before the stat call: cheap string
                # check, saves an open() on every node_modules/.git file.
                if _path_in_skip_dirs(file_path, path, skip):
                    continue
                if not fs.is_file(file_path):
                    continue

                try:
                    line_iter = fs.open_text(file_path)
                except (UnicodeDecodeError, PermissionError, OSError):
                    continue

                try:
                    for line_num, line in enumerate(line_iter, 1):
                        if regex:
                            match = compiled_pattern.search(line) is not None
                        else:
                            search_line = line if case_sensitive else line.lower()
                            match = pattern in search_line

                        if match:
                            rel_path = file_path.relative_to(path)
                            results.append(f"{rel_path}:{line_num}: {line.rstrip()}")
                except (UnicodeDecodeError, PermissionError, OSError):
                    continue

            if not results:
                return f"No matches found for pattern: {pattern}"

            result_text = f"Found {len(results)} match(es):\n\n"
            if len(results) > 100:
                result_text += "\n".join(results[:100])
                result_text += f"\n\n... and {len(results) - 100} more matches"
            else:
                result_text += "\n".join(results)

            return result_text
        except Exception as e:
            return f"Error searching files: {str(e)}"
