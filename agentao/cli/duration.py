"""Duration parsing for the ``/goal`` time budget.

``parse_duration("90s" | "30m" | "2h" | "1h30m") -> int`` (seconds). No such
helper existed in-tree before ``/goal`` (grep: no ``parse_duration``).

Unit-less numbers are rejected on purpose — a bare ``"30"`` is ambiguous (30
seconds? minutes?), so the ``/goal`` surface forces an explicit unit. Mirrors
the design in ``docs/design/codex-goal-mechanism-review.md`` §11.1 G.
"""

from __future__ import annotations

import re

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}
# One ``<int><unit>`` segment, e.g. ``90s`` / ``30m`` / ``2h``.
_TOKEN = re.compile(r"(\d+)([smh])", re.IGNORECASE)
# A whole string is valid only if it is one-or-more such segments back to back.
_WHOLE = re.compile(r"(?:\d+[smh])+", re.IGNORECASE)


class DurationParseError(ValueError):
    """Raised when a duration string cannot be parsed into positive seconds."""


def parse_duration(text: str) -> int:
    """Parse a compound duration like ``1h30m`` into total seconds.

    Accepts one or more ``<int><unit>`` segments (units: ``s``, ``m``, ``h``),
    optionally separated by whitespace, case-insensitive.

    Raises :class:`DurationParseError` for empty input, unit-less numbers
    (``"30"``), unknown units / junk, or a non-positive total (``"0s"``) —
    budgets must be positive.
    """
    if not text or not text.strip():
        raise DurationParseError("a duration is required (e.g. '90s', '30m', '2h', '1h30m')")
    # Collapse internal whitespace so "1h 30m" is accepted, then require the
    # *entire* string to be a run of <number><unit> tokens. A bare "30" has no
    # unit, so the whole-string match fails and we reject it.
    compact = re.sub(r"\s+", "", text.strip())
    if not _WHOLE.fullmatch(compact):
        raise DurationParseError(
            f"invalid duration {text!r}: use forms like '90s', '30m', '2h', '1h30m' "
            "(a unit is required on every segment)"
        )
    total = sum(int(num) * _UNIT_SECONDS[unit.lower()] for num, unit in _TOKEN.findall(compact))
    if total <= 0:
        raise DurationParseError(f"duration must be positive, got {text!r}")
    return total
