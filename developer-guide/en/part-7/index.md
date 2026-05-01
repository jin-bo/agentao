# Part 7 · Integration Blueprints

The first six parts are a reference. This part is a **cookbook** — five end-to-end blueprints that weave sandbox, permission, event, and skill into real customer scenarios.

Each blueprint answers the same four questions:

1. **Who & why** — what kind of product / what pain
2. **Architecture** — where Agentao sits, what it talks to
3. **Key code** — the 50–150 lines that matter
4. **Pitfalls** — what tends to break on day two

::: info Key terms in this Part
- **In-product assistant** — chat / agent embedded inside an existing SaaS UI; the most common shape · [§7.1](/en/part-7/1-saas-assistant), [G.4](/en/appendix/g-glossary#g-4-integration-patterns)
- **IDE plugin (ACP)** — host = editor, agent = subprocess speaking ACP; uses `session/load` + `request_permission` · [§7.2](/en/part-7/2-ide-plugin), [G.3](/en/appendix/g-glossary#g-3-acp-terms)
- **Ticket automation** — async handler reading from a queue; `prompt_once` style, no streaming UI · [§7.3](/en/part-7/3-ticket-automation), [G.4](/en/appendix/g-glossary#g-4-integration-patterns)
- **Data workbench** — interactive analyst session with shell + sandbox + skills · [§7.4](/en/part-7/4-data-workbench)
- **Batch scheduler** — cron-driven `prompt_once` for offline / nightly jobs; no end-user · [§7.5](/en/part-7/5-batch-scheduler), [G.4](/en/appendix/g-glossary#g-4-integration-patterns)
:::

## The five blueprints

| # | Blueprint | Integration mode | Star extensions |
|---|-----------|-------------------|------------------|
| [7.1](./1-saas-assistant) | SaaS in-product assistant | In-process SDK + FastAPI | Custom tool + PermissionEngine |
| [7.2](./2-ide-plugin) | IDE / editor plugin | ACP stdio | session/load + request_permission |
| [7.3](./3-ticket-automation) | Customer-support / ticket automation | In-process SDK | Custom tools hitting CRM |
| [7.4](./4-data-workbench) | Data analyst workbench | In-process SDK | Shell + sandbox + custom skill |
| [7.5](./5-batch-scheduler) | Offline batch / scheduled jobs | `prompt_once` | Skills + cron |

## How to read this part

- **If you already picked your scenario**, jump straight to that section.
- **If you're undecided**, 7.1 covers the most common case (in-product assistant) — the other four are specializations.
- Every blueprint links back to the relevant reference sections so you can drill down when needed.

## Runnable code

All five blueprints ship as self-contained projects inside the main repo at [`examples/`](https://github.com/jin-bo/agentao/tree/main/examples) — each subdirectory (`saas-assistant/`, `ide-plugin-ts/`, `ticket-automation/`, `data-workbench/`, `batch-scheduler/`) is a standalone `uv run` / `npm run` project. Every blueprint page below links to its matching subdirectory.

→ [Start with 7.1 SaaS Assistant →](./1-saas-assistant)
