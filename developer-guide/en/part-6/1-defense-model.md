# 6.1 Defense-in-Depth Model

> **What you'll learn**
> - The 7-layer defense stack and what fails when each layer is missing
> - Five agent-specific risks and which layer mitigates each
> - The minimum viable security posture vs. the production-grade one

Embedding an agent into your product isn't about one security boundary — it's layered. Each layer is independent. Never bet safety on a single layer.

## Seven-layer defense stack

```
        User request / upstream system
              │
              ▼
  ┌─────────────────────────────┐
  │ 1. Business access control  │  "Can this user even reach the agent?"
  │    (SSO / RBAC / tenant)    │
  └─────────────────────────────┘
              │
              ▼
  ┌─────────────────────────────┐
  │ 2. Credential boundary      │  "What identity does the agent act as?"
  │    (API key / STS / svcacct)│
  └─────────────────────────────┘
              │
              ▼
  ┌─────────────────────────────┐
  │ 3. Prompt-injection defense │  "Can user input override LLM rules?"
  │    (AGENTAO.md constraints, │
  │     tool-output tagging)    │
  └─────────────────────────────┘
              │
              ▼
  ┌─────────────────────────────┐
  │ 4. Permission engine        │  "Is this tool call allowed?" — rule-level
  │    (PermissionEngine rules) │
  └─────────────────────────────┘
              │
              ▼
  ┌─────────────────────────────┐
  │ 5. Tool confirmation        │  "Does the user approve?" — human-level
  │    (confirm_tool UI)        │
  └─────────────────────────────┘
              │
              ▼
  ┌─────────────────────────────┐
  │ 6. Shell sandbox            │  "What can the command actually do?"
  │    (macOS sandbox-exec)     │   — kernel-level
  └─────────────────────────────┘
              │
              ▼
  ┌─────────────────────────────┐
  │ 7. Network isolation        │  "What IPs are reachable?"
  │    (container / VPC / egress│   — infrastructure-level
  └─────────────────────────────┘
              │
              ▼
         Actual execution
              │
              ▼
  ┌─────────────────────────────┐
  │ 8. Audit log (cross-cutting)│  "What happened? Who signed off?"
  └─────────────────────────────┘
```

**Core principle**: if one layer fails, the others still hold.

## Threat model: five agent-specific risks

| Risk | Attack path | Primary defense |
|------|-------------|-----------------|
| **Prompt injection** | User input / web content / file content / tool output carries hidden instructions | Layers 3 + 4 |
| **Credential leakage** | LLM reply or log contains API keys / DB passwords | Layers 2 + 8 (scrubbing) |
| **Privilege escalation** | Agent tool crosses tenants or escalates privilege | Layers 1 + 4 + multi-tenant isolation (6.4) |
| **SSRF** (internal network) | LLM coaxed into `web_fetch http://169.254.169.254/` | Layer 4 (domain blocklist) + Layer 7 |
| **Resource exhaustion** | Infinite tool loops, huge files, context explosion | 6.7 resource governance |

## Responsibilities

| Layer | Owner | Frequency |
|-------|-------|-----------|
| 1. Access control | Your app | When users/roles change |
| 2. Credentials | DevOps | On issuance / rotation |
| 3. Prompt-injection defense | Developers (AGENTAO.md) | Design time |
| 4. Permission rules | Developers + security | Each new tool |
| 5. Confirmation UI | Frontend developers | Design time |
| 6. Shell sandbox | Platform team | Config time |
| 7. Network isolation | Ops / network | Deploy time |
| 8. Audit logs | Platform team | Runtime (monitored) |

## Minimum vs ideal

**Minimum (prototype / internal tool)**:

1. `OPENAI_API_KEY` set properly (not committed to git)
2. `PermissionEngine` on `WORKSPACE_WRITE` preset
3. `working_directory=` per session
4. Basic `confirm_tool` implementation
5. Default `agentao.log`

Enough for demos and internal use, not customer-facing.

**Ideal (production SaaS)**:

1. Per-tenant STS / service account, least-privilege
2. Custom `PermissionEngine` per tenant plan
3. Per-session `working_directory` + container isolation
4. Confirmation UI tied to SSO, approvals recorded for compliance
5. macOS: `sandbox-exec`; Linux: seccomp/namespaces (see 6.2)
6. Network egress rules (VPC + allowlist)
7. Logs to SIEM, metrics to APM
8. Red-team tests for prompt injection

Subsequent sections land each layer.

## Pre-deployment checklist

- [ ] No credentials hard-coded in code or AGENTAO.md
- [ ] `PermissionEngine` rules cover every custom tool and MCP server
- [ ] `confirm_tool` has a timeout (no infinite wait)
- [ ] `working_directory` isolated per session
- [ ] `agentao.log` path is writable and persisted
- [ ] Unit tests cover "expected rule hits"
- [ ] Alerts: tool failure rate, LLM 5xx rate, confirm-timeout rate

## TL;DR

- **Never bet safety on a single layer.** 7 stacked layers + cross-cutting audit.
- The five agent-specific risks: prompt injection, credential leakage, privilege escalation, SSRF, resource exhaustion.
- Minimum viable: API key off git + `WORKSPACE_WRITE` preset + per-session `working_directory` + basic `confirm_tool` + default log.
- Production-grade: per-tenant STS + custom `PermissionEngine` + container isolation + sandbox-exec / seccomp + VPC egress rules + SIEM logs + red-team drills.

→ [6.2 Shell Sandbox & Command Control](./2-shell-sandbox)
