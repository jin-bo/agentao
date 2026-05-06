# 3. Permissions & Modes

The single most important thing the CLI does for safety is **ask before risky tool calls**. This page covers the three permission modes, the confirmation UI, and macOS sandboxing.

## The four modes

Agentao has four permission modes. Three are user-selectable; one (`plan`) is set by `/plan` and lives in [4. Plan Mode](./4-plan-mode).

| Mode | Writes & shell | Web | Confirmation UI |
|------|----------------|-----|-----------------|
| `read-only` | **blocked** | blocked | n/a — refused outright |
| `workspace-write` | allowed for safe ops | **asks per domain** | shown for risky ops |
| `full-access` | allowed | allowed | **never shown** |
| `plan` | blocked | blocked | (research-only; see ch. 4) |

`workspace-write` is the default and the one you should run in 95% of the time.

## `/mode` — show or switch

```text
> /mode
Permission mode: workspace-write

> /mode read-only
✓ Permission mode: read-only  (write & shell tools are blocked)

> /mode full-access
✓ Permission mode: full-access  (all tools allowed without prompting)
```

The change is written to `.agentao/settings.json` under `mode`; the next launch from the same project uses it. Permission rules themselves are read only from user-level `~/.agentao/permissions.json` — see [10. Configuration Reference](./10-config-reference).

::: warning Plan-mode interaction
You can't change `/mode` while plan mode is active. Exit plan first with `/plan implement` or `/plan clear`. `/mode` will refuse and tell you so.
:::

## What each mode actually blocks

### `read-only`

Refused without asking:

- `write_file`, `replace` — any file mutation
- `run_shell_command` — every command, including read-only ones
- `web_fetch`, `web_search` — any network call

Allowed without asking:

- `read_file`, `list_directory`, `glob`, `search_file_content` — passive reads

Use when: you want the agent to investigate and explain, but **never** mutate. Code reviews, audits, "what does this codebase do" walkthroughs.

### `workspace-write` (default)

Allowed without asking:

- All read tools
- `write_file` / `replace` **inside the working directory**
- `run_shell_command` for the safe-read allowlist (`ls`, `cat`, `grep`, `git status`, `git diff`, `git log`, `pwd`, `which`, `env`, etc.)

Asks before running:

- `run_shell_command` for anything outside the safe-read allowlist
- `web_fetch` for domains not on the allow/deny lists (default lists pre-allow trusted docs sites; pre-deny SSRF targets like `localhost`, `127.0.0.1`, `169.254.169.254`)
- `web_search`
- `write_file` overwriting outside the working directory

Refused outright:

- `web_fetch` to blocklist domains (SSRF defenses) — these can't be allowed via the prompt; you have to edit `~/.agentao/permissions.json` to override

Use when: normal development. The default for a reason.

### `full-access`

Everything allowed without prompting. Skips the confirmation UI completely.

::: danger Don't leave full-access on
Switching to full-access is a **session-wide** decision. The next turn could `rm -rf`, exfiltrate data, or call paid APIs at scale. Use it only when:
- You're in a throwaway VM or sandbox
- You're scripting an unattended run with a known prompt
- You've already reviewed the plan and want to skip 50 confirmation prompts

When done, `/mode workspace-write` to step back down. Restart of the CLI also resets.
:::

## The confirmation UI

When a tool needs confirmation, the agent pauses, the spinner stops, and you see:

```text
⚠️  Tool Confirmation Required
Tool: run_shell_command
Arguments:
  • command: rm -rf node_modules

Choose an option:
 1. Yes
 2. Yes, allow all tools during this session
 3. No

Press 1, 2, or 3 (single key, no Enter needed) · Esc to cancel
```

Single-key input. No Enter needed. Behavior:

| Key | Effect |
|-----|--------|
| `1` | Run **this** tool call. Next risky call asks again. |
| `2` | Switch session to `full-access`. **Every subsequent tool call runs without asking** until you `/mode workspace-write` or restart. |
| `3` | Cancel this tool call. The agent gets `Tool execution cancelled by user` as the result and continues — usually it adapts. |
| `Esc` / `Ctrl+C` | Same as `3`. |
| Other keys | Silently ignored. |

::: tip Cancellation is a real choice
Picking `3` doesn't break the conversation. The agent sees the cancellation, often pivots ("OK, let me try a different approach"), and you keep going. Use it generously.
:::

## `/sandbox` — macOS sandbox-exec (macOS only)

`/sandbox` adds a second layer **under** `run_shell_command`: even if the agent gets approval to run a shell command, the macOS `sandbox-exec` profile constrains what that command can actually touch (filesystem, network).

Linux and Windows don't have this; the command is a no-op there.

```text
> /sandbox
Sandbox: enabled
  Default profile: workspace-only
  Workspace root:  /Users/you/projects/my-app
  Available:       workspace-only, network-allowed, strict-readonly
```

Subcommands:

| Command | Effect |
|---------|--------|
| `/sandbox` or `/sandbox status` | Show state, default profile, workspace root, available profiles |
| `/sandbox on` | Enable for this session |
| `/sandbox off` | Disable for this session |
| `/sandbox profile <name>` | Switch profile (session only) |
| `/sandbox profiles` | List available profile names |

::: danger Fail-closed on broken config
If `/sandbox` is enabled but the profile is malformed (typo, missing file), **all shell commands fail** until you fix it or `/sandbox off`. The status output flags this in red as `enabled but BROKEN`. This is intentional — silently falling back to "no sandbox" would be a security regression.
:::

## Where rules come from

`/status` shows the active mode plus a `Loaded sources:` line listing rule sources. Project-level `.agentao/permissions.json` is ignored; file rules are user-only. Precedence:

1. `~/.agentao/permissions.json` (user-global custom rules)
2. The current mode's built-in preset (safe-shell allowlists, SSRF denylists, etc.)
3. CLI `/mode` change (persisted to `.agentao/settings.json`, selects the current mode)

Edit `~/.agentao/permissions.json` directly to persist rule changes. The CLI re-reads it on each launch.

## Advanced: `/permission`

There's also a `/permission` command for inspecting the currently effective rules without leaving the CLI. It's a power-user surface and goes beyond what most CLI users need — see [Part 5.4 · Permission Engine](/en/part-5/4-permissions) for the full reference.

## Pitfalls

- **Confirmation UI hangs** — the spinner stops on confirmation. If the prompt seems frozen, you might be looking at the spinner-stopped state and missed the menu. Scroll up.
- **`workspace-write` and writes outside cwd** — overwriting `~/Documents/...` from a project at `/repo/foo` triggers a confirmation, even though you're in workspace-write. That's by design; the "workspace" is the launch dir.
- **Picking `2` in confirmation flips the whole session** — there's no undo other than `/mode workspace-write` or restart. Treat option 2 as "I'm done supervising this session".
- **Sandbox profile change is session-only** — to make the change stick across restarts, edit `default_profile` in `.agentao/sandbox.json` or `~/.agentao/sandbox.json`.

## Where to go next

| Want to… | Read |
|----------|------|
| Plan first, then commit to actions | [4. Plan Mode](./4-plan-mode) |
| Customize the rule set permanently | [10. Configuration Reference](./10-config-reference) → `permissions.json` |
| Understand the rule engine deeply | [Part 5.4 · Permission Engine](/en/part-5/4-permissions) |
| See the threat model behind these defaults | [Part 6.1 · Defense Model](/en/part-6/1-defense-model) |

---

::: info Where this fits
The CLI's confirmation UI is one implementation of the harness's `confirmation_callback` hook. An embedding host provides its own UI (modal in an IDE, button in a web app, audit log in a CI runner) by passing a callback to `Agentao(confirmation_callback=...)`. The mode model and rule engine are identical across CLI and embedded paths. See [Part 4.5 · Tool Confirmation UI](/en/part-4/5-tool-confirmation-ui).
:::

::: tip Authoritative help
Behavior described here is anchored in [`agentao/cli/transport.py:confirm_tool_execution`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/transport.py) (UI), [`agentao/permissions.py`](https://github.com/jin-bo/agentao/blob/main/agentao/permissions.py) (rules), and [`agentao/cli/help_text.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/help_text.py) (commands).
:::
