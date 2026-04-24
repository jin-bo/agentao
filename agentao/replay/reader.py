"""ReplayReader — offline inspection of replay files.

Active-session semantics are intentionally minimal:

- ``iter_events`` reads until the current end of file and stops.
- No follow/tail mode. A crash that leaves a partial final JSON line is
  tolerated: the partial line is skipped and logged, and the earlier
  events are still returned.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

from .events import EventKind
from .meta import ReplayMeta

logger = logging.getLogger(__name__)


def _replays_dir(project_root: Optional[Path] = None) -> Path:
    root = project_root if project_root is not None else Path.cwd()
    return root / ".agentao" / "replays"


def list_replays(project_root: Optional[Path] = None) -> List[ReplayMeta]:
    """Scan ``.agentao/replays/`` and return metadata for each instance file.

    Best-effort: unreadable or empty files are skipped rather than
    aborting the listing. The returned list is sorted by file mtime,
    newest last (matching shell ``ls -tr`` behavior for humans reading
    the output).
    """
    dir_ = _replays_dir(project_root)
    if not dir_.exists():
        return []
    metas: List[ReplayMeta] = []
    for path in sorted(dir_.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        meta = _summarize(path)
        if meta is not None:
            metas.append(meta)
    return metas


def open_replay(
    session_id: str,
    instance_id: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> Optional["ReplayReader"]:
    """Locate the replay file for ``session_id.instance_id``.

    When ``instance_id`` is ``None``, the most recent instance for that
    logical session is returned. Returns ``None`` if no match exists.
    """
    dir_ = _replays_dir(project_root)
    if not dir_.exists():
        return None
    safe_sid = re.sub(r"[^\w\-]", "_", session_id)
    if instance_id is not None:
        path = dir_ / f"{safe_sid}.{instance_id}.jsonl"
        if not path.exists():
            return None
        return ReplayReader(path)
    candidates = sorted(
        dir_.glob(f"{safe_sid}.*.jsonl"), key=lambda p: p.stat().st_mtime
    )
    if not candidates:
        return None
    return ReplayReader(candidates[-1])


def find_replay_candidates(
    requested: str,
    project_root: Optional[Path] = None,
) -> List[ReplayMeta]:
    """Return every :class:`ReplayMeta` whose ids match ``requested``.

    Matching is prefix-based against ``full_id`` (``session.instance``),
    ``short_id`` (the form shown in ``/replays`` listings),
    ``instance_id``, and ``session_id`` — so users can paste whatever
    they see on screen, or type just the leading characters of either
    component. An exact hit on any of these fields is returned alone to
    keep unambiguous lookups stable even when other entries happen to
    share a common prefix.
    """
    metas = list_replays(project_root)
    if not metas or not requested:
        return []
    exact = [
        m for m in metas
        if requested in (m.full_id, m.short_id, m.instance_id, m.session_id)
    ]
    if exact:
        return exact
    return [
        m for m in metas
        if m.full_id.startswith(requested)
        or m.short_id.startswith(requested)
        or m.instance_id.startswith(requested)
        or m.session_id.startswith(requested)
    ]


def resolve_replay_id(
    requested: str,
    project_root: Optional[Path] = None,
) -> Optional[ReplayMeta]:
    """Resolve ``<id>`` from the CLI to exactly one :class:`ReplayMeta`.

    Returns ``None`` when no candidate matches or the prefix is ambiguous.
    Call :func:`find_replay_candidates` to inspect the ambiguity case and
    surface a disambiguation hint to the user.
    """
    candidates = find_replay_candidates(requested, project_root)
    if len(candidates) == 1:
        return candidates[0]
    return None


class ReplayReader:
    """Iterate over events in a replay instance file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def meta(self) -> ReplayMeta:
        summary = _summarize(self.path)
        if summary is None:
            return ReplayMeta(
                session_id="",
                instance_id="",
                path=self.path,
            )
        return summary

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------

    def iter_events(
        self,
        kinds: Optional[Set[str]] = None,
        turn_id: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield events in sequence order, stopping at end-of-file."""
        if not self.path.exists():
            return
        try:
            raw_lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("replay: could not read %s: %s", self.path, exc)
            return
        total = len(raw_lines)
        for idx, line in enumerate(raw_lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                # Tolerate ONE malformed tail line (crash-during-write).
                # Any malformed line in the middle is still skipped but
                # logged — one bad line never aborts the read.
                if idx == total - 1:
                    logger.warning(
                        "replay: skipping partial tail line in %s",
                        self.path,
                    )
                else:
                    logger.warning(
                        "replay: skipping malformed line %d in %s: %s",
                        idx + 1, self.path, exc,
                    )
                continue
            if not isinstance(event, dict):
                continue
            if kinds is not None and event.get("kind") not in kinds:
                continue
            if turn_id is not None and event.get("turn_id") != turn_id:
                continue
            yield event

    def events(
        self,
        kinds: Optional[Set[str]] = None,
        turn_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return list(self.iter_events(kinds=kinds, turn_id=turn_id))


def _summarize(path: Path) -> Optional[ReplayMeta]:
    """Quick scan of a replay file to build a ``ReplayMeta`` summary."""
    try:
        stat = path.stat()
    except OSError:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    session_id = ""
    instance_id = ""
    created_at: Optional[str] = None
    event_count = 0
    turn_ids: Set[str] = set()
    has_errors = False
    malformed = 0
    lines = raw.splitlines()
    total = len(lines)
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Trailing partial line is normal after a crash; only count
            # middle-of-file malformation as "real" corruption.
            if idx != total - 1:
                malformed += 1
            continue
        if not isinstance(event, dict):
            continue
        event_count += 1
        if not session_id:
            session_id = str(event.get("session_id") or "")
        if not instance_id:
            instance_id = str(event.get("instance_id") or "")
        if event.get("kind") == EventKind.REPLAY_HEADER and created_at is None:
            payload = event.get("payload") or {}
            if isinstance(payload, dict):
                created_at = payload.get("created_at")
        tid = event.get("turn_id")
        if tid:
            turn_ids.add(tid)
        if event.get("kind") == EventKind.ERROR:
            has_errors = True
    if not session_id or not instance_id:
        # Fall back to filename pattern ``session.instance.jsonl`` when
        # the header is unreadable. Keeps /replays listings useful even
        # for partially-corrupted files.
        stem = path.stem
        if "." in stem:
            session_id = session_id or stem.split(".", 1)[0]
            instance_id = instance_id or stem.split(".", 1)[1]
    return ReplayMeta(
        session_id=session_id,
        instance_id=instance_id,
        path=path,
        created_at=created_at,
        updated_at=_fmt_mtime(stat.st_mtime),
        event_count=event_count,
        turn_count=len(turn_ids),
        has_errors=has_errors,
        malformed_lines=malformed,
    )


def _fmt_mtime(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
