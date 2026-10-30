"""Which CI shard a test runs in (``--shard I/N``, see ``tests/conftest.py``)."""

from __future__ import annotations

import zlib


def parse_shard(spec: str) -> tuple[int, int]:
    """``"2/3"`` -> ``(2, 3)``. Raises ``ValueError`` unless ``1 <= I <= N``."""
    index_text, sep, count_text = spec.partition("/")
    if not sep:
        raise ValueError(f"--shard expects I/N, got {spec!r}")
    index, count = int(index_text), int(count_text)
    if not 1 <= index <= count:
        raise ValueError(f"--shard expects 1 <= I <= N, got {spec!r}")
    return index, count


def shard_of(nodeid: str, count: int) -> int:
    """The 1-based shard that owns ``nodeid``.

    A hash of the node id, not the test's position: every shard gets the same answer
    for a test whatever else it collected, so the shards cannot overlap or miss one.
    Per test rather than per file, because a few files hold most of the suite's time:
    by file, two shards split about 59/41; by test, about 49/51 (measured 2026-10-30).
    ``crc32``, not ``hash()``, which ``PYTHONHASHSEED`` changes per process.
    """
    return zlib.crc32(nodeid.encode("utf-8")) % count + 1
