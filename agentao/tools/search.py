"""Search tools."""

import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import fnmatch
import re

from .base import Tool

# Files modified within this window are sorted by recency
RECENCY_THRESHOLD = 86400  # 24 hours


class FindFilesTool(Tool):
    """Tool for finding files using glob patterns."""

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

    def _sort_by_recency(self, base_path: Path, file_paths: List[str]) -> List[str]:
        """Sort files: recently modified (24h) by mtime desc, then rest alphabetically."""
        now = time.time()
        recent = []
        older = []
        for f in file_paths:
            try:
                mtime = (base_path / f).stat().st_mtime
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
            path = Path(directory).expanduser()
            if not path.exists():
                return f"Error: Directory {directory} does not exist"

            matches = []
            if "**" in pattern:
                for item in path.rglob(pattern.replace("**/", "")):
                    if item.is_file():
                        matches.append(str(item.relative_to(path)))
            else:
                for item in path.glob(pattern):
                    if item.is_file():
                        matches.append(str(item.relative_to(path)))

            if not matches:
                return f"No files found matching pattern: {pattern}"

            sorted_matches = self._sort_by_recency(path, matches)
            return f"Found {len(sorted_matches)} file(s):\n\n" + "\n".join(sorted_matches)
        except Exception as e:
            return f"Error finding files: {str(e)}"


class SearchTextTool(Tool):
    """Tool for searching text content in files."""

    @property
    def name(self) -> str:
        return "search_file_content"

    @property
    def description(self) -> str:
        return "Search for text patterns in files. Supports regex patterns and can search across multiple files."

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
    ) -> Optional[str]:
        """Try searching with git grep. Returns formatted result string, or None if git grep fails."""
        try:
            cmd = ["git", "grep", "-n", "--no-color"]
            if not case_sensitive:
                cmd.append("-i")
            if not regex:
                cmd.append("-F")  # fixed string (literal) mode

            # File pattern filter
            if file_pattern and file_pattern != "**/*":
                # Convert glob to git pathspec
                # e.g., "*.py" -> "*.py", "**/*.py" -> "*.py"
                clean_pattern = file_pattern.replace("**/", "")
                cmd.extend(["--", clean_pattern])

            # Insert pattern before pathspec args
            if file_pattern and file_pattern != "**/*":
                cmd.insert(-2, pattern)
            else:
                cmd.append(pattern)

            result = subprocess.run(
                cmd,
                cwd=str(directory),
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 1:
                # No matches
                return f"No matches found for pattern: {pattern}"
            if result.returncode != 0:
                # git grep error, fall back
                return None

            lines = result.stdout.strip().splitlines()
            total = len(lines)

            if total == 0:
                return f"No matches found for pattern: {pattern}"

            result_text = f"Found {total} match(es):\n\n"
            if total > 100:
                result_text += "\n".join(lines[:100])
                result_text += f"\n\n... and {total - 100} more matches"
            else:
                result_text += "\n".join(lines)

            return result_text
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
        """Search for text in files. Uses git grep when available for performance."""
        try:
            path = Path(directory).expanduser().resolve()
            if not path.exists():
                return f"Error: Directory {directory} does not exist"

            # Try git grep first (much faster in git repos)
            if self._is_git_repo(path):
                result = self._git_grep(path, pattern, file_pattern, case_sensitive, regex)
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

            files_to_search = []
            if "**" in file_pattern:
                files_to_search = list(path.rglob(file_pattern.replace("**/", "")))
            else:
                files_to_search = list(path.glob(file_pattern))

            results = []
            for file_path in files_to_search:
                if not file_path.is_file():
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        for line_num, line in enumerate(f, 1):
                            match = False
                            if regex:
                                match = compiled_pattern.search(line) is not None
                            else:
                                search_line = line if case_sensitive else line.lower()
                                match = pattern in search_line

                            if match:
                                rel_path = file_path.relative_to(path)
                                results.append(f"{rel_path}:{line_num}: {line.rstrip()}")
                except (UnicodeDecodeError, PermissionError):
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
