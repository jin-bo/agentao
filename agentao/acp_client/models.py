"""Data models for ACP client configuration and runtime state.

Defines typed dataclasses for project-local ACP server configs loaded from
``<cwd>/.agentao/acp.json``.  All downstream modules (process manager,
JSON-RPC client, CLI) depend on these models rather than reading raw JSON.

Runtime state types (``ServerState``, ``AcpProcessInfo``) are used by
``process.py`` and ``manager.py`` to track per-server subprocess lifecycle.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


def _expand_env_value(value: str) -> str:
    """Expand ``$VAR`` / ``${VAR}`` references from the process environment.

    Returns the original string unchanged when the referenced variable is
    not set, so configuration errors surface loudly in the server's stderr
    (rather than silently injecting an empty string).
    """
    if not isinstance(value, str):
        return value
    if "$" not in value:
        return value
    expanded = os.path.expandvars(value)
    return expanded


class AcpConfigError(ValueError):
    """Raised when ACP client configuration is invalid."""


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------


class ServerState(str, Enum):
    """Lifecycle states for an ACP server subprocess.

    Transition diagram::

        configured ─► starting ─► initializing ─► ready ◄─► busy
             │            │              │           │        │
             │            ▼              ▼           ▼        ▼
             │         failed         failed      stopping  waiting_for_user
             │                                       │        │
             │                                    failed    busy / ready / failed
             ▼
          (restart loops back to starting)

    - ``configured``: config loaded, process not yet started.
    - ``starting``: ``subprocess.Popen`` called, waiting for process to appear.
    - ``initializing``: process running, ACP handshake in progress (Issue 03).
    - ``ready``: handshake complete, server accepts requests.
    - ``busy``: currently processing a request (Issue 04).
    - ``waiting_for_user``: server requested user interaction (Issue 10).
    - ``stopping``: graceful shutdown initiated.
    - ``stopped``: process exited cleanly.
    - ``failed``: process crashed or startup/handshake failed.
    """

    CONFIGURED = "configured"
    STARTING = "starting"
    INITIALIZING = "initializing"
    READY = "ready"
    BUSY = "busy"
    WAITING_FOR_USER = "waiting_for_user"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class AcpProcessInfo:
    """Observable snapshot of a running (or terminated) ACP server process.

    Used by CLI status display and diagnostics.  Updated by
    :class:`~agentao.acp_client.process.ACPProcessHandle` on every state
    transition.
    """

    state: ServerState = ServerState.CONFIGURED
    pid: Optional[int] = None
    last_error: Optional[str] = None
    last_activity: Optional[float] = None  # time.time() epoch

    def touch(self) -> None:
        """Update ``last_activity`` to now."""
        self.last_activity = time.time()


# Required fields and their expected Python types.
_REQUIRED_FIELDS: Dict[str, type] = {
    "command": str,
    "args": list,
    "env": dict,
    "cwd": str,
}

# camelCase JSON key → snake_case field name (optional fields only).
_OPTIONAL_FIELD_MAP: Dict[str, str] = {
    "autoStart": "auto_start",
    "startupTimeoutMs": "startup_timeout_ms",
    "requestTimeoutMs": "request_timeout_ms",
    "capabilities": "capabilities",
    "description": "description",
}


@dataclass
class AcpServerConfig:
    """Configuration for a single ACP server.

    Required fields mirror the JSON schema: ``command``, ``args``, ``env``,
    ``cwd``.  Optional fields use sensible defaults when omitted.
    """

    command: str
    args: List[str]
    env: Dict[str, str]
    cwd: str  # Always stored as an absolute path after resolution.

    auto_start: bool = True
    startup_timeout_ms: int = 10_000
    request_timeout_ms: int = 60_000
    capabilities: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(
        cls,
        name: str,
        raw: dict,
        project_root: Path,
    ) -> "AcpServerConfig":
        """Parse and validate a server config dict.

        Args:
            name: Server name (used in error messages).
            raw: Raw dict from the ``servers`` map in ``acp.json``.
            project_root: Project root directory for resolving relative ``cwd``.

        Returns:
            A validated ``AcpServerConfig``.

        Raises:
            AcpConfigError: On missing/invalid required fields or bad types.
        """
        if not isinstance(raw, dict):
            raise AcpConfigError(
                f"server '{name}': expected a config object, got {type(raw).__name__}"
            )

        # --- Validate required fields ---
        missing: List[str] = []
        type_errors: List[str] = []

        for field_name, expected_type in _REQUIRED_FIELDS.items():
            if field_name not in raw:
                missing.append(field_name)
            elif not isinstance(raw[field_name], expected_type):
                type_errors.append(
                    f"'{field_name}' must be {expected_type.__name__}, "
                    f"got {type(raw[field_name]).__name__}"
                )

        errors: List[str] = []
        if missing:
            errors.append(f"missing required field(s): {', '.join(missing)}")
        if type_errors:
            errors.extend(type_errors)

        if errors:
            joined = "; ".join(errors)
            raise AcpConfigError(f"server '{name}': {joined}")

        # --- Resolve cwd ---
        raw_cwd = raw["cwd"]
        cwd_path = Path(raw_cwd)
        if not cwd_path.is_absolute():
            cwd_path = (project_root / cwd_path).resolve()
        resolved_cwd = str(cwd_path)

        # --- Extract optional fields ---
        auto_start = raw.get("autoStart", True)
        startup_timeout_ms = raw.get("startupTimeoutMs", 10_000)
        request_timeout_ms = raw.get("requestTimeoutMs", 60_000)
        capabilities = raw.get("capabilities", {})
        description = raw.get("description", "")

        # Expand $VAR / ${VAR} references in env values.
        expanded_env = {
            k: _expand_env_value(v) if isinstance(v, str) else v
            for k, v in raw["env"].items()
        }

        return cls(
            command=raw["command"],
            args=raw["args"],
            env=expanded_env,
            cwd=resolved_cwd,
            auto_start=auto_start,
            startup_timeout_ms=startup_timeout_ms,
            request_timeout_ms=request_timeout_ms,
            capabilities=capabilities,
            description=description,
        )


@dataclass
class AcpClientConfig:
    """Top-level ACP client configuration.

    Wraps the ``servers`` map from ``acp.json``.
    """

    servers: Dict[str, AcpServerConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict, project_root: Path) -> "AcpClientConfig":
        """Parse and validate the top-level config dict.

        Args:
            raw: Parsed JSON from ``acp.json``.
            project_root: Project root for resolving relative paths.

        Returns:
            A validated ``AcpClientConfig``.

        Raises:
            AcpConfigError: On structural errors or per-server validation failures.
        """
        if not isinstance(raw, dict):
            raise AcpConfigError(
                f"expected a JSON object at top level, got {type(raw).__name__}"
            )

        servers_raw = raw.get("servers", {})
        if not isinstance(servers_raw, dict):
            raise AcpConfigError(
                f"'servers' must be an object, got {type(servers_raw).__name__}"
            )

        servers: Dict[str, AcpServerConfig] = {}
        for name, server_raw in servers_raw.items():
            servers[name] = AcpServerConfig.from_dict(name, server_raw, project_root)

        return cls(servers=servers)
