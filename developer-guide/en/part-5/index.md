# Part 5 · Teach the Agent Your Business

The six extension points are Agentao's **business interface**. After this part you can shape the agent into an assistant that genuinely understands your domain.

::: info Key terms in this Part
- **Tool subclass** — the canonical way to expose a business capability: `name` / `description` / `parameters` / `execute()` · [§5.1](/en/part-5/1-custom-tools), [G.2](/en/appendix/g-glossary#g-2-extension-points)
- **`requires_confirmation`** — Tool flag → triggers the `ask_confirmation` UI for side-effecting calls · [§5.1](/en/part-5/1-custom-tools), [§5.4](/en/part-5/4-permissions)
- **PermissionEngine** — Tier 0 hard guards + presets + custom rules; the rule-based defense layer · [§5.4](/en/part-5/4-permissions), [G.5](/en/appendix/g-glossary#g-5-security-vocabulary)
- **Skill** — `SKILL.md` + YAML front-matter, dynamically discovered from `skills/`; LLM-side instructions · [§5.2](/en/part-5/2-skills), [G.2](/en/appendix/g-glossary#g-2-extension-points)
- **MemoryManager** — SQLite-backed dual-scope (`project` + `user`) persistent + session memory · [§5.5](/en/part-5/5-memory), [G.1](/en/appendix/g-glossary#g-1-core-concepts)
:::

## Coverage

- [**5.1 Custom Tools**](./1-custom-tools) — the preferred way to expose business APIs to the LLM
- [**5.2 Skills**](./2-skills) — Markdown instructions for the LLM
- [**5.3 MCP Server Integration**](./3-mcp) — reuse the community / official tool ecosystem
- [**5.4 Permission Engine**](./4-permissions) — the first rule-based defense, layered with `confirm_tool`
- [**5.5 Memory System**](./5-memory) — cross-session persistence and compliance
- [**5.6 System Prompt Customization**](./6-system-prompt) — the 3 of 11 prompt blocks you actually own

## How to choose

| Need | Best extension point |
|------|---------------------|
| Agent needs to call your business API | 5.1 Tools |
| Agent must follow company conventions | 5.2 Skills + 5.6 AGENTAO.md |
| Integrate GitHub / DB / Slack / etc. | 5.3 MCP |
| Control "what is allowed, what isn't" | 5.4 Permissions |
| Remember user preferences, project facts | 5.5 Memory |
| Inject project-wide hard constraints | 5.6 AGENTAO.md |

## Three pragmatic rules

1. **Start with 5.4**: write permission rules before deployment — avoid "we'll add safety later" tech debt
2. **Don't duplicate 5.1 and 5.3**: the same capability should be either a Tool or an MCP server, never both — the LLM gets confused
3. **Skills: small and specific**: one skill, one job, concrete trigger description; many small skills beat one monster skill

→ [Start with 5.1 →](./1-custom-tools)
