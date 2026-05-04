# Blueprint C · Ticket Automation

A webhook-style triage agent: reads a support ticket, looks up customer context, searches a KB, and either auto-sends a reply (confidence ≥ 0.9) or drafts one for a human reviewer.

Corresponds to [Part 7.3 of the developer guide](../../developer-guide/en/part-7/3-ticket-automation).

## What it demonstrates

- **Custom `Tool` subclasses** — `GetCustomerProfile`, `SearchKb`, `DraftReply`, `SendReply`
- **`PermissionEngine` subclass** — `ConfidenceGatedEngine` reads `confidence` from tool args and allows only `>= 0.9`
- **Per-session working dir** — each ticket gets its own `runs/<ticket-id>/` folder
- **Skill-shaped behavior** — tone, escalation matrix, and output contract live in [`.agentao/skills/support-triage/SKILL.md`](./.agentao/skills/support-triage/SKILL.md). Co-located rather than in the [skills gallery](../skills/README.md) because the policy references this blueprint's `ConfidenceGatedEngine` thresholds and would be meaningless lifted out.
- **In-memory mocks** — `_CUSTOMERS` / `_KB` / `_OUTBOX` stand in for real CRM systems; replace with HTTP clients in production

## How to run

```bash
cp .env.example .env                     # add OPENAI_API_KEY
uv sync
uv run python -m src.triage \
  --email alice@acme.io \
  --ticket-id T-1001 \
  "Hi, I forgot my password. Can you help?"
```

Expected: the agent looks up alice's profile, searches the KB, replies with confidence ≥ 0.9, and `OUTBOX:` shows one `"kind": "sent"` entry.

Try these variants to exercise the gate:

```bash
# Enterprise customer — skill forces draft_reply regardless of confidence
uv run python -m src.triage --email carol@bigco.com \
  "What's the status of my shipment?"

# Ambiguous — model should self-rate low confidence and draft
uv run python -m src.triage --email bob@startup.dev \
  "I want to cancel and never use this again."
```

## File map

| Path | Role |
|------|------|
| `src/triage.py` | Entry point, mock CRM/KB, 4 tools, `ConfidenceGatedEngine` |
| `.agentao/skills/support-triage/SKILL.md` | Tone + escalation policy + confidence guidance |
| `.env.example` | Credentials template |
| `runs/<ticket-id>/` | Per-ticket working dir, created at runtime |

## Not included

- Real CRM / Zendesk / Intercom integration — swap `_CUSTOMERS`/`_KB` for HTTP calls
- Webhook HTTP endpoint — wrap `build_agent()` in a FastAPI handler (see blueprint A for the pattern)
- Audit archive — add an `SdkTransport(on_event=archive)` hook to persist every event
