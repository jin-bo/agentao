"""``json.loads`` / ``json.load`` that report every malformed document as
:class:`json.JSONDecodeError`.

Since Python 3.11 (backported to 3.10.7), converting an integer literal
longer than ``sys.get_int_max_str_digits()`` (4300 by default) raises a
plain :class:`ValueError` from inside the decoder, not ``JSONDecodeError``
(#495). A handler written as ``except json.JSONDecodeError`` lets that one
input through: a settings file with ``"max_instances": 1111…`` crashed
startup and ``agentao doctor``, a model tool call carrying one raised out
of argument canonicalisation, and an ACP line holding one got no reply.

Catching ``ValueError`` at each site instead would also swallow unrelated
errors raised elsewhere in the same ``try``, and handlers that read
``exc.lineno`` would lose it. These wrappers convert the limit error into a
``JSONDecodeError`` pointing at the offending literal, so every existing
handler applies unchanged. The limit itself is not raised: it is CPython's
guard against quadratic-time conversion, and agentao is often embedded in a
host process whose global setting is not ours to change.

A :class:`UnicodeDecodeError` (``json.loads`` given undecodable bytes) is
re-raised as is: it is also a ``ValueError``, and several callers handle it
in a clause of its own. Importing this module is cheap (standard library
only).
"""

from __future__ import annotations

import json
import re
import sys
from typing import IO, Any, Union


def _too_long_error(doc: str, exc: ValueError) -> json.JSONDecodeError:
    limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    pos = 0
    if limit:
        match = re.search(r"\d{%d,}" % (limit + 1), doc)
        if match is not None:
            pos = match.start()
    msg = (
        f"Integer literal longer than {limit} digits"
        if limit
        else f"Number could not be converted ({exc})"
    )
    return json.JSONDecodeError(msg, doc, pos)


def loads(s: Union[str, bytes, bytearray], **kwargs: Any) -> Any:
    """:func:`json.loads`, raising ``JSONDecodeError`` for an oversized integer."""
    try:
        return json.loads(s, **kwargs)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise
    except ValueError as exc:
        doc = s if isinstance(s, str) else bytes(s).decode("utf-8", "replace")
        raise _too_long_error(doc, exc) from exc


def load(fp: IO[str], **kwargs: Any) -> Any:
    """:func:`json.load`, raising ``JSONDecodeError`` for an oversized integer."""
    return loads(fp.read(), **kwargs)
