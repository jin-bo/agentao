"""Test helper for constructing :class:`MemoryManager` with disk-backed stores.

After Issue #16, ``MemoryManager`` takes pre-built stores. The previous
``project_root=`` / ``global_root=`` shape is gone, so each test would
otherwise repeat 2-4 lines of ``SQLiteMemoryStore.open_or_memory(...)``
boilerplate. This helper keeps the per-test footprint to one line.

Production code constructs stores via the embedding factory
(``agentao.embedding.build_from_environment``), which performs the same
project-falls-back-to-memory / user-disabled-on-error contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from agentao.memory import MemoryManager, SQLiteMemoryStore


def make_memory_manager(
    tmp_path,
    *,
    with_user: bool = False,
    project_subdir: str = ".agentao",
    user_subdir: str = "global",
) -> MemoryManager:
    """Build a :class:`MemoryManager` rooted at ``tmp_path``.

    Args:
        tmp_path: pytest ``tmp_path`` fixture (or any writable Path / str).
        with_user: also create a user-scope store under ``tmp_path / user_subdir``.
            Defaults to ``False`` (project-scope only) — matches the most
            common pre-#16 ``global_root=None`` test shape.
        project_subdir: subdirectory under ``tmp_path`` for the project DB.
        user_subdir: subdirectory under ``tmp_path`` for the user DB.

    Returns:
        A fully-constructed :class:`MemoryManager`.
    """
    base = Path(tmp_path)
    project_store = SQLiteMemoryStore.open_or_memory(
        base / project_subdir / "memory.db"
    )
    user_store: Optional[SQLiteMemoryStore] = None
    if with_user:
        user_store = SQLiteMemoryStore.open(base / user_subdir / "memory.db")
    return MemoryManager(project_store=project_store, user_store=user_store)
