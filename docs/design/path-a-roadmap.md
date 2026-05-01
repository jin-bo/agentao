# Path A Roadmap — Embed-First Plan (2026-Q2)

**Status:** Strategic decision record. Locked 2026-04-30, converged across 5 rounds of internal review.
**Audience:** Agentao maintainers and strategic reviewers.
**Related docs:**
- `docs/design/embedded-harness-contract.md` — embedded-contract design rationale
- `docs/design/metacognitive-boundary.md` — injectable metacognitive boundary
- `docs/EMBEDDING.md` — embedding patterns walkthrough
- `docs/api/harness.md` — `agentao.harness` public API reference

---

## 1. Problem: why this roadmap exists

After 0.3.1 shipped the `agentao.harness` public contract, the next-step roadmap drifted away from agentao's stated embedded positioning across multiple "compare with competitors" review rounds. AGENTS.md, an `agentao serve` daemon, cross-platform sandbox, a benchmark platform — these accumulated into P0/P1, but more than half of them actually serve CLI users, remote deployers, or marketing narratives, **not the embedding host** (the README's self-stated "local-first, private-first, embeddable AI agents" persona).

After round 4's reverse review identified the positioning drift and round 5 caught operational bugs, this document fixes "Path A: be embedded as a dependency by Python projects" as the single strategic anchor and lays out the corresponding slim roadmap.

## 2. Decision: lock Path A

### 2.1 Success picture (12 months)

The success signal of agentao at 12 months is **being depended on**, observable through:

| Metric | 6-month target | 12-month target | Source |
|---|---:|---:|---|
| PyPI weekly downloads | 500 | 2,000 | pypistats.org |
| GitHub real dependents (lighthouse adoption) | 3 | 15 | github.com/jin-bo/agentao/network/dependents |
| `agentao` appears in others' `pyproject.toml` | 5 repos | 30 repos | grep.app + sourcegraph |
| Embed-shaped issues ÷ CLI-shaped issues | ≥ 1:1 | ≥ 2:1 | manual labels |
| `agentao.harness` public-API breaks | 0 | 0 | `tests/test_harness_schema.py` |
| Downstream embedding examples passing mypy strict | 100% | 100% | example-repo CI |

### 2.2 Anti-metrics (signals we drifted to Path B/C)

- ❌ Stars surging while dependents flat → wrong audience
- ❌ "Add CLI command X" issues outnumber "expose embedding API X"
- ❌ Big Twitter/HN traffic but PyPI downloads not rising

### 2.3 Explicit non-goals under Path A

The following items serve secondary personas or non-product needs and are **deferred to P2 or moved to separate projects**:

- ✗ TUI polish (opencode 152k stars already wins)
- ✗ VSCode extensions (Cline/Roo already win)
- ✗ Hosted SaaS (Anthropic/OpenHands already win)
- ✗ Rust/Go rewrite (contradicts "embed in Python host" axis)
- ✗ AGENTS.md — embedding hosts use `Agentao(project_instructions=...)`, not the file
- ✗ `agentao serve` daemon — clashes with "in-process harness" positioning
- ✗ Cross-platform strong sandbox — embedding hosts already isolate at process level
- ✗ Bilingual SWE-bench — should live in a separate `agentao-bench` repo
- ✗ A2A gateway — wait for protocol stability + actual demand
- ✗ Wasm sandbox — same reasoning as cross-platform sandbox

## 3. P0: minimum shippable set for reducing embedding friction

P0's goal is not "add new capabilities" but "remove embedding friction" so host projects are willing to add `agentao` to their `pyproject.toml`.

### 3.1 P0 work items

| ID | Item | Type | Estimate |
|---|---|---|---:|
| P0.1 | `py.typed` marker + wheel force-include | additive | 1h |
| P0.2 | README top flip: embed-first 30-line example up top, CLI moved down | additive | 4h |
| P0.3 | clean-install + embed construct smoke CI (`pip install . && python -c "from agentao import Agentao; Agentao(project_instructions='hi')"`) | additive | 1h |
| P0.4 | Public harness API typing gate: `agentao.harness`, `Agentao.events()`, `active_permissions()`, capability injection params consumable by downstream strict type checkers | additive | 3-5d |
| P0.5 | Lazy imports across the package (31 eager → lazy): `tools/web.py:10` `bs4`, `memory/retriever.py:11` `jieba`, all of `cli/*` | additive | 1w |
| P0.6 | 4 embedding examples: FastAPI background task / pytest fixture / Jupyter session / Slack bot | additive | 3-5d |
| P0.7 | Contract regression tests: multi-`Agentao()` no shared state / full capability injection paths / `arun` + `events` + `cancel` concurrency / no host-logger pollution / clean-install smoke | additive | 1w |
| P0.8 | JSONL audit sink: extend `agentao/replay/schema.py` v1.2 to accept `tool_lifecycle` / `subagent_lifecycle` / `permission_decision` kinds | additive | 3-5d |
| P0.9 | Dependency split: 6-item core + `[cli]` / `[web]` / `[i18n]` / `[full]` extras | **break** | 2-3d |
| P0.10 | `agentao` console script friendly missing-dep error ("`pip install 'agentao[cli]'`") + 0.3.x → 0.4.0 migration doc | break-mitigation | 1-2d |

### 3.2 Release split

P0 ships across three releases. **Do not ship 0.4.0 monolithically** — splitting reduces risk, paces version bumps, and keeps PyPI signal attributable per release.

```
0.3.3  ─────────────────────────────────  Day 1 (half-day to ship)
       P0.1  py.typed
       P0.2  README embed-first
       P0.3  clean-install smoke CI
       Fully additive, zero break

0.3.4  ─────────────────────────────────  Week 2-4
       P0.4  Public API typing gate
       P0.5  Lazy imports (internal refactor, user-invisible)
       P0.6  4 embedding examples
       P0.7  Contract regression tests
       P0.8  JSONL audit sink
       Still additive, zero break

0.4.0  ─────────────────────────────────  Week 5-8
       P0.9  Dependency split
       P0.10 console script friendly error + migration doc
       The single break, well-prepared by the prior two releases

Total cycle ≈ 2 months; version cadence: patch → patch → minor break.
```

### 3.3 0.4.0 migration safety net

Add a meta-extra to `pyproject.toml`:

```toml
[project.optional-dependencies]
full = ["agentao[cli,web,i18n]"]
```

Migration note, one line:

> **0.4.0 break change**: deps are split. The 0.3.x "everything bundled" behavior maps to:
> ```
> pip install 'agentao[full]'    # equivalent to 0.3.x
> pip install agentao            # now installs only 6 core embedding deps
> ```

CLI users who add `[full]` feel zero pain; users who don't upgrade can stay on 0.3.x.

## 4. P1: real extensions for embedding hosts (3-6 months)

Start only after P0 lands AND PyPI dependents grows (≥ 3 lighthouse adopters):

| ID | Item | Trigger |
|---|---|---|
| P1.1 | Usage/cost callback: `on_usage_event(tokens, cost, model)` so hosts can do billing | Any embedding host requests it |
| P1.2 | OTel exporter (built on top of P0.8 JSONL) | First enterprise user with concrete topology |
| P1.3 | `agentao-skill-pack` bundle: SKILL.md + tool manifest + boundary schema + permission profile | 2-3 adopters express "ship with skills" need |

**Discipline**: P1 is demand-driven, not calendar-driven. **Do not start without lighthouse evidence** — otherwise the design is speculative.

## 5. P2: external-demand pull only

The following are placeholders, **not actively pursued**, only considered when concrete external demand materializes:

- AGENTS.md support (with nested-lookup priority chain)
- `agentao serve` long-lived daemon (WebSocket + SSE + small HTTP control plane)
- Sandbox backend interface (macos-sandbox-exec / linux-bubblewrap / nsjail / windows-noop)
- A2A/ACP gateway (wait for LF A2A v1.0 + real demand)
- Wasm tool sandbox (plugin subset only)
- Bilingual coding bench (separate repo `agentao-bench`)

## 6. Day-1 action checklist

**Pure additive work shippable today** (the entire 0.3.3 release):

```bash
# 1. py.typed (1h)
touch agentao/py.typed
# Edit pyproject.toml [tool.hatch.build.targets.wheel] force-include to include py.typed

# 2. README flip (4h)
# Top of README.md / README.zh.md: add 30-line embedding example
# Move existing "Quick Start" CLI content under a "## CLI Quickstart" h2

# 3. clean-install smoke CI (1h)
# .github/workflows/ci.yml — add job:
#   pip install . && python -c "from agentao import Agentao; Agentao(project_instructions='hi')"

# 4. version + CHANGELOG
# agentao/__init__.py: __version__ = "0.3.3"
# CHANGELOG.md: new [0.3.3] section under [Added]

# 5. ship
uv build && twine upload dist/*
```

**Non-code work this week with the highest ROI:**

> Pick 1 lighthouse candidate (Chinese-community FastAPI util / pytest plugin / Jupyter data-science workflow) and **submit the integration PR yourself**: `feat: optional agentao integration for X`.
>
> One lighthouse adoption is worth more than 100 stars — it produces the first line of `agentao` inside someone else's `pyproject.toml` and starts moving the GitHub dependents graph.

## 7. Failure modes & guardrails

### 7.1 Failure modes

**Mode 1: stars rise but dependents flat.**
After an accidental Twitter/HN moment, the temptation is to optimize CLI UX for that traffic. **Resist.** On every star spike, immediately check PyPI dependents — if it didn't move, the traffic is the wrong audience.

**Mode 2: P0 fully shipped but no adopter at month 6.**
Code-level embedding contract is necessary, not sufficient. If P0 is done at month 6 with zero lighthouse adopters, the problem is **distribution, not technology** — all P1 work should stop and effort shifts to: write hands-on embedding blog posts (V2EX/掘金 in Chinese, dev.to in English), reach out to 5-10 candidate maintainers directly, demo in Chinese AI Agent communities.

### 7.2 Hard guardrail

**Monthly check `pypistats.org/agentao` and GitHub dependents. Three consecutive months of no movement → Path A has failed; convene to choose Path B/C or step back.**

This isn't pessimism — it's an honest guardrail. Five rounds of review taught us that direction without a guardrail is illusion.

## 8. Decision provenance

This document is the converged result of 5 internal review rounds:

| Round | Main correction |
|---|---|
| 1 | Initial report: positioning + 8 rough evolution directions |
| 2 | 4 factual corrections + roadmap reordering |
| 3 | 5 tactical refinements + architectural interface notes |
| 4 | **Strategic pivot**: positioning drift identified, A/B/C choice forced, 9 → 5 items |
| 5 | 5 operational fixes: lazy imports / console script / defer OTel / skill-pack lighthouse-gated / mypy strict scope |

Round 4's reverse review was the turning point — **it revealed that three rounds of "polish" were improving the wrong target**. Round 5 plugged the engineering bugs, after which the roadmap froze into this document.

No further strategic review rounds will be initiated; **execution begins now**. If a major external signal emerges (e.g., 3 consecutive months of flat PyPI dependents, or a disruptive ecosystem shift), a separate review-and-pivot document will be opened.

---

## 9. P0 implementation details

Sections 1-8 are the locked strategy. This section is the executable plan: per-item scope, target files, concrete changes, acceptance criteria, and tests. File references are accurate as of 2026-04-30; numbers may drift — re-`grep` before editing.

Format per item: **Goal → Files → Changes → Accept → Tests → Risk**.

### 9.1 P0.1 — `py.typed` marker (1h, additive)

- **Goal:** mypy/pyright in downstream projects pick up our type hints instead of treating `agentao` as an untyped third-party package.
- **Files:**
  - new: `agentao/py.typed` (empty file, PEP 561 marker)
  - edit: `pyproject.toml` `[tool.hatch.build.targets.wheel]` `force-include` (currently only includes `skills/skill-creator`)
- **Changes:**
  - `touch agentao/py.typed`
  - extend `force-include` to map `"agentao/py.typed" = "agentao/py.typed"` so the marker survives wheel packaging (hatch excludes dotfiles + non-Python by default).
- **Accept:**
  - `uv build && unzip -l dist/agentao-*.whl | grep py.typed` returns the marker at `agentao/py.typed`.
  - Downstream `mypy --strict` on `from agentao import Agentao` no longer raises `Skipping analyzing "agentao": module is installed, but missing library stubs`.
- **Tests:** add a one-liner in CI smoke step: `python -c "import importlib.resources, agentao; assert importlib.resources.files('agentao').joinpath('py.typed').is_file()"`.
- **Risk:** none. Pure metadata.

### 9.2 P0.2 — README embed-first flip (4h, additive)

- **Goal:** the first 30 lines a visitor sees show "embed in your Python project," not "install the CLI." This is the single highest-leverage piece of marketing-as-code under Path A.
- **Files:** `README.md`, `README.zh.md`.
- **Changes:**
  - Above the current `## Quick Start` (`README.md:21`), insert a new `## Embed in 30 lines` section with:
    1. one-line install: `pip install agentao` (intentionally *not* `[full]` — embedded users want the smallest core after 0.4.0)
    2. minimal pure-injection snippet (mirror `docs/EMBEDDING.md` "Pure-injection" block, not the env-discovery one — pure injection is the Path A north star)
    3. one-line teaser to `docs/EMBEDDING.md` and `docs/api/harness.md`.
  - Move the current `## Quick Start` content under a new `## CLI Quickstart` heading immediately after the embed section.
  - Mirror identically in `README.zh.md`.
- **Accept:**
  - First non-banner heading is `## Embed in 30 lines`.
  - Snippet copy-pastes and runs against a fresh venv with only `pip install agentao` (verified locally, then in CI smoke).
- **Tests:** the CI smoke step from P0.3 below executes the exact snippet from the README — drift fails CI.
- **Risk:** existing CLI users may complain about demotion. Mitigation: `## CLI Quickstart` is one scroll down, and the project description still leads with "embeddable AI agents."

### 9.3 P0.3 — clean-install + embedded-construct smoke (1h, additive)

- **Goal:** every PR proves that `pip install agentao` followed by *constructing* (not just importing) `Agentao(...)` works on a minimal environment.
- **Files:** `.github/workflows/ci.yml` — the existing `smoke` job (currently lines ~80–130) imports `Agentao` but never constructs an instance.
- **Changes:** extend the "Import check — package and public API" step to also execute an embedded-construct line. Concretely:
  ```yaml
  - name: Embedded-construct smoke (no env, no network)
    env:
      OPENAI_API_KEY: ""
      OPENAI_BASE_URL: ""
      OPENAI_MODEL: ""
    run: |
      python -c "
      from pathlib import Path
      from agentao import Agentao
      a = Agentao(
          working_directory=Path('.'),
          api_key='dummy', base_url='http://localhost:1', model='dummy',
          project_instructions='hi',
      )
      a.close()
      print('Embedded construct OK')
      "
  ```
  Verified 2026-04-30: `Agentao.__init__` validates that `api_key`/`base_url`/`model` are non-None but does *not* dial out — passing dummy strings constructs cleanly with no network call. This snippet therefore proves both invariants in one step (no env-discovery needed; no implicit network).
- **Accept:** smoke job stays green on a fresh runner with no env vars beyond `PATH`.
- **Tests:** this *is* the test. Also add a unit-level mirror at `tests/test_imports.py` so local dev catches regressions before CI.
- **Risk:** if a future change makes `LLMClient.__init__` open a connection, this snippet will start hanging or failing on `http://localhost:1`. That failure mode is the canary — keep the dummy URL deliberately unroutable so the regression is loud.

### 9.4 P0.4 — public harness API typing gate (3-5d, additive)

- **Goal:** downstream projects running `mypy --strict` against `agentao.harness` get zero errors. The harness package is the compatibility boundary — it must read cleanly to a strict type checker.
- **Files:**
  - audit: `agentao/harness/__init__.py`, `agentao/harness/models.py`, `agentao/harness/events.py`, `agentao/harness/projection.py`, `agentao/harness/schema.py`
  - audit: `Agentao.events()` and `Agentao.active_permissions()` return-type annotations in `agentao/agent.py`
  - audit: every capability-injection kwarg in `Agentao.__init__` (the block starting at `agentao/agent.py:75` — `llm_client`, `logger`, `memory_manager`, `skill_manager`, `project_instructions`, `mcp_manager`, `mcp_registry`, `filesystem`, `shell`, `bg_store`, `sandbox_policy`, `replay_config`)
- **Changes:**
  1. Run `mypy --strict --package agentao.harness` and fix every reported error inside the package (not by `# type: ignore`).
  2. Replace any `Any` in public signatures with concrete `Protocol`/Pydantic types. The capability protocols already exist under `agentao/capabilities/`; re-export the public ones from `agentao.harness` so hosts have one import path.
  3. Add `agentao/harness/protocols.py` re-exporting `FileSystem`, `MCPRegistry`, `ShellExecutor` (currently under `agentao.capabilities`) so embed users do not need to reach into `agentao.capabilities.*`.
  4. Update `docs/api/harness.md` import examples accordingly.
- **Accept:**
  - `uv run mypy --strict --package agentao.harness` exits 0.
  - A downstream example repo (created in P0.6) with `strict = true` mypy config passes against the wheel.
- **Tests:**
  - extend `tests/test_harness_schema.py` with a runtime check that `agentao.harness.__all__` matches the documented set in `docs/api/harness.md` (drift detection).
  - new `tests/test_harness_typing.py`: subprocess `mypy --strict` against a tiny script importing the entire public surface; skipped if `mypy` is not installed in the dev group.
- **Risk:** typing the capability protocols may require touching `agentao/capabilities/*.py`. Keep changes additive — do not narrow existing runtime types in ways that break in-tree consumers.

### 9.5 P0.5 — lazy imports across the package (1w, additive)

- **Goal:** `import agentao` on a fresh environment does not import `bs4`, `jieba`, `openai`, `rich`, `prompt_toolkit`, `readchar`, or `filelock`. Embed hosts that never use the CLI or web tools should not pay for those wheels.
- **Files (verified eager imports as of 2026-04-30):**
  - `agentao/llm/client.py:10` — `from openai import OpenAI` (top-level)
  - `agentao/tools/web.py:9-10` — `import httpx`, `from bs4 import BeautifulSoup`
  - `agentao/memory/retriever.py:11` — `import jieba`
  - `agentao/skills/registry.py:9` — `from filelock import FileLock`
  - `agentao/display.py:34-37` — `rich.{console,padding,syntax,text}`
  - `agentao/cli/_globals.py:6-7` — `rich.{console,theme}`
  - `agentao/cli/app.py:22-26` — `prompt_toolkit.*`
  - `agentao/cli/input_loop.py:13-15` — `readchar`, `prompt_toolkit`, `rich.markdown`
  - `agentao/cli/transport.py:8` — `readchar`
  - `agentao/cli/entrypoints.py:13-14` — `rich.{panel,prompt}`
  - `agentao/cli/commands.py:10-11`, `commands_ext/{acp,memory,agents,crystallize}.py`, `replay_render.py`, `replay_commands.py`, `ui.py`, `_utils.py`, `subcommands.py` — assorted `rich`/`prompt_toolkit`/`readchar`
  - The roadmap quote of "31 places" was approximate; the audit above lists ~20 distinct top-level imports. Treat ~20 as the floor and re-audit before each refactor PR; do not edit any file not on this list.
- **Changes:**
  - For *third-party* libs imported at module top: defer to function/class scope OR wrap with `TYPE_CHECKING`.
  - For *intra-package CLI* code: move `rich`/`prompt_toolkit`/`readchar` imports inside the `agentao/cli/*` boundary (already 95% true) and ensure non-CLI modules never reach into `agentao.cli`.
  - Establish an enforcement test: `tests/test_no_cli_deps_in_core.py` walks every `.py` under `agentao/` excluding `agentao/cli/`, parses imports with `ast`, and fails on any `rich`/`prompt_toolkit`/`readchar`/`filelock` reference. This guards future regressions cheaply.
  - Establish an import-time profile test: `tests/test_import_cost.py` runs `python -X importtime -c "import agentao"` in a subprocess and asserts the third-party modules in question do **not** appear. This is the canonical invariant for P0.5 success.
- **Accept:**
  - On a venv with only `agentao` core deps installed (post-P0.9), `python -c "import agentao; from agentao import Agentao"` succeeds.
  - `python -X importtime -c "import agentao" 2>&1 | grep -E "bs4|jieba|openai|rich|prompt_toolkit|readchar|filelock"` returns nothing.
- **Tests:** the two new tests above; existing test suite remains green.
- **Risk:** lazy imports inside hot paths add per-call overhead. Mitigation: lazify at *module* boundary, not per-call — cache the import in a module-level singleton inside the function with the standard `_X = None; def get_x(): global _X; if _X is None: import x as _X; return _X` pattern.

### 9.6 P0.6 — four embedding examples (3-5d, additive)

- **Goal:** four runnable example projects, each demonstrating one canonical embedding shape. Examples are first-class: each ships its own `pyproject.toml`, `README.md`, and CI step.
- **Existing assets (do not duplicate):** `examples/harness_events.py`, `examples/headless_worker.py`, `examples/batch-scheduler/`, `examples/data-workbench/`, `examples/ide-plugin-ts/`, `examples/saas-assistant/`, `examples/ticket-automation/`. These are useful primitives but none of them is the canonical "host-app one-liner" we need.
- **Files (new):**
  - `examples/fastapi-background/` — FastAPI route enqueues an Agentao job to a background task; demonstrates per-request `Agentao(working_directory=...)`, transport injection, and `arun()` cancellation on client disconnect. Distinct from `examples/saas-assistant/` (which is multi-tenant SaaS) — this one is the minimum 1-route sample.
  - `examples/pytest-fixture/` — `pytest` fixture yielding an `Agentao` per-test with a fake `LLMClient` (re-using `tests/support/`) so downstream test suites can copy-paste.
  - `examples/jupyter-session/` — `.ipynb` notebook constructing one Agentao for the kernel lifetime, showing `events()` driving a Jupyter widget.
  - `examples/slack-bot/` — slack-bolt app that maps each `app_mention` to one Agentao turn with `permission_engine` injected from a Slack-channel allowlist.
- **Changes:**
  - Each example has: `README.md` (≤ 50 lines), `pyproject.toml` (depends on `agentao` from PyPI, no editable install), runnable command.
  - `examples/README.md` gets a table mapping (host shape → example folder).
- **Accept:**
  - Each example's `README.md` has a copy-pasteable command that runs end-to-end against a fake LLM (no live API key required).
  - CI matrix gains four steps under a `examples` job that `pip install` each example into a fresh venv and runs its smoke command.
- **Tests:** the per-example CI step *is* the test. No unit tests inside example folders.
- **Risk:** examples drift faster than core code. Mitigation: pin `agentao` versions inside example `pyproject.toml`, bump them in the same release PR that touches the public API.

### 9.7 P0.7 — embedded-contract regression tests (1w, additive)

- **Goal:** every property the embedded contract promises has at least one test that fails loudly when broken.
- **Existing tests (audit; do not duplicate):** `test_harness_event_stream.py`, `test_active_permissions.py`, `test_harness_permission_events.py`, `test_harness_subagent_events.py`, `test_harness_tool_events.py`, `test_harness_schema.py`, `test_filesystem_capability_swap.py`, `test_mcp_registry_swap.py`, `test_shell_capability_swap.py`, `test_memory_store_swap.py`, `test_skill_manager_injection.py`, `test_mcp_manager_injection.py`, `test_llm_client_logger_injection.py`, `test_factory_build_from_environment.py`, `test_async_chat.py`, `test_no_subsystem_fallback_reads.py`, `test_per_session_cwd.py`.
- **New tests required:**
  1. `tests/test_multi_agentao_isolation.py` — construct two `Agentao()` instances in one process, run a turn on each, assert no cross-contamination of: message history, skill activations, permission state, MCP tool sets, memory writes, replay records.
  2. `tests/test_arun_events_cancel.py` — start `agent.arun(prompt)`, attach an `events()` subscriber on another task, fire `cancel()` mid-run, assert: cancellation reaches tool layer, events stream drains cleanly, no orphan asyncio tasks.
  3. `tests/test_no_host_logger_pollution.py` — capture root-logger handlers before and after `import agentao` and after `Agentao(...)` construction; assert agentao adds no handler, no filter, no level change. This is the property hosts care about most.
  4. `tests/test_clean_install_smoke.py` — local mirror of the CI step from P0.3; subprocess `pip install dist/*.whl` into a tmp venv and run the embed snippet.
- **Accept:** all four new tests pass; full suite stays green.
- **Tests:** N/A (these *are* the tests).
- **Risk:** test 4 needs network or a pre-built wheel artifact. Use `pytest -m slow` markers and run it in CI only on the same job that builds the wheel.

### 9.8 P0.8 — JSONL audit sink for harness lifecycle events (3-5d, additive)

- **Goal:** the JSONL replay format can record `tool_lifecycle`, `subagent_lifecycle`, `permission_decision` so embedded hosts have a single audit artifact instead of two parallel streams (replay + harness events).
- **Files:**
  - `agentao/replay/events.py` — declare new `EventKind` constants and a v1.2 vocabulary partition (`V1_2_NEW`, `V1_2`)
  - `agentao/replay/schema.py` — extend `_kinds_for_version("1.2")` and emit `schemas/replay-event-1.2.json`
  - `scripts/write_replay_schema.py` — bump to write the v1.2 file
  - `agentao/harness/projection.py` — add a sink that translates each `HarnessEvent` into the matching `ReplayEvent` and hands it to the replay recorder when one is wired
  - `agentao/replay/recorder.py` — accept the new kinds in its allow-set
- **Changes:**
  - Schema version becomes `1.2`. v1.0 and v1.1 schemas remain frozen and continue to validate older replays — backward-compatibility promise from `docs/replay/schema-policy.md` holds.
  - Payload shapes for the three new kinds borrow from `agentao/harness/models.py` (already Pydantic) — generate JSON-Schema fragments via `model_json_schema()` and embed them as the per-kind variant in `_kind_variant`.
- **Accept:**
  - `uv run python scripts/write_replay_schema.py` produces `schemas/replay-event-1.2.json`; `--check` mode passes in CI (drift detection already wired in `.github/workflows/ci.yml:30`).
  - Round-trip test: emit a `tool_lifecycle` harness event → recorder writes JSONL → reader parses → projection back to `ToolLifecycleEvent` produces the same Pydantic model.
- **Tests:** extend `tests/test_replay_schema.py` and `tests/test_event_schema_version.py`; add `tests/test_harness_to_replay_projection.py`.
- **Risk:** Pydantic-derived schemas may diverge from hand-rolled JSON Schema styling. Mitigation: keep one shared helper in `agentao/replay/schema.py` so harness and replay always go through the same emitter.

### 9.9 P0.9 — dependency split into core + extras (2-3d, **break**)

- **Goal:** `pip install agentao` installs the minimum needed to construct an `Agentao()` and call `chat()` against an OpenAI-compatible endpoint. CLI/web/i18n become opt-in extras.
- **Files:** `pyproject.toml` `[project] dependencies` and `[project.optional-dependencies]`.
- **Current state:** `dependencies` lists 13 packages. Extras already exist for `pdf`/`excel`/`image`/`crypto`/`google`/`crawl4ai`/`tokenizer` plus a `full` meta-extra — we keep those and add three more.
- **Target core (6 items):**
  - `openai>=1.0.0`
  - `httpx>=0.25.0`
  - `pydantic>=2`
  - `pyyaml>=6.0.3`
  - `mcp>=1.26.0`
  - `python-dotenv>=1.0.0` (core because `Agentao` discovery still reads `.env` even in some embedded paths; if P0.5 makes that path lazy, demote to an extra in 0.4.1)
- **New extras:**
  - `cli = ["rich>=13.0.0", "prompt-toolkit>=3.0.52", "readchar>=4.2.1", "pygments>=2.16.0"]`
  - `web = ["beautifulsoup4>=4.12.0"]`
  - `i18n = ["jieba>=0.42.1"]`
  - extend `full = ["agentao[cli,web,i18n,pdf,excel,image,crypto,google,crawl4ai,tokenizer]"]` so existing `[full]` consumers see no change.
- **Concrete check that core install works without extras:** P0.5 must land first. If a core-only venv hits an `ImportError` from `rich`/`bs4`/`jieba`, that is a P0.5 bug, not a P0.9 bug — fix at the source.
- **Accept:**
  - Fresh venv: `pip install agentao` then `python -c "from agentao import Agentao; Agentao(working_directory=__import__('pathlib').Path('.'), project_instructions='hi').close()"` succeeds.
  - `pip install 'agentao[full]'` reproduces the 0.3.x dependency closure exactly (CI compares `pip freeze` output to a checked-in baseline).
- **Tests:** `tests/test_dependency_split.py` runs the freeze comparison against `tests/data/full_extras_baseline.txt`.
- **Risk:** the *only* break in the entire P0 plan. Justify in CHANGELOG with the migration table; pre-announce in the 0.3.4 release notes.

### 9.10 P0.10 — friendly missing-dep error + migration doc (1-2d, break-mitigation)

- **Goal:** a 0.3.x → 0.4.0 user who runs `agentao` after upgrading and has not added `[cli]` gets a one-line actionable error, not an opaque `ModuleNotFoundError: rich`.
- **Files:**
  - `agentao/cli/__init__.py` (or `agentao/cli/entrypoints.py:entrypoint`) — wrap the first `rich`/`prompt_toolkit` import in try/except
  - new: `docs/migration/0.3.x-to-0.4.0.md`
  - update: `CHANGELOG.md` `[0.4.0]` section, `README.md` install section
- **Changes (entrypoint shim, sketch):**
  ```python
  def entrypoint():
      try:
          from agentao.cli.app import run  # imports rich/prompt_toolkit
      except ImportError as e:
          missing = e.name or "a CLI dependency"
          import sys
          sys.stderr.write(
              f"agentao CLI requires extra packages (missing: {missing}).\n"
              f"  pip install 'agentao[cli]'   # CLI only\n"
              f"  pip install 'agentao[full]'  # 0.3.x compatible\n"
          )
          sys.exit(2)
      run()
  ```
- **Accept:**
  - In a venv with only core, `agentao` exits 2 with the message above.
  - Same venv, `pip install 'agentao[cli]' && agentao --help` works.
- **Tests:** `tests/test_cli_missing_dep_message.py` uses subprocess + venv to verify the message.
- **Risk:** the shim itself could regress if someone adds a non-CLI top-level import to `agentao.cli.__init__`. Mitigation: P0.5 enforcement test (`test_no_cli_deps_in_core.py`) catches the inverse direction; for this direction add a CI step that imports `agentao.cli.entrypoints` in a core-only venv and asserts the friendly message path runs.

---

## 10. Sequencing, dependencies, gating

### 10.1 Hard ordering

```
P0.5 (lazy imports)  ─┬─►  P0.9 (dependency split)  ──►  P0.10 (friendly error)
                      └─►  P0.3 (embedded smoke)*

P0.4 (typing gate)   ─►  P0.6 (examples that promise mypy strict)
P0.8 (audit sink)    ─►  (independent; can ship 0.3.4 alongside others)
P0.1, P0.2, P0.7     ─►  (no hard deps)
```

\* P0.3 ships cleanly in 0.3.3 — verified 2026-04-30 that bare construction with dummy creds does no network call. The earlier concern about a `LLMClient` lazy-init prerequisite was disproved by direct check.

### 10.2 Per-release gate criteria

| Release | Must pass before tagging |
|---|---|
| **0.3.3** | P0.1 marker present in wheel; P0.2 README first-section diff approved; P0.3 smoke green on Python 3.10/3.11/3.12 |
| **0.3.4** | All four new regression tests from P0.7 green; downstream-example mypy strict CI green for at least one of the four P0.6 examples; v1.2 schema generated and `--check` clean; `python -X importtime` invariant from P0.5 green |
| **0.4.0** | `pip install agentao` (no extras) on a clean venv constructs `Agentao` and runs one fake-LLM turn end-to-end; freeze-diff between `[full]` and 0.3.x baseline ≤ patch-level differences only; friendly-error message verified in core-only venv |

If any gate fails, hold the release. Do not work around with `# type: ignore` or env-var gymnastics — the gates exist to prevent exactly those shortcuts.

### 10.3 CHANGELOG and version mechanics

- 0.3.3 entry under `[Added]` only (P0.1, P0.3) and `[Changed]` for P0.2 (README structure).
- 0.3.4 entry under `[Added]` (P0.4, P0.6, P0.7, P0.8) and `[Changed]` for P0.5 (internal refactor, behavior-preserving).
- 0.4.0 entry **leads with `### Breaking changes`** containing the full P0.9 migration table from §3.3, then `[Added]` for P0.10.
- Version bumps live in `agentao/__init__.py` `__version__` (Hatch reads `[tool.hatch.version] path = "agentao/__init__.py"`). Bump in the same PR that closes the release.

---

## 11. What is already done vs net-new

This audit tells the executor what *not* to redo. Verified against the working tree on 2026-04-30.

| Item | Status | Evidence |
|---|---|---|
| P0.1 `py.typed` | **done (0.3.3, working tree)** | `agentao/py.typed` present; `pyproject.toml` `force-include` ships it in wheel + sdist |
| P0.2 README embed-first | **done (0.3.3, working tree)** | `README.md` / `README.zh.md` lead with `## Embed in 30 lines`; CLI walkthrough preserved under `## CLI Quickstart` |
| P0.3 clean-install smoke | **done (0.3.3, working tree)** | `.github/workflows/ci.yml` smoke job constructs `Agentao(...)` from the README snippet verbatim and asserts `py.typed` presence |
| P0.4 typing gate | **partly done** | `agentao/harness/__init__.py` exports a clean surface; no `mypy --strict` CI; no `agentao.harness.protocols` re-export |
| P0.5 lazy imports | **partly done** | `agentao/__init__.py` already uses PEP 562 for `Agentao`/`SkillManager`; the offending eager imports listed in §9.5 are still top-level |
| P0.6 examples | **partly done** | `examples/` has 5 directories + 2 standalone scripts, but none is FastAPI/pytest/Jupyter/Slack — those four are net-new |
| P0.7 regression tests | **largely done** | 17 listed tests already exist; the 4 new tests in §9.7 are the gap |
| P0.8 audit sink | **partly done** | `agentao/replay/events.py` is at v1.1; v1.2 vocabulary, schema file, and harness→replay projection are net-new |
| P0.9 dependency split | **not done** | `pyproject.toml` `dependencies` still bundles 13 packages including CLI/web/i18n |
| P0.10 friendly error | **not done** | `agentao/cli/__init__.py` has no shim; entrypoint imports rich/prompt_toolkit directly |

The net-new work, summed across items, is roughly **2 weeks of focused engineering**, matching the §3.2 release plan (2 months end-to-end including review, release rituals, and lighthouse outreach).
