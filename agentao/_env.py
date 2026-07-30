"""Hygiene wrapper for python-dotenv that strips NUL bytes from values.

Pasted API keys occasionally carry embedded NUL bytes that crash
``os.environ[k] = v`` with ``ValueError: embedded null byte`` — which
in turn prevents agentao from starting at all. Borrowed from
hermes-agent commit 75643a615.

Use :func:`safe_load_dotenv` in place of ``dotenv.load_dotenv``; the
no-override default of ``load_dotenv`` is preserved.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

from dotenv import dotenv_values, find_dotenv


def safe_load_dotenv(dotenv_path: Optional[Union[str, Path]] = None) -> None:
    """``load_dotenv`` variant that scrubs NUL bytes from values.

    ``dotenv_values`` parses the file in pure Python without touching
    ``os.environ``, so it does not raise on NUL-containing values. We
    then strip NULs and assign, preserving the default no-override
    behavior of ``load_dotenv`` — with one deliberate exception.

    **Present-but-empty is treated as absent.** A plain ``setdefault``
    lets an empty string in the ambient environment permanently mask the
    real value in ``.env``: the key *is* set, so no-override declines to
    write, and every downstream ``os.getenv`` sees ``""``. That is not
    hypothetical — Claude Code injects ``ANTHROPIC_API_KEY=""`` into
    child processes to neuter their LLM calls, so any agentao invoked
    from a Claude Code session fails with "no API key" while a valid key
    sits unread in ``.env``. An empty or whitespace-only value carries no
    configuration meaning, so letting ``.env`` fill it in loses nothing
    and unbreaks that case. A non-empty ambient value still wins, which
    is the part of no-override callers actually rely on.
    """
    path = str(dotenv_path) if dotenv_path is not None else find_dotenv(usecwd=True)
    if not path:
        return
    for key, value in dotenv_values(path).items():
        if value is None:
            continue
        if os.environ.get(key, "").strip():
            continue  # a real ambient value wins (load_dotenv's no-override)
        os.environ[key] = value.replace("\x00", "")
