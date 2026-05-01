# 1.4 Hello Agentao in 5 min

Goal: get a minimal Python embedding running. Pure SDK path, **no custom code**.

> Want a non-Python first taste? Jump to [3.1 ACP Quick Try](/en/part-3/1-acp-tour#quick-try-in-60-seconds) instead.

::: tip ⚡ Runnable end-to-end (≈ 3 minutes)
**Outcome** — agent thinks, runs `glob` + `run_shell_command`, prints the 3 largest files under cwd.
**Stack** — `pip install 'agentao>=0.4.0'` + 3 env vars + 6 lines of Python.
**Run** — `python hello.py` (after pasting the snippet from Step 3 below).
:::

## Step 1 · Install (1 minute)

```bash
pip install 'agentao>=0.4.0'
```

`pip install agentao` ships the embedding-only core. Add extras (`[web]`, `[cli]`, `[i18n]`, …) later as needed — see [1.5 Requirements](./5-requirements).

## Step 2 · Configure credentials (1 minute)

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"   # or any OpenAI-compatible endpoint
export OPENAI_MODEL="gpt-5.4"
```

All three are required. DeepSeek / Gemini / vLLM work the same way — just point `OPENAI_BASE_URL` and `OPENAI_MODEL` at them.

## Step 3 · Run (1 minute)

Save as `hello.py`:

```python
from pathlib import Path
from agentao import Agentao

agent = Agentao(working_directory=Path.cwd())
print(agent.chat("List the 3 largest files under the current directory."))
agent.close()
```

```bash
python hello.py
```

You'll see Agentao think, call `run_shell_command` / `glob`, and print a final answer like:

```text
The three largest files under the current directory are:
1. ./node_modules/.cache/...   (12 MB)
2. ./dist/bundle.js            (4.1 MB)
3. ./README.md                 (38 KB)
```

## What just happened

- `Agentao(...)` created **one stateful session** — history, tools, and memory are bound to this instance
- `chat()` ran the full LLM loop: think → call tool → observe → think → answer
- `working_directory` rooted file/shell tools at the current dir. **Always pass an explicit `Path` in production** so concurrent instances don't share `Path.cwd()`
- `close()` released MCP subprocesses and DB handles — wrap in `try/finally` in real code

## Add streaming output (5 more lines)

```python
from pathlib import Path
from agentao import Agentao
from agentao.transport import SdkTransport

def stream(ev):
    if ev.type.name == "LLM_TEXT":
        print(ev.data["chunk"], end="", flush=True)

agent = Agentao(
    working_directory=Path.cwd(),
    transport=SdkTransport(on_event=stream),
)
agent.chat("List the 3 largest files under the current directory.")
agent.close()
```

That's the whole pattern. Tool confirmations, custom tools, permissions, memory — every other feature extends from these two calls (`Agentao(...)` + `chat(...)`).

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `ImportError: cannot import name 'Agentao'` | Forgot `pip install agentao`, or imported from `agentao.agent` (not the public path) |
| `ValueError: OPENAI_API_KEY is not set` | All three of `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` are required |
| Agent says `Tool execution cancelled by user` | Default permissions denied a write — see [5.4](/en/part-5/4-permissions) |
| `chat()` never returns | Likely tool loop or no `ask_user` callback — see [Appendix F.2](/en/appendix/f-faq#f-2-runtime-behavior) |

Full FAQ: [Appendix F](/en/appendix/f-faq).

## Where to go next

| If you want to… | Read |
|----------------|------|
| Wire one of your business APIs as a tool | [5.1 Custom Tools](/en/part-5/1-custom-tools) |
| Embed in FastAPI / Flask with SSE streaming | [2.7 FastAPI / Flask Embedding](/en/part-2/7-fastapi-flask-embed) |
| Drive Agentao from Node / Go / Rust / IDE | [Part 3 · ACP](/en/part-3/) |
| Verify environment requirements first | [1.5 Requirements](./5-requirements) |
| Understand the core nouns (Agent / Tool / Skill / …) | [1.2 Core Concepts](./2-core-concepts) |
