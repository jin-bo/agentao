# 1.2 Core Concepts

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
└── reference/*.md      # Optional; loaded only after activation
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
- `NullTransport` — silent, auto-approve (for tests)
- `SdkTransport` — callback-driven, **the default for library embedding**
- Rich CLI transport — for terminal users

## 5. Session / Working Directory

A **session** is the lifetime from `Agentao()` to `close()`, matching one conversation history.

**Working directory** is the "project root" this session sees. File tools, shell, skills, and `AGENTAO.md` loading are all relative to it.

⚠️ When embedding multiple instances in one process, **always pass `working_directory=Path(...)` explicitly**. Otherwise `Path.cwd()` is shared globally and sessions bleed into each other.

## 6. System Prompt (dynamic)

Not a static string — **rebuilt on every `chat()` call** from:

1. `AGENTAO.md` (project instructions, read from working_directory)
2. Base agent capability description
3. Current date + available skill catalog
4. Active skills' full text
5. Memory recall block `<memory-context>`
6. Task list (todos)

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

Next: [1.3 Integration Modes →](./3-integration-modes)
