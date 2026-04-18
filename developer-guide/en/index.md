---
layout: home
hero:
  name: Agentao Developer Guide
  text: Embed the harness in your product
  tagline: Two stable embedding paths · Six extension points · Production-ready security & observability
  actions:
    - theme: brand
      text: Start · Part 1
      link: /en/part-1/1-what-is-agentao
    - theme: alt
      text: 5-min Hello World
      link: /en/part-1/4-hello-agentao
features:
  - icon: 🐍
    title: Python In-Process SDK
    details: Instantiate Agentao directly with SdkTransport + callbacks — shortest path for Python backends.
  - icon: 🔌
    title: ACP Cross-Language Protocol
    details: Stdio + NDJSON JSON-RPC 2.0, so Node / Go / Rust / IDE hosts can drive Agentao.
  - icon: 🧩
    title: Six Extension Points
    details: Custom Tools, Skills, MCP servers, Permission engine, Memory, Sandbox — shape the agent to your domain.
---

## Who this guide is for

You are building **your own product** (SaaS backend, IDE plugin, support desk, data workbench…) and want to embed an agent that speaks your business language. This guide covers:

- Embedding Agentao as a library or service in your process
- Exposing your business APIs as agent tools
- Containing compliance, security, and audit inside a controlled boundary

## How to read

| Your role | Suggested path |
|-----------|----------------|
| Python backend engineer | Parts 1 → 2 → 4 → 5 → 6 → 8 |
| IDE / editor plugin author | Parts 1 → 3 → 4 → 8.2 |
| DevOps / SRE | Parts 1 → 6 → 7 |
| Security reviewer | Part 6 + Appendix D |

> This guide assumes familiarity with LLMs, function/tool calling, and JSON-RPC basics.
