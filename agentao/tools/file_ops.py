"""File operation tools."""

import difflib
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import Tool

# Maximum lines to show without explicit limit
MAX_LINES_DEFAULT = 2000
# Maximum characters per line before truncating (prevents minified JS / base64 blobs)
MAX_LINE_LENGTH = 2000
# Bytes to check for binary detection
BINARY_CHECK_SIZE = 8192


class ReadFileTool(Tool):
    """Tool for reading file contents with line numbers."""

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read file contents with line numbers. Use offset/limit for large files."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to read (can be absolute or relative)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-based, default: 1)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default: 0 = all lines)",
                },
            },
            "required": ["file_path"],
        }

    def execute(self, file_path: str, offset: int = 1, limit: int = 0) -> str:
        """Read file contents with line numbers and optional range."""
        try:
            path = Path(file_path).expanduser()

            if not path.exists():
                return f"Error: File {file_path} does not exist"

            if not path.is_file():
                return f"Error: {file_path} is not a file"

            # Binary detection: check first 8KB for null bytes
            with open(path, "rb") as f:
                chunk = f.read(BINARY_CHECK_SIZE)
                if b"\x00" in chunk:
                    size = path.stat().st_size
                    return f"Binary file: {file_path} ({size} bytes)"

            with open(path, "r", encoding="utf-8") as f:
                all_lines = f.readlines()

            total_lines = len(all_lines)

            # Apply offset (1-based)
            start = max(1, offset)
            start_idx = start - 1

            # Apply limit
            if limit > 0:
                end_idx = min(start_idx + limit, total_lines)
            else:
                # No limit specified: auto-truncate at MAX_LINES_DEFAULT
                if total_lines - start_idx > MAX_LINES_DEFAULT:
                    end_idx = start_idx + MAX_LINES_DEFAULT
                else:
                    end_idx = total_lines

            end_line = end_idx  # 1-based end line number

            # Build output with line numbers (cat -n format)
            output_lines = []
            long_lines = 0
            for i in range(start_idx, end_idx):
                line_num = i + 1
                line = all_lines[i].rstrip("\n")
                if len(line) > MAX_LINE_LENGTH:
                    long_lines += 1
                    line = line[:MAX_LINE_LENGTH] + f"[+{len(line) - MAX_LINE_LENGTH} chars]"
                output_lines.append(f"{line_num:6d}\t{line}")

            header = f"File: {file_path} ({total_lines} lines)"
            header += f"\nShowing lines {start}-{end_line}"

            result = header + "\n" + "\n".join(output_lines)

            # Add truncation warnings
            if limit == 0 and total_lines - start_idx > MAX_LINES_DEFAULT:
                result += (
                    f"\n\n[Truncated: showing lines {start}–{end_line} of {total_lines} total. "
                    f"Use offset={end_line + 1} to continue reading.]"
                )
            if long_lines:
                result += f"\n[{long_lines} line(s) truncated to {MAX_LINE_LENGTH} chars per line]"

            return result
        except UnicodeDecodeError:
            size = path.stat().st_size
            return f"Binary file: {file_path} ({size} bytes)"
        except Exception as e:
            return f"Error reading file: {str(e)}"


class WriteFileTool(Tool):
    """Tool for writing content to a file."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file. Creates the file if it doesn't exist. Supports append mode."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
                "append": {
                    "type": "boolean",
                    "description": "Append instead of overwrite (default: false)",
                },
            },
            "required": ["file_path", "content"],
        }

    @property
    def requires_confirmation(self) -> bool:
        """File writing requires user confirmation to prevent data loss."""
        return True

    def execute(self, file_path: str, content: str, append: bool = False) -> str:
        """Write content to file."""
        try:
            path = Path(file_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(path, mode, encoding="utf-8") as f:
                f.write(content)
            action = "appended to" if append else "wrote to"
            return f"Successfully {action} {file_path}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class EditTool(Tool):
    """Tool for editing files by replacing text."""

    @property
    def name(self) -> str:
        return "replace"

    @property
    def description(self) -> str:
        return "Edit a file by replacing old text with new text. The old text must match exactly. Use replace_all to replace all occurrences."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to edit",
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to replace",
                },
                "new_text": {
                    "type": "string",
                    "description": "The new text to insert",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default: false, first only)",
                },
            },
            "required": ["file_path", "old_text", "new_text"],
        }

    def _normalize_whitespace(self, text: str) -> str:
        """Normalize whitespace: strip trailing spaces per line, normalize line endings."""
        lines = text.splitlines()
        return "\n".join(line.rstrip() for line in lines)

    def _flexible_match(self, content: str, old_text: str) -> Optional[Tuple[int, int]]:
        """Try whitespace-normalized matching. Returns (start, end) indices in content, or None."""
        norm_old_lines = self._normalize_whitespace(old_text).splitlines()
        content_lines = content.splitlines()

        if not norm_old_lines:
            return None

        norm_content_lines = [line.rstrip() for line in content_lines]

        for i in range(len(content_lines) - len(norm_old_lines) + 1):
            candidate = norm_content_lines[i : i + len(norm_old_lines)]
            # Compare stripped versions (ignoring leading/trailing whitespace per line)
            if [l.strip() for l in candidate] == [l.strip() for l in norm_old_lines]:
                # Found a match — compute character offsets in original content
                # Sum lengths of lines before match start, plus newlines
                start = sum(len(line) + 1 for line in content_lines[:i])
                end = sum(len(line) + 1 for line in content_lines[: i + len(norm_old_lines)])
                # Adjust: don't count trailing newline if content doesn't end with one
                if not content.endswith("\n"):
                    end = min(end, len(content))
                return (start, end)
        return None

    def _not_found_hint(self, content: str, old_text: str, file_path: str) -> str:
        """Return an error message with the most similar snippet from content."""
        old_lines = old_text.splitlines()
        content_lines = content.splitlines()
        window = len(old_lines)

        if window == 0 or not content_lines:
            return f"Error: Old text not found in {file_path}"

        # Slide a window over content and find most similar chunk
        best_ratio = 0.0
        best_snippet = ""
        best_line = 0
        for i in range(max(1, len(content_lines) - window + 1)):
            chunk_lines = content_lines[i : i + window]
            chunk = "\n".join(chunk_lines)
            ratio = difflib.SequenceMatcher(None, old_text, chunk).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_snippet = chunk
                best_line = i + 1

        msg = f"Error: Old text not found in {file_path}"
        if best_ratio > 0.4:
            msg += f"\n\nMost similar text (lines {best_line}-{best_line + window - 1}, {best_ratio:.0%} similar):\n{best_snippet}"
        return msg

    def execute(self, file_path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        """Replace text in file with flexible whitespace matching fallback."""
        try:
            path = Path(file_path).expanduser()
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # 1. Exact match (original logic)
            if old_text in content:
                count = content.count(old_text)
                if replace_all:
                    new_content = content.replace(old_text, new_text)
                else:
                    new_content = content.replace(old_text, new_text, 1)
                    count = 1

                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                return f"Replaced {count} occurrence(s) in {file_path}"

            # 2. Flexible match: whitespace-normalized comparison
            match = self._flexible_match(content, old_text)
            if match:
                start, end = match
                matched_text = content[start:end]
                # Strip trailing newline from matched_text if old_text doesn't end with one
                if not old_text.endswith("\n") and matched_text.endswith("\n"):
                    matched_text = matched_text[:-1]
                    end -= 1

                if replace_all:
                    new_content = content.replace(matched_text, new_text)
                    count = content.count(matched_text)
                else:
                    new_content = content[:start] + new_text + content[end:]
                    count = 1

                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                return f"Replaced {count} occurrence(s) in {file_path} (flexible whitespace match)"

            # 3. Not found — return hint with most similar snippet
            return self._not_found_hint(content, old_text, file_path)
        except Exception as e:
            return f"Error editing file: {str(e)}"


class ReadFolderTool(Tool):
    """Tool for listing directory contents."""

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "list_directory"

    @property
    def description(self) -> str:
        return "List the contents of a directory, showing files and subdirectories."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "directory_path": {
                    "type": "string",
                    "description": "Path to the directory to list (defaults to current directory)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list recursively",
                    "default": False,
                },
            },
            "required": [],
        }

    def execute(self, directory_path: str = ".", recursive: bool = False) -> str:
        """List directory contents."""
        try:
            path = Path(directory_path).expanduser()
            if not path.exists():
                return f"Error: Directory {directory_path} does not exist"

            if not path.is_dir():
                return f"Error: {directory_path} is not a directory"

            results = []
            if recursive:
                items = sorted(path.rglob("*"), key=lambda e: (not e.is_dir(), str(e).lower()))
                for item in items:
                    rel_path = item.relative_to(path)
                    if item.is_dir():
                        results.append(f"[DIR]  {rel_path}/")
                    else:
                        size = item.stat().st_size
                        results.append(f"[FILE] {rel_path} ({size} bytes)")
            else:
                # Sort: directories first, then alphabetical (case-insensitive)
                items = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
                for item in items:
                    if item.is_dir():
                        results.append(f"[DIR]  {item.name}/")
                    else:
                        size = item.stat().st_size
                        results.append(f"[FILE] {item.name} ({size} bytes)")

            return f"Directory: {directory_path}\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing directory: {str(e)}"
