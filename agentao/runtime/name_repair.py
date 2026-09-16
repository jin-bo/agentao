"""Conservative repair for malformed tool-call *names*.

Claude-style models occasionally emit class-like names (``TodoTool_tool``,
``BrowserClick_tool``, ``PatchTool``) instead of the snake_case names they were
given. Without repair the planner returns "Unknown tool" and the model burns a
turn re-asking. This module normalises spelling — surrounding whitespace, case,
separators, camelCase, a trailing ``Tool`` suffix — and answers only when a
normalisation lands on a name the runtime is actually offering.

**It does not guess by similarity** (#261). Until 0.4.24 an unresolved name
fell through to ``difflib.get_close_matches`` at cutoff 0.7 and the winner was
*dispatched*, which is a different thing from suggesting it: a cutoff cannot
tell a misspelling from a different tool, because ``read_file`` and
``write_file`` are as close as a typo. Two rounds of patching that — first
refusing names that spell a known-but-unoffered tool, then ranking the pool
over the known names too — each closed the case in front of it and left the
shape intact, because the premise was wrong. Both peers agentao is measured
against reject the name instead: codex returns an "unsupported call"
(`codex-rs/core/src/tools/registry.rs:821`), and gemini-cli errors with a "did
you mean" built from edit distance but *never runs the suggestion*
(`packages/core/src/utils/tool-utils.ts` builds it,
`packages/core/src/scheduler/scheduler.ts:360` only puts it in the error text).

So an unresolved name is now unresolved. The model is told the tool was not
found and which tools exist, and re-issues the call — one extra turn on a
genuine typo, in exchange for never running a tool nobody asked for. Adding a
"did you mean" to that error would be a strict improvement and is deliberately
not done here; the error already lists the available tools. Note the cost is
"one extra turn" only while the model *changes* its answer: the planner's
doom-loop counter is keyed on the raw ``(name, arguments)`` pair and is
incremented before this lookup, so a model that re-issues the identical
misspelling ``DOOM_LOOP_THRESHOLD`` times halts the turn.

Removing the similarity pass also removed the only thing that was covering two
spellings the normalisations themselves do not reach, so both are handled here
explicitly: a name padded with whitespace or an edge separator (``"read_file\\n"``
— the padding is not part of the spelling), and an offered name that is not
lowercase snake_case (an MCP server's ``getFileContents``, reachable from
``get_file_contents`` only if *both* sides are normalised).
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set


_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")
_TOOL_SUFFIXES = ("_tool", "-tool", "tool")
# Whitespace and separators a provider can pad a name with. ``"read_file\n"``
# names the tool it names; ``_normalise_separators`` would otherwise map the
# padding to underscores and produce ``_read_file_``.
_EDGE_CHARS = " \t\r\n\f\v_-"
# Index value for a key that two *different* offered names both spell. It
# resolves to neither — picking whichever was iterated last would put the
# answer back at the mercy of ``PYTHONHASHSEED``.
_AMBIGUOUS = object()


def _trim(s: str) -> str:
    return s.strip(_EDGE_CHARS)


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


def _variants(s: str) -> List[str]:
    """Spellings of ``s``: as given, lowercased, separator- and camel-normalised.

    Deduplicated, and ``s`` itself always comes first — the index built below
    relies on that to skip an offered name's own verbatim spelling.
    """
    seen: Set[str] = set()
    out: List[str] = []
    for v in (s, s.lower(), _normalise_separators(s), _camel_to_snake(s)):
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def repair_tool_name(name: str, valid_names: Iterable[str]) -> Optional[str]:
    """Return the name in ``valid_names`` that ``name`` *spells*, or None.

    Every answer is a name from ``valid_names`` that one of the normalisations
    reproduces exactly — on either side, since an offered ``getFileContents``
    is only reachable from ``get_file_contents`` when the offered names are
    normalised too. So the result is decided by spelling alone: no similarity
    threshold, no ranking, and therefore nothing that can depend on iteration
    order or on which tools happen to be offered alongside.

    That is what makes the withheld-tool class *structurally* out of reach
    rather than guarded case by case. A sub-agent not given ``read_file``
    cannot have the call repaired into ``write_file``; a host tool the
    sub-agent was denied cannot be repaired into a different host tool it was
    granted (``deploy_site`` → ``deploy_docs``, #261). None of those spellings
    normalises to an offered name, so there is no candidate to return.

    One residual case is *not* closed by that, and is not a similarity guess
    either: the tool-suffix strip reads ``deploy_tool`` as a way of writing
    ``deploy``, so a genuinely distinct withheld tool whose name is an offered
    name plus a ``tool`` suffix does resolve to the offered one. That reading
    is the module's reason for existing, so it is kept; a deployment that names
    two different tools ``x`` and ``x_tool`` is the one that cannot rely on it.

    Candidate order is by **fidelity**, not alphabetical: a spelling the whole
    name reproduces beats one reached by discarding a word the model wrote. It
    is what decides ``PatchTool`` in a registry holding both ``patch`` and
    ``patch_tool``. Within one fidelity tier the order is ``sorted()``, so a
    tie cannot vary with ``PYTHONHASHSEED``.

    ``valid_names`` may be any iterable; it is materialised into a set for
    membership, and its order is never consulted.
    """
    if not name:
        return None
    valid: Set[str] = (
        valid_names
        if isinstance(valid_names, (set, frozenset))
        else set(valid_names)
    )
    if not valid:
        return None

    # Tier 0: the name as written. Tier 1: the same with padding removed.
    tiers: List[List[str]] = [_variants(name)]
    trimmed = _trim(name)
    if trimmed and trimmed != name:
        tiers.append(_variants(trimmed))

    frontier: Set[str] = {c for tier in tiers for c in tier}
    seen: Set[str] = set(frontier)
    # Strip a trailing tool-suffix up to twice so ``TodoTool_tool`` →
    # ``TodoTool`` → ``Todo`` → ``todo`` reduces all the way. Each round is its
    # own tier: one strip is a more faithful reading than two.
    for _ in range(2):
        nxt: Set[str] = set()
        for c in frontier:
            stripped = _strip_tool_suffix(c)
            if not stripped:
                continue
            for v in _variants(stripped):
                if v not in seen:
                    seen.add(v)
                    nxt.add(v)
        if not nxt:
            break
        tiers.append(sorted(nxt))
        frontier = nxt

    for tier in tiers:
        for c in tier:
            if c in valid:
                return c

    # Nothing the asked name spells is offered *verbatim*. Normalise the
    # offered names too and try once more: a registry name with a capital or a
    # dash in it (MCP servers routinely use camelCase) is otherwise reachable
    # by exact spelling alone, which is the one thing the caller already tried.
    # Still spelling, not similarity — every key is a spelling of the offered
    # name it maps to — and a key two offered names share resolves to neither.
    index: Dict[str, object] = {}
    for v in valid:
        for key in _variants(v)[1:]:  # [0] is ``v`` itself; already tried above
            prev = index.get(key)
            if prev is None:
                index[key] = v
            elif prev != v:
                index[key] = _AMBIGUOUS
    if not index:
        return None
    for tier in tiers:
        for c in tier:
            hit = index.get(c)
            if hit is not None and hit is not _AMBIGUOUS:
                return hit  # type: ignore[return-value]
    return None
