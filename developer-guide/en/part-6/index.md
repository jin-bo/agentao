# Part 6 · Security & Production Deployment

**Shipping an agent to real users** is 10× harder than running it on a developer's laptop. This part merges "security" and "production" — they're inseparable.

## Coverage

- [**6.1 Defense-in-Depth Model**](./1-defense-model) — 7-layer defense stack, 5 threat categories, minimum vs ideal
- [**6.2 Shell Sandbox & Command Control**](./2-shell-sandbox) — macOS sandbox-exec, 3 built-in profiles, Linux alternatives
- [**6.3 Network & SSRF Defense**](./3-network-ssrf) — Domain layering, httpx redirects, MCP network isolation
- [**6.4 Multi-Tenant & Filesystem Isolation**](./4-multi-tenant-fs) — working_directory golden rule, DB isolation, /tmp pollution
- [**6.5 Secrets & Prompt-Injection Defense**](./5-secrets-injection) — Five commandments, attack surfaces, red-team checklist
- [**6.6 Observability & Audit**](./6-observability) — 4 observation axes, event archive, compliance logs
- [**6.7 Resource Governance & Concurrency**](./7-resource-concurrency) — Session pool, TTL, token budgets, memory estimation
- [**6.8 Deployment, Canary & Rollback**](./8-deployment) — Dockerfile, K8s StatefulSet, canary dimensions

## Paths by role

| Role | Suggested sections |
|------|--------------------|
| DevOps / SRE | 6.6 → 6.7 → 6.8 |
| Security review | 6.1 → 6.4 → 6.5 |
| Platform engineer | 6.1 → 6.2 → 6.3 → 6.4 |
| PM (understand risk) | 6.1 → 6.5 risk sections |

## Mental model

> Security is layered, not a single checkpoint; production is governance, not luck.
> Each layer assumes the one above it has already failed — that's how you always have a safety net.

→ [Start with 6.1 →](./1-defense-model)
