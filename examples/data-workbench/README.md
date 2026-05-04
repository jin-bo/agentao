# Blueprint D · Data Analyst Workbench

A DuckDB + matplotlib workbench: ask a natural-language analytical question; the agent writes SQL, runs it, and optionally produces a PNG chart.

Corresponds to [Part 7.4 of the developer guide](../../developer-guide/en/part-7/4-data-workbench).

## What it demonstrates

- **Shell tool** used for `duckdb` / `python` invocations (sandbox-scoped to the workspace)
- **Two skills active at once** — `duckdb-analyst` and `matplotlib-charts` compose cleanly. Both live under [`.agentao/skills/`](./.agentao/skills/) because they're tightly coupled to this blueprint's `[CHART]` contract and parquet workspace; reusable skills go in the host-agnostic [skills gallery](../skills/README.md) instead.
- **Chart-marker contract** — `[CHART] <path>` line parsed from `LLM_TEXT` events via `SdkTransport(on_event=...)`
- **Read-only data layout** — `./data/*.parquet` is a symlinked shared pool; analysts can't mutate it
- **Headless matplotlib** — `MPLBACKEND=Agg` set in the entrypoint

## How to run

```bash
cp .env.example .env                    # add OPENAI_API_KEY
uv sync
uv run python -m src.workbench \
  "Which 3 products had the largest total revenue? Render a bar chart."
```

On first run the entry point seeds `./data/sales.parquet` with 10 rows of fake sales data, then drives the agent. Expected tail of stdout:

```
...
[CHART] chart-revenue.png

Generated charts:
  - /.../examples/data-workbench/workspaces/demo/chart-revenue.png
```

Open the PNG to inspect the chart.

## File map

| Path | Role |
|------|------|
| `src/workbench.py` | Entry point — seeds fake data, builds agent, activates both skills, parses `[CHART]` marker |
| `.agentao/skills/duckdb-analyst/SKILL.md` | How to query parquet with DuckDB + print the SQL |
| `.agentao/skills/matplotlib-charts/SKILL.md` | Chart format + `[CHART] …` return contract |
| `data/sales.parquet` | Seeded on first run (git-ignored) |
| `workspaces/demo/` | Per-session workdir with skill symlinks + data symlink (git-ignored) |

## Not included

- Web UI — blueprint A's FastAPI + SSE pattern plugs in here directly
- Authentication / per-user workspace isolation — use per-tenant `working_directory` (blueprint A)
- Sandbox profile configuration — real `.agentao/sandbox.json` with `workspace-write-no-network` is a production must; this example inherits defaults for ease of local running
- Query timeout — add `DuckDB SET statement_timeout` or wrap the shell tool with a timeout override
