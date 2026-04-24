"""ReplayRetentionPolicy — prune old replay files to a configured cap.

Rules (from SESSION_REPLAY_PLAN.md):

- keep the most recent ``N`` replay instances, default ``N = 20``
- prune best-effort after a new instance is created or one ends
- ``/replays prune`` runs the same logic on demand
- retention deletes replay files only and must not touch ``sessions/``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .config import REPLAY_DEFAULTS

logger = logging.getLogger(__name__)


@dataclass
class ReplayRetentionPolicy:
    max_instances: int = REPLAY_DEFAULTS["max_instances"]

    def prune(self, project_root: Optional[Path] = None) -> List[Path]:
        """Delete the oldest replay files above the cap.

        Returns the list of deleted paths (empty when nothing to prune).
        Never raises — individual delete failures are logged and skipped.
        """
        root = project_root if project_root is not None else Path.cwd()
        dir_ = root / ".agentao" / "replays"
        if not dir_.exists():
            return []
        # Sort oldest first so the slice-to-delete contains the oldest
        # files. mtime is "good enough" and cheap; instance_id is random
        # so lexical order would not match creation order.
        files = sorted(dir_.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if len(files) <= self.max_instances:
            return []
        to_delete = files[: len(files) - self.max_instances]
        deleted: List[Path] = []
        for path in to_delete:
            try:
                path.unlink()
                deleted.append(path)
            except OSError as exc:
                logger.warning("replay: could not prune %s: %s", path, exc)
        return deleted
