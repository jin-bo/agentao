"""Jev config and credential discovery/storage, kept outside the core constructor."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from dotenv import dotenv_values

from ..paths import user_root
from ..recommendations import JevConfig, JevSkillRecommender

_logger = logging.getLogger(__name__)


def _read_object(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("Refusing a symlink configuration file")
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Configuration must be an object")
    return data


def resolve_typesafe_key(project_root: Path, *, user_dir: Path | None = None) -> str:
    """Read env > project .env > user credentials, without mutating os.environ."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    try:
        key = (dotenv_values(project_root / ".env").get("TYPESAFE_API_KEY") or "").strip()
        if key:
            return key
    except (OSError, ValueError, UnicodeError):
        pass
    try:
        raw = _read_object((user_dir if user_dir is not None else user_root()) / "credentials.json")
        value = raw.get("typesafe_api_key", "")
        return value.strip() if isinstance(value, str) else ""
    except (OSError, ValueError, UnicodeError):
        return ""


def load_jev(project_root: Path, *, user_dir: Path | None = None) -> JevSkillRecommender:
    try:
        raw = _read_object(project_root / ".agentao" / "settings.json")
        config = JevConfig.from_dict(raw.get("jev", {}))
    except (OSError, ValueError, TypeError, UnicodeError):
        _logger.warning("Invalid Jev settings; recommendations disabled. Check the jev configuration.")
        config = JevConfig()
    return JevSkillRecommender(config, resolve_typesafe_key(project_root, user_dir=user_dir))


_SAVE_LOCK_TIMEOUT = 2.0


def _update_object(path: Path, update: dict, *, private: bool = False) -> None:
    from filelock import FileLock

    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=_SAVE_LOCK_TIMEOUT):
        data = _read_object(path)
        data.update(update)
        fd, name = tempfile.mkstemp(prefix=".jev-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if private:
                os.chmod(name, 0o600)
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def save_typesafe_key(key: str, *, user_dir: Path | None = None) -> None:
    key = key.strip()
    if not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("API key must be nonempty printable ASCII without whitespace")
    _update_object((user_dir if user_dir is not None else user_root()) / "credentials.json",
                   {"typesafe_api_key": key}, private=True)


def save_jev_settings(project_root: Path, config: JevConfig) -> None:
    _update_object(project_root / ".agentao" / "settings.json", {"jev": asdict(config)})
