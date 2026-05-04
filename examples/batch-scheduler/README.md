# Blueprint E · Batch / Scheduled Digest

A nightly agent that produces a structured daily digest file + a machine-parseable `RESULT:` line on stdout. No human in the loop.

Corresponds to [Part 7.5 of the developer guide](../../developer-guide/en/part-7/5-batch-scheduler).

## What it demonstrates

- **Unattended design**: `max_iterations=40` cap, strict output contract, fail-loud exits
- **SkillManager**: project-scoped skill loaded from [`.agentao/skills/daily-digest/SKILL.md`](./.agentao/skills/daily-digest/SKILL.md) — co-located rather than in the [skills gallery](../skills/README.md) because the skill bakes in this blueprint's `RESULT: {...}` stdout contract
- **Custom event tap**: token estimation via a `SdkTransport(on_event=…)` hook
- **Deterministic stdout**: one `RESULT: {...}` line for the scheduler to parse
- **k8s packaging**: reference `CronJob` manifest with `concurrencyPolicy: Forbid`

## How to run

```bash
cp .env.example .env                  # fill in OPENAI_API_KEY
uv sync
uv run python -m src.daily_digest
```

Expected stdout on success:

```json
{"status": "ok", "date": "2026-04-17", "tokens_est": 1823, "path": "digest.md", "items": 7}
```

Exit codes: `0` success, `2` agent error or contract violation.

## File map

| Path | Role |
|------|------|
| `src/daily_digest.py` | Entry point — builds agent, activates skill, parses `RESULT:` line |
| `.agentao/skills/daily-digest/SKILL.md` | Output contract the LLM must obey |
| `k8s/cronjob.yaml` | Reference Kubernetes schedule (not applied automatically) |
| `.env.example` | Template for the runtime credentials |
| `runs/<date>/` | Per-run working directory, created on first run (git-ignored) |

## Not included

- Email / Slack / S3 delivery — add after the `RESULT:` line parses successfully
- Retry policy — the job is designed to fail loud; orchestrator decides whether to alert
- CronJob deployment automation — `kubectl apply -f k8s/cronjob.yaml` is your call
