"""Glob and regex matchers used by hook rule selection.

Both helpers degrade pathological inputs to "no match" rather than
raising — a malformed plugin config must not crash dispatch.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def _glob_match(pattern: str, value: str) -> bool:
    """Simple glob match: ``*`` matches any substring, otherwise exact."""
    if pattern == "*":
        return True
    if "*" not in pattern:
        return pattern == value
    # Convert simple glob to a prefix/suffix check.
    parts = pattern.split("*")
    if len(parts) == 2:
        return value.startswith(parts[0]) and value.endswith(parts[1])
    # Fallback: use fnmatch.
    import fnmatch
    return fnmatch.fnmatch(value, pattern)


def _regex_match_full(pattern: str, value: str) -> bool:
    """Anchored full-match regex used by Claude-compat event matchers."""
    if not isinstance(pattern, str) or not isinstance(value, str):
        # Non-string matcher field (e.g. ``trigger: ["auto"]``) or payload
        # field. ``re.fullmatch`` would raise ``TypeError``; degrade to
        # no-match so a malformed plugin config doesn't crash dispatch.
        logger.warning(
            "Regex matcher requires string pattern and value; got "
            "pattern=%r value=%r — treating as no-match.",
            pattern, value,
        )
        return False
    try:
        return re.fullmatch(pattern, value) is not None
    except re.error:
        # A malformed pattern degrades to exact-equality so the rule is
        # not silently dropped at runtime.
        return pattern == value


def _claude_matcher_match(pattern: str, value: str) -> bool:
    """Evaluate a Claude-shaped string matcher against a tool name.

    **Measured, not inferred** (``docs/reference/hooks-probe-2.1.251.md`` §G3):
    ``*`` is a wildcard, and every other pattern is an **anchored full match**.
    Seven probe points agree with ``re.fullmatch`` exactly, and two of them rule
    out an unanchored search — ``ead`` does not match ``Read``, and neither does
    ``Rea|Wri``. Earlier revisions of the design said unanchored, from codex's
    implementation; this is the corrected reading.

    ``*`` has to be special-cased rather than passed through: it is not a valid
    regex, so the engine would raise on it — and :func:`_regex_match_full`
    degrades a malformed pattern to *exact equality*, which would silently make
    the most common matcher in the ecosystem match nothing at all.

    The **empty string** is the second wildcard spelling, and it is the one that
    fails quietly: the reference documents `""` and an omitted ``matcher`` as
    the same thing ("match all"), while ``re.fullmatch("", "Read")`` is a miss.
    A config copied out of a Claude Code setup that writes `""` would parse
    without a warning and then never fire.
    """
    if pattern in ("*", ""):
        return True
    return _regex_match_full(pattern, value)
