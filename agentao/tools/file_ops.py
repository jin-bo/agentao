"""File operation tools."""

import difflib
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import Tool
from ..security import PathPolicy, PathPolicyError

# Maximum lines to show without explicit limit
MAX_LINES_DEFAULT = 2000
# Maximum characters per line before truncating (prevents minified JS / base64 blobs)
MAX_LINE_LENGTH = 2000
# Bytes to check for binary detection
BINARY_CHECK_SIZE = 8192

# Codepoint table copied from codex-rs/apply-patch/src/seek_sequence.rs:79-92.
# Mirrors the fuzzy behaviour of `git apply` — applied as the final fallback
# in EditTool's match pyramid so byte-identical edits are unaffected.
_EDIT_DASHES = "‐‑‒–—―−"
_EDIT_SQUOTES = "‘’‚‛"
_EDIT_DQUOTES = "“”„‟"
_EDIT_SPACES = (
    "            　"
)

_EDIT_NORMALIZE_TABLE = str.maketrans(
    {
        **{c: "-" for c in _EDIT_DASHES},
        **{c: "'" for c in _EDIT_SQUOTES},
        **{c: '"' for c in _EDIT_DQUOTES},
        **{c: " " for c in _EDIT_SPACES},
    }
)


def _edit_normalize_for_match(s: str) -> str:
    """Map common typographic codepoints to ASCII for fuzzy edit matching."""
    return s.translate(_EDIT_NORMALIZE_TABLE)


# Success-message suffixes for non-exact match tiers. Tests assert against these,
# so they live as named constants instead of duplicated string literals.
_EDIT_SUFFIX_FLEXIBLE = " (flexible whitespace match)"
_EDIT_SUFFIX_UNICODE = " (unicode-normalized match)"


def _ambiguous_edit_message(
    *,
    total: int,
    remaining: int,
    old_text: str,
    new_text: str,
    where: str,
    suffix: str = "",
) -> str:
    """Report a first-match edit that had more than one candidate site.

    ``remaining`` is counted against the *written* content rather than
    derived as ``total - 1``: when ``new_text`` contains ``old_text``
    (``foo`` → ``foobar``) the substitution does not consume an
    occurrence, so the arithmetic guess would understate what is left.

    The ``replace_all=true`` suggestion is withheld in exactly that case.
    Re-running with ``replace_all`` there would rewrite the site already
    edited — ``foobar`` becomes ``foobarbar`` — so recommending it would
    hand the model a corruption it believes is a fix.
    """
    grows = old_text in new_text
    advice = (
        "include more surrounding context to make it unique"
        if grows
        else "include more surrounding context to make it unique, or pass "
        "replace_all=true to change all"
    )
    caution = (
        " Note: new_text contains old_text, so replace_all would re-edit the "
        "site just changed — target the remaining sites individually."
        if grows
        else ""
    )
    return (
        f"Replaced the first of {total} occurrences in {where}{suffix}. "
        f"{remaining} occurrence(s) of old_text remain — if you meant a "
        f"different one, {advice}.{caution}"
    )


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
            path = self._resolve_path(file_path)
            fs = self._get_fs()

            if not fs.is_file(path):
                return f"Error: File {file_path} does not exist"

            sniff = fs.read_partial(path, BINARY_CHECK_SIZE)
            if b"\x00" in sniff:
                return f"Binary file: {file_path} ({fs.stat(path).size} bytes)"

            try:
                all_lines = list(fs.open_text(path))
            except UnicodeDecodeError:
                return f"Binary file: {file_path} ({fs.stat(path).size} bytes)"

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
        except Exception as e:
            return f"Error reading file: {str(e)}"


class WriteFileTool(Tool):
    """Tool for writing content to a file."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Creates the file if it doesn't exist. "
            "Supports append mode. Best for new or small files; for surgical "
            "changes to existing files, prefer `replace` to minimize token "
            "usage and simplify review."
        )

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
            path = PathPolicy.for_tool(self).contain_file(file_path)
        except PathPolicyError as e:
            return f"Error: {e}"
        try:
            self._get_fs().write_text(path, content, append=append)
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
        return (
            "Edit a file by replacing old text with new text. The old text "
            "must match exactly. Use replace_all to replace all occurrences. "
            "Preferred for surgical edits to existing files: minimizes token "
            "usage, simplifies review, and avoids accidental deletions."
        )

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

    @property
    def requires_confirmation(self) -> bool:
        """File editing requires user confirmation to prevent silent rewrites."""
        return True

    def _normalize_whitespace(self, text: str) -> str:
        """Normalize whitespace: strip trailing spaces per line, normalize line endings."""
        lines = text.splitlines()
        return "\n".join(line.rstrip() for line in lines)

    def _line_window_matches(
        self,
        content: str,
        old_text: str,
        line_transform: Callable[[str], str] = lambda s: s,
    ) -> List[Tuple[int, int]]:
        """Find all non-overlapping spans where ``old_text`` matches ``content``.

        Comparison key per line is ``line_transform(line).strip()``. With the
        identity default this is the tier-2 whitespace-flexible match; with
        ``_edit_normalize_for_match`` it's the tier-3 typographic-Unicode match
        (mirrors `git apply` fuzzy behaviour). Returns ``(start, end)`` byte
        offsets in the *original* content; empty list if no match. ``replace_all``
        relies on getting *every* normalized-equivalent span here — delegating
        to ``str.replace`` only catches byte-identical copies of the first span.
        """
        norm_old_lines = self._normalize_whitespace(old_text).splitlines()
        # keepends=True so the prefix table accounts for the *actual* line
        # ending of each line — CRLF stays 2 bytes, LF stays 1, and a final
        # line without a trailing newline contributes only its content length.
        # Comparison still works because ``.strip()`` drops the trailing
        # ending bytes after ``line_transform``.
        content_lines = content.splitlines(keepends=True)

        if not norm_old_lines or len(content_lines) < len(norm_old_lines):
            return []

        pat = [line_transform(l).strip() for l in norm_old_lines]
        norm_content = [line_transform(l).strip() for l in content_lines]

        prefix = [0]
        for line in content_lines:
            prefix.append(prefix[-1] + len(line))

        matches: List[Tuple[int, int]] = []
        n = len(norm_old_lines)
        i = 0
        while i <= len(content_lines) - n:
            if norm_content[i : i + n] == pat:
                matches.append((prefix[i], prefix[i + n]))
                i += n
            else:
                i += 1
        return matches

    def _apply_match(
        self,
        path: Path,
        content: str,
        matches: List[Tuple[int, int]],
        old_text: str,
        new_text: str,
        replace_all: bool,
        suffix: str,
        file_path: str,
    ) -> str:
        """Splice ``new_text`` at one (first match) or all matched spans and write back.

        Spans are spliced in descending offset order so earlier spans remain
        valid mid-loop. Each span's trailing newline is dropped if ``old_text``
        does not itself end with one (matches the legacy single-tier behaviour).
        """
        spans = matches if replace_all else matches[:1]
        new_content = content
        for start, end in sorted(spans, reverse=True):
            # If old_text doesn't end with a newline, drop the matched span's
            # trailing line ending so the splice preserves it. Handle CRLF
            # before LF — half-trimming a CRLF would leave a stray '\r'.
            if not old_text.endswith("\n"):
                if content[end - 2 : end] == "\r\n":
                    end -= 2
                elif content[end - 1 : end] == "\n":
                    end -= 1
            new_content = new_content[:start] + new_text + new_content[end:]

        self._get_fs().write_text(path, new_content)

        if replace_all or len(matches) == 1:
            return f"Replaced {len(spans)} occurrence(s) in {file_path}{suffix}"
        # Same ambiguity signal as the exact-match tier: we spliced the
        # first of several candidate sites and the model needs to know a
        # choice was made on its behalf. Only one span was consumed, so
        # the rest of the normalized matches are still there.
        return _ambiguous_edit_message(
            total=len(matches),
            remaining=len(matches) - len(spans),
            old_text=old_text,
            new_text=new_text,
            where=file_path,
            suffix=suffix,
        )

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
        # ``"" in content`` is always True and ``content.count("")`` returns
        # ``len(content) + 1``, so an empty ``old_text`` would otherwise be
        # reported as hundreds of ambiguous occurrences — and with
        # ``replace_all=true`` Python inserts ``new_text`` between every
        # character, shredding the file. There is no sane interpretation of
        # "replace nothing", so refuse it outright.
        if not old_text:
            return (
                "Error: old_text is empty. Provide the exact text to replace; "
                "to create or overwrite a file use write_file instead."
            )
        try:
            path = PathPolicy.for_tool(self).contain_file(file_path)
        except PathPolicyError as e:
            return f"Error: {e}"
        fs = self._get_fs()
        try:
            content = fs.read_bytes(path).decode("utf-8")

            # 1. Exact match (original logic)
            if old_text in content:
                count = content.count(old_text)
                if replace_all:
                    new_content = content.replace(old_text, new_text)
                    fs.write_text(path, new_content)
                    return f"Replaced {count} occurrence(s) in {file_path}"

                new_content = content.replace(old_text, new_text, 1)
                fs.write_text(path, new_content)
                # First-match-only is the documented contract (see the
                # ``replace_all`` parameter description), but the model
                # supplies ``old_text`` believing it identifies one site.
                # When it does not, saying only "Replaced 1 occurrence(s)"
                # hides the ambiguity we already measured — and the model
                # has no other way to learn it may have patched the wrong
                # one. Report the denominator so it can check.
                if count > 1:
                    return _ambiguous_edit_message(
                        total=count,
                        remaining=new_content.count(old_text),
                        old_text=old_text,
                        new_text=new_text,
                        where=file_path,
                    )
                return f"Replaced 1 occurrence(s) in {file_path}"

            # 2. Flexible match: whitespace-normalized comparison
            matches = self._line_window_matches(content, old_text)
            if matches:
                return self._apply_match(
                    path, content, matches, old_text, new_text,
                    replace_all, _EDIT_SUFFIX_FLEXIBLE, file_path,
                )

            # 3. Unicode-fuzzy match: typographic codepoints normalized to ASCII
            matches = self._line_window_matches(content, old_text, _edit_normalize_for_match)
            if matches:
                return self._apply_match(
                    path, content, matches, old_text, new_text,
                    replace_all, _EDIT_SUFFIX_UNICODE, file_path,
                )

            # 4. Not found — return hint with most similar snippet
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
            path = self._resolve_path(directory_path)
            fs = self._get_fs()
            if not fs.exists(path):
                return f"Error: Directory {directory_path} does not exist"

            if not fs.is_dir(path):
                return f"Error: {directory_path} is not a directory"

            results = []
            if recursive:
                items = sorted(fs.glob(path, "*", recursive=True),
                               key=lambda e: (not fs.is_dir(e), str(e).lower()))
                for item in items:
                    rel_path = item.relative_to(path)
                    if fs.is_dir(item):
                        results.append(f"[DIR]  {rel_path}/")
                    else:
                        try:
                            size = fs.stat(item).size
                        except OSError:
                            size = 0
                        results.append(f"[FILE] {rel_path} ({size} bytes)")
            else:
                entries = fs.list_dir(path)
                items = sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))
                for item in items:
                    if item.is_dir:
                        results.append(f"[DIR]  {item.name}/")
                    else:
                        results.append(f"[FILE] {item.name} ({item.size} bytes)")

            return f"Directory: {directory_path}\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing directory: {str(e)}"
