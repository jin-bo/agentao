"""MCP server configuration loading and env var expansion."""

import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)


McpServerConfig = Dict[str, Any]
"""
Expected keys per server:
  # Stdio transport
  command: str           — executable to spawn
  args: list[str]        — command-line arguments
  env: dict[str,str]     — extra env vars (supports $VAR / ${VAR})
  cwd: str               — working directory

  # SSE transport
  url: str               — SSE endpoint URL
  headers: dict[str,str] — HTTP headers

  # Common
  timeout: int | dict    — see resolve_timeouts(); int = connect/startup
                           seconds (default 60), or {startup, request}
                           to also bound each tool call after init.
  trust: bool            — skip confirmation if True
"""

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

_DEFAULT_STARTUP_TIMEOUT = 60.0

_TIMEOUT_KEYS = ("startup", "request")


def _positive_or(value: Any, default: Optional[float]) -> Optional[float]:
    """Return ``value`` as a finite positive float, else ``default``.

    Rejects (returns ``default`` for):
    - ``bool`` — it is an ``int`` subclass, so ``True`` would otherwise sail
      through as ``1.0``;
    - non-numeric types and non-positive numbers;
    - non-finite floats (``inf`` / ``nan`` — ``json.loads`` parses the
      ``Infinity`` / ``NaN`` literals) and integers so large that
      ``float()`` overflows. Either would later blow up ``timedelta()``;
      catching them here keeps :func:`resolve_timeouts` total (never raises).
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)) and value > 0:
        try:
            result = float(value)
        except (OverflowError, ValueError):
            return default
        return result if math.isfinite(result) else default
    return default


def _coerce_timeout(value: Any, default: Optional[float], label: str) -> Optional[float]:
    """Resolve one timeout slot, warning when a *present* value is unusable.

    A missing key (``value is None``) silently takes ``default`` — an unset
    timeout is normal, not a misconfiguration. But a present-but-invalid value
    (wrong type, non-positive, non-finite) is almost always a config mistake
    that would otherwise vanish without trace, so it is logged.
    """
    if value is None:
        return default
    resolved = _positive_or(value, None)
    if resolved is None:
        _logger.warning(
            "MCP 'timeout.%s': ignoring invalid value %r (need a positive finite "
            "number); falling back to %s.",
            label,
            value,
            f"{default:g}s" if default is not None else "no per-request limit",
        )
        return default
    return resolved


def resolve_timeouts(config: McpServerConfig) -> tuple[float, Optional[float]]:
    """Resolve a server's ``timeout`` into ``(startup, request)`` seconds.

    ``timeout`` accepts two forms:

    * an ``int`` / ``float`` — the legacy form. Bounds the *connection /
      startup* phase (it is handed to the SSE transport and bounds the
      ``initialize()`` / ``list_tools()`` handshake); per-request tool calls
      stay unbounded (the MCP SDK default). Fully backward compatible.
    * an object ``{"startup": int, "request": int}`` — both keys optional.
      ``startup`` bounds connection/initialization (default 60 s);
      ``request`` bounds each tool call *after* init (``None`` = unbounded).

    Note: agentao's legacy ``timeout`` governs the *connect* phase (it is
    passed to ``sse_client``), so a legacy int maps to ``startup`` — the
    opposite of opencode #33977, whose legacy timeout meant *request*. The
    architectural difference is deliberate; don't "fix" it to match.

    Never raises: malformed / non-positive / non-finite values fall back to
    the defaults (``startup`` → 60 s, ``request`` → unbounded) and are logged
    via :func:`_coerce_timeout`. ``startup`` is therefore always a finite
    positive float; ``request`` is that or ``None``.
    """
    raw = config.get("timeout")
    if isinstance(raw, dict):
        unknown = sorted(set(raw) - set(_TIMEOUT_KEYS))
        if unknown:
            _logger.warning(
                "MCP 'timeout': ignoring unknown key(s) %s (expected 'startup' "
                "and/or 'request').",
                unknown,
            )
        startup = _coerce_timeout(raw.get("startup"), _DEFAULT_STARTUP_TIMEOUT, "startup")
        return startup, _coerce_timeout(raw.get("request"), None, "request")
    if raw is None:
        # Absent (or explicit null) → default startup, unbounded request.
        return _DEFAULT_STARTUP_TIMEOUT, None
    return _coerce_timeout(raw, _DEFAULT_STARTUP_TIMEOUT, "timeout"), None


def expand_env_vars(value: str) -> str:
    """Replace $VAR and ${VAR} references with environment values."""
    def _replace(m: re.Match) -> str:
        var_name = m.group(1) or m.group(2)
        return os.environ.get(var_name, "")
    return _ENV_VAR_RE.sub(_replace, value)


def _expand_config_env(config: McpServerConfig) -> McpServerConfig:
    """Expand env vars in a server config's string fields."""
    result = dict(config)

    # Expand env dict values
    if "env" in result and isinstance(result["env"], dict):
        result["env"] = {k: expand_env_vars(v) for k, v in result["env"].items()}

    # Expand header values
    if "headers" in result and isinstance(result["headers"], dict):
        result["headers"] = {k: expand_env_vars(v) for k, v in result["headers"].items()}

    # Expand command args
    if "args" in result and isinstance(result["args"], list):
        result["args"] = [expand_env_vars(a) for a in result["args"]]

    return result


def _load_json_file(path: Path) -> Dict[str, Any]:
    """Load a JSON file, returning empty dict if missing or invalid."""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_mcp_config(
    *,
    project_root: Path,
    user_root: Optional[Path] = None,
) -> Dict[str, McpServerConfig]:
    """Load MCP server configs from user-scope and project-scope files.

    Merge policy is **add-only for project scope**: a project-level
    entry may declare a *new* server name, but it cannot override a
    user-level entry with the same name. Name collisions are resolved
    in favor of the user file and a warning is logged. This prevents a
    checked-in ``.agentao/mcp.json`` from silently redirecting a
    well-known server name (e.g. ``github``) to a different transport
    or endpoint.

    Environment variables in config values are expanded.

    Args:
        project_root: Project directory to resolve
            ``<project_root>/.agentao/mcp.json`` against. Required:
            the loader performs no implicit cwd resolution.
        user_root: Optional user-scope directory to resolve
            ``<user_root>/mcp.json`` against. ``None`` (the default)
            skips the user-scope read; pass an explicit path
            (typically ``~/.agentao``) to opt in.

    Returns:
        Dict mapping server name to its expanded config.
    """
    if project_root is None:
        raise TypeError(
            "load_mcp_config requires a project_root keyword argument."
        )
    project_path = project_root / ".agentao" / "mcp.json"

    project_cfg = _load_json_file(project_path)
    global_cfg: Dict[str, Any] = (
        _load_json_file(user_root / "mcp.json") if user_root is not None else {}
    )

    user_servers = global_cfg.get("mcpServers", {})
    if not isinstance(user_servers, dict):
        user_servers = {}
    project_servers = project_cfg.get("mcpServers", {})
    if not isinstance(project_servers, dict):
        project_servers = {}

    # User scope wins on name collision; project may only add new names.
    servers: Dict[str, McpServerConfig] = dict(user_servers)
    for name, cfg in project_servers.items():
        if name in servers:
            _logger.warning(
                "Project mcp.json entry %r collides with a user-scope "
                "server; ignoring (project cannot override user). Rename "
                "the project entry or remove the user-scope one.",
                name,
            )
            continue
        servers[name] = cfg

    # Expand env vars in each server config
    return {name: _expand_config_env(conf) for name, conf in servers.items()}


def save_mcp_config(
    servers: Dict[str, McpServerConfig],
    *,
    config_dir: Path,
) -> Path:
    """Save MCP server configs to ``<config_dir>/mcp.json``.

    Args:
        servers: Server configs to save.
        config_dir: Directory in which to write ``mcp.json``. Callers
            pass ``<working_directory>/.agentao`` for project scope or
            ``~/.agentao`` for user scope.

    Returns:
        Path to the saved config file.
    """
    if config_dir is None:
        raise TypeError(
            "save_mcp_config requires a config_dir keyword argument."
        )

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "mcp.json"

    # Load existing to preserve other keys
    existing = _load_json_file(config_path)
    existing["mcpServers"] = servers

    config_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config_path
