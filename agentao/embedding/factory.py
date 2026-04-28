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


def discover_llm_kwargs() -> Dict[str, Any]:
    """Resolve the LLM kwargs from environment variables.

    Reads ``LLM_PROVIDER`` (default ``OPENAI``) and the provider-prefixed
    ``{PROVIDER}_API_KEY`` / ``{PROVIDER}_BASE_URL`` /
    ``{PROVIDER}_MODEL``, plus the provider-agnostic ``LLM_TEMPERATURE``
    and ``LLM_MAX_TOKENS``. Missing values are omitted from the returned
    dict so the caller can ``setdefault`` / merge without colliding with
    explicit ``None`` overrides.

    Test code that wants to mirror the factory's contract (e.g. the
    suite's autouse credential-stub fixture) should call this rather
    than re-implementing the prefix scheme.
    """
    provider = os.getenv("LLM_PROVIDER", "OPENAI").strip().upper()
    out: Dict[str, Any] = {}
    if (v := os.getenv(f"{provider}_API_KEY")) is not None:
        out["api_key"] = v
    if (v := os.getenv(f"{provider}_BASE_URL")) is not None:
        out["base_url"] = v
    if (v := os.getenv(f"{provider}_MODEL")) is not None:
        out["model"] = v
    if (v := os.getenv("LLM_TEMPERATURE")) is not None:
        out["temperature"] = float(v)
    if (v := os.getenv("LLM_MAX_TOKENS")) is not None:
        out["max_tokens"] = int(v)
    return out


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
    from ..agents.bg_store import BackgroundTaskStore
    from ..memory import MemoryManager
    from ..paths import user_root
    from ..permissions import PermissionEngine
    from ..replay import load_replay_config
    from ..sandbox import SandboxPolicy

    wd = (working_directory or Path.cwd()).expanduser().resolve()

    dotenv_path = wd / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path)
    else:
        load_dotenv()

    # Skip env-driven LLM discovery when the caller supplies a pre-built
    # ``llm_client``: those env values are unused on that path, and a
    # malformed ``LLM_TEMPERATURE`` / ``LLM_MAX_TOKENS`` would otherwise
    # raise here even though the values are about to be discarded.
    discovered_llm = (
        discover_llm_kwargs() if "llm_client" not in overrides else {}
    )

    permission_engine = overrides.pop("permission_engine", None)
    if permission_engine is None:
        permission_engine = PermissionEngine(
            project_root=wd,
            user_root=user_root(),
        )

    memory_manager = overrides.pop("memory_manager", None)
    if memory_manager is None:
        memory_manager = MemoryManager(
            project_root=wd / ".agentao",
            global_root=user_root(),
        )

    # Wire CLI defaults for the opt-in subsystems. Caller can disable
    # any of them by passing an explicit ``None`` — the ``in overrides``
    # check sees the key, skips the default, and forwards ``None``.
    if "bg_store" not in overrides:
        overrides["bg_store"] = BackgroundTaskStore(persistence_dir=wd)
    if "sandbox_policy" not in overrides:
        overrides["sandbox_policy"] = SandboxPolicy(project_root=wd)
    if "replay_config" not in overrides:
        # Best-effort: a missing/malformed replay config must not abort
        # session startup.
        try:
            overrides["replay_config"] = load_replay_config(wd)
        except Exception:
            pass

    # When the caller supplied an ``llm_client``, do not surface the
    # factory-discovered raw provider kwargs — the constructor would
    # reject the combination as a programmer error.
    kwargs: Dict[str, Any] = dict(
        working_directory=wd,
        permission_engine=permission_engine,
        memory_manager=memory_manager,
    )
    if "llm_client" not in overrides:
        kwargs.update(discovered_llm)
    kwargs.update(overrides)

    return Agentao(**kwargs)
