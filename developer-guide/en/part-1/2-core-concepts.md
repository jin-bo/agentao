# 1.2 Core Concepts

> **What you'll learn**
> - The six nouns the rest of this guide depends on
> - How they fit together in one diagram
> - Where each one is anchored in code, so you can dig further

Before writing integration code, learn Agentao's six core nouns. Every chapter that follows uses them.

## Concept map

```
┌─────────────────────────────────────────────────────────────┐
│                     Your Application (Host)                  │
│                                                             │
│   ┌──────────┐ confirm/ask/event                            │
│   │ Transport│◄────────────────┐                            │
│   └────┬─────┘                 │                            │
│        │ drive                 │                            │
│        ▼                       │                            │
│   ┌──────────────────────────────────────────────────┐      │
│   │                   Agent (Agentao)                │      │
│   │                                                  │      │
│   │   Session ──► Working Directory                  │      │
│   │      │                                           │      │
│   │      ▼                                           │      │
│   │   Tools ◄── Skills ◄── System Prompt             │      │
│   │      │                                           │      │
│   │      ▼                                           │      │
│   │   LLM Client (OpenAI/Anthropic/Gemini/…)         │      │
│   └──────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

## 1. Agent (Agentao instance)

A `agentao.Agentao` instance is a **stateful, single-session** object. It owns:

- Conversation history `self.messages`
- Tool registry `self.tools`
- Skill manager `self.skill_manager`
- Memory manager `self.memory_manager`
- One `working_directory` (the root for file operations)

One agent instance = one session. For multi-user / multi-session deployments, **construct a separate instance per session** (see Part 7).

## 2. Tool

An action the agent can invoke. Each tool declares:

- `name` — unique id
- `description` — what the LLM sees
- `parameters` — JSON Schema for args
- `execute(**kwargs) -> str` — the real implementation
- `requires_confirmation` — whether to prompt the user

Agentao ships dozens of built-ins (`read_file`, `write_file`, `run_shell_command`, `web_fetch`, `grep`, `glob`…). **Your business APIs become agent capabilities by being wrapped as tools** (Part 5.1).

## 3. Skill

**On-demand domain knowledge** packaged as a directory:

```
skills/my-skill/
├── SKILL.md            # Entry file; YAML frontmatter + body
└── references/*.md      # Optional; loaded only after activation
```

A skill is not code — it is **markdown that guides LLM behavior**. Contrast:

|  | Tool | Skill |
|---|------|-------|
| Shape | Python class | Markdown file |
| Active | On registration | After explicit activation |
| Typical use | "Do a thing" | "Do things our way" |

## 4. Transport

The **bidirectional channel** between the agent and the host. A transport implements 4 methods:

- `emit(event)` — agent → host (streaming text, tool start/end, thinking…)
- `confirm_tool(name, desc, args) -> bool` — agent asks host "may I run this?"
- `ask_user(question) -> str` — agent asks user back
- `on_max_iterations(count, messages) -> dict` — fallback when the loop cap is hit

Three built-ins:
- `NullTransport` — silent, auto-approve except an MCP Skills gated confirmation, which it refuses (for tests)
- `SdkTransport` — callback-driven, **the default for library embedding**
- Rich CLI transport — for terminal users

## 5. Session / Working Directory

A **session** is the lifetime from `Agentao()` to `close()`, matching one conversation history.

**Working directory** is the "project root" this session sees. File tools, shell, skills, and `AGENTAO.md` loading are all relative to it.

⚠️ When embedding multiple instances in one process, **always pass `working_directory=Path(...)` explicitly**. Otherwise `Path.cwd()` is shared globally and sessions bleed into each other.

## 6. System Prompt (dynamic)

Not a static string. The model gets its instructions in two parts:

1. **The system prompt**, a stable prefix that stays byte-identical across the turns of a session (so providers can cache it): `AGENTAO.md` (project instructions, read from working_directory), the base agent capability description, the available skill catalog, and the stable memories `<memory-stable>`.
2. **A volatile tail**, rebuilt for every request and sent as one trailing `<system-reminder>` message that history never stores: which skills are active (a local skill's full text is stored in history once instead), the task list (todos), and the memory recall block `<memory-context>`.

The current date and time are in neither part: they go in a `<system-reminder>` at the start of each user message.

Hosts inject business knowledge here by writing `AGENTAO.md` or custom skills (Part 5.6).

## Quick cross-reference

| Concept | Source location | Main touchpoint |
|---------|----------------|------------------|
| Agent | `agentao/agent.py` | `Agentao(...)` constructor |
| Tool | `agentao/tools/base.py` | Subclass `Tool` |
| Skill | `agentao/skills/manager.py` | Author `SKILL.md` |
| Transport | `agentao/transport/` | Instantiate `SdkTransport` |
| Session | `agent.messages` | Lifecycle + concurrency |
| System Prompt | `agent._build_system_prompt()` | Author `AGENTAO.md` |

## TL;DR

- **Agent = one stateful session.** One per user/conversation; rebuild for the next session.
- **Tool = capability.** Python class with `name` / `description` / `parameters` / `execute()`. Wrap your business APIs as tools.
- **Skill = on-demand prompt knowledge.** Markdown bundle activated explicitly; not code.
- **Transport = the UI bridge.** 4 callbacks: emit / confirm / ask_user / on_max_iterations.
- **Working directory = process-isolated session root.** Always pass an explicit `Path`.
- **System prompt = composed each turn** from AGENTAO.md + date + skills + memory.

Next: [1.3 Integration Modes →](./3-integration-modes)
