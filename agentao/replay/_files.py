"""Best-effort discovery of replay files shared by readers and retention."""

from pathlib import Path
from typing import List


def replay_files_oldest_first(directory: Path, pattern: str = "*.jsonl") -> List[Path]:
    candidates = []
    for path in directory.glob(pattern):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            # Another process can prune a file after glob sees it. An unreadable
            # entry must not hide every other replay or prevent retention.
            continue
        candidates.append((mtime, path))
    candidates.sort(key=lambda item: item[0])
    return [path for _, path in candidates]
