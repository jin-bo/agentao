"""Conservative repair for malformed tool-call *names*.

Claude-style models occasionally emit class-like names (``TodoTool_tool``,
``BrowserClick_tool``, ``PatchTool``) instead of the snake_case names they were
given. Without repair the planner returns "Unknown tool" and the model burns a
turn re-asking. This module normalises spelling — case, separators, camelCase,
a trailing ``Tool`` suffix — and answers only when a normalisation lands on a
name the runtime is actually offering.

**It does not guess by similarity** (#261). Until 0.4.24 an unresolved name
fell through to ``difflib.get_close_matches`` at cutoff 0.7 and the winner was
*dispatched*, which is a different thing from suggesting it: a cutoff cannot
tell a misspelling from a different tool, because ``read_file`` and
``write_file`` are as close as a typo. Two rounds of patching that — first
refusing names that spell a known-but-unoffered tool, then ranking the pool
over the known names too — each closed the case in front of it and left the
shape intact, because the premise was wrong. Both peers agentao is measured
against reject the name instead: codex returns an "unsupported call"
(`core/src/tools/registry.rs`), and gemini-cli errors with a "did you mean"
built from edit distance but *never runs the suggestion*
(`core/src/scheduler/scheduler.ts`).

So an unresolved name is now unresolved. The model is told the tool was not
found and which tools exist, and re-issues the call — one extra turn on a
genuine typo, in exchange for never running a tool nobody asked for. Adding a
"did you mean" to that error would be a strict improvement and is deliberately
not done here; the error already lists the available tools.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Set


_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")
_TOOL_SUFFIXES = ("_tool", "-tool", "tool")


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
    """Return the name in ``valid_names`` that ``name`` *spells*, or None.

    Every answer is a name from ``valid_names`` that one of the normalisations
    reproduces exactly, so the result is decided by spelling alone: no
    similarity threshold, no ranking, and therefore nothing that can depend on
    iteration order or on which tools happen to be offered alongside.

    That is the whole guarantee, and it is what makes the withheld-tool class
    unreachable rather than guarded. A sub-agent not given ``read_file`` cannot
    have the call repaired into ``write_file``; a host tool the sub-agent was
    denied cannot be repaired into a different host tool it was granted
    (``deploy_site`` → ``deploy_docs``, #261). None of those spellings
    normalises to an offered name, so there is no candidate to return.

    ``valid_names`` may be any iterable; it is materialised into a set for
    membership, and order is never consulted.
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

    # Sorted so that a name whose normalisations produce two *different*
    # offered names — possible in principle, e.g. a registry holding both
    # ``patch`` and ``patch_tool`` — always resolves to the same one.
    for c in sorted(candidates):
        if c and c in valid:
            return c
    return None
