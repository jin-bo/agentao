# 7.7 Blueprint G · Multi-Agent Scheduling on a Kanban Board

::: tip ⚡ Runnable end-to-end
**Outcome** — a derivative project (`agentao-kanban`) that turns three agentao sub-agents (`planner` / `worker` / `reviewer`) into a self-running kanban board: cards flow `INBOX → READY → DOING → REVIEW → DONE` with no human in the loop. (Verification has been folded into the reviewer role.)
**Stack** — Python · agentao sub-agents (Markdown + YAML frontmatter) · single-writer orchestrator + dispatcher daemon · per-card git worktree · MCP server + FastAPI web view.
**Source** — [`jin-bo/agentao-kanban`](https://github.com/jin-bo/agentao-kanban) (external repo, derivative of this project).
**Try it** — `uvx --from agentao-kanban kanban init --demo && uvx --from agentao-kanban kanban demo`
:::

::: warning External project
Unlike blueprints 7.1–7.6 (which live in this repo's `examples/`), this case study lives in a **separate repository** and evolves on its own release cadence. This page anchors on the stable architectural choices and the agentao surfaces being consumed; for current code paths, file names, and CLI flags, treat the repo's `README.md` and `docs/` as authoritative.
:::

**Scenario**: you've already broken a piece of work into "cards" — Linear tickets, GitHub issues, internal tasks. You want a small set of specialized agents to plan, execute, review, and verify each card with **no humans on the critical path**, while still letting humans peek and intervene.

## Who & why

- **Product shape**: long-running scheduler over a board of work items
- **Users**: engineering / ops humans who watch the board and read the deliverables — they don't drive each step
- **Pain**: a flat single-agent loop ("just keep working") loses the natural review/verify gates and gives you no place to plug in "different model for the reviewer"

## What this blueprint demonstrates about agentao

This is the most useful single sample for two questions you can't answer from any in-repo blueprint alone:

1. **Multi sub-agent orchestration outside the chat loop** — sub-agents (§3.1, §6) are usually thought of as "delegated by the parent agent during a chat." Kanban shows them as **first-class workers driven by an external scheduler**, with the kanban orchestrator — not an LLM — owning state transitions.
2. **Per-role backend routing (subagent vs. ACP)** — each role can be served by an in-process agentao sub-agent or by an external ACP CLI (Claude Code, Codex, …) without the rest of the system caring. This is the cleanest concrete example of agentao's [host-client architecture (§3.3)](/en/part-3/3-host-client-architecture) at work.

## Design principles for a multi-agent scheduler

1. **Single writer for state** — only the orchestrator changes a card's status. Agents return *results*, never status updates. (Mirrors agentao's "permission engine is the only authority" pattern from [§4.7](/en/part-4/7-host-contract).)
2. **Roles, not a hierarchy** — `planner` / `worker` / `reviewer` are peers selected per-card, not a tree. The orchestrator routes; agents don't call each other. (An earlier version had a separate `verifier` role; it has been merged into `reviewer`.)
3. **One worktree per card** — when the board is in a git repo, each card gets its own git worktree + branch, so concurrent workers don't collide. Detach (release the directory, keep the branch) on terminal states.
4. **Structured event log + raw transcript** — every agent run writes both a machine-readable event row *and* the raw LLM transcript. Debugging a stuck card is "open the transcript", not "re-run with --verbose".
5. **Lock-respecting writes everywhere** — CLI, MCP tools, and the web UI all respect `.daemon.lock`. The board has exactly one source of truth even with three entry points.

## Architecture

```
                ┌──────────── humans (read mostly) ──────────┐
                │                                            │
        kanban CLI         kanban-mcp (MCP)         kanban web (FastAPI)
                │                  │                         │
                └─────────► BoardStore (.kanban/) ◄───────────┘
                                   ▲
                  reads only       │  ONLY writer of card state
                                   │
                            ┌──────┴───────┐
                            │ Orchestrator │   ← one process, holds .daemon.lock
                            └──────┬───────┘
                                   │ pulls next ready card
                                   ▼
                       ┌───── Executor ──────┐
                       │  multi-backend route │
                       └────┬─────┬─────┬─────┘
                            │     │     │     per role: subagent | ACP CLI
                         planner worker reviewer
                            ▼     ▼     ▼
                   agentao sub-agents (Markdown + YAML)
                          │
                          ▼
              workspace/worktrees/<card>/   ← per-card git worktree
              workspace/raw/<card>/...      ← transcripts + rescued artifacts
              workspace/reports/...         ← human-facing deliverables
```

## What to study, in order

Rather than mirror code that will drift, here's the reading order that maps each piece back to the relevant chapter of this guide.

| Read this in the kanban repo | What agentao surface it exercises | Reference here |
|---|---|---|
| `kanban/agents.py` + `kanban/defaults/*.md` | Sub-agent definition format (Markdown + YAML frontmatter) | [§3.1 Plugin model](/en/part-3/), [§6.x sub-agents](/en/part-6/) |
| `kanban/orchestrator/` | Single-writer state machine over agent results | [§4.7 Host contract](/en/part-4/7-host-contract) |
| `kanban/executors/multi_backend.py` + `agent_profiles.yaml` | Per-role routing between in-process sub-agent and external ACP CLI | [§3.3 Host-client architecture](/en/part-3/3-host-client-architecture), [§3.2 Agentao as ACP server](/en/part-3/2-agentao-as-server) |
| `kanban/daemon/` + `runtime/claims/*.json` | Multi-worker concurrency over a single board (O_EXCL CAS lease) | [§6.7 Resource & concurrency](/en/part-6/7-resource-concurrency) |
| `workspace/worktrees/<card>/` machinery | Per-task sandbox isolation in a real codebase | [§6.x sandboxing](/en/part-6/) |
| `kanban/mcp/` (`kanban-mcp`) | Exposing the board as MCP tools to Claude Code / Codex | [§5 MCP](/en/part-5/) |
| `workspace/raw/<card>/<role>-<ts>.md` | Transcript + structured event capture for post-hoc audit | [§6 observability](/en/part-6/6-observability) |

## Minimal mental model (the 30-second version)

```python
# pseudo-code — the actual code is in kanban/orchestrator/
while True:
    card = board.next_ready()              # uses WIP, deps, priority
    if not card:
        sleep_or_idle(); continue

    role  = card.next_role()               # planner -> worker -> reviewer
    agent = profiles.pick(role, card)      # subagent or ACP backend
    result = agent.run(card, worktree=card.worktree())

    board.commit(card, role, result)       # ONLY status writer
```

Everything else in the repo — claims, leases, retry matrix, artifact rescue, web UI — is *operational scaffolding* on top of that loop.

## Why this is worth a separate sample

The six in-repo blueprints all answer "**how do I embed one Agentao instance into my product?**". Kanban answers a different shape:

> "How do I run **many specialized agents** as a system, with state, retries, and isolation, where Agentao is one of the backends?"

If you're building anything that has a *queue of work* (CI, batch eval, content pipelines, agentic refactor jobs, autonomous research), the kanban project is closer to your shape than 7.1–7.5 are.

## ⚠️ Pitfalls (extracted from the repo's own design notes)

| Day-2 bug | Root cause | Fix the repo applies |
|---|---|---|
| Two daemons writing the same board | No mutual exclusion across processes | `.daemon.lock` + `kanban daemon status` reports `running / stale / stopped` |
| Concurrent workers stomp the working tree | Single shared checkout | Per-card git worktree + auto-detach on `DONE`/`BLOCKED` |
| Worktree removed before deliverables copied out | `git worktree remove` is destructive of gitignored files | Artifact rescue snapshots `workspace/reports/...` to `workspace/raw/<card>/artifacts-<ts>/` first |
| Card silently stuck "in review" forever | No lease expiry | Claim leases + `kanban recover --stale` |
| Web UI accidentally exposed write endpoints to LAN | Bind-all + writes was the default | Writes off by default; non-loopback bind + writes requires explicit `--allow-remote-writes` |

## Pointers, not a recipe

- **Repo**: <https://github.com/jin-bo/agentao-kanban>
- **One-line install + demo**: `uvx --from agentao-kanban kanban init --demo && uvx --from agentao-kanban kanban demo`
- **Design docs worth reading first** (in the repo): `docs/worktree-isolation-design.md`, `docs/agent-router-design.md`, `docs/agent-profile-acp-design.md`, `docs/v0.1.2-concurrency-plan.md`

This page intentionally does **not** pin to a specific kanban version. If a CLI flag or filename here drifts from the repo, the repo wins — open an issue here so we can resync.

---

← [7.6 WeChat Intelligent Bot](./6-wechat-bot) · → [Appendices](/en/appendix/)
