# Metacognitive Boundary as a Host-Injectable Protocol

**Status:** Design record. Decision captured 2026-04-29. Implementation deferred.
**Audience:** agentao maintainers and host integrators considering self-vs-project
boundary semantics.
**Companion:** `metacognitive-boundary.zh.md`.

## Problem

LLM-driven agents systematically conflate two operations:

- **Configuring themselves** (skills, memory, system prompt, tool permissions, MCP).
- **Modifying the host project** (source code, tests, configs, deps, scripts).

Concrete failure modes:

- User says "改下你的回答风格" / "tweak how you respond" → agent edits a project
  prompt template instead of updating its own configuration.
- User pastes a GitHub URL and asks "install this skill" → agent commits the URL into
  project source as a TODO instead of installing into the agent's skills directory.
- User asks an agent in a harness's own source tree (e.g. agentao itself) to "调一下
  你的 tool 执行循环" → "你" is genuinely ambiguous between (a) the agent harness
  Claude Code is running on, and (b) the agentao module being developed.

A "metacognitive boundary" addresses these by making explicit which operations
target the **agent layer** versus the **project layer** versus other categories.

## Empirical basis

Survey of 13 production agents (Cursor, Copilot Chat, Aider, Continue.dev, Devin,
Windsurf Cascade, Claude Code, openai/codex, sst/opencode, charmbracelet/crush,
block/goose, princeton-nlp/SWE-agent, All-Hands-AI/OpenHands).

| Pattern | Observed in | Notes |
|---|---|---|
| Identity opens with "You are {AGENT_NAME}, …" | All except SWE-agent and Aider | SWE-agent: 1-sentence task framing; Aider: "Act as" (role, not being). |
| Pronoun "you" = agent, "USER" = user | All consistent; explicit only in Cursor & Cascade | Cursor's verbatim: "Refer to the USER in the second person and yourself in the first person." |
| Self-config-vs-project boundary stated in prose | **None** | All structural or implicit. |
| Self-config-vs-project boundary by structure | Claude Code (filesystem-anchored: `~/.claude/`, named CLAUDE.md, hooks-as-user, Skills as RPC); Continue.dev (chat/plan/agent mode taxonomy); Goose (main/subagent/tiny tiers); Devin (web-app vs sandbox split, product-enforced); OpenHands (`<UNTRUSTED_CONTENT>` XML envelope) | The dominant pattern. |
| Always-on pre-execution self-check | None | All defer to per-tool gating, mode-based authority, or implicit trust. |
| Hard-coded platform paths inside prompt | None | Paths injected by harness at assembly time (Claude Code: `${WORKING_DIR}`, `${AGENT_HOME}`). |
| Acknowledges "harness develops itself" recursion | **None** — including Cognition who openly builds Devin with Devin, and Claude Code which is itself an editable npm package | Universal blind spot. |

**Headline finding:** successful agents do not declare boundaries in prose; they
**bind each agent-layer concern to an addressable structural handle** — a path, a
mode, a tier, an envelope. Declarative "you should not …" lists are not how the
working systems work.

Full survey reports archived in user-local plan files; key quotes inlined in
references at the end of this document.

## Decision

agentao **does not** ship a fixed metacognitive-boundary block in its system prompt.
agentao ships:

1. A **schema** — invariants every host's boundary must satisfy.
2. A **default content set** — what gets injected if a host does nothing.
3. A **host-override protocol** — the contract by which embedders supply their own
   identity, paths, vocabulary, and disambiguation defaults.

Rationale: agentao's defining positioning is **embedded harness**. The 13 surveyed
agents are single-tenant CLIs that own their entire UX, so they can hard-code their
boundary. agentao runs inside heterogeneous host applications (CLI, IDE plugin,
internal data tool, customer-facing SaaS) whose UX semantics differ — what counts as
a "project object" or where the "agent home" lives is host-specific by definition.
Hard-coding would force every host into a CLI-shaped mental model. Capability
injection is the discipline agentao already follows for tools, skills, memory
backends, and MCP; metacognitive boundary is the same kind of concern.

## Schema (agentao-defined invariants)

These are stable across hosts — the host cannot opt out of them, only fill them in.

1. **Object taxonomy.** Every operation classifies into exactly one of:
   - **Project object** — files in the host-declared working area.
   - **Agent object** — agent-layer state (skills, memory, tool config, system
     prompt) at the host-declared agent home.
   - **External object** — resources reached via WebFetch / API / MCP.
   - **User-intent object** — text output that does not require persistence.
2. **Structure over declaration.** Boundary lives in *paths and types*, not in
   prohibitions. The injected prompt should *describe where things are*, not lecture
   on what not to do.
3. **Ambiguity → trigger, not default.** When a referent is genuinely ambiguous (the
   "you" pronoun, a path that straddles project and agent home, a verb like
   *install* with no specified target), the agent must ask, not silently default.
4. **Pronoun rule.** "You" refers to the agent; "USER" refers to the human — except
   when the host explicitly designates an in-project identifier with the same
   spelling.
5. **Harness-self-development opt-in.** When the host declares the working area is
   the source of an agent framework (including agentao itself, or another agent
   project), priority reverses for that area: in-project files matching agent-layer
   names (`skills/`, `memory/`, `agent.py`, etc.) are **project objects**, not agent
   objects. Modifying agent self-state still goes to the agent home.

## Content (host-injected)

The host supplies, at embed time, the values that fill the schema:

| Slot | Host responsibility | Example |
|---|---|---|
| Identity sentence | Who the agent is in this product | `"I am the Foo Assistant, embedded in Foo Workbench."` |
| Working-area description | What "project" means here | `"the active workspace at /workspaces/{user}/{project}"` |
| Agent-home location | Where agent-layer state lives | `"~/.foo/agent/"`, or remote, or none |
| Project vocabulary | Domain-specific names for project objects | `"datasets"`, `"notebooks"`, `"flows"` |
| Disambiguation default | How "you" resolves in this host's UI | usually agent; some products may differ |
| Self-development flag | Is the working area an agent framework's source? | bool; flips schema invariant #5 |

## Default content set

A CLI-like default ships with agentao for hosts that do not override. Approximate
shape (~25 lines, generated from defaults; not a literal prompt fragment to paste
verbatim into a host's system prompt):

> I am agentao, running in `${WORKING_DIR}`. I act on the USER's behalf — I do not
> explain how to do things, I do them.
>
> "You" refers to me (agentao); "USER" refers to the human, unless project code uses
> the same identifier.
>
> Operation classes by location:
> - **Project objects** — files under `${WORKING_DIR}`.
> - **Agent objects** — files under `${AGENT_HOME}` (default `~/.agentao/`):
>   skills, memory, tool config, system prompt.
> - **External objects** — accessed via WebFetch / API / MCP.
> - **User-intent objects** — text I produce without persisting.
>
> Override clause: when `${WORKING_DIR}` is itself the source of an agent framework,
> in-project `skills/`, `memory/`, `agent.py` are project objects. Agent-self
> modifications still target `${AGENT_HOME}`.
>
> Ask, do not default, when:
> - "you" is ambiguous in the current working area;
> - an operation straddles `${WORKING_DIR}` and `${AGENT_HOME}`;
> - install/configure target cannot be resolved by the host.

The default is explicitly **a default**, not a hard-coded contract.

## Differentiation

All 13 surveyed agents bake their boundary into a single proprietary system prompt.
This is structurally impossible for an embedded harness without forcing every host
into the same UX. Treating the boundary as an injection protocol — schema fixed,
content host-supplied — is novel among the surveyed prior art.

The harness-self-development opt-in (schema #5) is also unrepresented in the
surveyed prompts, despite being directly relevant to several of them (Cognition's
"build Devin with Devin", Claude Code as an editable npm package, Aider and
Continue.dev as open-source self-developable harnesses).

## What this document is not

- Not an implementation plan. The protocol's interface signature, the integration
  point in `agent.py::_build_system_prompt`, the migration of any boundary content
  out of `AGENTAO.md`, and the per-host default tuning are deferred.
- Not a finished prompt. The default-content sketch above is illustrative, not
  ready-to-ship copy.
- Not a recommendation that hosts should override every slot. Most hosts will adopt
  the default; the protocol exists for the ones whose UX requires deviation.

## References

- **Original proposal** (the ~80-line metacognitive-boundary draft that triggered
  this design review). Stored in conversation transcript.
- **Cursor** leaked prompt: <https://github.com/jujumilk3/leaked-system-prompts/blob/main/cursor-ide-sonnet_20241224.md>
- **GitHub Copilot Chat** leaked prompt (2024): <https://github.com/jujumilk3/leaked-system-prompts/blob/main/github-copilot-chat_20240930.md>
- **Aider** prompt sources: <https://github.com/Aider-AI/aider/blob/main/aider/coders/base_prompts.py>, <https://github.com/Aider-AI/aider/blob/main/aider/coders/editblock_prompts.py>
- **Continue.dev** mode prompts: <https://github.com/continuedev/continue/blob/main/core/llm/defaultSystemMessages.ts>
- **Devin / Cognition** "build Devin with Devin" post: <https://cognition.ai/blog/how-cognition-uses-devin-to-build-devin>
- **Windsurf Cascade** leaked prompt: <https://github.com/jujumilk3/leaked-system-prompts/blob/main/codeium-windsurf-cascade_20241206.md>
- **Claude Code** leaked prompt (third-party transcript): <https://github.com/asgeirtj/system_prompts_leaks/blob/main/Anthropic/claude-code.md>
- **openai/codex** prompt: `codex-rs/core/gpt_5_1_prompt.md` in <https://github.com/openai/codex>
- **sst/opencode** prompt routing: `packages/opencode/src/session/system.ts` in <https://github.com/sst/opencode>
- **charmbracelet/crush** prompt template: `internal/agent/templates/coder.md.tpl` in <https://github.com/charmbracelet/crush>
- **block/goose** prompts: `system.md`, `subagent_system.md`, `tiny_model_system.md` in <https://github.com/block/goose>
- **princeton-nlp/SWE-agent** prompt: `config/default.yaml` in <https://github.com/princeton-nlp/SWE-agent>
- **All-Hands-AI/OpenHands** prompt: `openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2` in <https://github.com/All-Hands-AI/OpenHands>
