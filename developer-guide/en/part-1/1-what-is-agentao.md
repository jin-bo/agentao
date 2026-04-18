# 1.1 What is Agentao

**Agentao is an embeddable agent harness** — a runtime shell that lets you drop LLM-powered capability (tool use, project-aware reasoning, multi-turn loops) into your own application.

## From CLI to Embeddable Framework

Agentao started as a command-line tool: `uv run agentao` opens a terminal REPL where it reads files, runs commands, queries docs. But the CLI is just the default skin. Since v0.2.10, the core runtime (the "harness") is decoupled and exposes two stable embedding surfaces:

- **Python in-process SDK** — `from agentao import Agentao` hands you a live agent instance
- **ACP protocol server** — `agentao --acp --stdio` speaks a standard JSON-RPC protocol any language can drive

> **Harness** here does not mean an end product. It is the **runtime skeleton** that orchestrates the LLM loop, tool invocation, permissions, memory, sessions, and sandboxing. Your application provides the "business muscles" (your APIs, your database, your UI); Agentao provides the "nervous system" (decision loop, state, safety rails).

## What Agentao is not

| Don't treat Agentao as | Reason |
|-------------------------|--------|
| A LangChain / LlamaIndex replacement | Those are toolkits to compose; Agentao is a preassembled runtime |
| An end-to-end agent product | It has no UI, user system, or billing — your app must install it |
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

Next: [1.2 Core Concepts →](./2-core-concepts)
