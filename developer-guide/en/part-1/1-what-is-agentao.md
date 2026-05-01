# 1.1 What is Agentao

> **What you'll learn**
> - What Agentao actually is, in 5 lines of code
> - What's in the box vs. what your app provides
> - How it differs from LangChain / a chatbot / a generic AI assistant

**Agentao is an embeddable Python agent runtime.** Three lines of code give your app a stateful, tool-using assistant — one that reads files, runs commands, calls your APIs, and remembers context across turns:

```python
from pathlib import Path
from agentao import Agentao

agent = Agentao(working_directory=Path.cwd())
print(agent.chat("Summarize the last 5 commits."))
```

That's the whole minimum. No web server, no extra services. The same runtime also speaks the **ACP** stdio JSON-RPC protocol, so non-Python hosts (IDE plugins, Node, Go, Rust) can drive it without re-implementing anything:

```bash
agentao --acp --stdio
```

## What you get out of the box

- **Built-in tools** — file read/write/edit, shell, web fetch, search, glob/grep, MCP bridge
- **Permissions** — rule-based engine + interactive confirm before risky actions
- **Memory** — SQLite-backed, project + user scopes, persists across sessions
- **Sessions** — conversation compression, working-directory isolation, multi-instance friendly
- **Two embedding paths** — direct Python import (shortest), or stdio JSON-RPC (any language)
- **LLM portability** — OpenAI / Anthropic / Gemini / DeepSeek / vLLM / any OpenAI-compatible endpoint
- **Forward-compatible host contract** — `agentao.host` ships a frozen, schema-snapshotted API so production code keeps working across releases ([4.7](/en/part-4/7-host-contract))

## From CLI to embeddable runtime

Agentao started as a command-line tool — `uv run agentao` opens a terminal REPL. But the CLI is just one skin. Since v0.2.10 the core runtime is decoupled with two stable embedding surfaces:

- **Python in-process SDK** — `from agentao import Agentao` gives you a live agent instance
- **ACP protocol server** — `agentao --acp --stdio` speaks JSON-RPC any language can drive

::: info Vocabulary note
You'll see the word **harness** in places — it refers to the runtime skeleton that orchestrates the LLM loop, tools, permissions, memory, and sandboxing. Your app provides the "business muscles" (APIs, DB, UI); Agentao provides the "nervous system" (decision loop, state, safety rails). Treat it as an explanatory label, not the product name.
:::

## What Agentao is not

| Don't treat Agentao as | Reason |
|-------------------------|--------|
| A LangChain / LlamaIndex replacement | Those are toolkits to compose; Agentao is a preassembled runtime |
| An end-to-end agent product | It has no UI, user system, or billing — your app must install it |
| A generic AI assistant or coding chatbot | The CLI is one surface; the product itself is the governed runtime behind it |
| Locked to one model vendor | Works with OpenAI / Anthropic / Gemini / DeepSeek / any OpenAI-compatible endpoint |
| A thin framework | Ships with batteries: file / shell / web / search / memory / MCP bridge tools |

## Why embed Agentao

1. **Batteries-included tool set** — file I/O, shell, web fetch, search, code editing, MCP bridge, all battle-tested
2. **Layered safety** — tool confirmation, permission engine, domain allowlist/blocklist, macOS sandbox-exec
3. **Mature session management** — conversation compression, memory persistence, `working_directory` isolation for concurrent instances
4. **Standard protocols** — native MCP client + ACP server interop with the Zed / Claude Code ecosystem
5. **Lightweight, controllable** — pure-Python dependency, no mandatory web server or database

## Guide layout

- **Part 2**: Python in-process embedding (shortest path)
- **Part 3**: ACP protocol for non-Python hosts
- **Part 4**: Event layer & UI integration (streaming, confirm, ask)
- **Part 5**: Six extension points — make Agentao speak **your** business
- **Parts 6 – 7**: Security and production deployment
- **Part 8**: Five cookbook blueprints

## TL;DR

- Agentao = **embeddable Python agent runtime**. `from agentao import Agentao` and you have a stateful tool-using assistant.
- Two embedding paths: **Python in-process SDK** (shortest) or **ACP stdio JSON-RPC** (any language).
- Batteries included: tools, permissions, memory, sessions, multi-tenant working dirs, MCP client.
- Your app provides the business muscles (APIs / DB / UI); Agentao provides the nervous system.

Next: [1.2 Core Concepts →](./2-core-concepts)
