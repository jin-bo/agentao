# Part 5 · Extend Agent Behavior

This part has two axes: the **capability plane** gives the agent business capabilities, knowledge, and rules; the **control plane** lets you intervene at lifecycle points the agent already passes through. Sections 5.1–5.6 and 5.8 are capability-plane extensions; 5.7 is the control plane.

::: info Key terms in this Part
- **Tool subclass** — the canonical way to expose a business capability: `name` / `description` / `parameters` / `execute()` · [§5.1](/en/part-5/1-custom-tools), [G.2](/en/appendix/g-glossary#g-2-extension-points)
- **`requires_confirmation`** — Tool flag → triggers the `ask_confirmation` UI for side-effecting calls · [§5.1](/en/part-5/1-custom-tools), [§5.4](/en/part-5/4-permissions)
- **PermissionEngine** — Tier 0 hard guards + presets + custom rules; the rule-based defense layer · [§5.4](/en/part-5/4-permissions), [G.5](/en/appendix/g-glossary#g-5-security-vocabulary)
- **Skill** — `SKILL.md` + YAML front-matter, dynamically discovered from `skills/`; LLM-side instructions · [§5.2](/en/part-5/2-skills), [G.2](/en/appendix/g-glossary#g-2-extension-points)
- **MemoryManager** — SQLite-backed dual-scope (`project` + `user`) persistent + session memory · [§5.5](/en/part-5/5-memory), [G.1](/en/appendix/g-glossary#g-1-core-concepts)
- **Plugin Hook** — `hooks.json` rules aligned with Claude Code; intercept / inject / continue at lifecycle points · [§5.7](/en/part-5/7-plugin-hooks)
:::

## Coverage

**Capability plane**

- [**5.1 Custom Tools**](./1-custom-tools) — the preferred way to expose business APIs to the LLM
- [**5.2 Skills**](./2-skills) — Markdown instructions for the LLM
- [**5.3 MCP Server Integration**](./3-mcp) — reuse the community / official tool ecosystem
- [**5.4 Permission Engine**](./4-permissions) — the first rule-based defense, layered with `confirm_tool`
- [**5.5 Memory System**](./5-memory) — cross-session persistence and compliance
- [**5.6 System Prompt Customization**](./6-system-prompt) — the 3 of 11 prompt blocks you actually own
- [**5.8 Host Tool Injection**](./8-tool-injection) — select the tool surface from the host: `extra_tools` / `disable_tools` / `enabled_tools` + runtime `add_tool` / `remove_tool`

**Control plane**

- [**5.7 Plugin Hooks**](./7-plugin-hooks) — `hooks.json` rules; injection and interception at `UserPromptSubmit` / `PreToolUse` / `Stop` / `PreCompact` and other lifecycle points

## Read by task

| What you are building | Recommended path | You should be able to |
|-----------------------|------------------|------------------------|
| Let the agent call your business API | [5.1](./1-custom-tools) → [5.4](./4-permissions) | Write a Tool and add confirmation or permission boundaries for side effects |
| Select / shrink the tool surface from the host | [5.1](./1-custom-tools) → [5.8](./8-tool-injection) | Inject, replace, or prune tools at construction or runtime — and know it's not a security boundary |
| Make the agent follow team conventions | [5.2](./2-skills) → [5.6](./6-system-prompt) | Separate on-demand Skills from project-wide prompt instructions |
| Integrate an existing service ecosystem | [5.3](./3-mcp) → [5.4](./4-permissions) | Connect MCP while constraining tool visibility and execution scope |
| Remember long-term facts or user preferences | [5.5](./5-memory) → [6.4](/en/part-6/4-multi-tenant-fs) | Design memory scope, cleanup, and tenant boundaries |
| Intercept, inject, or continue at lifecycle points | [5.7](./7-plugin-hooks) → [4.7](/en/part-4/7-host-contract) | Write `hooks.json` and know when stable event streams are the right audit surface |
| Choose between Tool, Skill, MCP, and Hook | [5.1](./1-custom-tools) → [5.2](./2-skills) → [5.3](./3-mcp) → [5.7](./7-plugin-hooks) | Decide by capability, instruction, external ecosystem, and lifecycle intervention |

## Three pragmatic rules

1. **Start with 5.4**: write permission rules before deployment — avoid "we'll add safety later" tech debt
2. **Don't duplicate 5.1 and 5.3**: the same capability should be either a Tool or an MCP server, never both — the LLM gets confused
3. **Skills: small and specific**: one skill, one job, concrete trigger description; many small skills beat one monster skill

→ [Start with 5.1 →](./1-custom-tools)
