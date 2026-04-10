"""MCP server configuration loading and env var expansion."""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


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
  timeout: int           — seconds (default 60)
  trust: bool            — skip confirmation if True
"""

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


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


def load_mcp_config(*, project_root: Optional[Path] = None) -> Dict[str, McpServerConfig]:
    """Load MCP server configs from global (~/.agentao/mcp.json) and project (.agentao/mcp.json).

    Project-level configs override global ones for the same server name.
    Environment variables in config values are expanded.

    Args:
        project_root: Optional project directory to resolve the project-level
            ``.agentao/mcp.json`` against. When ``None``, falls back to
            ``Path.cwd()`` (legacy CLI behavior). ACP sessions pass the
            session's cwd so two sessions in different directories see
            independent MCP configuration (Issue 05).

    Returns:
        Dict mapping server name to its expanded config.
    """
    global_path = Path.home() / ".agentao" / "mcp.json"
    project_cwd = project_root if project_root is not None else Path.cwd()
    project_path = project_cwd / ".agentao" / "mcp.json"

    global_cfg = _load_json_file(global_path)
    project_cfg = _load_json_file(project_path)

    # Merge: project overrides global
    servers: Dict[str, McpServerConfig] = {}
    for cfg in (global_cfg, project_cfg):
        mcp_servers = cfg.get("mcpServers", {})
        if isinstance(mcp_servers, dict):
            servers.update(mcp_servers)

    # Expand env vars in each server config
    return {name: _expand_config_env(conf) for name, conf in servers.items()}


def save_mcp_config(servers: Dict[str, McpServerConfig], *, global_config: bool = False) -> Path:
    """Save MCP server configs to the project or global config file.

    Args:
        servers: Server configs to save.
        global_config: If True, save to ~/.agentao/mcp.json; otherwise .agentao/mcp.json.

    Returns:
        Path to the saved config file.
    """
    if global_config:
        config_dir = Path.home() / ".agentao"
    else:
        config_dir = Path.cwd() / ".agentao"

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "mcp.json"

    # Load existing to preserve other keys
    existing = _load_json_file(config_path)
    existing["mcpServers"] = servers

    config_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config_path
