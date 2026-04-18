# 7.4 Blueprint D · Data Analyst Workbench

> **Run this example**: [`examples/data-workbench/`](https://github.com/jin-bo/agentao/tree/main/examples/data-workbench) — `uv run python -m src.workbench "your question"`

**Scenario**: internal analysts have a Jupyter-like workbench. You want to add "ask in English / Chinese, get back a chart and the SQL" — Agentao runs `duckdb`, writes a one-off Python script, and saves a PNG into the session's scratch directory. Since shell is involved, the sandbox is non-negotiable.

## Who & why

- **Product shape**: internal web app; backend per-analyst workspace
- **Users**: non-engineer analysts who know SQL barely or not at all
- **Pain**: BI team is a bottleneck for ad-hoc questions; pre-built dashboards don't cover the long tail

## Architecture

```
Web UI (chart viewer + transcript)
      │
      ▼
Backend (FastAPI) ──┐
      │             │
      ▼             │
Agentao instance    │
  ├─ working_directory = /workspaces/alice/
  ├─ Tools:
  │    - run_shell_command  (sandbox: workspace-write-no-network)
  │    - read_file / write_file
  │    - glob / grep
  ├─ Skills: "duckdb-analyst" + "matplotlib-charts"
  └─ Read-only mount of /data (parquet dumps)
```

## Key code

### 1 · Sandbox configuration

```json
// .agentao/sandbox.json
{
  "shell": {
    "enabled": true,
    "default_profile": "workspace-write-no-network",
    "allow_network": false,
    "allowed_commands_without_confirm": [
      "duckdb", "python", "python3", "uv", "head", "wc", "ls", "cat"
    ]
  }
}
```

Why `workspace-write-no-network`:

- Analysts never need outbound network inside shell — block SSRF, data exfil
- Full read of the parquet mount; writes only inside workspace
- The allowlist lets `duckdb` / `python` skip the confirmation prompt for interactivity

### 2 · Skills

```markdown
<!-- skills/duckdb-analyst/SKILL.md -->
---
name: duckdb-analyst
description: Use for any analytical question over /data/*.parquet. Prefer DuckDB, always show the SQL.
---

# DuckDB Analyst

## Conventions
- Data lives in `/data/*.parquet` (read-only). Never write there.
- Use DuckDB (`duckdb` CLI or `import duckdb` in Python).
- Always print the SQL you ran; cap to 1000 rows by default for speed.
- Save intermediate results as `workspace/cache-<slug>.parquet`.

## Workflow
1. `ls /data` to discover files
2. `duckdb -c "DESCRIBE SELECT * FROM read_parquet('/data/X.parquet') LIMIT 0"` to learn schema
3. Write the query; run with `LIMIT 1000`
4. If the user wants a chart, activate `matplotlib-charts`

## Guardrails
- If a query would scan > 10 GB, warn and ask first.
- Never `DELETE`, `UPDATE`, `DROP` — DuckDB on parquet can't anyway, but the LLM must still not suggest it.
```

```markdown
<!-- skills/matplotlib-charts/SKILL.md -->
---
name: matplotlib-charts
description: Produce PNG charts with matplotlib. Save to workspace/chart-<ts>.png.
---

# Matplotlib Charts

## Format
- 1 chart per question. No subplots unless asked.
- Dark-mode friendly palette: `matplotlib.style.use("default")`; set a readable size `figsize=(10, 6)`.
- Save with `plt.savefig(path, dpi=120, bbox_inches="tight")` — `plt.show()` does nothing in a headless env.

## Return contract
After saving the chart, print exactly:
`[CHART] workspace/chart-<ts>.png`

The UI parses this marker to render the image.
```

### 3 · Chart-marker parser in the backend

```python
# app.py (abridged)
from agentao import Agentao
from agentao.transport import SdkTransport
from agentao.transport.events import EventType
from pathlib import Path
import re, asyncio

CHART_RE = re.compile(r"\[CHART\]\s+(\S+)")

@app.post("/ask")
async def ask(req: dict, user=Depends(current_user)):
    workdir = Path(f"/workspaces/{user.username}")
    workdir.mkdir(exist_ok=True)

    charts: list[str] = []

    def on_event(ev):
        if ev.type is EventType.LLM_TEXT:
            for m in CHART_RE.finditer(ev.data["chunk"]):
                charts.append(m.group(1))

    transport = SdkTransport(on_event=on_event)
    agent = Agentao(working_directory=workdir, transport=transport)
    agent.skill_manager.activate_skill(
        "duckdb-analyst",
        task_description=f"Answer: {req['question']}",
    )
    reply = await asyncio.to_thread(agent.chat, req["question"])
    agent.close()

    return {
        "text": reply,
        "charts": [str(workdir / c) for c in charts],
    }
```

### 4 · Read-only mount of `/data`

```yaml
# docker-compose.yml (abridged)
volumes:
  - /srv/data:/data:ro                         # read-only parquet store
  - ./workspaces:/workspaces                   # writable per-analyst
```

Enforced at the OS level — even if the sandbox profile is loosened, the mount is RO.

## UX detail: show the SQL

Analysts trust answers only when they see the query. Parse `LLM_TEXT` chunks for fenced ```sql blocks client-side and render them as copy-able code. The `duckdb-analyst` skill's "always print the SQL" rule makes this reliable.

## Pitfalls

| Day-2 bug | Root cause | Fix |
|-----------|------------|-----|
| Query runs 10 minutes, client times out | No shell-tool timeout | Set a timeout on `run_shell_command` custom override or use DuckDB's `SET statement_timeout` |
| `workspaces/` fills disk | Old chart PNGs never cleaned | Cron-evict files older than N days |
| Analyst escapes the sandbox | They asked "please disable the sandbox to test something" → LLM complied via config file hint | Keep sandbox config outside the writable workspace; prompt injection guardrails (6.5) |
| Wrong data returned | LLM mis-read schema, printed confident SQL | Skill rule: always `DESCRIBE` first; Review UI highlights SQL for user approval |
| `matplotlib` crashes headless | No DISPLAY | `os.environ["MPLBACKEND"] = "Agg"` in the entrypoint |

## Runnable code

The full project lives in-repo at [`examples/data-workbench/`](https://github.com/jin-bo/agentao/tree/main/examples/data-workbench) — see the top-of-page "Run this example" link.

```bash
cd examples/data-workbench
uv sync && uv run python -m src.workbench "Which 3 products had the largest revenue?"
```

---

→ [7.5 Batch & Scheduler](./5-batch-scheduler)
