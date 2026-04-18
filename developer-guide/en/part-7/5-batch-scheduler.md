# 7.5 Blueprint E · Offline Batch & Scheduled Intelligent Jobs

> **Run this example**: [`examples/batch-scheduler/`](https://github.com/jin-bo/agentao/tree/main/examples/batch-scheduler) — `uv run python -m src.daily_digest`

**Scenario**: a nightly cron summarizes yesterday's GitHub activity, drafts a weekly newsletter from RSS feeds, or flags anomalies in yesterday's order data. **No user in the loop** — the agent must decide, act, and report cleanly or fail loud.

## Who & why

- **Product shape**: scheduled worker (cron / k8s CronJob / Airflow)
- **Users**: the stakeholders who read the output (nobody watches the run)
- **Pain**: you've got a pile of "if I had 10 minutes each morning I'd do X" tasks that never get done

## Design principles for unattended agents

1. **Fail loud, not quiet** — no silent auto-resume. If the LLM errors, the job exits non-zero.
2. **Bounded budget** — `max_iterations` smaller than interactive; token budget enforced hard.
3. **No `requires_confirmation` tools** — unattended mode has no one to confirm. Either allow the action (after stringent design review) or don't register the tool.
4. **Deterministic output contract** — the final reply must match a parseable schema so downstream systems can consume it.
5. **Idempotent** — running the job twice produces the same effect (use date stamps, tags, etc.).

## Architecture

```
cron / k8s CronJob
       │ 03:00 daily
       ▼
Python entrypoint
       │
       ├─ Agentao instance (fresh each run, closed cleanly)
       │    ├─ Skill: "daily-digest"
       │    ├─ Tools: web_fetch (read-only curated feeds), write_file
       │    └─ PermissionEngine: READ_ONLY + explicit write allowlist
       │
       ├─ Output: /reports/YYYY-MM-DD.md
       │
       └─ Post-processing: email / Slack / S3 upload
```

## Key code

### 1 · Minimal batch runner

```python
# jobs/daily_digest.py
import os, sys, json, traceback
from pathlib import Path
from datetime import date
from agentao import Agentao
from agentao.transport import SdkTransport
from agentao.transport.events import EventType

def run():
    today = date.today().isoformat()
    workdir = Path(f"/var/jobs/digest/{today}")
    workdir.mkdir(parents=True, exist_ok=True)

    tokens_used = 0
    def on_event(ev):
        nonlocal tokens_used
        if ev.type is EventType.LLM_TEXT:
            tokens_used += len(ev.data.get("chunk", "")) // 4

    transport = SdkTransport(on_event=on_event)
    agent = Agentao(
        working_directory=workdir,
        transport=transport,
        max_context_tokens=64_000,
    )
    agent.skill_manager.activate_skill(
        "daily-digest",
        task_description="Produce today's digest per the skill contract.",
    )

    try:
        reply = agent.chat(
            "Produce today's digest. End with a line "
            "`RESULT: {\"path\": \"...\", \"items\": N}` "
            "so the scheduler can consume it.",
            max_iterations=40,
        )
        parsed = parse_result(reply)
        print(json.dumps({
            "status": "ok",
            "date": today,
            "tokens_est": tokens_used,
            **parsed,
        }))
    finally:
        agent.close()

def parse_result(reply: str) -> dict:
    import re
    m = re.search(r"RESULT:\s*(\{.*\})\s*$", reply, re.MULTILINE)
    if not m:
        raise SystemExit(f"agent did not emit RESULT: line; got:\n{reply[-500:]}")
    return json.loads(m.group(1))

if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)
```

### 2 · The skill with the output contract

````markdown
<!-- skills/daily-digest/SKILL.md -->
---
name: daily-digest
description: Build a daily digest from curated sources. Follow the output contract strictly.
---

# Daily Digest

## Sources
Fetch these URLs in order, skip any that 404:
- https://github.com/jin-bo/agentao/commits/main
- https://news.ycombinator.com/
- (your RSS feeds)

## Output file
Write to `./digest.md`. Structure:

```
# Daily Digest — YYYY-MM-DD

## Agentao commits
- SHA  short message

## Tech highlights
- Title  one-line takeaway  (url)

## Action items (if any)
- short description
```

## Output contract
After writing, your FINAL message MUST end with exactly one line:

`RESULT: {"path": "digest.md", "items": TOTAL_BULLETS}`

This is machine-parsed. No additional text after this line.
````

### 3 · k8s CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: daily-digest
spec:
  schedule: "0 3 * * *"       # 03:00 UTC daily
  concurrencyPolicy: Forbid   # don't pile up if yesterday ran long
  jobTemplate:
    spec:
      backoffLimit: 1         # fail loud, don't retry 6 times
      template:
        spec:
          restartPolicy: Never
          containers:
          - name: runner
            image: your-agent:v0.2.10
            command: ["python", "-m", "jobs.daily_digest"]
            env:
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef: {name: agent-secrets, key: openai-key}
            resources:
              requests: {cpu: "200m", memory: "512Mi"}
              limits:   {cpu: "1",    memory: "2Gi"}
```

### 4 · Delivery step

```python
# jobs/deliver.py  — invoked after the main runner in the CronJob pod
import json, smtplib, subprocess
result = json.loads(subprocess.check_output(["python", "-m", "jobs.daily_digest"]))
if result["status"] != "ok":
    raise SystemExit(1)
send_email(to="team@x.com", path=result["path"])
```

Or wire `digest.md` to a Slack webhook, S3 bucket, etc.

## Using `ACPManager.prompt_once()` (when the agent isn't Python)

If the scheduled job lives in Node or Go, you can drive Agentao via ACP with a one-shot helper — build an `ACPClient` (see 7.2), send one `session/prompt`, collect the final message, tear down. For the Python-to-Python case where you're **invoking a different ACP agent** from your job, use `ACPManager.prompt_once()`:

```python
from agentao.acp_client import ACPManager

result = ACPManager().prompt_once(
    name="external-reviewer",
    prompt="Review yesterday's digest for PII leaks.",
    cwd="/var/jobs/digest/2026-04-16",
    timeout=120,
)
print(result.stop_reason)
```

## Pitfalls

| Day-2 bug | Root cause | Fix |
|-----------|------------|-----|
| Job runs forever, locks tomorrow's run | No per-run timeout | `concurrencyPolicy: Forbid` + `asyncio.wait_for` on `chat()` |
| Silent regression (digest empty for a week) | Nobody watches logs, output contract is lax | Alert on `items: 0` or missing `RESULT:` line |
| Quota burned overnight | Unbounded tokens | `max_iterations` cap + `TokenBudget` ([6.7](/en/part-6/7-resource-concurrency#token-budgets)) |
| Same digest twice on retry | Not idempotent | Tag by date; if `/reports/<today>.md` exists, refuse re-run |
| Secret leaked in failure email | Traceback included API key | Scrub filter on stderr ([6.5](/en/part-6/5-secrets-injection)) |

## Runnable code

The full project lives in-repo at [`examples/batch-scheduler/`](https://github.com/jin-bo/agentao/tree/main/examples/batch-scheduler) — see the top-of-page "Run this example" link.

```bash
cd examples/batch-scheduler
uv sync && uv run python -m src.daily_digest
```

---

## End of Part 7 — and of the guide's main arc

You now have:

- Two embedding paths ([Part 2](/en/part-2/) SDK, [Part 3](/en/part-3/) ACP)
- Event + UI integration ([Part 4](/en/part-4/))
- Six extension points ([Part 5](/en/part-5/))
- Security + production deployment ([Part 6](/en/part-6/))
- Five real-world blueprints (this part)

The appendices — full API reference, config key index, ACP message fields, error codes, migration notes, FAQ, glossary — are what you'll reach for as you build. They follow.

→ Appendices (coming soon)
