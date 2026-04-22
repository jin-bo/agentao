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
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional


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


@dataclass(frozen=True)
class ServerStatus:
    """Typed status snapshot for a single ACP server.

    Returned by :meth:`ACPManager.get_status`. ``state`` is the primary
    readiness signal; prefer :meth:`ACPManager.readiness` for a typed
    classification. ``last_error`` / ``last_error_at`` are secondary
    diagnostics that persist across successful turns until overwritten or
    cleared via :meth:`ACPManager.reset_last_error`.

    ``last_error_at`` is a ``datetime`` with ``tzinfo=timezone.utc``,
    assigned at the moment the error is stored on the manager.
    """

    server: str
    state: str
    pid: Optional[int]
    has_active_turn: bool
    active_session_id: Optional[str] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None
    inbox_pending: int = 0
    interaction_pending: int = 0
    config_warnings: List[str] = field(default_factory=list)

    # Backward-compat dict-style access for callers that used the old
    # List[Dict[str, Any]] return type of get_status().
    _LEGACY_ALIASES: ClassVar[Dict[str, str]] = {
        "name": "server",
        "interactions_pending": "interaction_pending",
    }
    _REMOVED_KEYS: ClassVar[Dict[str, Any]] = {
        "description": None,
        "last_activity": None,
        "stderr_lines": 0,
    }

    def __getitem__(self, key: str) -> Any:
        mapped = self._LEGACY_ALIASES.get(key)
        if mapped is not None:
            return getattr(self, mapped)
        if key in self._REMOVED_KEYS:
            return self._REMOVED_KEYS[key]
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return (
            key in self._LEGACY_ALIASES
            or key in self._REMOVED_KEYS
            or hasattr(self, key)
        )


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
    "nonInteractivePolicy": "non_interactive_policy",
    "maxRecoverableRestarts": "max_recoverable_restarts",
}

# Valid values for :class:`InteractionPolicy.mode`. Accepted both in
# ``.agentao/acp.json`` (as the ``mode`` field inside a structured
# ``nonInteractivePolicy`` object) and as a per-call string override.
INTERACTION_POLICY_MODES = frozenset({"reject_all", "accept_all"})


@dataclass(frozen=True)
class InteractionPolicy:
    """Non-interactive interaction policy (Week 3 minimal model).

    This is the single-dimension policy object that governs how a
    non-interactive turn responds to server-initiated
    ``session/request_permission`` and ``_agentao.cn/ask_user`` requests.

    - ``mode="reject_all"`` — auto-reject every such request; the turn
      ultimately surfaces as :class:`AcpInteractionRequiredError`.
    - ``mode="accept_all"`` — auto-approve every permission request
      that carries an allow-flavored option; ``ask_user`` still errors
      because there is no user to answer.

    The policy intentionally has no other knobs (no per-tool split, no
    timeout layer). When a second dimension becomes necessary, wrap
    this dataclass in a dedicated options object — do not grow this
    one beyond ``mode``.
    """

    mode: str  # Literal["reject_all", "accept_all"]

    def __post_init__(self) -> None:
        if self.mode not in INTERACTION_POLICY_MODES:
            raise AcpConfigError(
                f"InteractionPolicy.mode must be one of "
                f"{sorted(INTERACTION_POLICY_MODES)}, got {self.mode!r}"
            )


def classify_process_death(
    *,
    exit_code: Optional[int],
    signaled: bool,
    during_active_turn: bool,
    restart_count: int,
    max_recoverable_restarts: int,
    handshake_fail_streak: int = 0,
) -> str:
    """Classify an ACP subprocess death as recoverable or fatal (Week 4).

    Pure function — exposed for readability and direct unit testing.
    The return value is ``"recoverable"`` or ``"fatal"``. A recoverable
    death means the manager may (at most once per call site) rebuild
    the client / session on the next call; a fatal death means the
    server stays in its terminal state until an explicit
    :meth:`ACPManager.restart_server` clears it.

    Decision matrix (aligned with ``docs/HEADLESS_RUNTIME_ISSUES.md``
    Issue 16):

    - Signal-terminated process (``signaled=True``, typically
      OOM / SIGKILL / 128+signo) → ``fatal``. Avoids respawn storms on
      resource pressure or operator kills.
    - Two or more consecutive handshake failures after restart
      (``handshake_fail_streak >= 2``) → ``fatal``. This is a
      configuration / environment problem, not a transient fault. A
      single prior failure (streak ``== 1``) must not combine with an
      *unrelated* later idle/normal exit into sticky-fatal — the
      contract is strict pair of consecutive handshake failures.
    - Normal exit (``exit_code == 0``) during active turn → ``recoverable``.
      The current turn fails with ``last_error``; the next call
      rebuilds.
    - Normal exit while idle → ``recoverable`` (no-op on last_error;
      lazy restart on next call).
    - Non-zero exit while idle → ``recoverable`` up to the cap; beyond
      that → ``fatal``.
    - Stdio EOF / broken pipe with ``exit_code is None`` and the
      process still running → ``recoverable``; the caller is expected
      to rebuild the *client* without respawning the subprocess.
    """
    if signaled:
        return "fatal"
    if handshake_fail_streak >= 2:
        return "fatal"
    if exit_code is None:
        # Transport broke without the process dying.
        return "recoverable"
    if exit_code == 0:
        return "recoverable"
    if during_active_turn:
        # Active-turn deaths bypass the cap — the turn itself has
        # already failed, and the next caller should be allowed one
        # attempt to rebuild.
        return "recoverable"
    if restart_count >= max_recoverable_restarts:
        return "fatal"
    return "recoverable"


def _parse_non_interactive_policy(
    raw: Any, name: str,
) -> InteractionPolicy:
    """Parse the ``nonInteractivePolicy`` field of a server config.

    Week 3 accepts **only** the structured object form ``{"mode": ...}``.
    The legacy string form (``"reject_all"`` / ``"accept_all"``) is
    rejected at config load time with a migration-oriented message —
    no silent upgrade.
    """
    if raw is None:
        return InteractionPolicy(mode="reject_all")
    if isinstance(raw, str):
        raise AcpConfigError(
            f"server '{name}': 'nonInteractivePolicy' as a bare string "
            f"({raw!r}) is no longer supported. Migrate to the "
            f"structured object form:\n"
            f"    \"nonInteractivePolicy\": {{\"mode\": \"{raw}\"}}\n"
            f"See developer-guide/*/appendix/e-migration for the "
            f"full migration example."
        )
    if not isinstance(raw, dict):
        raise AcpConfigError(
            f"server '{name}': 'nonInteractivePolicy' must be an object "
            f"with a 'mode' field, got {type(raw).__name__}"
        )
    if "mode" not in raw:
        raise AcpConfigError(
            f"server '{name}': 'nonInteractivePolicy' object is missing "
            f"required field 'mode'"
        )
    mode = raw["mode"]
    if not isinstance(mode, str):
        raise AcpConfigError(
            f"server '{name}': 'nonInteractivePolicy.mode' must be a "
            f"string, got {type(mode).__name__}"
        )
    if mode not in INTERACTION_POLICY_MODES:
        raise AcpConfigError(
            f"server '{name}': 'nonInteractivePolicy.mode' must be one "
            f"of {sorted(INTERACTION_POLICY_MODES)}, got {mode!r}"
        )
    return InteractionPolicy(mode=mode)


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
    non_interactive_policy: InteractionPolicy = field(
        default_factory=lambda: InteractionPolicy(mode="reject_all"),
    )
    # Week 4 Issue 16 — cap for auto-recovered restarts after a
    # recoverable client/process death. Reset on the first successful
    # turn. Exceeding the cap flips the server into the fatal state
    # until an explicit ``restart_server(name)`` call clears it.
    max_recoverable_restarts: int = 3

    def __post_init__(self) -> None:
        restarts = self.max_recoverable_restarts
        if not isinstance(restarts, int) or isinstance(restarts, bool):
            raise AcpConfigError(
                f"AcpServerConfig.max_recoverable_restarts must be int, "
                f"got {type(restarts).__name__}"
            )
        if restarts < 0:
            raise AcpConfigError(
                f"AcpServerConfig.max_recoverable_restarts must be >= 0, "
                f"got {restarts}"
            )

        # Public, programmatic callers may pass a bare string (the field
        # used to be ``str``) or the documented dict form
        # ``{"mode": "accept_all"}`` that mirrors the JSON schema.
        # Normalize both to :class:`InteractionPolicy` here so reads of
        # ``server_cfg.non_interactive_policy.mode`` at send_prompt time
        # can never hit ``AttributeError``. Reject any other shape
        # eagerly — config errors must surface at construction, not on
        # the first non-interactive permission request.
        policy = self.non_interactive_policy
        if isinstance(policy, InteractionPolicy):
            return
        if isinstance(policy, str):
            if policy not in INTERACTION_POLICY_MODES:
                raise AcpConfigError(
                    f"AcpServerConfig.non_interactive_policy must be an "
                    f"InteractionPolicy or one of "
                    f"{sorted(INTERACTION_POLICY_MODES)}, got {policy!r}"
                )
            self.non_interactive_policy = InteractionPolicy(mode=policy)
            return
        if isinstance(policy, dict):
            if "mode" not in policy:
                raise AcpConfigError(
                    f"AcpServerConfig.non_interactive_policy dict is "
                    f"missing required field 'mode'; got {policy!r}"
                )
            mode = policy["mode"]
            if not isinstance(mode, str) or mode not in INTERACTION_POLICY_MODES:
                raise AcpConfigError(
                    f"AcpServerConfig.non_interactive_policy['mode'] "
                    f"must be one of {sorted(INTERACTION_POLICY_MODES)}, "
                    f"got {mode!r}"
                )
            self.non_interactive_policy = InteractionPolicy(mode=mode)
            return
        raise AcpConfigError(
            f"AcpServerConfig.non_interactive_policy must be an "
            f"InteractionPolicy, a dict with a 'mode' field, or one of "
            f"{sorted(INTERACTION_POLICY_MODES)}; got "
            f"{type(policy).__name__}"
        )

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
        non_interactive_policy = _parse_non_interactive_policy(
            raw.get("nonInteractivePolicy"), name,
        )
        max_recoverable_restarts = raw.get("maxRecoverableRestarts", 3)
        if not isinstance(max_recoverable_restarts, int) or isinstance(
            max_recoverable_restarts, bool,
        ):
            raise AcpConfigError(
                f"server '{name}': 'maxRecoverableRestarts' must be int, "
                f"got {type(max_recoverable_restarts).__name__}"
            )
        if max_recoverable_restarts < 0:
            raise AcpConfigError(
                f"server '{name}': 'maxRecoverableRestarts' must be >= 0, "
                f"got {max_recoverable_restarts}"
            )

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
            non_interactive_policy=non_interactive_policy,
            max_recoverable_restarts=max_recoverable_restarts,
        )


@dataclass
class PromptResult:
    """Typed result of a single ACP prompt turn.

    Returned by :meth:`ACPManager.prompt_once` (Phase 3). The shape is
    frozen here in Phase 1 so the public type surface stabilizes before
    the one-shot helper lands.
    """

    stop_reason: str
    raw: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    cwd: Optional[str] = None


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
