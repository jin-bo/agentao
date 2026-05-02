"""Reference host for the four ``agentao.host.protocols`` extension points.

Embedded hosts can replace any of Agentao's IO surfaces without forking.
This module demonstrates each one with a small, runnable implementation:

* :class:`InMemoryFileSystem` — dict-backed :class:`FileSystem`. Useful in
  tests, sandboxes, or hosts that virtualize the working tree (a Git ref,
  a tar archive, an S3 prefix).
* :class:`AuditingShellExecutor` — wraps an inner :class:`ShellExecutor`
  and appends every command to an audit log. Real deployments swap the
  inner executor for Docker exec or a remote runner.
* :class:`RecordingMCPRegistry` — counts ``list_servers()`` calls so a
  test can assert the registry was consulted. Real hosts replace this
  with a plugin discovery mechanism or a remote registry.
* :class:`DictMemoryStore` — a complete :class:`MemoryStore` backed by
  a Python ``dict``. The shape mirrors what a Redis / Postgres adapter
  has to satisfy.

The :func:`make_agent` factory shows the wiring: every protocol slot is
populated, no ``.agentao/`` directory is touched, and a fake LLM client
keeps the demo offline.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional
from unittest.mock import MagicMock

from agentao import Agentao
from agentao.host.protocols import (
    BackgroundHandle,
    FileEntry,
    FileStat,
    ShellRequest,
    ShellResult,
)
from agentao.memory import MemoryManager
from agentao.memory.models import (
    MemoryRecord,
    MemoryReviewItem,
    SessionSummaryRecord,
)


# ---------------------------------------------------------------------------
# 1. FileSystem — dict-backed, no real disk
# ---------------------------------------------------------------------------


class InMemoryFileSystem:
    """:class:`agentao.host.protocols.FileSystem` backed by a dict.

    Stores file contents as bytes keyed by absolute path. Directory
    membership is implicit: a path is a directory if some stored key
    begins with ``str(path) + os.sep``. Mutations are guarded by a lock
    so the host can hand the same instance to concurrent runs.
    """

    def __init__(self, files: Optional[Dict[str, bytes]] = None) -> None:
        self._files: Dict[str, bytes] = dict(files or {})
        self._lock = threading.Lock()

    # --- helpers -------------------------------------------------------
    @staticmethod
    def _key(path: Path) -> str:
        return str(Path(path).expanduser())

    def _is_dir_key(self, path: Path) -> bool:
        prefix = self._key(path).rstrip(os.sep) + os.sep
        return any(k.startswith(prefix) for k in self._files)

    # --- protocol surface ---------------------------------------------
    def read_bytes(self, path: Path) -> bytes:
        try:
            return self._files[self._key(path)]
        except KeyError as exc:
            raise FileNotFoundError(str(path)) from exc

    def read_partial(self, path: Path, n: int) -> bytes:
        return self.read_bytes(path)[:n]

    def open_text(self, path: Path) -> Iterator[str]:
        data = self.read_bytes(path)
        return iter(StringIO(data.decode("utf-8")).readlines())

    def write_text(self, path: Path, data: str, *, append: bool = False) -> None:
        key = self._key(path)
        with self._lock:
            existing = self._files.get(key, b"") if append else b""
            self._files[key] = existing + data.encode("utf-8")

    def list_dir(self, path: Path) -> List[FileEntry]:
        prefix = self._key(path).rstrip(os.sep) + os.sep
        seen: Dict[str, FileEntry] = {}
        for key, blob in self._files.items():
            if not key.startswith(prefix):
                continue
            tail = key[len(prefix):]
            head, _, _rest = tail.partition(os.sep)
            if _rest:
                seen.setdefault(
                    head, FileEntry(name=head, is_dir=True, is_file=False, size=0)
                )
            else:
                seen[head] = FileEntry(
                    name=head, is_dir=False, is_file=True, size=len(blob)
                )
        return list(seen.values())

    def glob(self, base: Path, pattern: str, *, recursive: bool) -> List[Path]:
        # Simple substring fallback — good enough for fixtures. Real
        # adapters can lean on ``fnmatch.fnmatch`` for full glob support.
        prefix = self._key(base).rstrip(os.sep) + os.sep
        return [
            Path(k)
            for k in self._files
            if k.startswith(prefix) and pattern.strip("*") in k
        ]

    def stat(self, path: Path) -> FileStat:
        key = self._key(path)
        if key in self._files:
            return FileStat(
                size=len(self._files[key]),
                mtime=0.0,
                is_dir=False,
                is_file=True,
            )
        if self._is_dir_key(Path(key)):
            return FileStat(size=0, mtime=0.0, is_dir=True, is_file=False)
        raise FileNotFoundError(str(path))

    def exists(self, path: Path) -> bool:
        key = self._key(path)
        return key in self._files or self._is_dir_key(Path(key))

    def is_dir(self, path: Path) -> bool:
        return self._is_dir_key(path)

    def is_file(self, path: Path) -> bool:
        return self._key(path) in self._files


# ---------------------------------------------------------------------------
# 2. ShellExecutor — audit proxy around an inner executor
# ---------------------------------------------------------------------------


@dataclass
class _AuditEntry:
    command: str
    cwd: Path
    timed_out: bool
    returncode: int


class AuditingShellExecutor:
    """:class:`ShellExecutor` that records every call before delegating.

    Hosts that need a tamper-evident command log (compliance, replay)
    wrap their real executor — local subprocess, Docker exec, remote
    SSH — with a shim like this. ``run_background`` is intentionally
    refused; the host opts in only when the real executor supports it.
    """

    def __init__(
        self,
        *,
        inner: Optional[Callable[[ShellRequest], ShellResult]] = None,
    ) -> None:
        self.entries: List[_AuditEntry] = []
        self._inner = inner or self._default_inner

    @staticmethod
    def _default_inner(request: ShellRequest) -> ShellResult:
        # Deterministic stub so the demo never spawns a real subprocess.
        return ShellResult(
            returncode=0,
            stdout=f"[audited] {request.command}\n".encode("utf-8"),
            stderr=b"",
            timed_out=False,
        )

    def run(self, request: ShellRequest) -> ShellResult:
        result = self._inner(request)
        self.entries.append(
            _AuditEntry(
                command=request.command,
                cwd=request.cwd,
                timed_out=result.timed_out,
                returncode=result.returncode,
            )
        )
        return result

    def run_background(self, request: ShellRequest) -> BackgroundHandle:
        raise NotImplementedError(
            "audit shell does not allow detached processes"
        )


# ---------------------------------------------------------------------------
# 3. MCPRegistry — programmatic source with call counter
# ---------------------------------------------------------------------------


class RecordingMCPRegistry:
    """:class:`MCPRegistry` returning a fixed dict and counting reads.

    Useful for asserting the registry was actually consulted at agent
    construction. Real hosts return server configs from a plugin system
    or a service discovery backend; the :class:`Dict` shape matches
    ``agentao.mcp.config.McpServerConfig``.
    """

    def __init__(self, servers: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._servers: Dict[str, Dict[str, Any]] = dict(servers or {})
        self.calls = 0

    def list_servers(self) -> Dict[str, Dict[str, Any]]:
        self.calls += 1
        return {name: dict(cfg) for name, cfg in self._servers.items()}


# ---------------------------------------------------------------------------
# 4. MemoryStore — dict-backed, soft-delete aware
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class DictMemoryStore:
    """Minimal :class:`MemoryStore` backed by Python dicts.

    Demonstrates the round-trip surface a remote-backed adapter has to
    satisfy: persistent memory CRUD with soft-delete, session summaries,
    and the crystallization review queue. Behavior matches
    :class:`agentao.memory.SQLiteMemoryStore` closely enough that
    :class:`agentao.memory.MemoryManager` works against it without
    changes.
    """

    memories: Dict[str, MemoryRecord] = field(default_factory=dict)
    summaries: List[SessionSummaryRecord] = field(default_factory=list)
    review: Dict[str, MemoryReviewItem] = field(default_factory=dict)

    # --- memory CRUD --------------------------------------------------
    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        existing = self.get_memory_by_scope_key(record.scope, record.key_normalized)
        if existing:
            existing.title = record.title
            existing.content = record.content
            existing.tags = list(record.tags)
            existing.keywords = list(record.keywords)
            existing.type = record.type
            existing.source = record.source
            existing.confidence = record.confidence
            existing.sensitivity = record.sensitivity
            existing.updated_at = _now_iso()
            return existing
        record.created_at = record.created_at or _now_iso()
        record.updated_at = _now_iso()
        self.memories[record.id] = record
        return record

    def get_memory_by_id(self, memory_id: str) -> Optional[MemoryRecord]:
        rec = self.memories.get(memory_id)
        return rec if rec and rec.deleted_at is None else None

    def get_memory_by_scope_key(
        self, scope: str, key_normalized: str
    ) -> Optional[MemoryRecord]:
        for rec in self.memories.values():
            if (
                rec.scope == scope
                and rec.key_normalized == key_normalized
                and rec.deleted_at is None
            ):
                return rec
        return None

    def list_memories(self, scope: Optional[str] = None) -> List[MemoryRecord]:
        return [
            r
            for r in self.memories.values()
            if r.deleted_at is None and (scope is None or r.scope == scope)
        ]

    def search_memories(
        self, query: str, scope: Optional[str] = None
    ) -> List[MemoryRecord]:
        q = query.lower()
        out: List[MemoryRecord] = []
        for r in self.list_memories(scope):
            haystack = " ".join(
                [r.title, r.content, r.key_normalized, *r.tags, *r.keywords]
            ).lower()
            if q in haystack:
                out.append(r)
        return out

    def filter_by_tag(
        self, tag: str, scope: Optional[str] = None
    ) -> List[MemoryRecord]:
        target = tag.lower()
        return [
            r
            for r in self.list_memories(scope)
            if any(t.lower() == target for t in r.tags)
        ]

    def soft_delete_memory(self, memory_id: str) -> bool:
        rec = self.memories.get(memory_id)
        if rec is None or rec.deleted_at is not None:
            return False
        rec.deleted_at = _now_iso()
        return True

    def clear_memories(self, scope: Optional[str] = None) -> int:
        n = 0
        for r in self.memories.values():
            if r.deleted_at is None and (scope is None or r.scope == scope):
                r.deleted_at = _now_iso()
                n += 1
        return n

    # --- session summaries -------------------------------------------
    def save_session_summary(self, record: SessionSummaryRecord) -> None:
        self.summaries.append(record)

    def list_session_summaries(
        self, session_id: Optional[str] = None, limit: int = 20
    ) -> List[SessionSummaryRecord]:
        rows = [s for s in self.summaries if session_id is None or s.session_id == session_id]
        rows.sort(key=lambda s: s.created_at, reverse=True)
        return rows[:limit]

    def clear_session_summaries(self, session_id: Optional[str] = None) -> int:
        if session_id is None:
            n = len(self.summaries)
            self.summaries.clear()
            return n
        keep = [s for s in self.summaries if s.session_id != session_id]
        n = len(self.summaries) - len(keep)
        self.summaries = keep
        return n

    # --- review queue -------------------------------------------------
    def upsert_review_item(self, item: MemoryReviewItem) -> MemoryReviewItem:
        for existing in self.review.values():
            if (
                existing.scope == item.scope
                and existing.key_normalized == item.key_normalized
                and existing.status == "pending"
            ):
                existing.title = item.title
                existing.content = item.content
                existing.tags = list(item.tags)
                existing.evidence = item.evidence
                existing.source_session = item.source_session
                existing.occurrences += max(item.occurrences, 1)
                existing.confidence = (
                    "inferred" if existing.occurrences >= 2 else item.confidence
                )
                existing.updated_at = _now_iso()
                return existing
        item.created_at = item.created_at or _now_iso()
        item.updated_at = _now_iso()
        self.review[item.id] = item
        return item

    def get_review_item(self, item_id: str) -> Optional[MemoryReviewItem]:
        return self.review.get(item_id)

    def list_review_items(
        self, status: Optional[str] = "pending", limit: int = 50
    ) -> List[MemoryReviewItem]:
        rows = [
            r for r in self.review.values() if status is None or r.status == status
        ]
        rows.sort(key=lambda r: (-r.occurrences, r.created_at), reverse=False)
        return rows[:limit]

    def update_review_status(self, item_id: str, status: str) -> bool:
        item = self.review.get(item_id)
        if item is None:
            return False
        item.status = status  # type: ignore[assignment]
        item.updated_at = _now_iso()
        return True


# ---------------------------------------------------------------------------
# Factory: an Agentao with all four protocols injected
# ---------------------------------------------------------------------------


def _fake_llm_client() -> MagicMock:
    """Mimic the ``LLMClient`` shape Agentao reads at construction."""
    fake = MagicMock(name="FakeLLMClient")
    fake.logger = MagicMock(name="FakeLLMLogger")
    fake.model = "fake-model"
    fake.api_key = "fake-key"
    fake.base_url = "http://localhost:1"
    fake.temperature = 0.0
    fake.max_tokens = 256
    fake.total_prompt_tokens = 0
    fake.total_completion_tokens = 0
    return fake


@dataclass
class HostHandles:
    """Bundled references to every injected protocol implementation."""

    agent: Agentao
    filesystem: InMemoryFileSystem
    shell: AuditingShellExecutor
    mcp: RecordingMCPRegistry
    memory_store: DictMemoryStore


def make_agent(
    working_directory: Path,
    *,
    files: Optional[Dict[str, bytes]] = None,
    mcp_servers: Optional[Dict[str, Dict[str, Any]]] = None,
) -> HostHandles:
    """Construct an ``Agentao`` with every host protocol injected.

    The caller seeds the in-memory FS with ``files`` (path → bytes) and
    the registry with ``mcp_servers`` (server-name → config dict). The
    returned :class:`HostHandles` keep a reference to each adapter so a
    test or host can read back what the agent did to them.
    """
    fs = InMemoryFileSystem(files=files)
    shell = AuditingShellExecutor()
    mcp = RecordingMCPRegistry(servers=mcp_servers)
    store = DictMemoryStore()

    agent = Agentao(
        working_directory=working_directory,
        llm_client=_fake_llm_client(),
        filesystem=fs,
        shell=shell,
        mcp_registry=mcp,
        memory_manager=MemoryManager(project_store=store),
    )
    return HostHandles(
        agent=agent, filesystem=fs, shell=shell, mcp=mcp, memory_store=store
    )


__all__ = [
    "AuditingShellExecutor",
    "DictMemoryStore",
    "HostHandles",
    "InMemoryFileSystem",
    "RecordingMCPRegistry",
    "make_agent",
]
