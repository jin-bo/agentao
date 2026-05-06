# 12. Non-Interactive Entry Points

`agentao` is not only a REPL. The top-level command also supports setup, one-shot prompts, session resume, ACP server mode, and skill / plugin management.

## `agentao init` — write `.env`

For first use in a project, run the setup wizard:

```bash
agentao init
```

It asks for provider, API key, base URL, and model, then writes:

```bash
LLM_PROVIDER=OPENAI
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4
```

If `.env` already exists, it asks before overwriting.

## `agentao -p` / `--print` — one-shot run

Print mode sends one prompt, prints the answer, and exits.

```bash
agentao -p "Summarize README"
cat issue.md | agentao --print "Create a fix plan from the content below"
```

Exit codes:

| Code | Meaning |
|------|---------|
| `0` | Completed normally |
| `1` | Runtime error |
| `2` | Max tool iterations reached; answer may be incomplete |

## `--resume` — resume at launch

```bash
agentao --resume
agentao --resume a1b2c3
```

Without an id, this resumes the latest session. With an id, it matches by prefix. Inside the REPL, the equivalent is `/sessions resume <id>`.

## `--acp --stdio` — run as an ACP server

```bash
agentao --acp --stdio
```

This starts Agentao as an ACP stdio JSON-RPC server for IDEs, host processes, or other agents. `--stdio` is currently meaningful only with `--acp`.

## `--plugin-dir` — load plugins temporarily

```bash
agentao --plugin-dir ./my-plugin
agentao plugin --plugin-dir ./my-plugin list
```

`--plugin-dir` is repeatable. Use it while developing a plugin locally without packaging or installing it.

## `agentao skill ...`

The top-level `skill` subcommands manage skills installed on disk:

```bash
agentao skill install owner/repo[:path][@ref]
agentao skill list
agentao skill list --installed
agentao skill remove <name>
agentao skill update <name>
agentao skill update --all
```

REPL `/skills` controls what the current session can see and activate. Top-level `agentao skill ...` controls what is installed on disk.

## `agentao plugin list`

```bash
agentao plugin list
agentao plugin list --json
```

This loads plugins and emits diagnostics, useful for CI or pre-release checks. REPL `/plugins` is the interactive version of the same surface.

---

::: tip Authoritative reference
The top-level parser lives in [`agentao/cli/entrypoints.py:_build_parser`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/entrypoints.py). Non-interactive print mode is `run_print_mode` in the same file. Skill / plugin subcommands live in [`agentao/cli/subcommands.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/subcommands.py).
:::
