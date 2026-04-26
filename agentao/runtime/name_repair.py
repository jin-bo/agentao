"""Conservative repair for malformed tool-call *names*.

Claude-style models occasionally emit class-like names (``TodoTool_tool``,
``BrowserClick_tool``, ``PatchTool``) instead of the snake_case names
they were given. Without repair the planner returns "Unknown tool" and
the model burns a turn re-asking. This module tries cheap normalisations
before falling back to fuzzy match (``difflib`` at cutoff 0.7 — the
safety rail that prevents guessing across unrelated names).
"""

from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Iterable, Optional, Set


_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")
_TOOL_SUFFIXES = ("_tool", "-tool", "tool")
_FUZZY_CUTOFF = 0.7


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


def repair_tool_name(name: str, valid_names: Iterable[str]) -> Optional[str]:
    """Return a name from ``valid_names`` that the LLM probably meant, or None.

    ``valid_names`` is iterated multiple times — pass a set/frozenset for O(1)
    membership, or a list/tuple if order matters for fuzzy ranking.
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

    matches = get_close_matches(lowered, valid, n=1, cutoff=_FUZZY_CUTOFF)
    if matches:
        return matches[0]
    return None
