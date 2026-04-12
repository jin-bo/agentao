"""Explicit ACP routing detection.

Parses a raw user input string and, if it explicitly names a configured ACP
server, returns a typed :class:`AcpExplicitRoute`.  No fuzzy / LLM-based
guessing — only deterministic prefix forms described in Issue 12.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class AcpExplicitRoute:
    server: str
    task: str
    syntax: str   # at_mention | colon | zh_explicit
    raw_prefix: str


# Chinese explicit verbs — v1.1 extension.
_ZH_VERBS = ("让", "请")


def _escape_names(names: Iterable[str]) -> str:
    # Sort by length descending so overlapping names like ``qa`` and
    # ``qa.bot`` don't misroute: regex alternation is left-to-right
    # greedy, so the longer name must come first or ``@qa.bot task``
    # would bind to ``qa`` with ``.bot task`` as the task text.
    names_list = [n for n in names if n]
    names_list.sort(key=len, reverse=True)
    escaped = [re.escape(n) for n in names_list]
    return "|".join(escaped)


def detect_explicit_route(
    text: str,
    server_names: List[str],
) -> Optional[AcpExplicitRoute]:
    """Detect an explicit target-server prefix in *text*.

    Recognised forms (deterministic only):

    1. ``@server-name <task>``
    2. ``server-name: <task>``
    3. ``让 server-name <task>`` / ``请 server-name <task>`` (v1.1)

    Returns ``None`` when no known server name is matched.

    Ambiguity handling:
      - If the text matches multiple different servers with the same
        syntax, we return ``AcpExplicitRoute(server="", ...)`` sentinel
        is NOT used — instead we return ``None`` and let the caller
        treat it as ambiguous via a separate helper.  In this module we
        keep the surface small: the first deterministic match wins per
        syntax, and only one syntax is tried at a time.

    Args:
        text: Raw user input (already stripped of surrounding whitespace
            by the caller).
        server_names: Configured ACP server names.  Order-insensitive.

    Returns:
        An :class:`AcpExplicitRoute` or ``None``.
    """
    if not text or not server_names:
        return None

    stripped = text.lstrip()
    if not stripped:
        return None

    alt = _escape_names(server_names)
    if not alt:
        return None

    # 1) @server-name <task>
    m = re.match(rf"@({alt})(?:\s+|$)(.*)", stripped, flags=re.DOTALL)
    if m:
        name = m.group(1)
        task = (m.group(2) or "").strip()
        prefix = stripped[: m.start(2)] if m.group(2) else stripped
        return AcpExplicitRoute(
            server=name,
            task=task,
            syntax="at_mention",
            raw_prefix=prefix.rstrip(),
        )

    # 2) server-name: <task>
    m = re.match(rf"({alt})\s*[:：]\s*(.*)", stripped, flags=re.DOTALL)
    if m:
        name = m.group(1)
        task = (m.group(2) or "").strip()
        prefix = stripped[: m.start(2)] if m.group(2) else stripped
        return AcpExplicitRoute(
            server=name,
            task=task,
            syntax="colon",
            raw_prefix=prefix.rstrip(),
        )

    # 3) 让 / 请 server-name <task>
    # Require ≥1 whitespace after the verb so ``请问...`` / ``让我...``
    # (ordinary Chinese prose) cannot match, even when followed by a
    # configured server name.
    verb_alt = "|".join(_ZH_VERBS)
    m = re.match(
        rf"({verb_alt})\s+({alt})(?:\s+|$)(.*)",
        stripped,
        flags=re.DOTALL,
    )
    if m:
        name = m.group(2)
        task = (m.group(3) or "").strip()
        prefix = stripped[: m.start(3)] if m.group(3) else stripped
        return AcpExplicitRoute(
            server=name,
            task=task,
            syntax="zh_explicit",
            raw_prefix=prefix.rstrip(),
        )

    return None
