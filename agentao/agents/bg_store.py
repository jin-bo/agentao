"""Per-Agentao store for background sub-agent task state.

Each ``Agentao`` owns exactly one ``BackgroundTaskStore``, anchored to
its working directory; tools and CLI commands receive the store via
the registry. Persistence path is
``<persistence_dir>/.agentao/background_tasks.json`` when
``persistence_dir`` is provided, otherwise state is in-memory only.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Literal, Optional, get_args

from ..cancellation import CancellationToken
from . import store as persistence

BgTaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]

_VALID_BG_STATUSES: frozenset = frozenset(get_args(BgTaskStatus))

# Cap on pending notifications. If the parent agent never drains (e.g.
# session abandoned mid-task while subagents are still running), oldest
# entries roll off so memory cannot grow without bound.
_NOTIFICATION_CAPACITY = 256

# Per-process guard: two Agentao instances anchored to the same project
# share one persistence file. If both ran recover(), the second would
# reclassify pending/running tasks owned by the first as "failed" even
# though their threads are still alive. Track the resolved paths already
# recovered in this process so only the first store reclassifies orphans.
_recovered_paths: set = set()
_recovered_paths_lock = threading.Lock()

# Per-path flush locks: serialize flushes from multiple stores in the
# same process anchored to the same persistence file. Each flush is a
# load-modify-save cycle, so without this, two stores can interleave
# their reads and races overwrite each other's changes on disk.
_path_flush_locks: Dict[str, threading.Lock] = {}
_path_flush_locks_lock = threading.Lock()


def _flush_lock_for(path_key: str) -> threading.Lock:
    with _path_flush_locks_lock:
        lock = _path_flush_locks.get(path_key)
        if lock is None:
            lock = threading.Lock()
            _path_flush_locks[path_key] = lock
        return lock


def _reset_recovery_guard_for_tests() -> None:
    """Test helper to clear the per-process recovery guard."""
    with _recovered_paths_lock:
        _recovered_paths.clear()
    with _path_flush_locks_lock:
        _path_flush_locks.clear()


class BackgroundTaskStore:
    """Owns the in-memory state of background sub-agent tasks for one Agentao.

    Thread-safe. ``_lock`` guards tasks; ``_token_lock`` guards the
    token registry; ``_notify_lock`` guards the notification deque.
    Flushes are serialized via a process-wide per-path lock so that
    multiple stores anchored to the same persistence file cannot race
    on the load-modify-save cycle. Each flush also merges with the
    current on-disk snapshot using ``_owned_ids`` so that a store only
    rewrites tasks it owns and never drops tasks owned by another store.
    """

    def __init__(
        self,
        persistence_dir: Optional[Path] = None,
        *,
        persistence_dir_provider: Optional[Callable[[], Optional[Path]]] = None,
    ):
        if persistence_dir is not None and persistence_dir_provider is not None:
            raise ValueError(
                "Pass either persistence_dir or persistence_dir_provider, not both"
            )
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._tokens: Dict[str, CancellationToken] = {}
        self._token_lock = threading.Lock()
        self._notifications: Deque[str] = deque(maxlen=_NOTIFICATION_CAPACITY)
        self._notify_lock = threading.Lock()

        # Task IDs this store is responsible for on disk. A store mutates
        # only the keys it owns when flushing; everything else in the
        # on-disk snapshot is preserved. A key with no entry in _tasks but
        # present in _owned_ids represents a delete waiting to flush.
        self._owned_ids: set = set()

        # Owned IDs we have successfully written to disk. If one of these
        # later disappears from on-disk, a sibling store deleted it — we
        # then drop ownership locally so a subsequent flush cannot
        # resurrect the row.
        self._known_persisted_ids: set = set()

        # Owned task → the persistence Path it was registered under. Pinned
        # at register() time so a later cwd change cannot retarget an
        # in-flight task's flushes to a different project's file. For
        # provider-backed stores, ``_check_persistence_rebind()`` keeps the
        # entry intact for in-flight (pending/running) tasks across rebinds
        # so the background thread's later mark_running()/update() calls
        # still find the record and persist to the original path.
        self._owner_path: Dict[str, Path] = {}

        # Persistence directory may be either frozen at construction (ACP
        # sessions, tests) or supplied via a provider so it follows
        # ``Path.cwd()`` lazily for default Agentao() sessions whose cwd
        # can change after construction.
        self._frozen_persistence_dir: Optional[Path] = (
            Path(persistence_dir) if persistence_dir is not None else None
        )
        self._persistence_dir_provider: Optional[Callable[[], Optional[Path]]] = (
            persistence_dir_provider
        )

        # Cache of the last resolved persistence file path. For provider-
        # backed stores, ``_check_persistence_rebind()`` compares the
        # current resolution against this and rebinds state when the cwd
        # has changed since the previous operation. Frozen stores resolve
        # to the same key forever, so the check is a no-op.
        self._last_known_path_key: Optional[str] = self._resolve_path_key()
        self._rebind_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Persistence path resolution
    # ------------------------------------------------------------------

    def _resolve_persistence_path(self) -> Optional[Path]:
        """Return the current persistence file path, or None if unset.

        For frozen stores (ACP sessions, tests), returns the captured path.
        For provider-backed stores (default Agentao() sessions), evaluates
        the provider on each call so a process ``chdir`` immediately
        retargets persistence to the new project's
        ``.agentao/background_tasks.json``.
        """
        if self._frozen_persistence_dir is not None:
            base = self._frozen_persistence_dir
        elif self._persistence_dir_provider is not None:
            base = self._persistence_dir_provider()
        else:
            return None
        if base is None:
            return None
        return Path(base) / ".agentao" / "background_tasks.json"

    def _resolve_path_key(self, path: Optional[Path] = None) -> Optional[str]:
        if path is None:
            path = self._resolve_persistence_path()
        return str(path.resolve()) if path is not None else None

    def _check_persistence_rebind(self) -> None:
        """Re-key state when the resolved persistence path changes.

        Provider-backed stores follow ``Path.cwd()`` lazily, so a process
        ``chdir`` after construction retargets ``_resolve_persistence_path()``
        to a different project. Without re-keying, sibling-loaded snapshots
        from the previous path would still surface in ``/agent status`` and
        flushes would write into the wrong project's file.

        Owned tasks survive the rebind selectively:
          * In-flight (pending/running): preserved along with their pinned
            ``_owner_path`` and cancellation token. The background thread
            running the task will later call ``mark_running()``/``update()``
            through this same store; the record must still be reachable so
            the result/notification isn't silently dropped, and its flushes
            must still target the original project's file.
          * Settled (completed/failed/cancelled): flushed once to their
            pinned path so the final state lands on disk, then dropped
            from in-memory state — they no longer need to be tracked.

        Sibling-loaded entries (not in ``_owned_ids``) are unconditionally
        dropped: they were a snapshot of the OLD path's on-disk state and
        are replaced with the NEW path's snapshot at the end.

        Frozen-path stores (ACP sessions, tests) never rebind.
        """
        if self._persistence_dir_provider is None:
            return
        new_path = self._resolve_persistence_path()
        new_key = self._resolve_path_key(new_path)

        # Hold ``_rebind_lock`` across the entire flush + re-key + load so a
        # concurrent caller cannot observe state that's been re-keyed but
        # not yet cleared.
        with self._rebind_lock:
            if new_key == self._last_known_path_key:
                return

            # Phase 1: flush current state to its pinned paths first so a
            # task that completed in another thread between this rebind's
            # detection and a still-in-flight flush call doesn't lose its
            # settled state when we drop it below.
            self._flush_to_disk()

            self._last_known_path_key = new_key

            with self._lock:
                in_flight_owned = {
                    agent_id
                    for agent_id in self._owned_ids
                    if (rec := self._tasks.get(agent_id)) is not None
                    and rec.get("status") in ("pending", "running")
                }
                self._tasks = {aid: self._tasks[aid] for aid in in_flight_owned}
                self._owned_ids = set(in_flight_owned)
                self._known_persisted_ids &= in_flight_owned
                self._owner_path = {
                    aid: self._owner_path[aid]
                    for aid in in_flight_owned
                    if aid in self._owner_path
                }
            with self._token_lock:
                self._tokens = {
                    aid: tok
                    for aid, tok in self._tokens.items()
                    if aid in in_flight_owned
                }

            if new_path is None:
                return

            loaded = persistence.load_bg_task_store(new_path)
            with self._lock:
                for agent_id, rec in loaded.items():
                    if agent_id not in self._owned_ids:
                        self._tasks[agent_id] = rec

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def push_notification(self, msg: str) -> None:
        with self._notify_lock:
            self._notifications.append(msg)

    def drain_notifications(self) -> List[str]:
        """Return all pending completion notifications and clear the queue."""
        with self._notify_lock:
            msgs = list(self._notifications)
            self._notifications.clear()
            return msgs

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    def register(self, agent_id: str, agent_name: str, task_summary: str) -> None:
        self._check_persistence_rebind()
        persistence_path = self._resolve_persistence_path()
        with self._lock:
            self._tasks[agent_id] = {
                "agent_name": agent_name,
                "task": task_summary,
                "status": "pending",
                "result": None,
                "error": None,
                "created_at": time.time(),
                "started_at": None,
                "finished_at": None,
                "turns": 0,
                "tool_calls": 0,
                "tokens": 0,
                "duration_ms": 0,
            }
            self._owned_ids.add(agent_id)
            if persistence_path is not None:
                self._owner_path[agent_id] = persistence_path
        self._flush_to_disk()

    def mark_running(self, agent_id: str) -> bool:
        """Transition pending → running. Returns True if state changed."""
        self._check_persistence_rebind()
        should_flush = False
        with self._lock:
            rec = self._tasks.get(agent_id)
            if rec and rec["status"] == "pending":
                rec["status"] = "running"
                rec["started_at"] = time.time()
                should_flush = True
        if should_flush:
            self._flush_to_disk()
        return should_flush

    def update(
        self,
        agent_id: str,
        *,
        status: BgTaskStatus,
        result: Optional[str] = None,
        error: Optional[str] = None,
        turns: int = 0,
        tool_calls: int = 0,
        tokens: int = 0,
        duration_ms: int = 0,
    ) -> None:
        assert status in _VALID_BG_STATUSES, f"Invalid bg task status: {status!r}"
        self._check_persistence_rebind()
        agent_name: Optional[str] = None
        with self._lock:
            rec = self._tasks.get(agent_id)
            if rec:
                rec["status"] = status
                rec["result"] = result
                rec["error"] = error
                rec["finished_at"] = time.time()
                rec["turns"] = turns
                rec["tool_calls"] = tool_calls
                rec["tokens"] = tokens
                rec["duration_ms"] = duration_ms
                agent_name = rec["agent_name"]

        if agent_name is None:
            return

        self._flush_to_disk()

        # Push notification outside the lock to avoid lock-ordering issues.
        if status == "completed" and result is not None:
            preview = result[:300] + "…" if len(result) > 300 else result
            self.push_notification(
                f"Background agent '{agent_name}' (ID: {agent_id}) completed.\n{preview}"
            )
        elif status == "failed":
            self.push_notification(
                f"Background agent '{agent_name}' (ID: {agent_id}) failed: {error}"
            )
        elif status == "cancelled":
            self.push_notification(
                f"Background agent '{agent_name}' (ID: {agent_id}) was cancelled."
            )

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        self._check_persistence_rebind()
        self._refresh_sibling_tasks()
        current_path = self._resolve_persistence_path()
        with self._lock:
            rec = self._tasks.get(agent_id)
            if rec is None:
                return None
            if (
                agent_id in self._owned_ids
                and self._owner_path.get(agent_id) != current_path
            ):
                # Owned but pinned to a different project (background thread
                # is still running there). Hide from this project's view.
                return None
            return dict(rec)

    def list(self) -> List[Dict[str, Any]]:
        self._check_persistence_rebind()
        self._refresh_sibling_tasks()
        current_path = self._resolve_persistence_path()
        with self._lock:
            return [
                dict(v) | {"id": k}
                for k, v in self._tasks.items()
                if k not in self._owned_ids
                or self._owner_path.get(k) == current_path
            ]

    def cancel(self, agent_id: str) -> str:
        """Cancel a task. Returns a human-readable result string.

        Sibling-owned tasks (loaded via ``recover()`` from another store on
        the same persistence file) cannot be cancelled here: a pending-task
        flush would not propagate without ownership, and a running task's
        cancellation token lives in the owning store's process memory.
        Both cases return a clear failure rather than falsely reporting
        success.
        """
        self._check_persistence_rebind()
        cancelled_before_start = False
        agent_name: Optional[str] = None

        with self._lock:
            rec = self._tasks.get(agent_id)
            if rec is None:
                return f"No background agent found with ID: {agent_id}"

            status = rec["status"]
            agent_name = rec["agent_name"]

            if status in ("completed", "failed", "cancelled"):
                return f"Agent '{agent_name}' ({agent_id}) is already {status} — nothing to cancel."

            if status == "pending":
                if agent_id not in self._owned_ids:
                    return (
                        f"Agent '{agent_name}' ({agent_id}) is owned by another Agentao "
                        f"instance on the same project — cancel it from that instance."
                    )
                rec["status"] = "cancelled"
                rec["result"] = None
                rec["error"] = None
                rec["finished_at"] = time.time()
                rec["turns"] = 0
                rec["tool_calls"] = 0
                rec["tokens"] = 0
                rec["duration_ms"] = 0
                cancelled_before_start = True

        if cancelled_before_start:
            self._flush_to_disk()
            self.push_notification(
                f"Background agent '{agent_name}' (ID: {agent_id}) was cancelled."
            )
            with self._token_lock:
                self._tokens.pop(agent_id, None)
            return f"Agent '{agent_name}' ({agent_id}) cancelled before it started."

        # Running: signal the token; the thread catches AgentCancelledError → "cancelled"
        with self._token_lock:
            token = self._tokens.get(agent_id)
        if token is None:
            return (
                f"Agent '{agent_name}' ({agent_id}) is running in another Agentao "
                f"instance on the same project — cancel it from that instance."
            )
        token.cancel("user-cancel")
        return (
            f"Cancellation signal sent to agent '{agent_name}' ({agent_id}). "
            f"It will stop at the next safe point."
        )

    def delete(self, agent_id: str) -> str:
        self._check_persistence_rebind()
        # Refresh sibling-owned snapshots first: when the target task is
        # owned by another Agentao instance on the same persistence file,
        # our in-memory copy can be stale (e.g. still "running" after the
        # owner already flushed "completed"). Without this, /agent delete
        # would refuse forever until some other read happened to refresh.
        # Owned-task state is authoritative in memory and untouched.
        self._refresh_sibling_tasks()
        with self._lock:
            rec = self._tasks.get(agent_id)
            if rec is None:
                return f"No background agent found with ID: {agent_id}"

            status = rec["status"]
            agent_name = rec["agent_name"]
            if status in ("pending", "running"):
                return (
                    f"Agent '{agent_name}' ({agent_id}) is still {status} and cannot be deleted. "
                    f"Cancel it first or wait for it to finish."
                )

            del self._tasks[agent_id]
            # Keep agent_id in _owned_ids so the next flush removes it
            # from disk. If the store didn't already own it (e.g. it was
            # loaded as an orphan via recover()), mark ownership now so
            # the deletion still propagates.
            self._owned_ids.add(agent_id)
            # Pin the deletion to the current path so _flush_to_disk
            # knows which file to rewrite. For tasks already owned, the
            # original pin is preserved.
            if agent_id not in self._owner_path:
                persistence_path = self._resolve_persistence_path()
                if persistence_path is not None:
                    self._owner_path[agent_id] = persistence_path

        with self._token_lock:
            self._tokens.pop(agent_id, None)

        self._flush_to_disk()
        return f"Deleted background agent '{agent_name}' ({agent_id}) from history."

    # ------------------------------------------------------------------
    # Cancellation tokens
    # ------------------------------------------------------------------

    def register_token(self, agent_id: str, token: CancellationToken) -> None:
        with self._token_lock:
            self._tokens[agent_id] = token

    def unregister_token(self, agent_id: str) -> None:
        with self._token_lock:
            self._tokens.pop(agent_id, None)

    def get_token(self, agent_id: str) -> Optional[CancellationToken]:
        with self._token_lock:
            return self._tokens.get(agent_id)

    # ------------------------------------------------------------------
    # Persistence + recovery
    # ------------------------------------------------------------------

    def recover(self) -> bool:
        """Load persisted tasks and reclassify orphaned ones as failed.

        Returns True iff orphan reclassification ran (i.e. this is the
        first store in the process anchored to this persistence file).
        On the guarded path — when another store has already recovered
        the same file — we still load the on-disk snapshot into
        ``_tasks`` but skip reclassification. Loading is required: a
        flush from this store would otherwise overwrite the file with
        an empty snapshot and drop tasks owned by the first store.
        """
        self._check_persistence_rebind()
        persistence_path = self._resolve_persistence_path()
        if persistence_path is None:
            return False

        path_key = self._resolve_path_key(persistence_path)
        with _recovered_paths_lock:
            already_recovered = path_key in _recovered_paths
            if not already_recovered:
                _recovered_paths.add(path_key)

        loaded = persistence.load_bg_task_store(persistence_path)
        if not loaded:
            return False

        if already_recovered:
            # Another store already reclassified orphans; just load the
            # current on-disk snapshot into our view. We do NOT take
            # ownership — those tasks belong either to no one (historical)
            # or to whichever store registered them in this process.
            with self._lock:
                for agent_id, rec in loaded.items():
                    self._tasks[agent_id] = rec
            return False

        with self._lock:
            for agent_id, rec in loaded.items():
                if rec.get("status") in ("pending", "running"):
                    rec["status"] = "failed"
                    rec["error"] = "process exited before task finished"
                    if rec.get("finished_at") is None:
                        rec["finished_at"] = time.time()
                self._tasks[agent_id] = rec
                # The first store to recover claims ownership of orphan
                # records so its reclassification flush propagates via the
                # merge path and so a later /agents delete from this store
                # actually removes them from disk.
                self._owned_ids.add(agent_id)
                self._owner_path[agent_id] = persistence_path
        self._flush_to_disk()
        return True

    def _drop_sibling_deleted(
        self,
        on_disk: Dict[str, Dict[str, Any]],
        persistence_path: Path,
    ) -> None:
        """Release ownership of tasks a sibling store deleted from disk.

        An owned task pinned to ``persistence_path`` that we previously
        persisted but is no longer in ``on_disk`` was deleted by another
        store sharing the file. Drop local state for it so the next flush
        cannot rewrite it back. Tasks pinned to other paths are not
        considered here — their disk record lives in a different file.
        Caller must hold the per-path flush lock for ``persistence_path``.
        """
        with self._lock:
            sibling_deleted = [
                agent_id
                for agent_id in self._known_persisted_ids
                if self._owner_path.get(agent_id) == persistence_path
                and agent_id not in on_disk
            ]
            for agent_id in sibling_deleted:
                self._tasks.pop(agent_id, None)
                self._owned_ids.discard(agent_id)
                self._known_persisted_ids.discard(agent_id)
                self._owner_path.pop(agent_id, None)

    def _refresh_sibling_tasks(self) -> None:
        """Re-sync sibling-owned tasks from the on-disk snapshot.

        Callers are expected to have already invoked
        ``_check_persistence_rebind()`` so the current persistence path is
        the one we want to refresh against.

        When two Agentao instances share one persistence file, each store
        owns only the tasks it registered; sibling-owned tasks are copies
        loaded by ``recover()``. Without refresh, those copies stay frozen
        and ``/agent status`` / the live dashboard show stale ``running``
        records after the owning instance completes, fails, or cancels
        the task. Owned tasks remain authoritative in this store's memory
        and are not reloaded; only sibling rows (and any newly-registered
        sibling tasks discovered on disk) are merged in.
        """
        persistence_path = self._resolve_persistence_path()
        path_key = self._resolve_path_key(persistence_path)
        if persistence_path is None or path_key is None:
            return
        flush_lock = _flush_lock_for(path_key)
        with flush_lock:
            on_disk = persistence.load_bg_task_store(persistence_path)
            self._drop_sibling_deleted(on_disk, persistence_path)
        with self._lock:
            for agent_id, rec in on_disk.items():
                if agent_id not in self._owned_ids:
                    self._tasks[agent_id] = rec
            stale = [
                agent_id for agent_id in self._tasks
                if agent_id not in self._owned_ids and agent_id not in on_disk
            ]
            for agent_id in stale:
                self._tasks.pop(agent_id, None)

    def _flush_to_disk(self) -> None:
        """Flush each owned task to its pinned persistence path.

        Tasks owned by this store are grouped by ``_owner_path`` and
        flushed under that path's per-path lock. This decouples flushes
        from the store's *current* resolved persistence path, so an
        in-flight task that survived a cwd rebind still writes back to
        the project where it was registered.
        """
        with self._lock:
            owner_path_snap = dict(self._owner_path)
        owned_paths = {
            owner_path_snap[aid] for aid in owner_path_snap
        }
        if not owned_paths:
            return

        for persistence_path in owned_paths:
            path_key = self._resolve_path_key(persistence_path)
            if path_key is None:
                continue
            # Per-path lock so concurrent flushes from sibling stores
            # anchored to the same file cannot interleave their
            # load-modify-save cycles.
            flush_lock = _flush_lock_for(path_key)
            with flush_lock:
                on_disk = persistence.load_bg_task_store(persistence_path)
                self._drop_sibling_deleted(on_disk, persistence_path)

                with self._lock:
                    owned_for_path = {
                        aid for aid in self._owned_ids
                        if self._owner_path.get(aid) == persistence_path
                    }
                    tasks_snap = {
                        aid: dict(self._tasks[aid])
                        for aid in owned_for_path
                        if aid in self._tasks
                    }

                merged: Dict[str, Dict[str, Any]] = dict(on_disk)
                for aid in owned_for_path:
                    if aid in tasks_snap:
                        merged[aid] = tasks_snap[aid]
                    else:
                        # Owned but absent from in-memory state ⇒ deleted
                        # by this store; drop it from on-disk too.
                        merged.pop(aid, None)
                persistence.save_bg_task_store(persistence_path, merged)

                with self._lock:
                    # Refresh _known_persisted_ids for tasks pinned here.
                    self._known_persisted_ids -= {
                        aid for aid in self._known_persisted_ids
                        if self._owner_path.get(aid) == persistence_path
                    }
                    self._known_persisted_ids.update(
                        aid for aid in owned_for_path if aid in merged
                    )
                    # Tasks owned-but-removed (delete()s) are now off-disk;
                    # drop their ownership/pin so subsequent flushes don't
                    # consider them anymore.
                    for aid in owned_for_path:
                        if aid not in merged:
                            self._owned_ids.discard(aid)
                            self._owner_path.pop(aid, None)
