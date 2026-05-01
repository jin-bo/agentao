# Part 6 · Security & Production Deployment

**Shipping an agent to real users** is 10× harder than running it on a developer's laptop. This part merges "security" and "production" — they're inseparable.

::: info Key terms in this Part
- **Defense-in-depth** — every layer assumes the one above failed; security never lives in one place · [§6.1](/en/part-6/1-defense-model), [G.5](/en/appendix/g-glossary#g-5-security-vocabulary)
- **SSRF blocklist** — bans `127.0.0.1`, `169.254.169.254`, link-local, RFC1918 by default; **only extend, never disable** · [§6.3](/en/part-6/3-network-ssrf), [G.5](/en/appendix/g-glossary#g-5-security-vocabulary)
- **Working-directory golden rule** — one tenant = one CWD; never share — file tools resolve there · [§6.4](/en/part-6/4-multi-tenant-fs)
- **Session pool** — TTL + LRU eviction over `(tenant_id, session_id)` keys; the production lifecycle pattern · [§6.7](/en/part-6/7-resource-concurrency)
- **Sticky session** — `StatefulSet` + PVC + `sessionAffinity`; how the same session lands on the same pod · [§6.8](/en/part-6/8-deployment)
:::

## Coverage

- [**6.1 Defense-in-Depth Model**](./1-defense-model) — 7-layer defense stack, 5 threat categories, minimum vs ideal
- [**6.2 Shell Sandbox & Command Control**](./2-shell-sandbox) — macOS sandbox-exec, 3 built-in profiles, Linux alternatives
- [**6.3 Network & SSRF Defense**](./3-network-ssrf) — Domain layering, httpx redirects, MCP network isolation
- [**6.4 Multi-Tenant & Filesystem Isolation**](./4-multi-tenant-fs) — working_directory golden rule, DB isolation, /tmp pollution
- [**6.5 Secrets & Prompt-Injection Defense**](./5-secrets-injection) — Five commandments, attack surfaces, red-team checklist
- [**6.6 Observability & Audit**](./6-observability) — 4 observation axes, built-in replay, compliance logs
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
