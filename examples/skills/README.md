# Skills Gallery

> 中文版: [README.zh.md](./README.zh.md)

Host-agnostic, drop-in **skills** — capability packs that an Agentao agent can activate at runtime. Different from the host-integration samples in the parent [`examples/`](../README.md) (which show *how to embed* Agentao); these show *what to give the embedded agent to do*.

A skill is just a directory with a `SKILL.md` (YAML frontmatter + instructions) and any helper files (`scripts/`, `reference/`, …). At startup `SkillManager` discovers skills from three locations, in priority order ([`agentao/skills/manager.py`](../../agentao/skills/manager.py)):

1. `~/.agentao/skills/` — global (every project sees them)
2. `<project>/.agentao/skills/` — per-project config
3. `<project>/skills/` — repo-root, highest priority

## Skills in this gallery

| Directory | What it does | Trigger | Needs |
|-----------|--------------|---------|-------|
| [`zootopia-ppt/`](./zootopia-ppt/) | Turn a presentation script into a PPT-ready image set in **anthropomorphic-animal 3D-animation** style. Three steps: outline → per-slide image prompts → batched image generation via `scripts/image_gen_ppt*.py`. | "make this deck in Zootopia / 3D-animation style" | `TENSORLAB_API_KEY` (default backend) — or `GEMINI_API_KEY` / `QWEN_API_KEY` / OpenRouter key for the alt backends |
| [`pro-ppt/`](./pro-ppt/) | Same pipeline, **editorial-premium business** style (light-grey base + gold accents + deep navy, McKinsey/Apple-Keynote vibe). **Reuses** `zootopia-ppt/scripts/image_gen_ppt.py`, so install both together. | "make this deck in a premium / consulting / editorial style" | Same as `zootopia-ppt` |
| [`ocr/`](./ocr/) | One-shot OCR on an image file via Qwen-VL (`scripts/ocr.py`). | "OCR this screenshot / extract text from this image" | `QWEN_API_KEY` + `QWEN_BASE_URL` in `.env` |

## Install

Pick **one** of the discovery locations above, then copy or symlink the skill directory in.

```bash
# Easiest: install globally (works for any project you launch agentao from)
cp -R examples/skills/ocr ~/.agentao/skills/

# Or scope to one project
cp -R examples/skills/zootopia-ppt /path/to/your/project/.agentao/skills/
cp -R examples/skills/pro-ppt      /path/to/your/project/.agentao/skills/
```

Restart Agentao (or run `/skills` to list) — the skill should appear under "Available skills". Activate via the `activate_skill` tool or by asking the agent to use it. See [docs/features/skills.md](../../docs/features/skills.md) for the activation lifecycle.

## Note on image-gen credits

`zootopia-ppt` and `pro-ppt` call paid image-generation APIs (TensorsLab / Gemini / DashScope-Wan / OpenRouter). Each script reads the key from `.env` or `--api-key`; nothing is hardcoded. Run a small outline first to gauge cost before generating a 30-page deck.

## Install (with deps)

Each gallery skill ships a `requirements.txt` listing its Python deps:

```bash
# Inside your project venv
pip install -r examples/skills/zootopia-ppt/requirements.txt
pip install -r examples/skills/ocr/requirements.txt
# pro-ppt's requirements.txt re-exports zootopia-ppt's via `-r ../zootopia-ppt/requirements.txt`
```

The image-gen backends in `zootopia-ppt` (`google-genai`, `dashscope`, etc.) are alternatives — comment out the lines you don't use.

## Embedded harness perspective — see it live

Skills are part of the **embedded-harness** story, not separate from it. A host application that embeds Agentao usually wants to ship its own domain skills alongside its own tools and `AGENTAO.md`. The gallery here is the host-agnostic half; the **co-located** half is already demonstrated in three host blueprints in this repo:

| Host blueprint | Co-located skill(s) | Why it can't live in the gallery |
|----------------|---------------------|----------------------------------|
| [`data-workbench/`](../data-workbench/.agentao/skills/) | `duckdb-analyst`, `matplotlib-charts` | Tightly coupled to that blueprint's `[CHART] <path>` parsing contract and parquet workspace layout |
| [`ticket-automation/`](../ticket-automation/.agentao/skills/) | `support-triage` | References the blueprint's escalation matrix + `ConfidenceGatedEngine` thresholds; meaningless without them |
| [`batch-scheduler/`](../batch-scheduler/.agentao/skills/) | `daily-digest` | Bound to the blueprint's `RESULT: {...}` stdout contract that the cron orchestrator parses |

Copy from this gallery if your skill is reusable; co-locate inside the host (under `<host>/.agentao/skills/`) if it only makes sense alongside that host's tools or output contract.

## Contribute

Got a skill that earns its keep in real work? PRs welcome. A skill belongs in this gallery if:

- It's **host-agnostic** — a SaaS bot, a Jupyter kernel, and a CLI session could all benefit
- The `SKILL.md` has a sharp **trigger description** (when should the agent reach for it?)
- Helper scripts read credentials from env / `.env` / CLI args — never hardcoded
- It's small enough to grok in one read

Co-located, host-specific skills (e.g. a Slack thread-summarizer that only makes sense inside `examples/slack-bot/`) belong inside their host example, not here.
