# Appendix E · Migrating from LangChain / AutoGen / CrewAI

If you've already built agents with another framework, the mental model of Agentao will feel familiar in most places and different in a few load-bearing ones. This appendix is the concordance.

## E.1 One-page mental-model map

| Concept | LangChain | AutoGen | CrewAI | Agentao |
|---------|-----------|---------|--------|---------|
| Agent unit | `AgentExecutor` (+ chain / graph) | `ConversableAgent` | `Agent` role | `Agentao` instance |
| Tool | `BaseTool` | function with docstring | `tool` decorator | `Tool` ABC |
| Multi-agent | LangGraph | `GroupChat` | `Crew` | spawn another `Agentao` or use ACP reverse call |
| Memory | `ConversationBufferMemory` / vector stores | `memory` on agent | `memory` on agent | `MemoryManager` (SQLite, project+user) |
| Streaming | callbacks / LCEL `astream` | `register_hook` | event hooks | `Transport` + `AgentEvent` |
| Tool approval | HITL via `interrupt` | `a_human_input_mode` | `human_input=True` | `Transport.confirm_tool` |
| External model context | MCP adapter | function calling | n/a | first-class MCP (stdio + SSE) |
| Host-proc isolation | n/a (in-proc) | n/a (in-proc) | n/a (in-proc) | **ACP** (subprocess) |

## E.2 From LangChain

### What's the same

- **Tools** — the boilerplate is almost identical. LangChain's `BaseTool.name/description/args_schema/_run` maps 1-to-1 to Agentao's `Tool.name/description/parameters/execute`.
- **Streaming via callbacks** — LangChain's callback handlers and Agentao's `Transport.emit(AgentEvent)` serve the same purpose.
- **Prompt composition** — system-prompt blocks in LangChain become Agentao's `AGENTAO.md` + skills.

### What's different

- **No LCEL / graph** — Agentao is a single execution loop, not a composable pipeline. If you were building a DAG of chains, collapse it into one system prompt + tools. Branching logic belongs in the LLM, not the framework.
- **No retrievers-as-tools glue** — write a thin custom tool that wraps your existing retriever's `.get_relevant_documents()` call.
- **Memory is not a vector store** — Agentao memory is structured key/value SQLite. If you relied on vector recall, keep your vector DB; expose it as an MCP server or custom tool and let the LLM call it.
- **Agents run forever by default** — LangChain has `max_iterations`; Agentao has it too (`max_iterations=` on `chat()`) but defaults to 100. Turn it down for cost control.

### Migration recipe

1. Port tools first. `_run(self, **kw)` → `execute(self, **kw)`; `args_schema` Pydantic → `parameters` JSON Schema (or generate it with `pydantic.TypeAdapter`).
2. Move prompt text to `AGENTAO.md` + skill files. Any per-request dynamic context stays in the user message.
3. Replace `AgentExecutor(..., memory=ConversationBufferMemory())` with `Agentao(...)`; history lives in `agent.messages` automatically.
4. Wire streaming: replace callback handlers with a `SdkTransport(on_event=…)`.
5. For RAG, expose your vector store as an MCP server or a `Tool` subclass.

## E.3 From AutoGen

### What's the same

- **Conversational loop** — AutoGen's "agent talks, tool is called, result returns" matches Agentao's inner loop.
- **Async-friendly** — both are OK under `asyncio.to_thread` / event-loop integration.

### What's different

- **No `GroupChat`** — AutoGen's strength is multi-agent orchestration. Agentao supports sub-agents (one `Agentao` spawning another, or ACP reverse-calling a different server), but there's no built-in "group chat manager". For multi-role conversations, model them as skills + one agent, or wire your own coordinator.
- **Tool calling is OpenAI-style only** — AutoGen supports multiple LLM providers with varying tool-call formats; Agentao standardizes on the OpenAI-compatible tool-call schema. Non-OpenAI providers must speak the OpenAI format.
- **Human-in-the-loop** — AutoGen's `human_input_mode` parameter ≈ `Transport.confirm_tool` + `Transport.ask_user`.
- **No `UserProxyAgent`** — the user sits outside the loop, communicating through `chat()` calls. Host code is the "user proxy".

### Migration recipe

1. Identify the single "most autonomous" AutoGen agent — that's your `Agentao`.
2. Collapse other `ConversableAgent`s into either tools (if stateless) or skills (if they shape behavior).
3. `GroupChat` managers become your host-side code (FastAPI endpoint, scheduler loop) that decides when to call `agent.chat()`.
4. Port `register_function` calls to `Tool` subclasses.

## E.4 From CrewAI

### What's the same

- **Role / goal / backstory framing** — CrewAI's per-agent `role` + `goal` + `backstory` → Agentao's `AGENTAO.md` + activated skills.
- **Tools as discrete units** — CrewAI `@tool` ≈ Agentao `Tool` subclass.

### What's different

- **No `Crew` orchestrator** — CrewAI's explicit `tasks` / `process` pipeline is the opposite of Agentao's style. In Agentao, the LLM decides what to do next based on tools + skills + user message. Multi-step workflows live in the prompt or in a host-side loop, not in framework config.
- **Hierarchical vs flat** — CrewAI `Process.hierarchical` / `sequential` become either one super-agent + skills or host-side orchestration.
- **No manager agent abstraction** — if you had one, reshape it into host code that calls `agent.chat()` multiple times with different prompts.

### Migration recipe

1. Pick the most useful CrewAI agent — port it first with its tools.
2. For `Process.sequential`: write a host-side function that calls `agent.chat("step 1 …")`, inspects output, then `agent.chat("step 2 …")`.
3. For `Process.hierarchical`: the manager becomes host code; worker agents become either additional `Agentao` instances (isolated) or skills (if they just change tone/approach).
4. Port tools.
5. Migrate `memory=True` to the `MemoryManager` (see [5.5](/en/part-5/5-memory)).

## E.5 Decision matrix — when to move

You might **not** want to migrate if:

- You rely heavily on LangGraph DAGs (stay on LC) or AutoGen group chats (stay on AG)
- Your RAG pipeline is deep and you don't want to wrap it as MCP
- You need Python in-proc only, cross-language isn't a goal, and you already have ops maturity on the other framework

You probably **want** to migrate if:

- You need embeddable (Python SDK + ACP for non-Python hosts)
- You need rigorous sandbox + permissions (see [Part 6](/en/part-6/))
- You need first-class MCP and a small, reviewable core
- You want deterministic lifecycle (`chat()` → `close()`) rather than long-lived chains

## E.6 Patterns that translate cleanly

| Pattern | LC / AG / CrewAI | Agentao |
|---------|------------------|---------|
| "Tool that calls our API" | BaseTool | `Tool` subclass |
| "Inject company policy into prompt" | system message | `AGENTAO.md` |
| "Task-specific behavior profile" | system prompt branch | Skill (activate on demand) |
| "Human must approve writes" | HITL callback | `Transport.confirm_tool` |
| "Per-user conversation memory" | `ConversationBufferMemory` keyed by user | Per-user `working_directory` → project-scope memory |
| "RAG over docs" | retriever tool | MCP filesystem / custom retriever tool |
| "Cancel a running turn" | LCEL abort / AG cancel | `CancellationToken` |

## E.7 Headless runtime — `nonInteractivePolicy` shape change (Week 3)

The pre-Week-3 bare-string form of `nonInteractivePolicy` is no longer accepted. `AcpClientConfig.from_dict` / `load_acp_client_config` raise `AcpConfigError` **at config-load time** — the failure cannot slip through to `send_prompt`.

Before:

```json
{
  "servers": {
    "my-server": {
      "command": "…",
      "args": [],
      "env": {},
      "cwd": ".",
      "nonInteractivePolicy": "reject_all"
    }
  }
}
```

After:

```json
{
  "servers": {
    "my-server": {
      "command": "…",
      "args": [],
      "env": {},
      "cwd": ".",
      "nonInteractivePolicy": { "mode": "reject_all" }
    }
  }
}
```

Notes:

- Drop `nonInteractivePolicy` entirely to accept the default `{"mode": "reject_all"}`.
- For a single-call override, don't touch the config — use the `interaction_policy=` kwarg on `ACPManager.send_prompt` / `ACPManager.prompt_once`. See [3.4 Reverse ACP calls](/en/part-3/4-reverse-acp-call).
- There is no silent upgrade. If you need to roll old configs through automation, parse each one with `AcpClientConfig.from_dict` in a dry-run, catch `AcpConfigError`, and rewrite the flagged servers explicitly.

## E.8 Common pitfalls when migrating

1. **Over-abstracting** — you don't need a DAG. Trust the LLM + tools.
2. **Under-trusting tool descriptions** — Agentao has no chain-of-thought scaffold; the tool description and AGENTAO.md *are* the behavior plan. Make them rich.
3. **Mixing user + project memory** — keep tenant isolation as the default (see [6.4](/en/part-6/4-multi-tenant-fs)); user-scope is for single-user setups only.
4. **Leaving confirmations off** — other frameworks often default to "ask everything". Agentao lets you allow-list; use it, but only after you've audited the tool's blast radius.

---

End of appendix.
