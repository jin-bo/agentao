# 6.8 Deployment, Canary & Rollback

> **What you'll learn**
> - Why agent deployments differ from typical web services (long containers, heavy deps, many canary dimensions)
> - A multi-stage Dockerfile that doesn't ship `uv` to runtime
> - Kubernetes patterns: `StatefulSet`, PVC, `sessionAffinity` for sticky sessions

Agent deployments differ from typical web services in three ways: **long-lived containers** (session state), **heavy deps** (Python + openai + mcp), and **many canary dimensions** (model, skills, rules can each be versioned independently).

## Dockerfile template

```dockerfile
# ────────────────────────────────
# Stage 1: builder
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# ────────────────────────────────
# Stage 2: runtime
FROM python:3.12-slim

RUN useradd -m -u 10001 agent
WORKDIR /app
COPY --from=builder --chown=agent:agent /app/.venv /app/.venv
COPY --chown=agent:agent your_app/ ./your_app/
COPY --chown=agent:agent skills/ ./skills/
COPY --chown=agent:agent AGENTAO.md ./

RUN mkdir -p /data /tmp/agent && chown agent:agent /data /tmp/agent
USER agent

WORKDIR /data
ENV PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    TMPDIR=/tmp/agent \
    HOME=/tmp/agent

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "from agentao import Agentao; print('ok')" || exit 1

CMD ["python", "-m", "your_app.server"]
```

### Key points

- **Multi-stage**: final image has no `uv` or build tools
- **Non-root**: USER 10001
- **Read-only root**: `--read-only` + tmpfs `/tmp`
- **HOME pointed to /tmp/agent**: memory DB doesn't land in /root/
- Use `python -u` or JSON logger for unbuffered logs

## docker-compose template

```yaml
version: "3.9"

services:
  agent:
    image: your-agent:${TAG:-latest}
    read_only: true
    tmpfs:
      - /tmp:size=512M
    volumes:
      - ./data:/data
      - ./logs:/data/logs
    environment:
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      OPENAI_MODEL: gpt-5.4
      TENANT_ID: ${TENANT_ID}
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    mem_limit: 2g
    cpus: 1.0
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "from agentao import Agentao"]
      interval: 30s
      timeout: 5s
```

## Kubernetes notes

```yaml
apiVersion: apps/v1
kind: StatefulSet       # session state → StatefulSet, not Deployment
metadata:
  name: agent
spec:
  serviceName: agent
  replicas: 3
  template:
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        fsGroup: 10001
      containers:
      - name: agent
        image: your-agent:v0.2.14
        resources:
          requests: {cpu: "500m", memory: "1Gi"}
          limits: {cpu: "2", memory: "4Gi"}
        securityContext:
          readOnlyRootFilesystem: true
          allowPrivilegeEscalation: false
          capabilities:
            drop: ["ALL"]
        env:
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef: {name: agent-secrets, key: openai-key}
        volumeMounts:
        - name: data
          mountPath: /data
        - name: tmp
          mountPath: /tmp
        livenessProbe:
          exec:
            command: ["python", "-c", "from agentao import Agentao"]
          periodSeconds: 30
      volumes:
      - name: tmp
        emptyDir: {medium: Memory, sizeLimit: 512Mi}
  volumeClaimTemplates:
  - metadata: {name: data}
    spec:
      accessModes: ["ReadWriteOnce"]
      resources: {requests: {storage: 10Gi}}
```

**StatefulSet over Deployment**: sessions/memory live in the PVC; a restarted pod rejoins the same session storage.

**Sticky routing**: configure Service `sessionAffinity: ClientIP` (or Ingress sticky) so a user's requests always hit the same pod. Without it, session routing fragments.

## What you can canary

Agentao products have more canary dimensions than typical services:

| Dimension | How | Risk |
|-----------|-----|------|
| **Model version** | Small % of traffic on new model | Medium — behavior may shift |
| **Code version** | Standard blue-green / canary | Medium — same as any service |
| **Skills** | Fork `skills/` per tenant | Low |
| **Permission rules** | Gradual tightening | High — tightening can block valid tools |
| **Sandbox profile** | Change `default_profile` in sandbox.json | High — misconfig stops the shell tool |
| **AGENTAO.md** | Project-level change | Medium — affects instruction-following |

**Rhythm**: model and skills can canary **weekly**; rule / sandbox / AGENTAO.md changes need **full tests + canary + rollback plan**.

## Model switching

Agentao supports runtime model swap ([2.3](/en/part-2/3-lifecycle)):

```python
# Some users on the new model
if user.id in beta_users:
    agent.set_model("gpt-5")
else:
    agent.set_model("gpt-5.4")
```

**Note**: swap doesn't clear history; if the new model's context format differs (rare), also `clear_history()`.

## Rollback plan

Agent changes often **show no user-visible issue but accumulate** (behavior drift, cost creep). Rollback must:

| Change | Rollback | Target time |
|--------|----------|-------------|
| Code bug | Blue-green swap | < 1 min |
| Permission rule mis-block | Revert JSON | < 5 min |
| Sandbox profile broken | Revert + `/sandbox off` | < 5 min |
| Model perf regression | Swap back old model id | < 1 min |
| Skill bug | git revert + restart | < 10 min |

**Practice**: keep key configs in git (`.agentao/*.json`, `skills/`, `AGENTAO.md`). Every change is a commit, instantly revertable.

## Session migration (graceful pod shutdown)

In a Kubernetes rolling update, pre-shutdown flow:

```python
def on_sigterm(signum, frame):
    stop_accepting_new_sessions()
    for sid, (agent, _, _) in pool._pool.items():
        persist_session(sid, agent.messages)
        agent.close()
    for agent in active_agents:
        if agent._current_token:
            agent._current_token.cancel("shutdown")
    sys.exit(0)
```

Clients reopen sessions via `session/load` (ACP) or your SDK-side `add_message()` loop.

## CI/CD test plan

| Stage | Tests |
|-------|-------|
| Build | Unit tests (tools, skill YAML format) |
| Pre-package | System prompt length, AGENTAO.md no-secret scan |
| Pre-deploy | Red-team prompt injection ([6.5](./5-secrets-injection)) |
| Staging | Full user journey smoke tests |
| Production | Continuous canary session (hourly fixed prompt, diff output) |

## Cost monitoring alerts

```
• Daily tokens > last week * 1.5  → P2 (cost anomaly)
• Single-session tokens > 50k      → P3 (possibly stuck)
• Tool failure rate > 10%           → P2 (misconfig likely)
• LLM 5xx rate > 5%                 → P1 (vendor issue)
```

---

**End of Part 6.** You now have the full production stack — sandbox to deployment. The final part weaves these into **canonical integration blueprints**: hands-on examples for real customer scenarios.

## TL;DR

- Multi-stage Dockerfile: build with `uv`, ship only the resolved venv + your code. Don't ship `uv` to runtime.
- Use `StatefulSet` (not `Deployment`) when sessions are sticky; pair with PVC for `/data` and `sessionAffinity: ClientIP` on the Service.
- Canary by **dimension** — model, skills, permission rules each get their own rollout knob. Don't bundle them.
- Rollback drill: keep N-1 model + N-1 skill bundle hot-swappable in seconds; the LLM tier is your most volatile dependency.

→ [Part 7 · Integration Blueprints](/en/part-7/) (coming soon)
