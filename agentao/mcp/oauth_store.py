"""The OAuth credential record store and its locks (docs/design/mcp-oauth.md §6, §5.3).

One JSON file per MCP server URL under ``user_root()/mcp-oauth/``, mode 0600 in
a 0700 directory, written atomically. Every write — a refresh, a login's
commit, a logout — goes through :meth:`OAuthRuntime.exclusive`, which holds two
locks for the whole read-modify-write:

* a per-record ``asyncio.Lock`` owned by the runtime, i.e. by one
  ``McpClientManager`` and its event loop. Every holder takes it first, so the
  file lock below is never asked twice on that loop — ``filelock.FileLock`` is
  reentrant per thread, so two coroutines on one thread would otherwise both
  "hold" it at once;
* a ``FileLock`` on ``<hash>.json.lock``, acquired by polling
  ``acquire(timeout=0)`` with an ``asyncio.sleep`` between tries, so a lock
  held by another process (or another manager in this one) never blocks the
  event loop every MCP server shares.

Once both are held, the work runs as its own task under ``asyncio.shield``: a
caller cancelled mid-refresh cannot abandon a rotation between the token
endpoint spending the old refresh token and the new one reaching disk. The
runtime remembers those tasks so the manager's shutdown can wait for them
(:meth:`OAuthRuntime.wait_critical`) — ``shield`` survives a caller's cancel,
not the loop stopping.

This module imports nothing from the MCP SDK, so the record format and the
locking can be tested without it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, TypeVar
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Record format version. A file with any other version is ignored, not
#: guessed at: an unreadable record means "log in again", never a crash.
RECORD_VERSION = 1

#: How long a refresh's token request may take. Bounds the time a logout or a
#: login's commit can wait behind it, and the manager's shutdown wait.
TOKEN_REQUEST_TIMEOUT_S = 30.0

#: Polling interval for a file lock held elsewhere.
_LOCK_POLL_S = 0.05

#: Past the token timeout, how much longer to wait for a file lock before
#: giving up with an ordinary error (another process's critical section is
#: bounded by the same token timeout).
_LOCK_MARGIN_S = 5.0


class OAuthLockTimeout(TimeoutError):
    """Another holder kept a credential record locked past its own bound."""


def canonical_server_url(url: str) -> str:
    """The URL a record is keyed by: lowercase scheme and host, no default port, no fragment.

    The path and query are kept exactly — ``/mcp`` and ``/mcp/`` can be two
    different endpoints, and guessing otherwise would hand one server's token
    to another.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    port = parts.port
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path, parts.query, ""))


@dataclass
class OAuthRecord:
    """One server's stored credential (docs/design/mcp-oauth.md §6.2).

    ``issuer`` is the SDK's binding key — ``context.auth_server_url``, falling
    back to the metadata issuer only when that is unset — stored exactly as the
    SDK reported it and compared exactly. ``expires_at`` is absolute (epoch
    seconds), or ``None`` when the server sent no ``expires_in``.
    """

    server_url: str
    issuer: Optional[str]
    token_endpoint: str
    access_token: str
    token_type: str = "Bearer"
    resource: Optional[str] = None
    token_endpoint_auth_methods: List[str] = field(default_factory=list)
    client_info: Dict[str, Any] = field(default_factory=dict)
    expires_at: Optional[float] = None
    refresh_token: Optional[str] = None
    scope: Optional[str] = None

    def expires_within(self, seconds: float, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) + seconds >= self.expires_at

    def to_json(self) -> Dict[str, Any]:
        return {"version": RECORD_VERSION, **asdict(self)}

    @classmethod
    def from_json(cls, data: Any) -> Optional["OAuthRecord"]:
        if not isinstance(data, dict) or data.get("version") != RECORD_VERSION:
            return None
        try:
            record = cls(
                server_url=data["server_url"],
                issuer=data.get("issuer"),
                token_endpoint=data["token_endpoint"],
                access_token=data["access_token"],
                token_type=data.get("token_type") or "Bearer",
                resource=data.get("resource"),
                token_endpoint_auth_methods=list(data.get("token_endpoint_auth_methods") or []),
                client_info=dict(data.get("client_info") or {}),
                expires_at=data.get("expires_at"),
                refresh_token=data.get("refresh_token"),
                scope=data.get("scope"),
            )
        except (KeyError, TypeError, ValueError):
            return None
        for name in ("server_url", "token_endpoint", "access_token"):
            if not isinstance(getattr(record, name), str) or not getattr(record, name):
                return None
        if record.expires_at is not None and not isinstance(record.expires_at, (int, float)):
            return None
        return record


class RecordStore:
    """Files on disk. Reads need no lock — writes are atomic replaces."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _stem(self, server_url: str) -> str:
        return hashlib.sha256(canonical_server_url(server_url).encode("utf-8")).hexdigest()

    def path(self, server_url: str) -> Path:
        return self.root / f"{self._stem(server_url)}.json"

    def lock_path(self, server_url: str) -> Path:
        # Separate from the record, and never deleted by agentao — not even by
        # a logout: removing a lock file another process is waiting on would
        # let two holders exist. (filelock itself may unlink it on release; it
        # handles that race on its own side.)
        return self.root / f"{self._stem(server_url)}.json.lock"

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:  # pragma: no cover - e.g. a filesystem without modes
            pass

    def stamp(self, server_url: str) -> Optional[float]:
        """The record file's mtime, or ``None`` — a cheap "did it change?"."""
        try:
            return self.path(server_url).stat().st_mtime_ns / 1e9
        except OSError:
            return None

    def load(self, server_url: str) -> Optional[OAuthRecord]:
        try:
            raw = self.path(server_url).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            # Garbage, not an I/O failure: treated like malformed JSON (an
            # unreadable record, logged below), so a login can replace it.
            raw = None
        except OSError as e:
            logger.warning(f"MCP OAuth: could not read the credential record: {e}")
            return None
        try:
            record = OAuthRecord.from_json(json.loads(raw)) if raw is not None else None
        except ValueError:
            record = None
        if record is None:
            logger.warning(
                "MCP OAuth: ignoring an unreadable credential record for this server; "
                "log in again to replace it"
            )
            return None
        # The guard opencode's ``getForUrl`` has: a record names the URL it was
        # written for, and a hash collision or a hand-copied file is ignored.
        try:
            stored_url = canonical_server_url(record.server_url)
        except ValueError:
            # A damaged record (``https://h:bad/mcp``) is unreadable like
            # malformed JSON, so a login can replace it rather than fail on it.
            logger.warning(
                "MCP OAuth: ignoring a credential record with an unparseable URL; "
                "log in again to replace it"
            )
            return None
        if stored_url != canonical_server_url(server_url):
            return None
        return record

    def save(self, record: OAuthRecord) -> None:
        self.ensure_root()
        target = self.path(record.server_url)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(self.root))
        try:
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):  # pragma: no cover - Windows
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(record.to_json(), fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def delete(self, server_url: str) -> bool:
        try:
            self.path(server_url).unlink()
            return True
        except FileNotFoundError:
            return False


def default_root() -> Path:
    from ..paths import user_root

    return user_root() / "mcp-oauth"


class OAuthRuntime:
    """Per-manager OAuth state: the store, the per-record locks, the shielded tasks.

    Bound to one event loop (the manager's). Never shared between managers;
    between managers, and between processes, the file lock is the exclusion.
    """

    def __init__(self, root: Optional[Path] = None, *, token_timeout: float = TOKEN_REQUEST_TIMEOUT_S):
        self._root = Path(root) if root is not None else None
        self.token_timeout = token_timeout
        self._locks: Dict[str, asyncio.Lock] = {}
        self._critical: Set["asyncio.Task[Any]"] = set()

    @property
    def store(self) -> RecordStore:
        # Resolved lazily so a test that patches the home directory sees it.
        return RecordStore(self._root if self._root is not None else default_root())

    def _lock(self, server_url: str) -> asyncio.Lock:
        key = canonical_server_url(server_url)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def locked(self, server_url: str) -> bool:
        return self._lock(server_url).locked()

    async def _acquire_file_lock(self, server_url: str) -> Any:
        from filelock import FileLock, Timeout

        store = self.store
        store.ensure_root()
        lock = FileLock(str(store.lock_path(server_url)))
        deadline = time.monotonic() + self.token_timeout + _LOCK_MARGIN_S
        while True:
            try:
                lock.acquire(timeout=0)
                return lock
            except Timeout:
                if time.monotonic() >= deadline:
                    raise OAuthLockTimeout(
                        "the MCP OAuth credential record stayed locked by another "
                        "process; try again"
                    ) from None
                await asyncio.sleep(_LOCK_POLL_S)

    async def exclusive(self, server_url: str, work: Callable[[], Awaitable[T]]) -> T:
        """Run ``work`` holding the record's in-process lock and file lock.

        Waiting is cancellable and holds nothing. Once both locks are held,
        ``work`` runs as a shielded task that releases them itself, so a
        cancelled caller returns at once while the task finishes and writes.
        """
        lock = self._lock(server_url)
        await lock.acquire()
        try:
            file_lock = await self._acquire_file_lock(server_url)
        except BaseException:
            lock.release()
            raise

        async def critical() -> T:
            try:
                return await work()
            finally:
                try:
                    file_lock.release()
                finally:
                    lock.release()

        task = asyncio.get_running_loop().create_task(critical())
        self._critical.add(task)
        task.add_done_callback(self._critical.discard)
        # A caller cancelled while the task runs never reads its outcome.
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return await asyncio.shield(task)

    async def wait_critical(self, timeout: float) -> int:
        """Wait for shielded critical sections in flight; return how many were left."""
        pending = {t for t in self._critical if not t.done()}
        if not pending:
            return 0
        _, still = await asyncio.wait(pending, timeout=timeout)
        return len(still)
