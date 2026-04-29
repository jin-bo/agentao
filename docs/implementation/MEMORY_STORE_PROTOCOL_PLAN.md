# Issue #16 Implementation Plan: `MemoryStore` Protocol + `MemoryManager` Refactor

**Date:** 2026-04-29
**Status:** Plan locked in; ready to execute (revised after over-design review — see "Revision notes" below)
**Source epic:** [`EMBEDDED_HARNESS_GITHUB_EPIC.md`](EMBEDDED_HARNESS_GITHUB_EPIC.md) — Issue 10 (became GitHub #16)
**Strategy doc:** [`EMBEDDED_HARNESS_IMPLEMENTATION_PLAN.md`](EMBEDDED_HARNESS_IMPLEMENTATION_PLAN.md)
**Milestone:** M5 of the embedded-harness epic — bundles with Issues #17 (`MCPRegistry`) and #18 (`docs/EMBEDDING.md`) to cut `0.3.0`

## Revision notes (2026-04-29)

The first draft was reviewed for over-design. Five things were cut or
tightened; two borderline calls were left as judgment for the executor.
The body below is the **revised** plan — all sections reflect the
post-review decisions. The cuts:

1. **Dropped `@runtime_checkable` on the Protocol.** No `isinstance(store, MemoryStore)` callsite is planned, and the existing `isinstance(...)` checks in `agentao/tools/shell.py:237` / `agentao/tools/search.py:234` test the **concrete class** (`LocalShellExecutor` / `LocalFileSystem`), which doesn't need the decorator. The "match `filesystem.py` precedent" justification in the original draft was cargo-cult.
2. **Dropped the `MemoryManager.from_paths()` classmethod (Strategy A).** The first draft picked it explicitly to avoid test churn — but ~25 mechanical edits across 12 test files is exactly what `sed` is for, and a single constructor surface beats two for a public API. Strategy B (purist) below.
3. **Replaced `open_or_memory(..., fallback_to_memory: bool)` with two methods** (`SQLiteMemoryStore.open()` strict, `SQLiteMemoryStore.open_or_memory()` falls back). Two callsites with two different bool values is a weak abstraction; method names disambiguate semantics directly.
4. **Dropped the new file `tests/test_factory_memory_store_fallback.py`.** One assertion doesn't justify a new file. The ACP regression tests in `tests/test_per_session_cwd.py:603-655` already cover factory-level memory fallback at the right layer; the user-DB-failure case is one more assertion in that suite.
5. **Rewrote the CHANGELOG `### Removed` framing.** With Strategy B, `MemoryManager(project_root=, global_root=)` is genuinely removed (not renamed to a classmethod). The migration text now says explicitly "construct stores via `SQLiteMemoryStore.open_or_memory(...)` then pass them in."

Two borderline items left as judgment calls for the executor:

- **§5.1 `InMemoryMemoryStore` size** — full reimpl (~200 lines, matches `tests/test_filesystem_capability_swap.py`) vs Spy that records calls (~50 lines, leaner). Both are defensible. Default to the full reimpl for precedent consistency.
- **§3 re-export of `SQLiteMemoryStore` from `capabilities/__init__.py`** — clean abstraction says capabilities re-exports protocols only; ergonomics says re-export concrete class for symmetry with `LocalFileSystem`. Default to the symmetry-with-precedent choice.

---

## Pre-flight verification (verify before executing)

- **Assumes** the only callers reaching into `MemoryManager.project_store` / `user_store` directly are `MemoryCrystallizer.submit_to_review` (`agentao/memory/crystallizer.py:213`), `.promote` (`:252`), and the two test fault-injection tests (`tests/test_memory_manager.py:392-454`, `tests/test_per_session_cwd.py:603-614`). **Verify** by `grep -rn "\.project_store\|\.user_store" agentao/ tests/`.
- **Assumes** `MemoryRetriever` accesses `manager.write_version` and `manager.get_all_entries()` only — no SQLite-specific access. Confirmed by reading `agentao/memory/retriever.py:234-247`.
- **Assumes** `agentao/memory/render.py` does not depend on store internals. Confirmed (no `store`/`SQLite`/`sqlite3` references).

---

## 1. Protocol surface (`agentao/capabilities/memory.py`)

**Bias toward byte-equivalent lift, not redesign.** The Protocol is exactly the public methods of `SQLiteMemoryStore` minus the internal helpers. The leaky-abstraction audit is at the bottom.

### 1.1 Methods to put on `MemoryStore` Protocol

Mirrors `SQLiteMemoryStore` (`agentao/memory/storage.py:86-512`) lift-and-shift, **all model dataclasses, no `sqlite3.Row`** at the boundary:

```python
from __future__ import annotations
from typing import List, Optional, Protocol
from ..memory.models import (
    MemoryRecord,
    MemoryReviewItem,
    SessionSummaryRecord,
)


class MemoryStore(Protocol):
    """Persistent memory contract.

    Embedded hosts inject this to redirect memory + session-summary +
    review-queue persistence through their own storage (Redis,
    Postgres, in-memory, remote API). The default
    :class:`agentao.memory.storage.SQLiteMemoryStore` is byte-equivalent
    to the pre-Protocol implementation.

    Schema-less contract: implementations only need to round-trip the
    model dataclasses (``MemoryRecord``, ``SessionSummaryRecord``,
    ``MemoryReviewItem``). No SQL, no row factories, no connection
    objects bleed across this boundary.

    Soft-delete semantics: ``upsert_memory`` / ``list_memories`` /
    ``search_memories`` / ``filter_by_tag`` / ``get_memory_by_id`` /
    ``get_memory_by_scope_key`` exclude soft-deleted rows.
    ``soft_delete_memory`` / ``clear_memories`` set ``deleted_at`` on
    rows; they do NOT physically remove. Implementations that lack a
    soft-delete primitive should mark the record and filter on read.

    Lifecycle: ``MemoryManager`` does not call ``close``; the factory
    or embedded host owns the lifetime.
    """

    # --- Memory CRUD -----------------------------------------------------
    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord: ...
    def get_memory_by_id(self, memory_id: str) -> Optional[MemoryRecord]: ...
    def get_memory_by_scope_key(
        self, scope: str, key_normalized: str
    ) -> Optional[MemoryRecord]: ...
    def list_memories(self, scope: Optional[str] = None) -> List[MemoryRecord]: ...
    def search_memories(
        self, query: str, scope: Optional[str] = None
    ) -> List[MemoryRecord]: ...
    def filter_by_tag(
        self, tag: str, scope: Optional[str] = None
    ) -> List[MemoryRecord]: ...
    def soft_delete_memory(self, memory_id: str) -> bool: ...
    def clear_memories(self, scope: Optional[str] = None) -> int: ...

    # --- Session summaries -----------------------------------------------
    def save_session_summary(self, record: SessionSummaryRecord) -> None: ...
    def list_session_summaries(
        self, session_id: Optional[str] = None, limit: int = 20
    ) -> List[SessionSummaryRecord]: ...
    def clear_session_summaries(self, session_id: Optional[str] = None) -> int: ...

    # --- Review queue ----------------------------------------------------
    def upsert_review_item(self, item: MemoryReviewItem) -> MemoryReviewItem: ...
    def get_review_item(self, item_id: str) -> Optional[MemoryReviewItem]: ...
    def list_review_items(
        self, status: Optional[str] = "pending", limit: int = 50
    ) -> List[MemoryReviewItem]: ...
    def update_review_status(self, item_id: str, status: str) -> bool: ...
```

15 methods. Exact 1:1 with `SQLiteMemoryStore` public surface (`storage.py:132-459`). `_init_db`, `_connect`, `_row_to_*`, `db_path`, `_is_memory`, `_persistent_conn` are SQLite-only and stay private to the implementation.

### 1.2 Leaky-abstraction audit

| Concern | Verdict |
|---|---|
| `sqlite3.Row` returns | **Clean.** All public methods already return `MemoryRecord` / `SessionSummaryRecord` / `MemoryReviewItem` via `_row_to_*` helpers. No `Row` escapes. |
| `db_path` attribute | Currently public on `SQLiteMemoryStore` (`storage.py:90`). Not referenced in `manager.py` or anywhere else — **safe to keep on the concrete class**, off the Protocol. |
| `_is_memory` flag | Only used inside `_connect` to share connection for `:memory:`. Stays private. Off the Protocol. |
| `crystallizer.py:213,252` reaches `manager.project_store` | Already takes `Any` shape — works on Protocol with no change. |
| Soft-delete leak | `MemoryRecord.deleted_at` is part of the dataclass. Embedded backends without soft-delete must filter on read. Documented in the docstring above. |
| Schema versioning (`_SCHEMA_VERSION = 3`) | SQLite-only concern. Off the Protocol. |
| `tags_json` / `keywords_json` raw JSON columns | Already de/serialized inside `_row_to_memory`. Not exposed at the boundary. |

**No leaky-abstraction redesign needed.** Lift unchanged.

---

## 2. Recommendation: Where the in-memory fallback lives

**Answer: Option (c) — fallback lives in the factory, `MemoryManager` always assumes well-built stores.**

Reasoning:

- The epic's `Issue 5` deletion (already shipped per `git log b3b403f`) was explicit: *delete `Path.home()` fallback from `MemoryManager`; project_root and global_root required*. Issue #16 should continue that direction, not regress it. After M5, `MemoryManager` knows nothing about disk.
- Option (a) — `MemoryStoreFactory` callables — pollutes the Protocol with constructor semantics. Hosts injecting an in-process store don't need a callable layer.
- Option (b) — fallback in `SQLiteMemoryStore` — pushes filesystem awareness into the storage class, which the Protocol abstraction is supposed to keep concrete-class-private. Also doesn't help non-SQLite backends.
- **Option (c) keeps the load-bearing tests green** because the factory (`agentao/embedding/factory.py:118-123`) is the *only* path that constructs `MemoryManager` with disk roots today. The two regression tests (`test_memory_manager.py:392`, `test_per_session_cwd.py:603-614`) monkeypatch `SQLiteMemoryStore.__init__` — those tests **migrate** to a new `tests/test_sqlite_memory_store_fallback.py` that asserts `SQLiteMemoryStore.open_or_memory(path)` (a new classmethod on the concrete class) returns a `:memory:` store on `OSError` / `sqlite3.Error`. Then the factory calls that classmethod.
- After the refactor, an embedded host that wants the fallback semantic uses the factory; an embedded host that wants strict construction passes its own pre-built `MemoryStore` and gets a clean `TypeError` if it can't open.

---

## 3. Recommendation: File layout

**Answer: Protocol in `agentao/capabilities/memory.py`. Concrete `SQLiteMemoryStore` STAYS at `agentao/memory/storage.py`.**

Reasoning:

- Capability precedent (`capabilities/filesystem.py`) co-locates `LocalFileSystem` next to its Protocol. But that's a pure-IO shim with no schema, no models, no `_init_db`, no soft-delete logic.
- `SQLiteMemoryStore` is 513 lines of schema-aware persistence that **lives next to** `models.py`, `guards.py`, `crystallizer.py`, `manager.py`. Moving it into `capabilities/` splits a cohesive subsystem across the package for cosmetic consistency.
- Compromise: re-export from `capabilities/__init__.py` so embedded hosts see `from agentao.capabilities import MemoryStore, SQLiteMemoryStore` — same import ergonomics as `FileSystem` / `LocalFileSystem`.

---

## 4. Factory wiring change (`agentao/embedding/factory.py:118-123`)

**Current:**
```python
memory_manager = overrides.pop("memory_manager", None)
if memory_manager is None:
    memory_manager = MemoryManager(
        project_root=wd / ".agentao",
        global_root=user_root(),
    )
```

**After:**
```python
memory_manager = overrides.pop("memory_manager", None)
if memory_manager is None:
    from ..memory.storage import SQLiteMemoryStore
    # Project store always succeeds — degrades to ``:memory:`` on disk error.
    project_store = SQLiteMemoryStore.open_or_memory(wd / ".agentao" / "memory.db")
    # User store is optional — disabled on disk error, not degraded.
    user_store: Optional[SQLiteMemoryStore] = None
    user = user_root()
    if user is not None:
        try:
            user_store = SQLiteMemoryStore.open(user / "memory.db")
        except (OSError, sqlite3.Error) as exc:
            logger.warning(
                "User memory store at %s unavailable (%s: %s); "
                "user-scope memory disabled for this session.",
                user / "memory.db", type(exc).__name__, exc,
            )
    memory_manager = MemoryManager(
        project_store=project_store,
        user_store=user_store,
    )
```

**Disk-fallback semantic**: project store always succeeds (`open_or_memory` falls back to `:memory:`); user store either succeeds (`open`) or is disabled with a warning. The two methods make the asymmetry explicit at the call site; no boolean disambiguation needed.

---

## 5. Test plan

### 5.1 New test: `tests/test_memory_store_swap.py`

Following the `test_filesystem_capability_swap.py` pattern. Concrete shape:

```python
"""Issue #16 — MemoryManager routes through an injected MemoryStore.

A swappable MemoryStore means embedded hosts can back memory with any
storage (Redis, Postgres, in-process dict, remote API). The tests
below confirm wire-up: a fake store captures every call and the
manager never reaches for SQLite when a fake is injected.
"""
from __future__ import annotations
from typing import Dict, List, Optional
import pytest
from agentao.capabilities import MemoryStore  # re-exported
from agentao.memory import MemoryManager, SaveMemoryRequest
from agentao.memory.models import (
    MemoryRecord, MemoryReviewItem, SessionSummaryRecord,
)


class InMemoryMemoryStore:
    """In-process fake. Required by the epic acceptance criteria."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self._memories: Dict[str, MemoryRecord] = {}
        self._summaries: Dict[str, SessionSummaryRecord] = {}
        self._reviews: Dict[str, MemoryReviewItem] = {}

    # ... 15 methods, each appending to self.calls and round-tripping
    #     the dataclass through self._{memories,summaries,reviews}.
    #     Soft-delete = mutate deleted_at and filter on read.
    #     Search = case-insensitive substring over title+content+
    #     key_normalized+tags+keywords.

# Tests:
def test_manager_routes_writes_to_injected_store():
    """MemoryManager.upsert calls store.upsert_memory exactly once."""
    fake = InMemoryMemoryStore()
    mgr = MemoryManager(project_store=fake)
    mgr.upsert(SaveMemoryRequest(key="k", value="v", tags=[]))
    assert any(c.startswith("upsert_memory:") for c in fake.calls)

def test_manager_with_no_user_store_downgrades_user_scope():
    """MemoryManager(project_store=fake, user_store=None) downgrades user scope."""
    # Mirrors manager.py:120 logic; assert ends up in project store.

def test_manager_search_unions_project_and_user_stores():
    """Search across both stores when both are set."""

def test_manager_filter_by_tag_unions_both_stores():

def test_manager_session_summary_cycle():
    """save -> list -> archive -> clear_session round-trips through fake."""

def test_manager_review_queue_cycle():
    """upsert_review -> approve_review_item promotes correctly."""

def test_manager_does_not_open_any_sqlite_db(tmp_path, monkeypatch):
    """With an injected fake store, sqlite3.connect is never called."""
    import sqlite3
    real_connect = sqlite3.connect
    calls: list = []
    def trap(*a, **k):
        calls.append(a)
        return real_connect(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", trap)
    fake = InMemoryMemoryStore()
    mgr = MemoryManager(project_store=fake)
    mgr.upsert(SaveMemoryRequest(key="k", value="v", tags=[]))
    mgr.search("k")
    mgr.delete_by_title("k")
    assert calls == []  # no SQLite touch
```

### 5.2 Existing tests that must update

All `MemoryManager(project_root=..., global_root=...)` call sites migrate to the explicit-store signature. **Strategy B (purist)** — no `from_paths` convenience layer; every callsite constructs stores explicitly. Single public constructor.

The mechanical edit is local: replace
```python
MemoryManager(project_root=p, global_root=g)
```
with
```python
MemoryManager(
    project_store=SQLiteMemoryStore.open_or_memory(p / "memory.db"),
    user_store=SQLiteMemoryStore.open(g / "memory.db") if g else None,
)
```

Most test sites pass `global_root=None`, in which case the `user_store=None` form simplifies further. A small helper at the top of each affected test file (or in a shared `tests/support/memory.py`) keeps the per-test boilerplate to one line.

Sites to update (from `grep MemoryManager(` audit):

| File | Line(s) | Change |
|---|---|---|
| `tests/test_memory_guards.py` | 242, 249, 255, 261, 268, 275 | explicit-store construction |
| `tests/test_memory_renderer.py` | 357, 392 | explicit-store construction |
| `tests/test_memory_session.py` | 11, 47, 264, 300 | explicit-store construction |
| `tests/test_crystallizer.py` | 323 | explicit-store construction |
| `tests/test_context_manager.py` | 32 | explicit-store construction |
| `tests/test_memory_management.py` | 11 | explicit-store construction |
| `tests/test_memory_injection.py` | 29 | explicit-store construction |
| `tests/test_memory_store.py` | 519 | explicit-store construction |
| `tests/test_retriever.py` | 24 | explicit-store construction |
| `tests/test_memory_manager.py` | 14 (via `_make_manager`), 248, 409, 441 | factor into `_make_manager` helper using `SQLiteMemoryStore.open_or_memory` |
| `tests/test_no_subsystem_fallback_reads.py` | 89-91, 166 | the `TypeError` test on 89-91 still passes (required-arg semantic preserved); line 166 constructs stores explicitly |
| `agentao/agent.py` | 216 | inline: `MemoryManager(project_store=SQLiteMemoryStore.open_or_memory(self._working_directory / ".agentao" / "memory.db"))` |
| `agentao/embedding/factory.py` | 118-123 | explicit-store construction per §4 |

**Test-helper option:** if the per-test `SQLiteMemoryStore.open_or_memory(...)` boilerplate adds noise, extract a `tests/support/memory.py::make_memory_manager(tmp_path, *, with_user=False)` helper. ~10 lines of test infra; not part of the public API. Optional — only adopt if the inline form gets ugly.

### 5.3 `:memory:` fallback test migration

Both fault-injection tests (`test_memory_manager.py:392-420` and `test_memory_manager.py:423-453`, plus `test_per_session_cwd.py:603-614,637-655`) currently monkeypatch `SQLiteMemoryStore.__init__`. After the refactor:

- **`test_per_session_cwd.py` tests** (the load-bearing ACP regression): keep monkeypatching `SQLiteMemoryStore.__init__` — they still hit the same path because the factory is what constructs the SQLite store. The factory's new `SQLiteMemoryStore.open_or_memory(...)` and `SQLiteMemoryStore.open(...)` both flow through `__init__`, so the patch still triggers. Verify by running the tests after the refactor without changes.
- **`test_memory_manager.py:392`** (project SQLite error → `:memory:` fallback): **migrate** to `tests/test_memory_store.py` (existing — already covers SQLiteMemoryStore-direct surface) as `test_open_or_memory_falls_back_on_oserror`. The new test asserts the classmethod, not `MemoryManager` behavior — because `MemoryManager` no longer has disk knowledge.
- **`test_memory_manager.py:423`** (user SQLite error → user_store None): **migrate to `tests/test_per_session_cwd.py`** as one more test in the existing factory-fallback suite (`test_per_session_cwd.py:603-655`). That suite already tests factory-level memory fallback at the right layer — adding the user-DB-failure case is one assertion, not a new file. Asserts `agent._memory_manager.user_store is None` when `HOME` points at a fault-injected path.

---

## 6. CHANGELOG entry (drop-in body for `[Unreleased]`)

Place under the existing `[Unreleased]` block (currently has `### BREAKING` and `### Removed`):

```markdown
### Added

- **`MemoryStore` capability protocol** (Issue #16). Embedded hosts
  can now swap memory backends — Redis, Postgres, in-process dict,
  remote API — without subclassing or forking `MemoryManager`. The
  `SQLiteMemoryStore` default is unchanged and remains the CLI/ACP
  backing store. Re-exported as
  `from agentao.capabilities import MemoryStore` /
  `from agentao.capabilities import SQLiteMemoryStore` to mirror the
  `FileSystem` / `LocalFileSystem` ergonomics shipped in 0.2.16.
- `SQLiteMemoryStore.open(path)` — strict path-based constructor that
  raises on disk error. `SQLiteMemoryStore.open_or_memory(path)` —
  graceful constructor that degrades to `:memory:` on
  `OSError` / `sqlite3.OperationalError`. The two classmethods make
  the asymmetry between project-store-falls-back and
  user-store-disables explicit at every callsite.

### Changed

- `MemoryManager(project_store=..., user_store=...)` now accepts
  pre-built `MemoryStore` instances. Path-based construction (the
  pre-#16 shape) moves to the call site:
  ```python
  # before:
  mgr = MemoryManager(project_root=p, global_root=g)
  # after:
  mgr = MemoryManager(
      project_store=SQLiteMemoryStore.open_or_memory(p / "memory.db"),
      user_store=SQLiteMemoryStore.open(g / "memory.db") if g else None,
  )
  ```
- The `:memory:` fallback for unwritable project DBs has moved from
  `MemoryManager.__init__` into
  `SQLiteMemoryStore.open_or_memory(...)`. The factory uses this
  classmethod; behavior is observably identical (project store still
  degrades to `:memory:` on `OSError` / `sqlite3.OperationalError`,
  user store is still disabled with a warning on the same errors).
- `agentao.memory.MemoryManager` no longer imports `sqlite3` and has
  no filesystem knowledge. Embedded hosts that construct it directly
  with custom stores see zero disk I/O from the manager.

### Removed

- `MemoryManager.__init__(project_root=, global_root=)` — replaced by
  the explicit-store signature above. Migration: build the stores via
  `SQLiteMemoryStore.open_or_memory(path)` (or `.open(path)`) and pass
  them as `project_store=` / `user_store=` kwargs. CLI and ACP users
  see no change because the factory
  (`agentao.embedding.build_from_environment()`) absorbs the new
  construction shape internally.
```

---

## 7. Commit structure

**Recommend: single commit, b3b403f-style with a long body.**

Justification:
- The b3b403f release-cited precedent ("single deletion PR" with long commit body) was the recent Issue #5 deletion, structurally analogous: extract / move public-surface code, ripple through callsites.
- M5 changes form one logical unit: the Protocol, the manager refactor, the factory rewire, the test migration. Splitting introduces a window where the test suite is partially migrated.
- The diff is bounded — net new file (`capabilities/memory.py`), one factory edit, one manager rewrite, one storage classmethod addition, ~12 mechanical test renames. Reviewers can read it in one pass.

Suggested message header: `refactor(memory): extract MemoryStore Protocol from SQLiteMemoryStore (#16)`

---

## 8. Risks the prompt didn't list

1. **`MemoryRetriever` index invalidation** (`agentao/memory/retriever.py:234`): the inverted index keys off `manager.write_version`. If a fake store mutates state without `MemoryManager` incrementing `write_version`, the retriever returns stale results. **Mitigation:** the Protocol only sees `MemoryStore` calls; `write_version` increments stay inside `MemoryManager.upsert/delete/clear` (manager.py:147,243,246,267). No risk if callers go through the manager. Document the invariant: "direct store mutation bypasses retriever index — always go through the manager."

2. **`MemoryCrystallizer` reaches into `manager.project_store`** (`crystallizer.py:213,252`). After the refactor, `manager.project_store` is now a `MemoryStore` Protocol type. Crystallizer calls `store.upsert_review_item` and `store.update_review_status` — both on the Protocol surface. **Verified safe.** Add a type annotation `manager.project_store: MemoryStore` so the static checker keeps an eye on this.

3. **`agentao/memory/__init__.py:6,23`** re-exports `SQLiteMemoryStore`. After the refactor, also export `MemoryStore` for symmetry, but keep `SQLiteMemoryStore` exported (`test_memory_store.py:8` imports it directly).

4. **`tests/test_memory_store.py`** is 519 lines of `SQLiteMemoryStore` direct tests. None of them go through `MemoryManager`. They keep working unchanged — `SQLiteMemoryStore` keeps its public API. Verify by re-running.

5. **`@runtime_checkable` deliberately omitted.** Verified during the over-design review: no `isinstance(store, MemoryStore)` callsite is planned. Existing `isinstance` checks in `agentao/tools/shell.py:237` and `agentao/tools/search.py:234` test the **concrete class** (`LocalShellExecutor` / `LocalFileSystem`), which doesn't require `@runtime_checkable` on the Protocol. Add the decorator later if a real isinstance use case shows up.

6. **`MemoryGuard` is constructor-injected into `MemoryManager`** (`manager.py:46,50`). The new constructor must keep `guard: Optional[MemoryGuard] = None` for back-compat — none of the test callsites pass it but the API surface should stay.

7. **`_session_id` lifecycle is on `MemoryManager`, not the store.** The session uuid (`manager.py:92`) lives on the manager — which is correct, because it's a per-process session marker, not persistence state. Don't move it. `archive_session` / `clear_session` / `clear_all_session_summaries` semantics described in `manager.py:308-351` stay on the manager.

8. **The tests at `test_memory_manager.py:248` and `test_memory_session.py:47`** construct `MemoryManager(project_root=Path("/nonexistent/readonly/path"), global_root=None)` to assert no-crash on disk write failures. After migration to explicit-store construction (`MemoryManager(project_store=SQLiteMemoryStore.open_or_memory(...))`), the `:memory:` fallback path must still trigger inside `open_or_memory` for these tests to pass. **Verify** by running these tests post-refactor — same end-state, just routed through the new classmethod.

---

## 9. Execution order (numbered punch list)

Each step's deliverable is verifiable in isolation. Run `pytest tests/` after step 6 expecting full pass.

1. **Create `agentao/capabilities/memory.py`** — paste the Protocol from §1.1. Imports models from `agentao.memory.models`.

2. **Add `agentao/memory/storage.py:open_or_memory` classmethod** to `SQLiteMemoryStore`. Signature:
   ```python
   @classmethod
   def open_or_memory(
       cls, db_path: Path | str
   ) -> "SQLiteMemoryStore":
       """Open a SQLite store at db_path. Strict — raises on
       ``OSError`` / ``sqlite3.Error``."""
       Path(db_path).parent.mkdir(parents=True, exist_ok=True)
       return cls(str(db_path))

   @classmethod
   def open_or_memory(
       cls, db_path: Path | str
   ) -> "SQLiteMemoryStore":
       """Open a SQLite store at db_path; degrade to ``:memory:`` on
       ``OSError`` / ``sqlite3.Error``. Mirrors the historical
       MemoryManager.__init__ try/except (manager.py:59-73)."""
       try:
           return cls.open(db_path)
       except (OSError, sqlite3.Error) as exc:
           logger.warning(
               "Memory store at %s unavailable (%s: %s); "
               "falling back to transient in-memory store.",
               db_path, type(exc).__name__, exc,
           )
           return cls(":memory:")
   ```
   Add `import logging; logger = logging.getLogger(__name__)` reusing the existing module logger (`storage.py:13`). Two methods, no boolean — semantic asymmetry between project store (always succeeds) and user store (disabled on failure) is now explicit at every call site.

3. **Rewrite `agentao/memory/manager.py:42-95`** — new constructor:
   ```python
   def __init__(
       self,
       project_store: "MemoryStore",
       user_store: Optional["MemoryStore"] = None,
       guard: Optional[MemoryGuard] = None,
   ) -> None:
       self.project_store = project_store
       self.user_store = user_store
       self.guard = guard or MemoryGuard()
       self._session_id: str = uuid.uuid4().hex[:12]
       self._write_version: int = 0
   ```
   - Delete `_project_root` / `_global_root` storage (manager.py:48-49).
   - Delete the two try/except blocks (manager.py:59-89).
   - Delete `import sqlite3` and `from pathlib import Path` if unused after edit (Path stays — it's used in `_USERDICT_PATH`-style code? Confirm by re-reading). Path is not used after the edit; remove the import.
   - Update `_store_for_scope` return type to `"MemoryStore"` (manager.py:442).

4. **Update `agentao/memory/__init__.py`** to also export `MemoryStore` (Protocol). Re-export from `capabilities/memory.py`:
   ```python
   from ..capabilities.memory import MemoryStore
   __all__ = [..., "MemoryStore", ...]
   ```

5. **Update `agentao/capabilities/__init__.py:9-33`** to add `MemoryStore` and re-export `SQLiteMemoryStore`:
   ```python
   from .memory import MemoryStore
   from ..memory.storage import SQLiteMemoryStore
   __all__ = [..., "MemoryStore", "SQLiteMemoryStore"]
   ```

6. **Update `agentao/agent.py:216`** — replace the bare `MemoryManager(project_root=..., global_root=None)` call with explicit-store construction:
   ```python
   from .memory.storage import SQLiteMemoryStore
   self._memory_manager = MemoryManager(
       project_store=SQLiteMemoryStore.open_or_memory(
           self.working_directory / ".agentao" / "memory.db"
       ),
   )
   ```
   The bare-construction path stays project-scope-only (matches current `global_root=None` behavior).

7. **Rewrite `agentao/embedding/factory.py:118-123`** per §4. Add `import sqlite3` (used for the user-store try/except). Re-use the existing module logger.

8. **Migrate test callsites** per §5.2 — the 12-file mechanical migration from `MemoryManager(project_root=p, global_root=g)` → explicit-store construction. Use `grep -rln "MemoryManager(project_root=" tests/` to find them. Optional: extract a `tests/support/memory.py::make_memory_manager()` helper if the per-test boilerplate gets noisy.

9. **Migrate fault-injection tests** per §5.3:
    - `test_memory_manager.py:392-420` → migrate to a new test in `tests/test_memory_store.py` that calls `SQLiteMemoryStore.open_or_memory("/nonexistent/...")` and asserts in-memory fallback.
    - `test_memory_manager.py:423-453` → migrate to `tests/test_per_session_cwd.py` as one more test in the existing factory-fallback suite. Asserts `agent._memory_manager.user_store is None` when `HOME` points at a fault-injected path.
    - `test_per_session_cwd.py:603-614,637-655` → unchanged. Verify they still pass (the SQLiteMemoryStore.__init__ patch fires inside `open` / `open_or_memory`).

10. **Add `tests/test_memory_store_swap.py`** per §5.1.

11. **Update CHANGELOG** per §6.

12. **Run the full suite** (`pytest tests/`). Expect 2090+ passed, 0 failed (the new swap tests add ~7 cases plus the migrated user-DB-failure assertion; other test counts unchanged). Verify the `test_per_session_cwd.py` ACP regression and `test_no_subsystem_fallback_reads.py:91` `TypeError` test both pass without modification.

13. **Commit** as a single commit with the message style of `b3b403f` — long body referencing Issue #16 and the M5 milestone.

---

## Critical Files for Implementation

- `/Users/bluerose/Documents/Data/ToDo/2024-AGI/src/agentao/agentao/memory/manager.py`
- `/Users/bluerose/Documents/Data/ToDo/2024-AGI/src/agentao/agentao/memory/storage.py`
- `/Users/bluerose/Documents/Data/ToDo/2024-AGI/src/agentao/agentao/capabilities/memory.py` (new)
- `/Users/bluerose/Documents/Data/ToDo/2024-AGI/src/agentao/agentao/embedding/factory.py`
- `/Users/bluerose/Documents/Data/ToDo/2024-AGI/src/agentao/tests/test_memory_store_swap.py` (new)
