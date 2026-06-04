# Memory Improvement Plan

Status: draft for follow-up research and implementation planning

This document captures the current agreed plan for improving Agentao's memory system.
It is intentionally scoped to near-term, incremental changes that fit the current
architecture.

Related code:

- `agentao/memory/retriever.py`
- `agentao/memory/manager.py`
- `agentao/memory/render.py`
- `agentao/memory/crystallizer.py`
- `agentao/memory/storage.py`

## Goals

1. Improve retrieval quality without replacing the current storage backend.
2. Preserve historical memory evolution where overwrite semantics are wrong.
3. Improve episodic memory extraction without turning the memory subsystem into
   a large async platform project.
4. Keep prompt growth bounded as memory volume increases.
5. Defer higher-cost features such as embeddings and background consolidation
   until the lower-risk improvements are validated.

## Non-Goals

- No embedding pipeline in the first implementation phases.
- No ANN/vector index work in the first implementation phases.
- No background consolidation worker system in the first implementation phases.
- No full graph-memory design in the first implementation phases.

## Roadmap

### P0a: Stable Block Budget And Deterministic Truncation

Problem:

- Stable memory growth can pressure the system prompt.
- This becomes more urgent once append-only memory types are introduced.

Requirements:

- Keep a hard budget for `<memory-stable>`.
- Make prioritization explicit and testable.
- Keep output stable across runs when the input set is unchanged.

Priority order:

1. `user` scope memories
2. `project` structural memories
3. incidental project memories

Suggested structural set for stable inclusion:

- `preference`
- `profile`
- `constraint`
- `workflow`

`decision` should be reviewed carefully once append-only behavior lands. If it
becomes append-only, stable inclusion should likely prefer only the latest
non-superseded decision records rather than all historical decisions.

Implementation notes:

- Keep budget enforcement in `agentao/memory/render.py`.
- Keep selection policy in `agentao/memory/manager.py`.
- Ensure renderer-side truncation and manager-side selection do not drift.
- Exclude superseded records from stable rendering by default once P1 lands.

Testing requirements:

- Add golden tests in `tests/test_memory_renderer.py`.
- Cover priority ordering under budget pressure.
- Cover stable ordering when no new memories are added.
- Cover future interaction with superseded records.

### P0b: Retrieval Quality Upgrade Without Backend Replacement

Problem:

- Retrieval is still fundamentally lexical.
- Current scoring misses semantically related recalls when term overlap is weak.

Scope:

- Stay inside the current in-memory inverted index design.
- Do not migrate to SQLite FTS5 in this phase.

Planned changes:

1. Expand content matching beyond the first 500 characters.
2. Introduce BM25-style weighting on top of the existing inverted index.
3. Preserve and fuse current signals:
   - tag match
   - title overlap
   - keyword match
   - content match
   - filepath hint
   - recency
4. Add concrete entity-hint rules:
   - detect filenames and path segments
   - detect function, class, and module identifiers
   - boost exact matches for those identifiers
5. Add a small static alias table:
   - bounded dictionary only
   - no open-ended synonym system
   - intended size: roughly 10-50 high-value aliases

Examples of acceptable alias scope:

- `postgres` -> `postgresql`
- `pyproject` -> `pyproject.toml`

Examples out of scope for this phase:

- model-generated synonyms
- embedding-based expansion
- dynamic ontology generation

Primary files:

- `agentao/memory/retriever.py`

### P1: Append-Only Semantics For Historical Memory Types

Problem:

- Current `scope + key` upsert semantics erase history for memory types that
  should preserve evolution.

Agreed write semantics:

- Continue upsert for:
  - `preference`
  - `profile`
  - `constraint`
- Move to append-only for:
  - `decision`
  - `project_fact`
  - `note`

Initial history model:

- Add `is_superseded` field to `memories`.
- Default queries filter `is_superseded = 0`.
- History-aware CLI views can opt out of that filter later.

Reason for choosing `is_superseded` over `supersedes`:

- lower migration cost
- simpler query logic
- sufficient for "current effective record vs history"

Schema notes:

- This phase requires schema migration.
- Migration must be applied to both:
  - project store: `.agentao/memory.db`
  - user store: `<home>/.agentao/memory.db`
- User store may be absent and must be skipped safely.
- Migration should run idempotently during `SQLiteMemoryStore` initialization.

Example migration shape:

```sql
ALTER TABLE memories ADD COLUMN is_superseded INTEGER NOT NULL DEFAULT 0;
```

Primary files:

- `agentao/memory/manager.py`
- `agentao/memory/storage.py`
- `agentao/memory/models.py`

### P2: LLM-Structured Episodic Extraction With Deterministic Review-Queue Ingestion

Problem:

- Regex-only crystallization is adequate for strong preference/decision phrases
  but weak for episodic knowledge.

Agreed design:

- Let the summarization step do semantic understanding.
- Let the crystallizer do deterministic parsing, normalization, classification,
  and review-queue submission.

In other words:

- LLM extracts
- crystallizer normalizes
- review queue gates promotion

Planned changes:

1. Extend session summarization prompt so it can emit structured memory blocks.
2. Parse those blocks after summarization.
3. Convert parsed items into review candidates.
4. Keep direct auto-promotion out of scope for this phase.

Protocol requirement:

- Define a stable structured output protocol before implementation.

Recommended direction:

- tagged blocks or other parser-friendly structured text
- avoid loose markdown prose as the main interchange format

Illustrative shape:

```text
<episode>
type: workaround
title: uv lock mismatch after python upgrade
context: ...
resolution: ...
confidence: high
</episode>
```

Protocol must define:

- block types
- required fields
- optional fields
- per-field length limits
- parse-failure behavior

Recommended parse-failure behavior:

- do not auto-write into live memory
- optionally downgrade to a review-queue raw-note candidate
- never silently pollute stable long-term memory

Primary files:

- `agentao/context_manager.py`
- `agentao/memory/crystallizer.py`
- `agentao/memory/manager.py`

### P3: Explicit Read-Only Memory Recall Tool

Problem:

- The agent currently relies on automatic prompt injection and cannot explicitly
  query memory when automatic recall is insufficient.

Status:

- intentionally deferred until P0-P2 are stable

Proposed tool name:

- `recall_memory`

Reason for name:

- pairs naturally with existing `save_memory`
- better reflects the intended use than a generic `search_memory`

Tool boundary:

- read-only
- no delete
- no clear
- no unrestricted memory administration

Description guidance:

- automatic recall already injects relevant memory into the prompt
- explicit recall should be used only when:
  - user explicitly asks what the agent remembers
  - cross-session context is needed
  - the query is short or under-specified
  - scoped filtering is required

Possible filters:

- `scope`
- `type`
- `tag`
- time window

### P4: Re-evaluate Embeddings And Background Consolidation

This phase is intentionally deferred.

Only revisit after P0-P2 are implemented and evaluated.

Open questions for a future phase:

- whether lexical + BM25-style retrieval is still insufficient
- whether embedding generation should be local or remote
- whether review-queue dedupe needs semantic similarity
- whether background consolidation is justified by measured pain

## Design Constraints

### Determinism

- Selection and rendering should remain deterministic wherever possible.
- Semantic understanding can use the LLM, but write-path ingestion should remain
  rule-governed and inspectable.

### Low Migration Risk

- Prefer small additive schema changes.
- Prefer idempotent startup migrations.
- Avoid requiring one-off operator steps for local databases.

### Prompt Safety

- Memory blocks remain data, not instructions.
- Stable memory growth must remain bounded.

## Testing Matrix

### P0a

- stable block priority under budget pressure
- output stability across repeated renders
- cross-session summary coexistence with stable facts
- superseded-record exclusion once P1 lands

### P0b

- identifier boosts for filenames, functions, classes, modules
- alias-table expansion behavior
- BM25-style weighting regression tests
- recall quality on low-overlap but still lexical queries

### P1

- idempotent migration for project DB
- idempotent migration for user DB
- missing user DB path does not fail initialization
- append-only insert behavior for historical types
- upsert behavior remains unchanged for current-state types
- superseded rows excluded from default reads

### P2

- parser accepts valid structured summary blocks
- parser rejects malformed blocks safely
- parse failure does not write live memory
- structured episodes enter review queue correctly

### P3

- tool remains read-only
- filter semantics are correct
- tool description encourages sparse, explicit invocation

## Immediate Next Step

Produce a detailed implementation proposal split by `P0a`, `P0b`, `P1`, `P2`,
and `P3`, including:

- concrete schema diffs
- structured summary protocol
- migration strategy
- affected tests
- rollback considerations
