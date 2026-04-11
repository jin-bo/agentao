"""ACP client configuration loader.

Reads ``<project_root>/.agentao/acp.json`` and returns a validated
:class:`~agentao.acp_client.models.AcpClientConfig`.  v1 only supports
project-level configuration — no global fallback, no parent-directory
traversal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import AcpClientConfig, AcpConfigError


def load_acp_client_config(
    project_root: Optional[Path] = None,
) -> AcpClientConfig:
    """Load and validate ACP client config from ``.agentao/acp.json``.

    Args:
        project_root: Directory containing the ``.agentao/`` folder.
            Defaults to ``Path.cwd()`` when ``None``.

    Returns:
        Validated :class:`AcpClientConfig`.  If the config file does not
        exist, returns an empty config (no servers).

    Raises:
        AcpConfigError: On invalid JSON, unreadable file, or schema
            validation failure.
    """
    root = project_root if project_root is not None else Path.cwd()
    config_path = root / ".agentao" / "acp.json"

    if not config_path.is_file():
        return AcpClientConfig()

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AcpConfigError(f"cannot read {config_path}: {exc}") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AcpConfigError(f"invalid JSON in {config_path}: {exc}") from exc

    return AcpClientConfig.from_dict(parsed, project_root=root)
