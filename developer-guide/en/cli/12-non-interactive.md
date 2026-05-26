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

Since 0.4.x, `-p` is a thin shim over `agentao run --format text --prompt …`, so both surfaces share the unified exit-code table documented under [`agentao run`](#agentao-run-structured-automation-surface) below.

> **Migration note (0.3.x → 0.4.x):** under `-p`, `max_iterations` was previously exit `2`. It is now `4`; `2` means "invalid usage / spec validation failed".

## `agentao run` — structured automation surface

`agentao run` is the automation contract: a structured spec (stdin or `--spec`) merged with explicit CLI overrides, executed as one Agentao turn, returning a machine-readable result.

```bash
# Spec from stdin
agentao run --format json < task.yaml

# Spec from file with flag overrides
agentao run --spec .agentao/tasks/review.yaml --model gpt-5.5 --format json

# Inline prompt convenience (no spec file needed)
agentao run --prompt "Summarize the current directory" --format json
```

`--spec` and piped stdin are mutually exclusive — passing both exits `2`.

### M0 spec shape

```yaml
prompt: string                 # required (or pass via --prompt). Templated.
instructions: string           # appended to system prompt. Templated. Routes to
                               # Agentao(project_instructions=…) when non-empty
                               # after .strip(); skips AGENTAO.md disk read.
parameters:                    # typed slots for spec-level --param substitution
  - name: string               # ASCII identifier; not a Jinja-reserved name
    required: boolean          # default false; mutually exclusive with `default`
    default: string            # v1: string only. Must be in `choices` if set.
    choices: [string]          # optional enum
cwd: string                    # working directory for the run
model: string                  # overrides env-derived LLM model
base_url: string               # overrides env-derived base URL
permission_mode: read-only | workspace-write | full-access | plan
interaction_policy: reject     # M0 only accepts "reject"
permissions:
  allow:
    - tool: string             # glob — same syntax as ~/.agentao/permissions.json
      args: { ... }            # optional arg-pattern map
      domain:                  # optional URL/domain matcher
        url_arg: string
        allowlist: [string]
        blocklist: [string]
  deny:
    - tool: string
      args: { ... }
      domain: { ... }
max_iterations: int            # default 100
skills: [string]               # appended to discovered active skills
replay: boolean                # enable ReplayManager for this run
output:
  format: text | json
```

`extra="forbid"` — unknown spec fields fail with exit `2`. Secrets (`api_key`) are **never** accepted in the spec; they stay in the environment or in a host-injected client.

CLI flags only override spec values when explicitly provided (argparse defaults do not erase spec fields).

### Parameters & templating (`--param`)

`prompt` and `instructions` are Jinja2 templates rendered against typed parameter values. Other spec fields are not templated (templating `permissions` or `skills` invites footguns).

```yaml
# .agentao/runs/review-pr.yaml
parameters:
  - name: pr_number
    required: true
  - name: depth
    default: shallow
    choices: [shallow, deep]
instructions: |
  You are reviewing PR #{{ pr_number }}.
  Use {{ depth }} mode: shallow = surface issues; deep = trace data flow.
prompt: Review PR #{{ pr_number }}. Focus on correctness bugs.
permission_mode: workspace-write
max_iterations: 30
```

```bash
agentao run --spec .agentao/runs/review-pr.yaml \
            --param pr_number=142 --param depth=deep
```

`--param KEY=VALUE` is repeatable. Splits on the **first** `=` only — values may contain further `=` chars (e.g. `--param expr=a=b` → `expr` → `a=b`).

**Trigger rule.** The renderer is skipped entirely when both `spec.parameters` and `--param` are empty — literal <span v-pre>`{{ }}`</span> in a parameterless spec passes through to the LLM untouched. When the spec has no `parameters` but the CLI supplies `--param`, the run exits `2` so typos surface.

**Errors (all exit `2` / `invalid_spec`):**

- Missing required param, unknown param, choices violation.
- Malformed `--param` (`expected KEY=VALUE`, duplicate key, non-identifier key).
- Undefined template variable (StrictUndefined): "template uses undefined variable 'X' (declare it in spec.parameters)".
- Render-time errors propagated through Jinja (<span v-pre>`{{ 1/0 }}`</span> → `ZeroDivisionError`, `{% include %}` without loader, etc.) are caught and reported as "template error in spec.\<field\>".
- Sandbox-blocked operation: the renderer uses `jinja2.sandbox.SandboxedEnvironment`, so attribute-walking escapes (e.g. <span v-pre>`{{ ''.__class__.__mro__ }}`</span>) are refused — recipes from shared/untrusted sources cannot reach Python internals before `permission_mode` and tool permissions apply.

**Reserved parameter names.** Names that look like ASCII identifiers but are reserved by Jinja are rejected at spec-validation time: constants (`true`/`True`/`false`/`False`/`none`/`None`), keywords (`for`/`if`/`in`/`set`/`is`/`not`/`or`/…), and the runtime-injected names `self` / `parent`. The full list lives in `agentao/cli/run_models.py::_JINJA_RESERVED_NAMES`.

**Instructions precedence.** When `spec.instructions` (after rendering) is non-empty and contains at least one non-whitespace char, it routes to `Agentao(project_instructions=…)` and the agent skips the `AGENTAO.md` disk read. Whitespace-only output (e.g. a YAML block scalar that renders to `"\n"`) falls through to `AGENTAO.md`, so a template that resolves to nothing doesn't silently nuke your project instructions.

For the full design rationale — including v1 scope, deferred typing (`number` / `boolean`), and the goose-recipes comparison — see [docs/design/run-spec-parameters.md](https://github.com/jin-bo/agentao/blob/main/docs/design/run-spec-parameters.md).

### Output

`--format text` writes only the final assistant text to stdout. Diagnostics go to stderr. This is the closest analog to `agentao -p`.

`--format json` emits one envelope after the run completes:

```json
{
  "status": "ok",
  "session_id": "...",
  "turn_id": "...",
  "cwd": "/abs/path/to/project",
  "model": "gpt-5.5",
  "final_text": "...",
  "replay_path": ".agentao/replays/<id>.jsonl",
  "usage": {
    "prompt_tokens": 12000,
    "completion_tokens": 900,
    "total_tokens": 12900
  },
  "tool_calls": 7,
  "warnings": []
}
```

On failure, `final_text` is `null` and `error` carries `{ type, message, tool_name?, tool_call_id?, question?, matched_rule? }`. Error `type` is one of `permission_required`, `permission_denied`, `interaction_required`, `max_iterations`, `runtime_error`, `invalid_spec`, `interrupted`. Consumers should treat the envelope as forward-compatible (extra fields ignored).

### Exit codes (unified across `agentao run` and `agentao -p`)

| Code  | Meaning |
|-------|---------|
| `0`   | Completed normally |
| `1`   | Runtime error |
| `2`   | Invalid usage / spec validation failed / unknown spec field |
| `3`   | Permission or interaction required (no interactive approval available) |
| `4`   | Max tool iterations reached; answer may be incomplete |
| `130` | Interrupted (SIGINT / SIGTERM) |

For the full M0 design — merge rules, non-goals, post-MVP scope (`jsonl` event stream, `attachments`, `provider`, per-run `plugins`, session resume) — see [docs/implementation/NON_INTERACTIVE_RUN_PLAN.md](https://github.com/jin-bo/agentao/blob/main/docs/implementation/NON_INTERACTIVE_RUN_PLAN.md).

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

## `agentao doctor` — health snapshot

```bash
agentao doctor
agentao doctor --json
```

Aggregates every health signal the harness already produces — `.env` provider check (API-key *presence*, never the value), `settings.json`, permissions, MCP, replay config, ACP schema export, project + user memory stores, plugin diagnostics, and optional-dep probes — into one report. `--json` is the contract surface for CI / hosts; the human-readable default is for terminal use.

Output contract:

- `{"ok": bool, "sections": {...}, "findings": [...]}`
- `ok` is `false` iff at least one finding has `"level": "error"`; in that case the command exits **1**. Warnings (e.g. "API key not set" on a fresh clone) keep `ok=true` and exit **0** so they don't trip CI.
- Findings carry `level` (`info` / `warning` / `error`), `area` (the section the issue belongs to), a human message, and `source` (path or env-var label).
- Doctor is **read-only**: it never creates a memory DB or mutates files. Probing an absent `memory.db` reports `"absent"` without bootstrapping the file.
- Unknown flags fail with exit **2** (like `agentao run`), so a CI typo (`--jsno`) surfaces instead of exiting 0 against the wrong invocation.

## `agentao config validate` — explicit config check

```bash
agentao config validate
agentao config validate --json
```

A narrower companion to `doctor` that *only* validates user-editable config — `settings.json`, `.env` provider variables, `permissions.json`, `mcp.json`, the `replay` block in `settings.json`, and the memory-store probe. Plugin discovery is intentionally excluded (use `doctor` or `plugin list`).

Validation surfaces problems the runtime would otherwise swallow silently:

- malformed JSON or non-object shapes in any of the above files;
- `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` that fail to parse;
- per-server MCP entries that aren't objects, with non-string `env` / `headers` / `args` values that would crash `expand_env_vars` at runtime;
- user/project `mcp.json` name collisions (project entries are dropped at runtime — validate warns so you notice);
- replay block values that `ReplayConfig.from_mapping` silently coerces away (`max_instances: 0`, non-bool capture flags, unknown flag keys, …).

Runtime behavior is **unchanged** — `agentao config validate` is the explicit surface. The factory stays best-effort so an embedded host with a bad optional config isn't blocked from running.

Both commands honor `--plugin-dir DIR` (doctor uses it for plugin discovery) and require the `[cli]` extra to be installed; without it the standard missing-dep guard prints an install hint.

---

::: tip Authoritative reference
The top-level parser lives in [`agentao/cli/entrypoints.py:_build_parser`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/entrypoints.py). Non-interactive print mode is `run_print_mode` in the same file (a shim over [`agentao/cli/run.py:execute`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/run.py)). Spec models live in [`agentao/cli/run_models.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/run_models.py); the Jinja2 sandboxed renderer (`prompt` + `instructions`) lives in [`agentao/cli/run_template.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/run_template.py). Skill / plugin subcommands live in [`agentao/cli/subcommands.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/subcommands.py).
:::
