# 1.6 Compared with Vendor Agent SDKs

> **What you'll learn**
> - How Agentao differs from the Claude Agent SDK, the OpenAI Agents SDK and Strands
> - Where each of those is the stronger choice
> - What to check before you pick one

Several vendors now ship an agent loop you can embed in your own program. If you are choosing one, the question is not "which has more features" — they overlap heavily — but which trade-offs fit your host. This page compares Agentao with three of them on the points that most often decide it.

::: info Checked on 2026-10-01
Facts about other projects come from their own documentation and repositories on that date (versions under [Sources](#sources)). These projects release often; follow the links before relying on a detail. Corrections are welcome as issues.
:::

## At a glance

| | **Agentao** | **Claude Agent SDK** | **OpenAI Agents SDK** | **Strands Agents** (AWS) |
|---|---|---|---|---|
| **Where the agent loop runs** | In your Python process | In a bundled Claude Code CLI that the SDK starts as a child process | In your Python process | In your process |
| **Languages** | Python; other languages over ACP | Python, TypeScript | Python (separate JS/TS SDK) | Python, TypeScript |
| **Models** | Any OpenAI-compatible Chat Completions endpoint, plus native Anthropic Messages and OpenAI Responses wires; switch provider at runtime | Claude only — via the Anthropic API, Bedrock, Vertex AI or Microsoft Foundry; routing to non-Claude models is not supported | OpenAI natively; any OpenAI-compatible endpoint; LiteLLM / Any-LLM adapters documented as best-effort beta | Bedrock by default; Anthropic, OpenAI, Gemini, Ollama, LiteLLM and others |
| **Sent to the vendor by default** | Nothing — no telemetry | Inherits Claude Code's defaults: usage metrics go to Anthropic when using the Claude API (off on Bedrock / Vertex / Foundry); can be disabled | Traces go to OpenAI's tracing backend; can be disabled or redirected | OpenTelemetry export is opt-in; the Strands harness touches no telemetry unless it is configured |
| **Audit / observability** | Local JSONL replay files and an in-process event stream; no OpenTelemetry | OpenTelemetry export (opt-in); client-side cost estimates | Tracing with pluggable processors and many third-party integrations | OpenTelemetry |
| **Permissions** | Four modes (`read-only`, `workspace-write`, `full-access`, `plan`); allow / deny / ask rules; per-call approval through the host | Six modes; allow / deny / ask rules; a `canUseTool` callback | Per-tool `needs_approval` with pause/resume; input, output and tool guardrails | Interrupts for human approval; the Strands harness adds opt-in "interventions" (it runs tools unasked by default) |
| **Hooks** | Eight shell-hook events; reads hook files written for Claude Code (an enumerated subset) | Ten events in the Python SDK, more in TypeScript | Run and agent lifecycle callbacks | Typed hook events |
| **MCP** | stdio, Streamable HTTP, SSE; OAuth login for URL servers | In-process servers, stdio, HTTP, SSE | Hosted MCP, Streamable HTTP, SSE, stdio | Yes |
| **Sandbox** | macOS `sandbox-exec`, off by default | Claude Code's sandbox: macOS, Linux and WSL2, off by default | Sandbox agents on local Unix or Docker, or hosted providers (E2B, Modal, Daytona, …) | Pluggable sandbox backends (Docker, SSH, custom) |
| **ACP (agent ↔ editor)** | Built in: `agentao --acp --stdio` (and an ACP client) | Through a separate adapter published by the Agent Client Protocol project | Not documented | TypeScript `strands` CLI only (`--acp-server`); not in the Python packages |
| **License** | MIT | MIT, use governed by Anthropic's Commercial Terms | MIT | Apache-2.0 |

All four also have sub-agents and saved, resumable sessions.

## Where Agentao is different

- **No vendor loop, no vendor data path.** The loop is Agentao's own code running in your process, it talks to whichever endpoint you configure, and it sends no telemetry. The Claude Agent SDK runs Claude Code itself (a separate binary), and the OpenAI Agents SDK exports traces to OpenAI unless you turn that off.
- **Provider-neutral as a design point, not an adapter.** Three wire protocols are implemented natively, and history stays in one format across them, so `/provider` or a host call can switch model or vendor mid-session.
- **The audit trail is a file you own.** Every turn can be recorded as JSONL under `.agentao/replays/`; the host also gets a typed event stream (`agentao.host`). Nothing depends on a hosted tracing backend.
- **One runtime, three surfaces.** The same package is a Python library, the `agentao` CLI and an ACP server, so a non-Python host or an ACP editor drives exactly what a Python host embeds ([1.3 Integration Modes](./3-integration-modes)).
- **Chinese-language support.** Bilingual documentation, and memory recall that segments Chinese text (jieba).

## Where another choice is stronger

- **You only use Claude and want Claude Code's behaviour exactly** → the Claude Agent SDK runs the same agent, with more hook events, file checkpointing, and Claude Code's sandbox on Linux as well as macOS.
- **You want managed sandboxes or the OpenAI tracing ecosystem** → the OpenAI Agents SDK ships Docker and hosted sandbox backends, many session stores and tracing integrations.
- **You are on AWS, or want OpenTelemetry, an evals package, or multi-agent patterns (graph, swarm, A2A)** → Strands covers those.
- **You need Linux or container sandboxing, or OpenTelemetry, today** → Agentao does not have them yet.
- **You need an ecosystem** → the vendor SDKs have far larger communities; Agentao is a small project.

## Before you choose

Answer these for your host, then pick:

1. Must prompts, code and traces stay off third-party services other than the model endpoint you choose?
2. Will you need models from more than one vendor, or to switch vendor without changing code?
3. Is your host Python, or does it need a process boundary (another language, an editor)?
4. Do you need OS-level isolation on Linux, or is per-call approval plus your own process isolation enough?

If 1–3 are mostly "yes" and 4 is "approval is enough", Agentao is built for that case. Otherwise, start from the "stronger" list above.

## Sources

Checked 2026-10-01.

- **Claude Agent SDK** 0.2.163 (2026-09-30): [overview](https://code.claude.com/docs/en/agent-sdk/overview), [permissions](https://code.claude.com/docs/en/agent-sdk/permissions), [hooks](https://code.claude.com/docs/en/agent-sdk/hooks), [MCP](https://code.claude.com/docs/en/agent-sdk/mcp), [observability](https://code.claude.com/docs/en/agent-sdk/observability), [LLM gateways](https://code.claude.com/docs/en/llm-gateway), [sandboxing](https://code.claude.com/docs/en/sandboxing), [data usage](https://code.claude.com/docs/en/data-usage), [ACP adapter](https://github.com/agentclientprotocol/claude-agent-acp)
- **OpenAI Agents SDK** 0.22.3 on PyPI (2026-09-17): [docs](https://openai.github.io/openai-agents-python/), [models](https://openai.github.io/openai-agents-python/models/), [tracing](https://openai.github.io/openai-agents-python/tracing/), [human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [MCP](https://openai.github.io/openai-agents-python/mcp/), [sandbox agents](https://openai.github.io/openai-agents-python/sandbox_agents/), [repository](https://github.com/openai/openai-agents-python)
- **Strands Agents** `strands-agents` 1.57.2 (2026-10-01) and `strands-harness` 0.1.2 (2026-09-22): [repository](https://github.com/strands-agents/harness-sdk), [user guide](https://strandsagents.com/docs/user-guide/)
- **Agentao**: [configuration reference](https://github.com/jin-bo/agentao/blob/main/docs/reference/configuration.md), [host API](https://github.com/jin-bo/agentao/blob/main/docs/reference/host-api.md)

→ [Part 2 · Embedding in Python](/en/part-2/)
