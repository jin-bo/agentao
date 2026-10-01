"""ACP client configuration loader.

Reads ``<project_root>/.agentao/acp.json`` and returns a validated
:class:`~agentao.acp_client.models.AcpClientConfig`.  v1 only supports
project-level configuration — no global fallback, no parent-directory
traversal.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .models import AcpClientConfig, AcpConfigError, AcpServerConfig


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
        text = config_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        # Subclasses ValueError, not OSError — without this clause a
        # UTF-16 acp.json bypassed AcpConfigError and surfaced as a raw
        # traceback, the one failure mode this function exists to prevent.
        raise AcpConfigError(
            f"{config_path} is not valid UTF-8 ({exc.reason} at byte "
            f"{exc.start}). Re-save it as UTF-8 — PowerShell 5.1 writes UTF-16LE from `>` and `Out-File`."
        ) from exc
    except OSError as exc:
        raise AcpConfigError(f"cannot read {config_path}: {exc}") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AcpConfigError(f"invalid JSON in {config_path}: {exc}") from exc

    return AcpClientConfig.from_dict(parsed, project_root=root)


def add_server_entry(
    name: str,
    server: Dict[str, Any],
    project_root: Optional[Path] = None,
) -> AcpServerConfig:
    """Add one server to ``<project_root>/.agentao/acp.json``.

    The existing file is read and validated first: an unreadable or invalid
    file is refused rather than overwritten, and a name that is already
    configured is refused rather than replaced. Every other top-level field
    and server entry is kept as it was. The new entry is validated before
    anything is written, and the file is replaced atomically (a temporary
    file in the same directory, then ``os.replace``), so a failure leaves
    the previous file intact.

    Args:
        name: Server name — the key under ``servers``.
        server: The server object, in ``acp.json`` shape.
        project_root: Directory containing ``.agentao/``; ``Path.cwd()`` by
            default.

    Returns:
        The validated :class:`AcpServerConfig` for the new entry — what
        :meth:`ACPManager.add_server` takes.

    Raises:
        AcpConfigError: Invalid name or entry, an existing file that cannot
            be read or is invalid, a name collision, or a failed write.
    """
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise AcpConfigError(f"invalid server name {name!r}")
    root = project_root if project_root is not None else Path.cwd()
    config_path = root / ".agentao" / "acp.json"
    validated = AcpServerConfig.from_dict(name, server, project_root=root)

    from filelock import FileLock, Timeout

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AcpConfigError(f"cannot create {config_path.parent}: {exc}") from exc
    # The lock spans the whole read-modify-write: two writers that each read
    # before either replaced the file would otherwise both succeed, and the
    # second replacement would silently drop the first one's entry.
    lock = FileLock(str(config_path.parent / _CONFIG_LOCK_NAME), timeout=_CONFIG_LOCK_TIMEOUT_S)
    try:
        lock.acquire()
    except Timeout as exc:
        raise AcpConfigError(
            f"timed out after {_CONFIG_LOCK_TIMEOUT_S}s waiting for another "
            f"process to finish updating {config_path}; try again"
        ) from exc
    try:
        _add_locked(config_path, root, name, server)
    finally:
        lock.release()
    return validated


#: Lock file guarding ``acp.json`` read-modify-writes, next to the file.
_CONFIG_LOCK_NAME = ".acp.json.lock"
_CONFIG_LOCK_TIMEOUT_S = 10.0


def _add_locked(config_path: Path, root: Path, name: str, server: Dict[str, Any]) -> None:
    """``add_server_entry`` body — caller holds the config lock."""
    if config_path.exists():
        # Same reader and validation the manager uses; raises AcpConfigError.
        load_acp_client_config(project_root=root)
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    else:
        raw = {}
    servers = raw.setdefault("servers", {})
    if name in servers:
        raise AcpConfigError(
            f"server '{name}' already exists in {config_path}; choose another name"
        )
    servers[name] = server
    text = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(config_path.parent), prefix=".acp.json.", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, config_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise AcpConfigError(f"cannot write {config_path}: {exc}") from exc
