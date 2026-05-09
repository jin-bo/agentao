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
