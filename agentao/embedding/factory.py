"""``build_from_environment()`` — the CLI-style auto-discovery factory.

Pulls in everything ``Agentao.__init__`` used to read implicitly:

- ``.env`` via :func:`dotenv.load_dotenv`
- ``LLM_PROVIDER`` and provider-prefixed env vars
- ``working_directory or Path.cwd()`` resolved to absolute
- ``<wd>/.agentao/permissions.json`` + ``~/.agentao/permissions.json``
- ``<wd>/.agentao/mcp.json`` + ``~/.agentao/mcp.json``
- memory roots (``<wd>/.agentao`` + ``~/.agentao``)

Then constructs subsystems explicitly and forwards them to
:class:`Agentao`. This factory is the single entry point that
touches the surrounding environment, so embedded hosts can construct
:class:`Agentao` directly with explicit injections instead.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from dotenv import load_dotenv

if TYPE_CHECKING:
    from ..agent import Agentao


def build_from_environment(
    working_directory: Optional[Path] = None,
    **overrides: Any,
) -> "Agentao":
    """Build an :class:`Agentao` instance from the surrounding environment.

    Args:
        working_directory: Project root used for ``.agentao/`` lookups.
            When ``None``, falls back to ``Path.cwd()``. The result is
            always resolved to an absolute path before forwarding to
            ``Agentao(working_directory=...)`` so the runtime is frozen
            (no later cwd-implicit reads).
        **overrides: Any keyword accepted by ``Agentao.__init__`` —
            takes priority over the values discovered from disk / env.
            ``llm_client``, ``permission_engine``, ``memory_manager``,
            ``skill_manager``, ``project_instructions``, ``mcp_manager``,
            ``filesystem``, ``shell``, ``transport``, ``logger``,
            ``temperature``, ``max_context_tokens``, ``plan_session``
            are all valid here.

    Returns:
        A fully-constructed :class:`Agentao` instance bound to
        ``working_directory``.
    """
    # Local imports keep the embedding package light — pulling
    # ``Agentao`` (and through it the LLM stack) at module import time
    # would defeat the point of having a thin entry surface.
    from ..agent import Agentao
    from ..memory import MemoryManager
    from ..permissions import PermissionEngine

    wd = (working_directory or Path.cwd()).expanduser().resolve()

    dotenv_path = wd / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path)
    else:
        load_dotenv()

    provider = os.getenv("LLM_PROVIDER", "OPENAI").strip().upper()
    discovered_api_key = os.getenv(f"{provider}_API_KEY")
    discovered_base_url = os.getenv(f"{provider}_BASE_URL")
    discovered_model = os.getenv(f"{provider}_MODEL")

    permission_engine = overrides.pop("permission_engine", None)
    if permission_engine is None:
        permission_engine = PermissionEngine(project_root=wd)

    memory_manager = overrides.pop("memory_manager", None)
    if memory_manager is None:
        memory_manager = MemoryManager(
            project_root=wd / ".agentao",
            global_root=Path.home() / ".agentao",
        )

    # When the caller supplied an ``llm_client``, do not surface the
    # factory-discovered raw provider kwargs — the constructor would
    # reject the combination as a programmer error.
    kwargs: Dict[str, Any] = dict(
        working_directory=wd,
        permission_engine=permission_engine,
        memory_manager=memory_manager,
    )
    if "llm_client" not in overrides:
        kwargs["api_key"] = discovered_api_key
        kwargs["base_url"] = discovered_base_url
        kwargs["model"] = discovered_model
    kwargs.update(overrides)

    return Agentao(**kwargs)
