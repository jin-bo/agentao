"""Flag parsing for ``/replays show`` and ``/replays tail``."""

from __future__ import annotations

from typing import Optional


class _ShowFlags:
    """Parsed flags for /replays show / tail."""

    __slots__ = ("raw", "turn", "kind", "errors", "rest")

    def __init__(self) -> None:
        self.raw: bool = False
        self.turn: Optional[str] = None
        self.kind: Optional[str] = None
        self.errors: bool = False
        self.rest: list = []


def _parse_show_flags(tokens: list) -> _ShowFlags:
    """Parse the tokens after ``<id>`` into a :class:`_ShowFlags`.

    Accepts both ``--flag value`` and ``--flag=value`` shapes. Unknown
    tokens land in ``flags.rest`` so ``tail`` can still consume its
    numeric argument.
    """
    flags = _ShowFlags()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--raw":
            flags.raw = True
            i += 1
        elif tok.startswith("--turn="):
            flags.turn = tok.split("=", 1)[1]
            i += 1
        elif tok == "--turn" and i + 1 < len(tokens):
            flags.turn = tokens[i + 1]
            i += 2
        elif tok.startswith("--kind="):
            flags.kind = tok.split("=", 1)[1]
            i += 1
        elif tok == "--kind" and i + 1 < len(tokens):
            flags.kind = tokens[i + 1]
            i += 2
        elif tok == "--errors":
            flags.errors = True
            i += 1
        else:
            flags.rest.append(tok)
            i += 1
    return flags
