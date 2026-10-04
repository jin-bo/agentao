"""``read_skill_file`` — read a file of a loaded MCP skill (docs/design/mcp-skills.md §6.1).

Beside ``resource_tools.py`` for the same reason: ``tools`` importing ``mcp``
would close an import cycle.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..tools.base import Tool
from .resources import format_size
import copy

from .skills import READ_SKILL_FILE_TOOL, is_binary, relative_path, wrap_skill_file

#: The largest page returned, below the formatter's 40,000-character spill
#: threshold: skill content is never spilled to disk, so a page longer than
#: that would lose its middle with no way back to it.
PAGE_CHARS = 30_000

#: Every name this module registers. Accepted by ``disable_tools`` and
#: ``enabled_tools`` beside ``BUILTIN_TOOL_NAMES``; a test pins it to the
#: literal in ``tooling/registry.py``.
MCP_SKILL_TOOL_NAMES = frozenset({READ_SKILL_FILE_TOOL})


class ReadSkillFileTool(Tool):
    """Read one manifest file of an MCP skill loaded in this session.

    There is no parameter that names a server: the read always goes to the
    skill's own server, so a cross-origin read cannot be expressed (§6.1).
    The path must be in the held manifest — an unlisted one is refused with
    no request — and the bytes are verified before they are returned.
    """

    def __init__(self, mcp_skills: Any, view: Optional[int] = None):
        super().__init__()
        self._skills = mcp_skills
        #: The conversation generation this instance reads (``None``: the
        #: session's current one). A sub-agent gets a copy bound to the one it
        #: was spawned in (:meth:`for_view`), so neither reads skills the
        #: other's conversation loaded.
        self._view = view

    def for_view(self, view: Optional[int]) -> "ReadSkillFileTool":
        clone = copy.copy(self)
        clone._view = view
        return clone

    @property
    def name(self) -> str:
        return READ_SKILL_FILE_TOOL

    @property
    def description(self) -> str:
        return (
            "Read a file of an MCP skill that is active in this session. Give the "
            "full name of the skill (mcp:<server>:<SKILL.md URI>) and the path of "
            "the file relative to the skill directory. The activation result lists "
            "these paths. The tool checks the content against the skill manifest."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Full name of an active MCP skill: mcp:<server>:<SKILL.md URI>.",
                },
                "path": {
                    "type": "string",
                    "description": "File path relative to the skill directory, e.g. references/FORMS.md.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Character offset to start at (default 0), for a long file.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Characters to return (default and maximum {PAGE_CHARS}).",
                },
            },
            "required": ["skill", "path"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def requires_confirmation(self) -> bool:
        # The content was approved with the skill (§6.1): this reads only
        # manifest files of a loaded skill, from that skill's own server.
        return False

    def execute(
        self, skill: str = "", path: str = "", offset: Any = 0, limit: Any = None, **_: Any,
    ) -> str:
        if not skill or not path:
            return "Error: read_skill_file requires both skill and path."
        key = self._skills.resolve_name(skill)
        held = self._skills.held(key, self._view) if key is not None else None
        if held is None:
            return (
                f"Error: '{skill}' is not an MCP skill loaded in this session. "
                "Activate it with activate_skill first."
            )
        expected, error = self._skills.resolve_file(held, path)
        if expected is None:
            return error
        data, mime, error = self._skills.read_file(held, expected)
        if data is None:
            return error
        shown = relative_path(held.entry, expected.uri)
        if is_binary(data, mime):
            return (
                f"[binary file {shown} from MCP skill {held.entry.model_name}: "
                f"{mime or 'unknown type'}, {format_size(len(data))} — not shown]"
            )
        text = data.decode("utf-8")
        page = _page(text, offset, limit)
        if isinstance(page, str) and page.startswith("Error:"):
            return page
        body, note = page
        return wrap_skill_file(held.entry.label, expected.uri, body) + note


def _page(text: str, offset: Any, limit: Any) -> Any:
    """``(body, note)`` for one page of a verified file, or an error string.

    The whole file was verified before this; paging only chooses what to
    show. ``note`` tells the model how to continue when there is more.
    """
    try:
        start = int(offset or 0)
        size = PAGE_CHARS if limit is None else int(limit)
    except (TypeError, ValueError):
        return "Error: offset and limit must be integers."
    if start < 0 or size <= 0:
        return "Error: offset must be >= 0 and limit > 0."
    size = min(size, PAGE_CHARS)
    total = len(text)
    if start == 0 and total <= size:
        return text, ""
    if start >= total:
        return f"Error: offset {start} is past the end of the file ({total} characters)."
    end = min(start + size, total)
    note = f"\n[characters {start}-{end} of {total}"
    note += f"; continue with offset={end}]" if end < total else "; end of file]"
    return text[start:end], note
