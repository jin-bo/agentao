# Agentao Integration Examples

> 中文版: [README.zh.md](./README.zh.md)

Runnable companions to [Part 7 of the developer guide](../developer-guide/en/part-7/). Each subdirectory is a self-contained project — `cd` in, install, run.

## Canonical embedding shapes (P0.6 — offline smoke in CI)

Four minimum-shape samples that run end-to-end against a fake LLM, no API key required. Each has its own `pyproject.toml`, ≤ 50-line README, and a `tests/` smoke suite.

| Directory | Host shape | Smoke |
|-----------|------------|-------|
| [`fastapi-background/`](./fastapi-background/) | FastAPI route + asyncio background task; per-request `Agentao` | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |
| [`pytest-fixture/`](./pytest-fixture/) | Drop-in `agent` / `agent_with_reply` / `fake_llm_client` fixtures | `uv sync --extra dev && uv run pytest tests/` |
| [`jupyter-session/`](./jupyter-session/) | One `Agentao` per kernel; `events()` drives display | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |
| [`slack-bot/`](./slack-bot/) | slack-bolt `app_mention` → one turn; channel-scoped permissions | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |
| [`wechat-bot/`](./wechat-bot/) | WeChat polling daemon → one turn; contact-scoped permissions | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |
| [`protocol-injection/`](./protocol-injection/) | All four `agentao.host.protocols` slots replaced (in-memory FS, audit shell, programmatic MCP registry, dict MemoryStore) | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |

## Larger blueprints (live LLM, end-to-end stacks)

| # | Directory | Blueprint | Stack | Run |
|---|-----------|-----------|-------|-----|
| A | [`saas-assistant/`](./saas-assistant/) | SaaS assistant API | Python · FastAPI · SSE | `uv run uvicorn app.main:app --reload` |
| B | [`ide-plugin-ts/`](./ide-plugin-ts/) | IDE / editor plugin | TypeScript · VS Code | `npm install && npm run compile` |
| C | [`ticket-automation/`](./ticket-automation/) | Support-ticket triage | Python · custom tool + skill | `uv run python -m src.triage "ticket text"` |
| D | [`data-workbench/`](./data-workbench/) | Data analysis workbench | Python · DuckDB · matplotlib | `uv run python -m src.workbench` |
| E | [`batch-scheduler/`](./batch-scheduler/) | Nightly scheduled job | Python · cron / CronJob | `uv run python -m src.daily_digest` |

## Single-file demos

| File | What it shows | Run |
|------|---------------|-----|
| [`headless_worker.py`](./headless_worker.py) | `ACPManager` driving an inline mock ACP server (success / interaction-required / cancel paths). Authoritative Week 1 regression fixture for [`docs/features/headless-runtime.md`](../docs/features/headless-runtime.md). | `uv run python examples/headless_worker.py` |
| [`host_events.py`](./host_events.py) | Public harness contract (since 0.3.1): `agent.events()` async iterator + `agent.active_permissions()` snapshot, wired alongside `agent.arun(...)` via `asyncio.gather`. See [`docs/api/host.md`](../docs/api/host.md). | `OPENAI_API_KEY=sk-... uv run python examples/host_events.py` |
| [`host_audit_pipeline.py`](./host_audit_pipeline.py) | End-to-end tenant audit pipeline: drains `agent.events()` into a local SQLite `agent_audit` table, pins an `active_permissions()` snapshot at session start, dumps the table after the turn. Companion to [`developer-guide §4.7`](../developer-guide/en/part-4/7-host-contract.md). | `OPENAI_API_KEY=sk-... uv run python examples/host_audit_pipeline.py` |

## Persona gallery (`AGENTAO.md` only — no code)

Not an integration example — a collection of `AGENTAO.md` files (project-level prompt configurations) drawn from real use. Drop one into your project root and edit. See [`personas/`](./personas/README.md) ([中文](./personas/README.zh.md)).

## Skills gallery (`SKILL.md` + helper scripts — drop-in capability packs)

Host-agnostic skills the embedded agent can activate at runtime — the "what to give the agent to do" counterpart to the host-integration samples above. Today: `zootopia-ppt`, `pro-ppt`, `ocr`. Copy a directory into `~/.agentao/skills/` (global), `<project>/.agentao/skills/` (per-project), or `<project>/skills/` (repo-root) and the `SkillManager` will pick it up. See [`skills/`](./skills/README.md) ([中文](./skills/README.zh.md)).

> Skills can also be **co-located** with a host example when they only make sense alongside that host's tools or output contract. Three blueprints already do this: [`data-workbench`](./data-workbench/.agentao/skills/) (`duckdb-analyst` + `matplotlib-charts`), [`ticket-automation`](./ticket-automation/.agentao/skills/) (`support-triage`), and [`batch-scheduler`](./batch-scheduler/.agentao/skills/) (`daily-digest`). The gallery above is for the host-agnostic counterpart.

## Conventions

- **Independent dependencies** — each project has its own `pyproject.toml` or `package.json`; nothing is shared. Install inside each directory.
- **Mock over real** — external systems (CRM, Jira, RSS) are stubbed with in-memory fakes so you can run without credentials beyond `OPENAI_API_KEY`.
- **Code mirrors the guide** — every snippet is lifted verbatim from the matching Part 7 page. If you spot a drift, the guide is authoritative.
- **`.env.example`** — copy to `.env` and fill in your `OPENAI_API_KEY` before running.

## Requirements

- Python ≥ 3.10 and [`uv`](https://github.com/astral-sh/uv) for the Python examples
- Node ≥ 20 and `npm` for the TypeScript example
- A working `OPENAI_API_KEY` (or a compatible provider — see each README)

## Not included

- CI wiring — these are reference projects, not a test harness
- VS Code marketplace publishing for Blueprint B
- Actual Kubernetes deployment for Blueprint E's `cronjob.yaml`

Open an issue on the Agentao repo if a blueprint fails to run on a clean environment.
