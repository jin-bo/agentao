"""Read a config file without blocking on something that is not a file.

``Path.read_text`` on a named pipe waits for a writer, so a FIFO at
``.agentao/skills_config.json`` hung startup. ``is_file()`` in front of the
read avoids that, but then a directory or a pipe reads as a missing file
and the reader's own failure policy (warn, fail closed, refuse to write)
never runs.

:func:`read_config_bytes` opens non-blocking, checks the open descriptor,
and raises :class:`NotARegularFileError` — an ``OSError`` — for anything but
a regular file, so every reader's existing ``except OSError`` reports it the
way it reports an unreadable file. A missing path returns ``None``.

A leaf: imports nothing from ``agentao``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional, Union

# Windows has no ``O_NONBLOCK`` and no FIFOs a path can name; ``os.open`` on a
# directory there raises ``PermissionError``, which is still an ``OSError``.
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class NotARegularFileError(OSError):
    """The path exists but is a directory, a pipe, a socket or a device."""


def read_config_bytes(path: Union[str, Path]) -> Optional[bytes]:
    """The file's bytes, or ``None`` when the path does not exist.

    Raises :class:`NotARegularFileError` for anything but a regular file, and
    any other ``OSError`` the open or read raises (``PermissionError``, …).
    """
    try:
        fd = os.open(path, os.O_RDONLY | _O_NONBLOCK)
    except (FileNotFoundError, NotADirectoryError):
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotARegularFileError("not a regular file")
        f = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with f:
        return f.read()


def read_config_text(path: Union[str, Path]) -> Optional[str]:
    """:func:`read_config_bytes` decoded as ``utf-8-sig``.

    ``utf-8-sig`` so a BOM'd file loads. A file that is not UTF-8 raises
    ``UnicodeDecodeError`` (a ``ValueError``, not an ``OSError``), as
    ``Path.read_text`` does.
    """
    data = read_config_bytes(path)
    return None if data is None else data.decode("utf-8-sig")
