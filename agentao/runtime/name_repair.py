"""Conservative repair for malformed tool-call *names*.

Claude-style models occasionally emit class-like names (``TodoTool_tool``,
``BrowserClick_tool``, ``PatchTool``) instead of the snake_case names
they were given. Without repair the planner returns "Unknown tool" and
the model burns a turn re-asking. This module tries cheap normalisations
before falling back to fuzzy match (``difflib`` at cutoff 0.7 — the
safety rail that prevents guessing across unrelated names).

A cutoff cannot tell a misspelling from a different tool, though: ``read_file``
and ``write_file`` are as close as a typo. So a name that already spells a real
tool is never repaired into another one (see ``repair_tool_name``).
"""

from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Iterable, Optional, Set


_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")
_TOOL_SUFFIXES = ("_tool", "-tool", "tool")
_FUZZY_CUTOFF = 0.7
# Namespaces whose every name is a tool of its own: an MCP tool, an agent tool.
_TOOL_NAMESPACES = ("mcp_", "agent_")


def _normalise_separators(s: str) -> str:
    return s.lower().replace("-", "_").replace(" ", "_")


def _camel_to_snake(s: str) -> str:
    return _CAMEL_BOUNDARY_RE.sub("_", s).lower()


def _strip_tool_suffix(s: str) -> Optional[str]:
    lc = s.lower()
    for suffix in _TOOL_SUFFIXES:
        if lc.endswith(suffix):
            return s[: -len(suffix)].rstrip("_-")
    return None


def repair_tool_name(
    name: str,
    valid_names: Iterable[str],
    *,
    known: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Return a name from ``valid_names`` that the LLM probably meant, or None.

    ``valid_names`` is iterated multiple times — pass a set/frozenset for O(1)
    membership, or a list/tuple if order matters for fuzzy ranking.

    ``known`` names tools that exist whether or not this runtime offers them
    (default: the built-ins). A name that spells one of them, or any ``mcp_`` /
    ``agent_`` name, means that tool: when it is not offered, the answer is
    None, not the closest tool that is. Otherwise a call for a tool the
    runtime withheld ran a different one. A sub-agent not given ``read_file``
    wrote with ``write_file``, and one whose host had replaced
    ``check_background_agent`` cancelled the task it meant to check.
    """
    if not name:
        return None
    valid: Set[str] = valid_names if isinstance(valid_names, set) else set(valid_names)
    if not valid:
        return None

    lowered = name.lower()
    if lowered in valid:
        return lowered
    normalised = _normalise_separators(name)
    if normalised in valid:
        return normalised

    candidates: Set[str] = {name, lowered, normalised, _camel_to_snake(name)}
    # Strip trailing tool-suffix up to twice so ``TodoTool_tool`` →
    # ``TodoTool`` → ``Todo`` → ``todo`` reduces all the way.
    for _ in range(2):
        extra: Set[str] = set()
        for c in candidates:
            stripped = _strip_tool_suffix(c)
            if stripped:
                extra.add(stripped)
                extra.add(_normalise_separators(stripped))
                extra.add(_camel_to_snake(stripped))
        candidates |= extra

    for c in candidates:
        if c and c in valid:
            return c

    if known is None:
        # Deferred: ``tooling`` registers tools, which is above ``runtime``.
        from ..tooling.registry import BUILTIN_TOOL_NAMES as known
    known_set = known if isinstance(known, (set, frozenset)) else set(known)
    if any(c and (c in known_set or c.startswith(_TOOL_NAMESPACES)) for c in candidates):
        return None

    matches = get_close_matches(lowered, valid, n=1, cutoff=_FUZZY_CUTOFF)
    if matches:
        return matches[0]
    return None
