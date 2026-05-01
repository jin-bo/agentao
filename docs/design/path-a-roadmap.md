# Path A Roadmap — Embed-First Plan (2026-Q2)

**Status:** Strategic decision record. Locked 2026-04-30, converged across 5 rounds of internal review.
**Audience:** Agentao maintainers and strategic reviewers.
**Related docs:**
- `docs/design/embedded-host-contract.md` — embedded-contract design rationale
- `docs/design/metacognitive-boundary.md` — injectable metacognitive boundary
- `docs/EMBEDDING.md` — embedding patterns walkthrough
- `docs/api/host.md` — `agentao.host` public API reference

---

## 1. Problem: why this roadmap exists

After 0.3.1 shipped the `agentao.host` public contract, the next-step roadmap drifted away from agentao's stated embedded positioning across multiple "compare with competitors" review rounds. AGENTS.md, an `agentao serve` daemon, cross-platform sandbox, a benchmark platform — these accumulated into P0/P1, but more than half of them actually serve CLI users, remote deployers, or marketing narratives, **not the embedding host** (the README's self-stated "local-first, private-first, embeddable AI agents" persona).

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
| `agentao.host` public-API breaks | 0 | 0 | `tests/test_host_schema.py` |
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
| P0.4 | Public harness API typing gate: `agentao.host`, `Agentao.events()`, `active_permissions()`, capability injection params consumable by downstream strict type checkers | additive | 3-5d |
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
    3. one-line teaser to `docs/EMBEDDING.md` and `docs/api/host.md`.
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

- **Goal:** downstream projects running `mypy --strict` against `agentao.host` get zero errors. The harness package is the compatibility boundary — it must read cleanly to a strict type checker.
- **Files:**
  - audit: `agentao/host/__init__.py`, `agentao/host/models.py`, `agentao/host/events.py`, `agentao/host/projection.py`, `agentao/host/schema.py`
  - audit: `Agentao.events()` and `Agentao.active_permissions()` return-type annotations in `agentao/agent.py`
  - audit: every capability-injection kwarg in `Agentao.__init__` (the block starting at `agentao/agent.py:75` — `llm_client`, `logger`, `memory_manager`, `skill_manager`, `project_instructions`, `mcp_manager`, `mcp_registry`, `filesystem`, `shell`, `bg_store`, `sandbox_policy`, `replay_config`)
- **Changes:**
  1. Run `mypy --strict --package agentao.host` and fix every reported error inside the package (not by `# type: ignore`).
  2. Replace any `Any` in public signatures with concrete `Protocol`/Pydantic types. The capability protocols already exist under `agentao/capabilities/`; re-export the public ones from `agentao.host` so hosts have one import path.
  3. Add `agentao/host/protocols.py` re-exporting `FileSystem`, `MCPRegistry`, `ShellExecutor` (currently under `agentao.capabilities`) so embed users do not need to reach into `agentao.capabilities.*`.
  4. Update `docs/api/host.md` import examples accordingly.
- **Accept:**
  - `uv run mypy --strict --package agentao.host` exits 0.
  - A downstream example repo (created in P0.6) with `strict = true` mypy config passes against the wheel.
- **Tests:**
  - extend `tests/test_host_schema.py` with a runtime check that `agentao.host.__all__` matches the documented set in `docs/api/host.md` (drift detection).
  - new `tests/test_host_typing.py`: subprocess `mypy --strict` against a tiny script importing the entire public surface; skipped if `mypy` is not installed in the dev group.
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
- **Existing assets (do not duplicate):** `examples/host_events.py`, `examples/headless_worker.py`, `examples/batch-scheduler/`, `examples/data-workbench/`, `examples/ide-plugin-ts/`, `examples/saas-assistant/`, `examples/ticket-automation/`. These are useful primitives but none of them is the canonical "host-app one-liner" we need.
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
- **Existing tests (audit; do not duplicate):** `test_host_event_stream.py`, `test_active_permissions.py`, `test_host_permission_events.py`, `test_host_subagent_events.py`, `test_host_tool_events.py`, `test_host_schema.py`, `test_filesystem_capability_swap.py`, `test_mcp_registry_swap.py`, `test_shell_capability_swap.py`, `test_memory_store_swap.py`, `test_skill_manager_injection.py`, `test_mcp_manager_injection.py`, `test_llm_client_logger_injection.py`, `test_factory_build_from_environment.py`, `test_async_chat.py`, `test_no_subsystem_fallback_reads.py`, `test_per_session_cwd.py`.
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
  - `agentao/host/projection.py` — add a sink that translates each `HostEvent` into the matching `ReplayEvent` and hands it to the replay recorder when one is wired
  - `agentao/replay/recorder.py` — accept the new kinds in its allow-set
- **Changes:**
  - Schema version becomes `1.2`. v1.0 and v1.1 schemas remain frozen and continue to validate older replays — backward-compatibility promise from `docs/replay/schema-policy.md` holds.
  - Payload shapes for the three new kinds borrow from `agentao/host/models.py` (already Pydantic) — generate JSON-Schema fragments via `model_json_schema()` and embed them as the per-kind variant in `_kind_variant`.
- **Accept:**
  - `uv run python scripts/write_replay_schema.py` produces `schemas/replay-event-1.2.json`; `--check` mode passes in CI (drift detection already wired in `.github/workflows/ci.yml:30`).
  - Round-trip test: emit a `tool_lifecycle` harness event → recorder writes JSONL → reader parses → projection back to `ToolLifecycleEvent` produces the same Pydantic model.
- **Tests:** extend `tests/test_replay_schema.py` and `tests/test_event_schema_version.py`; add `tests/test_host_to_replay_projection.py`.
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
| P0.4 typing gate | **done (PR 1, working tree)** | `mypy --strict --package agentao.host` clean; `agentao/host/protocols.py` re-export added; CI `Typing gate` job enforces; `tests/test_host_typing.py` covers package + downstream-shaped consumer + `__all__` drift |
| P0.5 lazy imports | **done (PR 2, working tree)** | `from agentao import Agentao` no longer pulls bs4/jieba/openai/rich/filelock/click/pygments/starlette/uvicorn (the §9.5 invariant); `display.py` moved under `agentao/cli/`; new `tests/test_no_cli_deps_in_core.py` (AST walk) + `tests/test_import_cost.py` (subprocess `python -X importtime`) enforce both shapes |
| P0.6 examples | **done (PR 5, working tree)** | Five new dirs added: `fastapi-background/`, `pytest-fixture/`, `jupyter-session/`, `slack-bot/`, `wechat-bot/` (the last inspired by `Wechat-ggGitHub/wechat-claude-code`, transport-agnostic via a `WeChatClient` Protocol) — each with own `pyproject.toml` + `tests/test_smoke.py` running offline against a fake LLM; CI `examples` matrix runs each smoke suite; `examples/README.md` gains the canonical-shapes table |
| P0.7 regression tests | **done (PR 3, working tree)** | 17 prior + 4 new: `test_no_host_logger_pollution.py`, `test_multi_agentao_isolation.py`, `test_arun_events_cancel.py`, `test_clean_install_smoke.py` (slow-marked); `slow` marker registered in `pyproject.toml` |
| P0.8 audit sink | **done (PR 4, working tree)** | `agentao/replay/events.py` declares `V1_2_NEW`; `schemas/replay-event-1.2.json` ships with Pydantic-derived per-kind payload schemas; `agentao.host.replay_projection` provides `HostReplaySink` + reverse projection; `tests/test_host_to_replay_projection.py` covers round-trip + schema validation |
| P0.9 dependency split | **not done** | `pyproject.toml` `dependencies` still bundles 13 packages including CLI/web/i18n |
| P0.10 friendly error | **not done** | `agentao/cli/__init__.py` has no shim; entrypoint imports rich/prompt_toolkit directly |

The net-new work, summed across items, is roughly **2 weeks of focused engineering**, matching the §3.2 release plan (2 months end-to-end including review, release rituals, and lighthouse outreach).

---

## 12. 0.3.4 PR plan (next 2 weeks)

§11 says what's left; this section says the order to ship it. Five branches, five PRs, sized so each one stays under ~400 lines of diff and reviewable in one sitting. The order minimizes rebase pain: typing changes land before the lazy-import refactor (so the refactor inherits typed signatures), examples land last (so they pin against the final 0.3.4 wheel).

### 12.1 PR sequence

| # | Branch | Scope | Depends on | Net-new diff (est.) |
|---|---|---|---|---:|
| **1** | `roadmap/p0-4-typing-gate` | P0.4 only — `mypy --strict --package agentao.host` clean; new `agentao/host/protocols.py` re-export; CI step | — | ~250 lines |
| **2** | `roadmap/p0-5-lazy-imports` | P0.5 only — defer `bs4`/`jieba`/`openai`/`rich`/`prompt_toolkit`/`readchar`/`filelock`; add `tests/test_no_cli_deps_in_core.py` + `tests/test_import_cost.py` | PR 1 (typed kwargs) | ~350 lines |
| **3** | `roadmap/p0-7-regression-tests` | P0.7 only — the 4 new tests from §9.7 (`test_multi_agentao_isolation.py`, `test_arun_events_cancel.py`, `test_no_host_logger_pollution.py`, `test_clean_install_smoke.py`) | PR 2 (host-logger cleanliness easier post-lazy) | ~200 lines |
| **4** | `roadmap/p0-8-replay-v1-2` | P0.8 only — replay schema v1.2, harness→replay projection, `tests/test_host_to_replay_projection.py` | — (independent) | ~300 lines |
| **5** | `roadmap/p0-6-examples` | P0.6 only — 4 new example directories with their own `pyproject.toml`, `examples` CI matrix step | PRs 1–4 (examples pin against 0.3.4 wheel) | ~600 lines (mostly new files) |

**Parallel slack:** PR 4 (replay v1.2) has no dependency and can be picked up in any week. If review queue stalls, start PR 4 against `main` directly while waiting on PRs 1–3.

### 12.2 Per-PR gate (CI must pass before merge)

| PR | Gate added by this PR (must be green) |
|---|---|
| 1 | `uv run mypy --strict --package agentao.host` exits 0; `tests/test_host_typing.py` runs in dev |
| 2 | `python -X importtime -c "import agentao"` does **not** mention `bs4`/`jieba`/`openai`/`rich`/`prompt_toolkit`/`readchar`/`filelock`; `tests/test_no_cli_deps_in_core.py` green |
| 3 | All four new tests in §9.7 green; full suite still green |
| 4 | `scripts/write_replay_schema.py --check` clean for v1.2; `tests/test_host_to_replay_projection.py` green |
| 5 | `examples` CI job runs all 4 example smoke commands against a fresh venv with fake LLM |

### 12.3 0.3.4 release tagging

Tag only after all five PRs are merged AND the §10.2 0.3.4 row's full gate set is green. Bump `agentao/__init__.py` `__version__` to `0.3.4` and append the CHANGELOG entry in the same PR that closes the release; **do not tag from a release-prep branch that lingers** — keep it to one release-cut PR to avoid divergence.

If any PR slips into a third week, ship the merged subset as 0.3.4 and the rest as 0.3.5 — **release cadence is more valuable than batch completeness** under Path A. PyPI download deltas are easier to attribute to one small release than to a big one.

---

## 13. 0.4.0 break dress-rehearsal

P0.9 is the single break in the entire P0 plan. §3.2 schedules it as week 5–8, but the most expensive failure mode is "users discover the break only after `pip install -U agentao` runs in production." This section is the rehearsal protocol that prevents that failure mode.

### 13.1 Pre-tag rehearsal (T-7 days before 0.4.0)

Run on a fresh macOS + a fresh Linux runner, in a venv with **no other agentao install present**:

```bash
# 1. Install the candidate wheel (built from release-prep branch) — core only
pip install ./dist/agentao-0.4.0-py3-none-any.whl

# 2. Embed-only smoke — no CLI, no rich, no prompt_toolkit
python -c "
from pathlib import Path
from agentao import Agentao
a = Agentao(working_directory=Path('.'),
            api_key='dummy', base_url='http://localhost:1', model='dummy',
            project_instructions='hi')
a.close()
"

# 3. CLI runs the friendly-error path (rich not installed)
agentao   # must exit 2 with the §9.10 message; must NOT raise ModuleNotFoundError

# 4. Add CLI extra; CLI now boots
pip install 'agentao[cli]'
agentao --help  # must work

# 5. Add full extra; pip freeze must match the 0.3.x baseline
pip install 'agentao[full]'
diff <(pip freeze | sort) tests/data/full_extras_baseline.txt
# expected: zero diff except patch-level version drift
```

Any of steps 2–5 failing blocks the tag. Steps 3 and 5 are the two most likely failure modes — step 3 catches an accidental top-level `rich` import; step 5 catches an accidental dependency drop from the `full` meta-extra.

### 13.2 Pre-announce window

7 days before 0.4.0 tag, post a 0.3.x → 0.4.0 migration note in:

- `CHANGELOG.md` `[Unreleased]` section, top of file
- `README.md` install section (single banner line: "0.4.0 is approaching; if you depend on `agentao`, see migration note")
- the most recent 0.3.x release notes (edit in place, append "### Heads-up" subsection linking the migration doc)

The discipline here is: **the break is announced before it ships, not after**. Users on 0.3.x get one full release cycle to see the warning before their CI pipeline breaks.

### 13.3 Post-tag rollback criteria

If within 48 hours of 0.4.0 hitting PyPI, more than one external issue reports the break with no `[full]` workaround clearing it, **yank the release** (`twine upload --skip-existing` cannot un-publish, but PyPI's "yank" flag prevents `pip install` from picking it). Rollback path:

1. Yank `agentao 0.4.0` on PyPI (admin UI).
2. Cut `0.4.1` immediately, restoring the bundled-deps default (still publishing the `cli`/`web`/`i18n` extras as additive — no need to revert P0.9 entirely).
3. Open a 30-day investigation window; the next 0.5.0 retries the split with whatever was missing identified.

This is not pessimism — it is the cost of preserving Path A's "zero public-API breaks" metric (§2.1). The dependency split is a *packaging* break, not an API break, and a yank-and-redo round trip is cheaper than eroding the metric.

---

## 14. Lighthouse outreach plan

§7.1 Mode 2 names this as the dominant non-engineering risk: "P0 fully shipped but no adopter at month 6." Engineering work alone does not move PyPI dependents; it has to be paired with deliberate outreach. This section concretizes §6's "Pick 1 lighthouse candidate" line into a 12-week schedule.

### 14.1 Candidate criteria (in priority order)

A lighthouse adopter is worth 100 stars only if it meets **all** of:

1. **Active project** — committed in the last 30 days; ≥ 3 contributors; not a personal scratch repo.
2. **Has a real "agent-shaped" use case** — somewhere in the project there is already a TODO or open issue describing a workflow that would benefit from an LLM-driven loop with tools (test triage, doc generation, ticket pre-classification, code review preprocessing).
3. **Python primary** — Path A is "embed in Python host." A TypeScript repo that vendors a Python sidecar is harder to land cleanly.
4. **Maintainer reachable** — public GitHub email or a non-empty `CODEOWNERS` / `MAINTAINERS` file. Cold outreach via discussions/issues works; cold-emailing strangers does not.

Disqualifiers: hobby projects, archived projects, projects whose maintainers explicitly say "no LLM features" in their README.

### 14.2 Candidate shortlist (refresh quarterly)

Build a list of 10–15 candidates split across four shapes (mirroring §9.6):

| Shape | Where to look | Bar |
|---|---|---|
| FastAPI utility | github.com search `language:Python topic:fastapi pushed:>2026-01-01 stars:50..2000` filtered by "ticket / issue / triage" in description | ≥ 3 contributors |
| pytest plugin | github.com search `language:Python topic:pytest topic:plugin pushed:>2026-01-01` | ≥ 1 release in the last quarter |
| Jupyter / notebook tool | search `language:Python topic:jupyter` plus a manual scan of the JupyterLab extension registry | active in the last 60 days |
| Slack / chat bot framework | github.com `topic:slack-bot language:Python` | actually deployed (not template-only) |

Maintain the list in a private file (`docs/dev-notes/lighthouse-candidates.md`, gitignored or `private/`-prefixed) — not in the public roadmap. Public commitment to specific repos creates social pressure that makes outreach awkward.

### 14.3 Outreach cadence

Twelve weeks, three phases:

| Week | Action | Volume |
|---|---|---|
| W1–W2 | Build the shortlist. Read each candidate's README + last 5 issues. **Do not message yet** — outreach without context fails. | 10–15 candidates |
| W3–W4 | Open a discussion / draft issue on each candidate's repo: "Has anyone considered adding an opt-in `agentao` integration for [specific use case the maintainer cares about]?" Include a 30-line snippet using their actual code. | ≤ 3 messages per week — slower volume signals quality, not spam |
| W5–W12 | For each candidate that responds positively, **submit the integration PR yourself**. PR scope: optional dependency on `agentao` (in `[ai]` extra), one new module, one example, one test. Keep diff < 500 lines. | 1–2 PRs/month |

The expected hit rate is low: 10–15 candidates, perhaps 4 responses, perhaps 1–2 merges by month 6. That is the intended throughput — three lighthouse adopters at month 6 is the §2.1 target, and one merge per ~6 weeks of outreach hits it.

### 14.4 What the integration PR looks like

Use this template for the PR description (English; mirror in Chinese if the host project is Chinese-led):

> **What:** optional `agentao` integration for [specific feature name].
>
> **Why:** [restate the maintainer's existing problem statement from their issue/discussion]. With `agentao` as an opt-in dependency, [feature] gains [concrete capability — pre-classification / test triage / doc draft / X].
>
> **Cost:** zero impact on existing users — `agentao` is in the `[ai]` extra, gated by an env var, fully removable. [Link to the relevant Path A guarantees: `docs/EMBEDDING.md` for capability injection, `docs/api/host.md` for the public surface.]
>
> **Test plan:** [two or three concrete checks the maintainer can run locally]

The PR must work with no API key (use a fake LLM client in the test). Maintainers will not merge code that requires them to have an OpenAI key to run tests.

### 14.5 Tracking signal

Each merged integration PR is one row in §11's eventual successor — a "lighthouse adopters" table. Re-check monthly:

- Is the integration still in the host's `pyproject.toml`? (drift detection)
- Did the host bump `agentao` versions when we released? (engagement)
- Did issues mentioning `agentao` get filed on the host repo? (real usage)

A lighthouse that merged the PR but never bumped the version after six months is **not** a lighthouse — it is a stale dependency. The §2.1 target is 3 *active* lighthouses at month 6, not 3 historical ones.

### 14.6 Failure mode

If at week 12 zero PRs are merged, the failure is not in the candidates — it is in **us**. Re-read the discussion threads: did we lead with `agentao`'s features, or with the maintainer's problem? Maintainers respond to "I think I can solve your X" far more than "look at this cool tool." Restart the cadence with a sharper opening message.

This is the only section of this roadmap that does not measure progress in code. The §7.2 hard guardrail still applies: three months of flat PyPI dependents triggers a Path B/C review, regardless of how many outreach threads are open.

---

## 15. Metrics collection playbook

§2.1 names six metrics; §7.2 says "monthly check `pypistats.org/agentao` and GitHub dependents." Neither says *how*. This section is the runnable script — when month +1 arrives, the maintainer copy-pastes from here, not improvises.

### 15.1 Monthly snapshot — exact commands

Save the output of each month's run to `docs/dev-notes/metrics/YYYY-MM.md` (gitignored or `private/`-prefixed; it includes outreach status). Keep the file small — these are anchor points for the §7.2 trend, not a dashboard.

```bash
# 1. PyPI weekly downloads (target: 500 @ M+6, 2000 @ M+12)
curl -s 'https://pypistats.org/api/packages/agentao/recent' | python -m json.tool
# Record: data.last_week

# 2. GitHub dependents (target: 3 @ M+6, 15 @ M+12)
# No public API; scrape the dependents page. Record both counts (repos + packages).
curl -sL 'https://github.com/jin-bo/agentao/network/dependents' \
  | grep -oE '[0-9,]+\s+(Repositories|Packages)'

# 3. agentao in pyproject.toml elsewhere (target: 5 repos @ M+6, 30 @ M+12)
# grep.app — query: file:pyproject.toml agentao
# Record manually; deduplicate forks. URL:
#   https://grep.app/search?q=agentao&filter[file][0]=pyproject.toml

# 4. Embed-shaped vs CLI-shaped issues (target: ≥1:1 @ M+6, ≥2:1 @ M+12)
# Manual classification of issues opened in the last 30 days.
gh issue list --repo jin-bo/agentao --state all --limit 100 \
  --search "created:>$(date -v-30d +%Y-%m-%d)" \
  --json number,title,labels,createdAt
# Tag each as embed/cli/neutral; record ratio.

# 5. agentao.host public-API breaks (target: 0)
git log --since="30 days ago" --oneline -- agentao/host/ \
  | grep -iE 'breaking|break:|!:' || echo "0 breaks"
# Plus: tests/test_host_schema.py must be green on every release.

# 6. Downstream example mypy strict (target: 100%)
# Run after each release; per-example CI step records pass/fail.
gh run list --repo jin-bo/agentao --workflow ci.yml --limit 1 \
  --json conclusion,headSha
```

### 15.2 Snapshot template

Each `metrics/YYYY-MM.md` follows this minimal shape:

```
# Metrics snapshot — YYYY-MM

| Metric | Target M+6 | Target M+12 | Now | Δ vs last month |
|---|---:|---:|---:|---:|
| PyPI weekly downloads | 500 | 2000 | __ | __ |
| GitHub dependents (repos + packages) | 3 | 15 | __ | __ |
| pyproject.toml hits (grep.app) | 5 | 30 | __ | __ |
| Embed:CLI issue ratio (30d) | ≥1:1 | ≥2:1 | __:__ | __ |
| Public-API breaks (30d) | 0 | 0 | __ | __ |
| Example mypy strict pass rate | 100% | 100% | __% | __ |

## Notes
- Lighthouse status (per row): adopter X bumped from 0.3.4 → 0.3.5 ✔
- Outreach: __ open threads, __ PRs in flight
- Anomalies: any single metric moving in the wrong direction
```

### 15.3 Trend rules (when to act)

- **Any one metric flat for 1 month** → note in next month's snapshot, no action.
- **Any one metric flat for 2 months** → review §14 outreach quality (sharpen messaging, refresh shortlist).
- **PyPI downloads OR GitHub dependents flat for 3 months** → §7.2 hard guardrail fires. Open a separate Path B/C review document; **do not edit this roadmap to argue around the guardrail**.
- **Anti-metric tripped** (stars surging while dependents flat — see §2.2) → the next snapshot must include a "where is the traffic from?" investigation. Do not optimize CLI UX in response.

### 15.4 Automation cap

Resist the urge to build a metrics dashboard. The monthly cadence is the point — frequent automated polling produces noise, monthly hand-collection forces interpretation. If the snapshot file ever balloons past two screens, prune it; the goal is "one paragraph the maintainer can grok in 60 seconds."

The only piece worth automating is the snapshot **template** itself: a `scripts/metrics_snapshot.sh` that runs the §15.1 commands and pre-fills the template's "Now" column. The "Δ vs last month" and "Notes" columns must stay manual — that is where judgment lives.

---

## 16. Strategic review checkpoints

§7.2 names the failure-side guardrail (three flat months → review). This section adds the success-side checkpoints — calendar dates where the maintainer pauses, looks at §15's snapshots, and decides whether the strategy still fits the data.

These are not status updates; they are decision events. Each checkpoint may end with "no change," "retune within Path A," or "open a Path B/C document." Skipping a checkpoint is the same failure mode as skipping the §7.2 guardrail.

### 16.1 Checkpoint calendar

| Date | Type | Inputs | Decisions to make |
|---|---|---|---|
| **2026-07-31 (M+3)** | Light review | First 3 monthly snapshots; 0.3.4 + 0.4.0 release retros | Are P0 gates being met? Is outreach producing responses (not yet PRs)? Adjust cadence if W3–W4 produced zero replies. |
| **2026-10-31 (M+6)** | Heavy review | 6 monthly snapshots; lighthouse adopter count; embed:CLI issue ratio | Did we hit `3 lighthouse adopters` and `500 weekly downloads`? If yes, P1 unlocks (§4 trigger). If no, the §7.1 Mode 2 protocol fires — stop P1 design, shift effort to distribution. |
| **2027-01-31 (M+9)** | Light review | Trend over 9 months; P1 progress if started | Is P1 work demand-driven (§4 discipline holding) or has it drifted into speculative builds? Cut any P1 item without a named adopter. |
| **2027-04-30 (M+12)** | Strategic review | All 12 snapshots; §2.1 12-month targets | Hit the 2k downloads / 15 dependents / 30 pyproject.toml hits? If yes, write the "Path A v2" successor to this document. If no, mandatory Path B/C review — this roadmap retires either way. |

### 16.2 Pre-checkpoint discipline

One week before each checkpoint:

1. Read the last N monthly snapshots back-to-back. Look for trend slope, not absolute numbers.
2. Re-read this roadmap's §1–§8 (the strategy). Ask: is the success picture still the right success picture? External shifts (e.g., an upstream protocol stabilizes, a competitor pivots) may have changed what "embedded" means.
3. List one question that *would* change the answer if you knew it. The checkpoint's job is to answer that question — not to produce a generic status update.

### 16.3 Post-checkpoint output

Each checkpoint produces exactly one file: `docs/dev-notes/checkpoints/YYYY-MM-DD.md`. Three sections, no more:

- **What the snapshots say** (≤ 5 bullets, factual).
- **What we are changing** (≤ 3 bullets, concrete; can be "nothing").
- **What this roadmap should say differently** (edits to apply to `path-a-roadmap.md`, or "no edits").

If a checkpoint produces no edits to this roadmap *and* the snapshots show no anomaly, the checkpoint is doing its job — that is the success state, not a wasted hour.

### 16.4 Roadmap retirement

This roadmap retires when one of:

- **2027-04-30 strategic review** writes the successor (success path).
- **§7.2 hard guardrail** fires earlier (failure path; Path B/C document supersedes).
- **An external shift** invalidates the embed-first thesis (e.g., a Python-native agent runtime ships with stdlib-level distribution; in that case agentao becomes infrastructure, not the product, and a separate retirement doc explains the pivot).

When the roadmap retires, the file stays in-tree as a dated record. **Do not edit the locked sections (§1–§8) post-retirement** — write the successor as a separate document so the historical decision is preserved verbatim. §9–§16 may receive a final "as-shipped" annotation pass, but that pass is read-only commentary, not strategy revision.
