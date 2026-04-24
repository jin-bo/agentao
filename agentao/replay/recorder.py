"""ReplayRecorder — writes one JSONL replay file per replay instance.

Lifecycle:

    recorder = ReplayRecorder.create(session_id, project_root)
    recorder.record("session_started", payload={...})
    ...
    recorder.record("session_ended", payload={...})
    recorder.close()

The recorder owns the monotonic ``seq`` counter and a ``threading.Lock``
so concurrent tool executions (ToolRunner's ThreadPoolExecutor) cannot
interleave partially-written JSONL lines or allocate duplicate sequence
numbers.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .events import SCHEMA_VERSION, EventKind, ReplayEvent
from .redact import merge_hits
from .sanitize import SanitizeStats, sanitize_event

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _short_id() -> str:
    return _uuid.uuid4().hex[:12]


class ReplayRecorder:
    """Append-only JSONL writer for a single replay instance."""

    def __init__(
        self,
        session_id: str,
        instance_id: str,
        path: Path,
        *,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.session_id = session_id
        self.instance_id = instance_id
        self.path = path
        self._logger = logger_ or logger
        self._seq = 0
        self._lock = threading.Lock()
        self._closed = False
        self._fp = None
        # Cumulative sanitization stats rolled up from every event.
        # Exposed as a property so ``replay_footer`` can snapshot them
        # without reaching into internals.
        self._redaction_hits: Dict[str, int] = {}
        self._dropped_field_hits: Dict[str, int] = {}
        self._truncated_field_hits: Dict[str, int] = {}
        # Populated by :meth:`create`; kept here so subsequent readers can
        # re-emit the value on ``replay_footer`` for symmetry.
        self._capture_flags: Dict[str, bool] = {}
        self._open()

    # -- construction -------------------------------------------------------

    @classmethod
    def create(
        cls,
        session_id: str,
        project_root: Path,
        *,
        logger_: Optional[logging.Logger] = None,
        capture_flags: Optional[Dict[str, bool]] = None,
    ) -> "ReplayRecorder":
        """Create a fresh recorder for a brand-new replay instance.

        Emits the mandatory ``replay_header`` as the very first JSONL
        line. ``capture_flags`` is snapshotted into the header so a
        reader can tell the difference between "that event was not
        captured" and "nothing happened" for deep-capture-only events.
        """
        instance_id = _short_id()
        replays_dir = Path(project_root) / ".agentao" / "replays"
        replays_dir.mkdir(parents=True, exist_ok=True)
        safe_sid = re.sub(r"[^\w\-]", "_", session_id)
        path = replays_dir / f"{safe_sid}.{instance_id}.jsonl"
        rec = cls(session_id, instance_id, path, logger_=logger_)
        rec._capture_flags = dict(capture_flags) if capture_flags else {}
        header_payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "instance_id": instance_id,
            "created_at": _iso_now(),
        }
        if rec._capture_flags:
            header_payload["capture_flags"] = dict(rec._capture_flags)
        rec.record(EventKind.REPLAY_HEADER, payload=header_payload)
        return rec

    # -- writer -------------------------------------------------------------

    def _open(self) -> None:
        try:
            self._fp = self.path.open("a", encoding="utf-8")
        except OSError as exc:
            self._logger.warning("replay: could not open %s: %s", self.path, exc)
            self._fp = None
            self._closed = True

    def record(
        self,
        kind: str,
        *,
        turn_id: Optional[str] = None,
        parent_turn_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append one event to the replay file.

        Never raises. Sanitization and write failures are logged and
        swallowed so the runtime is never broken by replay bookkeeping.
        """
        if self._closed:
            return
        try:
            clean, stats = sanitize_event(
                kind, payload or {}, capture_flags=self._capture_flags,
            )
        except Exception as exc:
            self._logger.warning("replay: sanitize failed for %s: %s", kind, exc)
            clean = {"redacted": "filter_error"}
            stats = SanitizeStats()
        with self._lock:
            if self._closed or self._fp is None:
                return
            self._seq += 1
            event = ReplayEvent(
                event_id=_short_id(),
                session_id=self.session_id,
                instance_id=self.instance_id,
                seq=self._seq,
                ts=_iso_now(),
                kind=kind,
                turn_id=turn_id,
                parent_turn_id=parent_turn_id,
                payload=clean,
            )
            try:
                line = json.dumps(event.to_dict(), ensure_ascii=False)
                self._fp.write(line + "\n")
                self._fp.flush()
            except Exception as exc:
                # Do not mark closed — transient write errors may succeed
                # on the next record() call. We just skip this one.
                self._logger.warning("replay: write failed: %s", exc)
            # Roll sanitize stats into the cumulative counters AFTER a
            # successful write attempt so a write failure doesn't
            # double-book the hits on the next retry.
            if stats.redaction_hits:
                self._redaction_hits = merge_hits(
                    self._redaction_hits, stats.redaction_hits,
                )
            for name in stats.dropped_fields:
                self._dropped_field_hits[name] = (
                    self._dropped_field_hits.get(name, 0) + 1
                )
            for name in stats.truncated_fields:
                self._truncated_field_hits[name] = (
                    self._truncated_field_hits.get(name, 0) + 1
                )

    def close(self) -> None:
        """Flush a ``replay_footer`` summary and close the file handle.

        The footer carries a roll-up of the sanitization counters so a
        reader can quickly answer "how many secrets were redacted" and
        "what field names were dropped or truncated" without scanning
        every event. Safe to call more than once — the second call is a
        no-op.
        """
        # Emit the footer outside the lock so record() (which takes the
        # lock internally) can still fire its last write. After this
        # point no further events can enter the file, so the counters
        # here represent the final tally.
        already_closed = False
        with self._lock:
            already_closed = self._closed
        if not already_closed:
            try:
                self.record(
                    EventKind.REPLAY_FOOTER,
                    payload={
                        "closed_at": _iso_now(),
                        "event_count": self._seq,
                        "redaction_hits": dict(self._redaction_hits),
                        "dropped_field_hits": dict(self._dropped_field_hits),
                        "truncated_field_hits": dict(self._truncated_field_hits),
                        "capture_flags": dict(self._capture_flags),
                    },
                )
            except Exception:
                # Footer is best-effort — a failure here must not
                # propagate or block the subsequent close().
                pass
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fp = self._fp
            self._fp = None
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass

    # -- introspection ------------------------------------------------------

    @property
    def current_seq(self) -> int:
        """Last allocated sequence number (0 before the first event)."""
        return self._seq

    @property
    def redaction_hits(self) -> Dict[str, int]:
        """Cumulative secret-scanner hits by kind (snapshot copy)."""
        return dict(self._redaction_hits)

    @property
    def dropped_field_hits(self) -> Dict[str, int]:
        """Cumulative count of dropped fields by field name (snapshot copy)."""
        return dict(self._dropped_field_hits)

    @property
    def truncated_field_hits(self) -> Dict[str, int]:
        """Cumulative count of truncated fields by field name (snapshot copy)."""
        return dict(self._truncated_field_hits)

    @property
    def capture_flags(self) -> Dict[str, bool]:
        """Snapshot of the capture flags active at recorder-create time."""
        return dict(self._capture_flags)
