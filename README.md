# Agentao (Agent + Tao)

```
   ___                      _
  / _ \ ___ _ ___  ___  ___| |_  ___  ___
 /  _  // _` / -_)| _ \/ _ \  _|/ _` / _ \
/_/ |_| \__, \___||_// \___/\__|\__,_\___/
        |___/        (The Way of Agents)
```

> **"Order in Chaos, Path in Intelligence."**
>
> **Agentao** is a **Governed Agent Runtime** — a local-first, private-first, embeddable agent harness for Python hosts. Permissions, protocols, memory, plugins, and multi-session control are all first-class.

[中文版本 README.zh.md](README.zh.md)

---

## 📚 Documentation — read this first

The full handbook lives in `developer-guide/` (VitePress, bilingual). Production site: **[agentao.cn](https://agentao.cn)**.

| Audience | Read | Site |
|---|---|---|
| **Coding agents** — Claude Code / Codex / … embedding Agentao into another project | [`docs/guides/embed-for-agents.md`](docs/guides/embed-for-agents.md) (distilled, copy-paste playbook) | — |
| **CLI users** — driving `agentao` in the terminal | [`developer-guide/en/cli/`](developer-guide/en/cli/) (12 chapters: slash commands · plan mode · memory · replay · …) | [agentao.cn/en/cli/](https://agentao.cn/en/cli/) |
| **Embedding developers** — building Agentao into your app | [`developer-guide/en/`](developer-guide/en/) (Parts 1–7 + Appendix) | [agentao.cn](https://agentao.cn) |
| **中文** | [`developer-guide/zh/cli/`](developer-guide/zh/cli/) · [`developer-guide/zh/`](developer-guide/zh/) | [agentao.cn/zh/cli/](https://agentao.cn/zh/cli/) · [agentao.cn/zh/](https://agentao.cn/zh/) |

Browse locally:

```bash
cd developer-guide && npx vitepress dev
```

For schema-level reference (every config file, every env var, every public API), the canonical doc is [`docs/reference/configuration.md`](docs/reference/configuration.md). Everything else is in the Developer Guide.

---

## Embed in 30 lines

```bash
pip install agentao
```

```python
from pathlib import Path
from agentao import Agentao
from agentao.llm import LLMClient
from agentao.transport import NullTransport

agent = Agentao(
    working_directory=Path("/tmp/agent-run-1"),
    llm_client=LLMClient(
        api_key="sk-...",
        base_url="https://api.openai.com/v1",
        model="gpt-5.4",
    ),
    transport=NullTransport(),
)
reply = agent.chat("Summarize today's logs.")
print(reply)
agent.close()
```

This constructs an agent with **no env-discovery, no implicit network, no global state** — the host owns every input. For env-driven discovery (the path the CLI uses), call `agentao.embedding.build_from_environment(working_directory=...)` instead.

Embedding details: [Developer Guide · Part 2 (Lifecycle)](https://agentao.cn/en/part-2/) and [Part 4 (Host Contract)](https://agentao.cn/en/part-4/7-host-contract).

> **Coding agent** (Claude Code, Codex, …) tasked with embedding Agentao into another project? Read [`docs/guides/embed-for-agents.md`](docs/guides/embed-for-agents.md) — a distilled, copy-paste integration playbook with construction skeletons, import rules, and a checklist.

---

## CLI Quickstart

```bash
pip install 'agentao[cli]'

# .env in your project (all three are required):
printf "OPENAI_API_KEY=sk-your-key\nOPENAI_BASE_URL=https://api.openai.com/v1\nOPENAI_MODEL=gpt-5.4\n" > .env

# Smoke test — non-interactive
agentao -p "Reply with the single word: OK"

# Interactive REPL
agentao
```

> **Upgrading from 0.3.x?** From 0.4.0 the CLI deps moved into the `[cli]` extra. Use `pip install 'agentao[full]'` for zero behaviour change. See [docs/migration/0.3.x-to-0.4.0.md](docs/migration/0.3.x-to-0.4.0.md).

First commands once the REPL is up:

```text
/help       Every slash command + tools the agent has
/status     Model, mode, tokens, active skills
/model      Switch model on the current provider
/mode       Switch permission mode (read-only · workspace-write · full-access · plan)
/plan       Enter plan mode (read-only thinking with .agentao/plan.md)
/memory     Inspect persistent memory
/mcp list   MCP server status
/exit       Leave cleanly (don't Ctrl+C)
```

CLI handbook: **[agentao.cn/en/cli/](https://agentao.cn/en/cli/)** — 12 chapters covering every slash command and the mental model behind them.

---

## Why Agentao?

The name encodes the design: *Agent* (capability) + *Tao* (governance). Three pillars of a governed runtime:

| Pillar | What it means | How Agentao implements it |
|---|---|---|
| **Constraint** (约束) | Agents must not act without consent | Tool confirmation · permission modes (`read-only` / `workspace-write` / `full-access` / `plan`) · macOS `sandbox-exec` |
| **Connectivity** (连接) | Agents must reach the world beyond training | MCP (stdio / SSE) · ACP (full-agent JSON-RPC) · plugins · hooks |
| **Observability** (可观测性) | Agents must show their work | Live thinking display · streaming tool output · full LLM logging · JSONL replay |

---

## Feature Overview

| Area | What you get | Deep dive |
|---|---|---|
| **Governance** | Tool confirmation, four permission modes, plan mode, macOS sandbox | [CLI ch. 3](developer-guide/en/cli/3-permissions-modes.md) · [ch. 4](developer-guide/en/cli/4-plan-mode.md) |
| **Context** | Token tracking, LLM-summarized compaction, overflow recovery, file re-injection | [CLI ch. 7](developer-guide/en/cli/7-context-status.md) |
| **Memory** | SQLite-backed persistent memory with two scopes (user / project), automatic recall, jieba 中文 segmentation | [CLI ch. 6](developer-guide/en/cli/6-memory.md) |
| **Skills** | Auto-discovered from `skills/`, GitHub-installable (`agentao skill install owner/repo[:path][@ref]`), plus `/crystallize` workflow | [CLI ch. 5](developer-guide/en/cli/5-skills-crystallize.md) |
| **Protocols** | MCP (stdio / SSE) for tools · ACP (stdio JSON-RPC) for full agents · plugin lifecycle | [CLI ch. 8](developer-guide/en/cli/8-mcp-acp-plugins.md) |
| **Sub-agents** | Built-in `codebase-investigator` / `generalist` · custom `.agentao/agents/<name>.md` · foreground/background dashboard | [CLI ch. 11](developer-guide/en/cli/11-sessions-agents.md) |
| **Replay & Output** | JSONL session recordings under `.agentao/replays/` · markdown-toggle · `/copy` last reply | [CLI ch. 9](developer-guide/en/cli/9-replay-output.md) |
| **Embedding** | `Agentao(...)` constructor · `events()` stream · `active_permissions()` · capability injection · ACP Pydantic schemas | [DG Part 2](https://agentao.cn/en/part-2/) · [Part 4](https://agentao.cn/en/part-4/) |

---

## Installation

```bash
# Embedding host (Python `from agentao import Agentao`) — smallest closure
pip install agentao

# CLI user (`agentao` console script) — adds rich/prompt-toolkit/readchar/pygments
pip install 'agentao[cli]'

# Zero-behaviour-change upgrade from 0.3.x — full closure
pip install 'agentao[full]'
```

**Required Python:** 3.10+. **Required env:** `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` — all three, or startup raises `ValueError`.

For Anthropic / Gemini / DeepSeek / any OpenAI-compatible provider, set `<NAME>_API_KEY` + `<NAME>_BASE_URL` + `<NAME>_MODEL` and pick it via `LLM_PROVIDER` or `/provider` at runtime. Full list: [`docs/reference/configuration.md`](docs/reference/configuration.md).

---

## For contributors

```bash
git clone https://github.com/jin-bo/agentao
cd agentao
uv sync
cp .env.example .env

# Run the CLI from source
uv run agentao
# or
./run.sh

# Tests
uv run python -m pytest tests/
```

Contributor entry points:

| What | Where |
|---|---|
| Project layout, code conventions | [`CLAUDE.md`](CLAUDE.md) |
| Adding a tool / agent / skill | [Developer Guide · Part 5](https://agentao.cn/en/part-5/) |
| Plugin author guide | [Developer Guide · §5.7](https://agentao.cn/en/part-5/7-plugin-hooks) |
| Embedding contract & ACP schemas | [Developer Guide · Part 4](https://agentao.cn/en/part-4/) |
| Examples (skills · personas · integration blueprints) | [`examples/`](examples/) |

---

## Design Principles

1. **Minimalism (极简)** — `pip install agentao` and you're running. No databases, no cloud dependencies.
2. **Transparency (透明)** — Reasoning chain on screen in real time. Every LLM call and tool call logged to `agentao.log`.
3. **Integrity (完整)** — Context never silently dropped: LLM-summarized compaction, automatic memory recall, conversation continuity across restarts.

---

## Etymology

**Agentao** = *Agent* + *Tao* (道) — the natural order that underlies all things. Three intertwined meanings:

- **Laws (法则)** — rules that constrain and shape behavior
- **Methods (方法)** — paths and techniques for accomplishing goals
- **Paths (路径)** — routes through which things flow and connect

An agent without Tao is powerful but unpredictable. *Agentao* is the structure that makes that power trustworthy.

---

## License

Open source. Use and modify as needed.

## Acknowledgments

- LLM client: [OpenAI Python SDK](https://github.com/openai/openai-python)
- CLI: [Rich](https://github.com/Textualize/rich) · [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) · [readchar](https://github.com/magmax/python-readchar)
- Optional web fetch: [Crawl4AI](https://github.com/unclecode/crawl4ai)
- MCP: [Model Context Protocol SDK](https://github.com/modelcontextprotocol/python-sdk) — architecture inspired by [Gemini CLI](https://github.com/google-gemini/gemini-cli)
- Inspired by [Claude Code](https://github.com/anthropics/claude-code)
