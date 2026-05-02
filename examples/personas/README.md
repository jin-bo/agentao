# AGENTAO.md Persona Gallery

> 中文版: [README.zh.md](./README.zh.md)

A collection of real-world `AGENTAO.md` files — the per-project instructions that shape how an Agentao agent talks, decides, and remembers. Drop one into your project root as `AGENTAO.md` and Agentao will pick it up on the next turn (see `agentao/agent.py::_build_system_prompt`).

> Looking for runnable **host-integration** examples (FastAPI, Slack, Jupyter, …)? See the parent [`examples/`](../README.md). This gallery is purely about prompt configuration — no code, no `pyproject.toml`, just instructions.

## Personas

| Directory | Persona | Vibe |
|-----------|---------|------|
| [`daily-driver/`](./daily-driver/AGENTAO.md) | The author's day-to-day research / coding assistant | Evidence-first, privacy-conscious, workspace-organized |
| [`kawaii-buddy/`](./kawaii-buddy/AGENTAO.md) | Emotional-value pocket helper | Cute, bilingual chatter, always asks how you feel |

## How to use

1. Pick a persona that's close to what you want.
2. Copy its `AGENTAO.md` to **your project root** (the directory from which you launch `agentao`).
3. Edit freely — these are starting points, not contracts.

Agentao re-composes `AGENTAO.md` into the system prompt on every turn, so changes apply on the next message; no restart needed.

## Contribute

Got a persona that earned its keep in real work? PRs welcome. Aim for:

- A short `AGENTAO.md` (one screenful is plenty)
- A descriptive directory name (`code-reviewer/`, `pair-programmer/`, …)
- One row in the table above

We're not looking for completeness — we want **flavor**.
