---
layout: home
hero:
  name: Agentao
  text: Embed an AI agent into your own product
  tagline: A 3-line Python import, or stdio JSON-RPC for any language. Tools, permissions, memory, multi-tenant — included.
  actions:
    - theme: brand
      text: Run Hello in 5 min →
      link: /en/part-1/4-hello-agentao
    - theme: alt
      text: Recipes (1-click)
      link: /en/recipes/
features:
  - icon: ⚡
    title: I just want to try it
    details: 3 lines of Python, get a streaming reply. No web server, no extra services.
    link: /en/part-1/4-hello-agentao
    linkText: 5-min Hello →
  - icon: 🧰
    title: I want the agent to call my API
    details: Wrap an HTTP / DB / business function as a Tool — typed, auditable, confirmable.
    link: /en/part-5/1-custom-tools
    linkText: Custom tools →
  - icon: 🔌
    title: My host is not Python
    details: Run Agentao as a subprocess, drive it from Node / Go / Rust / IDE via ACP (stdio + JSON-RPC).
    link: /en/part-3/1-acp-tour
    linkText: ACP tour →
  - icon: 🛡️
    title: I'm going to production
    details: Permissions, sandbox, SSRF defense, multi-tenant isolation, observability, deployment.
    link: /en/part-6/1-defense-model
    linkText: Defense in depth →
  - icon: 🌐
    title: I'm building a web backend
    details: FastAPI / Flask templates with SSE streaming, session pool, cancellation, auth.
    link: /en/part-2/7-fastapi-flask-embed
    linkText: Web backend embedding →
  - icon: 📐
    title: I want my host code to keep working across releases
    details: "The `agentao.host` package is the stable, schema-snapshotted host API. Use it for audit / observability / billing pipelines."
    link: /en/part-4/7-host-contract
    linkText: Harness contract →
  - icon: 🆘
    title: Something is broken
    details: FAQ by symptom, error code reference, migration guide.
    link: /en/appendix/f-faq
    linkText: Troubleshooting →
---

## What is Agentao, in one paragraph

Agentao is an **embeddable Python agent runtime** — `from agentao import Agentao` and you have a stateful, tool-using assistant inside your app. Same runtime also speaks the **ACP** stdio protocol so non-Python hosts (IDE plugins, Node, Go, Rust) can drive it without re-implementing anything.

What's in the box: built-in tools (files / shell / web / search), MCP client, permission engine, multi-tenant working directories, conversation compression, SQLite-backed memory, runtime LLM swap.

## Pick your starting point

| If you're… | Read in order |
|-----------|---------------|
| Just exploring | [1.4 Hello](/en/part-1/4-hello-agentao) → [1.2 Core Concepts](/en/part-1/2-core-concepts) |
| A Python backend engineer shipping a feature | [1.4](/en/part-1/4-hello-agentao) → [Part 2](/en/part-2/) → [5.1 Custom Tools](/en/part-5/1-custom-tools) → [Part 6](/en/part-6/) |
| Building an IDE / editor plugin | [1.3 Modes](/en/part-1/3-integration-modes) → [Part 3 · ACP](/en/part-3/) → [Part 4 · Events](/en/part-4/) |
| DevOps / SRE | [1.5 Requirements](/en/part-1/5-requirements) → [Part 6](/en/part-6/) |
| Security reviewer | [Part 6 · Defense](/en/part-6/) + [Appendix D · Errors](/en/appendix/d-error-codes) |

Prefer Chinese? → [简体中文版本](/zh/)

> Assumes basic familiarity with LLMs and function/tool calling. JSON-RPC is only needed for the ACP path.
