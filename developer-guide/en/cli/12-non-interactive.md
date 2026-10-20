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

Since 0.4.x, `-p` is a thin shim over `agentao run --format text --prompt …`, so both surfaces share the unified exit-code table documented under [`agentao run`](#agentao-run-—-structured-automation-surface) below.

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
interaction_policy: reject | deny   # default reject; see below
max_denials: int               # deny only; default 5
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
thinking: string               # thinking depth (0.5.15): minimal | low | medium
                               # | high, another level the model takes, or "off"
replay: boolean                # enable ReplayManager for this run
output:
  format: text | json
```

`extra="forbid"` — unknown spec fields fail with exit `2`. Secrets (`api_key`) are **never** accepted in the spec; they stay in the environment or in a host-injected client.

CLI flags only override spec values when explicitly provided (argparse defaults do not erase spec fields).

#### Thinking depth (`thinking`, `--thinking`)

`thinking` (or `--thinking LEVEL`, which wins over the spec) sets the thinking depth, the same setting as the interactive `/thinking`. Agentao writes the level into the request field the wire reads:

| Wire | Request field | Levels |
|---|---|---|
| `openai-completions` | `reasoning_effort` | `minimal`, `low`, `medium`, `high`. Another single word is sent as written, and the provider checks it |
| `anthropic-messages` | `output_config.effort` | `low`, `medium`, `high`, `xhigh`, `max`. Another word exits `2` before any request |
| `openai-responses` | `reasoning.effort` | As on `openai-completions` |

`off` removes an explicit level, for example one that `LLM_EXTRA_BODY` set, so the provider's default behaviour applies. It does not promise to turn the model's reasoning off: a model that reasons by default keeps doing so. Write it quoted, `thinking: "off"`. YAML reads a bare `off`, `false` or `no` as the boolean `false`, and a spec takes that as `off` too. `true` and numbers are refused (exit `2`). A quoted `"false"` or `"no"` stays a string, so it is sent as a level. More than one word exits `2`. A host LLM client with no `extra_body` dict to write into exits `1` (`runtime_error`). The result envelope's `thinking` gives the level in the field the wire reads (a `reasoning_effort` left over on another wire is not it), from `thinking` or from `LLM_EXTRA_BODY`. It does not say the request is free of another wire's field: one that the host set in `LLM_EXTRA_BODY` is sent as it is. It is left out when no level was set. There is no auto-recovery: a model that rejects the level fails every request. `LLM_EXTRA_BODY` stays the way to send any other provider field.

#### Interaction policy (`interaction_policy`, `--interaction-policy`)

A run has nobody to approve a tool call that needs approval (one the rules answer with *ask*, such as a shell command outside the read-only allowlist). The policy decides what happens to that call:

| Policy | A call that needs approval | `ask_user` |
|---|---|---|
| `reject` (default) | Ends the run: `permission_required`, exit `3`. What the run did before it is kept on disk, but the turn never reaches its closing steps. | `interaction_required`, exit `3` |
| `deny` (0.5.16) | Not run. The model gets a tool result that says approval is unavailable in non-interactive mode, tells it not to retry the call, and allows an alternative the current permission rules permit. The turn goes on. | `interaction_required`, exit `3`, as under `reject` |

A confirmation that the MCP Skills gate asks (consent to a server skill's content, or to a call made while one is loaded) is declined with the plain "not approved" result instead, which offers no alternative. It still counts toward `max_denials`. `deny` changes only the *ask* case. A call that a rule or the `read-only` mode denies still ends the run with `permission_denied`, under both policies.

`max_denials` (or `--max-denials N`, default `5`) bounds `deny`. It counts every declined call of the run, a foreground sub-agent's calls too. (A run has no background sub-agents; see *Output* below.) The first `N` are declined back to the model. Call `N + 1` ends the run as `reject` does (`permission_required`, exit `3`, the message names `max_denials`), and the other calls of its batch are cancelled. `0` makes `deny` act as `reject`. Setting `max_denials` without `interaction_policy: deny` exits `2`, and so does a negative value.

The result lists each declined call in `denials` (`tool_name`, `tool_call_id`, `matched_rule` when an engine rule asked), on success and on failure. A sub-agent's call is listed as `[<agent>] <tool>`, without `tool_call_id` or `matched_rule`. In `--format text` each one is a line on stderr. **Exit `0` under `deny` means the model gave a final answer. It does not mean the task is complete**: a step the model wanted may be in `denials`, and nothing is rolled back. Check `denials` before you treat the result as done.

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
    "total_tokens": 12900,
    "cache_read_tokens": 9500,
    "cache_creation_tokens": 0,
    "reasoning_tokens": 600
  },
  "tool_calls": 7,
  "thinking": "high",
  "denials": [],
  "warnings": []
}
```

`usage` is what the run's LLM requests reported, summed: the agent's own calls, its compaction summaries, and — since 0.5.1 — every **foreground sub-agent** it delegated to (before that a sub-agent's requests were counted nowhere, so a delegating run under-reported). **`usage` is a snapshot, read when the envelope is written**, and nothing revises it afterwards. Since 0.5.6 `agentao run` offers no background sub-agents at all (its factory passes `bg_store=None`, so `run_in_background` is not in the schema): the process exits when the turn ends, and a background worker is a daemon thread that would have been cut off with it. Every sub-agent a run delegates to is therefore a foreground one, finished and counted before the envelope is written. A host that needs a per-sub-agent figure reads `SubagentLifecycleEvent.usage` instead. `prompt_tokens` includes cached input, which providers bill at a different rate; `cache_read_tokens` and `cache_creation_tokens` (0.5.1) say how much of it that was — they are parts **of** `prompt_tokens`, not additions to it, and are not in `total_tokens` a second time. `reasoning_tokens` (0.5.15) is likewise a part **of** `completion_tokens`: the output the model spent thinking, which a thinking-mode provider bills as output but does not show. Chat Completions (`completion_tokens_details.reasoning_tokens`) and `openai-responses` (`output_tokens_details.reasoning_tokens`) state it. On `anthropic-messages` it is 0, because `output_tokens` includes thinking with no separate count. Agentao reports these quantities and applies no prices. A request that failed mid-stream is counted for what the server had **reported** by then (0.5.2): on the `anthropic-messages` wire that is the whole input, stated in the stream's first event; on Chat Completions usage arrives only in the last chunk, so a stream that dies before it adds nothing. A failed attempt and the retry after it are two requests and both count. Reported is not billed — treat `usage` as usage, not as an invoice.

On failure, `final_text` is `null` and `error` carries `{ type, message, reason?, tool_name?, tool_call_id?, question?, matched_rule? }`. Error `type` is one of `permission_required`, `permission_denied`, `interaction_required`, `max_iterations`, `runtime_error`, `invalid_spec`, `interrupted`, `empty_response`, `length_truncated`, `doom_loop`, `hook_stop`. Consumers should treat the envelope as forward-compatible (extra fields ignored).

These types mean the turn completed with no answer to hand back, and `reason` carries the runtime's exact classification for all of them — branch on `reason`, not on `message`:

| `type` | `reason` | Meaning |
|---|---|---|
| `empty_response` | `no_output` | The model emitted nothing. |
| `empty_response` | `reasoning_only` | The model emitted reasoning but no answer text. |
| `length_truncated` | `length_truncated` | Output cut off at the token limit — a tool call whose arguments were truncated (the harness then halts the turn), or a final answer cut off mid-sentence. |
| `doom_loop` | `doom_loop` | The model repeated the same call until the detector halted the turn. |
| `hook_stop` | `hook_stop` | A plugin hook ended the turn. `PreToolUse` and `PostToolUse` / `PostToolUseFailure` reach this with `continue: false`; `UserPromptSubmit` reaches it by blocking or stopping the prompt, before the model is called. The message carries the hook's reason. |
| `runtime_error` | `llm_error` | The LLM API call failed (provider 5xx / rate limit / auth) after retries; the message carries the provider's actual error. |

The split is deliberate: `empty_response` means the model said nothing, `length_truncated` / `doom_loop` mean the harness halted a non-converging turn (often worth retrying with a smaller task, which "the model said nothing" usually is not), `hook_stop` means your own configuration stopped it deliberately, and `llm_error` means the provider call failed. All exit `1`, and the set is closed — every turn that ends without a complete answer classifies, so a pipeline never reads one as success at exit `0`.

Note that a turn can end this way *after* running tools: the model may do the work and then fail to summarize it. `tool_calls` on the same envelope reports how many ran, and their side effects have already landed — an `empty_response` failure does **not** mean the workspace is untouched.

### Exit codes (unified across `agentao run` and `agentao -p`)

| Code  | Meaning |
|-------|---------|
| `0`   | Completed normally. Under `interaction_policy: deny`, check `denials`: exit `0` does not mean every call ran |
| `1`   | Runtime error, or a turn that produced no answer (`runtime_error`, `empty_response`, `length_truncated`, `doom_loop`, `hook_stop`) |
| `2`   | Invalid usage / spec validation failed / unknown spec field |
| `3`   | Permission or interaction required (no interactive approval available; under `deny`, past `max_denials`) |
| `4`   | Max tool iterations reached; answer may be incomplete |
| `130` | Interrupted (SIGINT / SIGTERM) |

For the full M0 design — merge rules, non-goals, post-MVP scope (`jsonl` event stream, `attachments`, `provider`, per-run `plugins`, session resume) — see [docs/history/implementation/non-interactive-run-plan.md](https://github.com/jin-bo/agentao/blob/main/docs/history/implementation/non-interactive-run-plan.md).

## `--resume` — resume at launch

```bash
agentao --resume
agentao --resume a1b2c3
```

Without an id, this resumes the latest session. With an id, it matches by prefix. Inside the REPL, the equivalent is `/sessions resume <id>`.

Combined with `--acp`, `--resume` resumes on the **first `session/new`** instead — see [`--acp --stdio`](#acp-stdio-—-run-as-an-acp-server) below.

## `--acp --stdio` — run as an ACP server

```bash
agentao --acp --stdio
agentao --acp --resume               # resume the latest session on first session/new
agentao --acp --resume <SESSION_ID>  # resume a specific session on first session/new
```

This starts Agentao as an ACP stdio JSON-RPC server for IDEs, host processes, or other agents. `--stdio` is currently meaningful only with `--acp`. Adding `--resume` arms a one-shot directive that the first `session/new` consumes to hydrate + replay a persisted session (any miss degrades to a fresh session). See [3.2 Agentao as an ACP Server → Resume a session on startup](/en/part-3/2-agentao-as-server#resume-a-session-on-startup).

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
