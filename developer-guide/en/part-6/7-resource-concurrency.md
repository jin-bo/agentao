# 6.7 Resource Governance & Concurrency

Agents are **unpredictable workloads** — a simple Q&A may take 200 ms; a complex task may run 10 minutes and call dozens of tools. Without governance you either DoS yourself or light the cloud bill on fire.

## Four resources to cap separately

| Resource | Unbounded → | Primary control |
|----------|-------------|-----------------|
| LLM tokens | Runaway bill | `max_context_tokens` + `max_iterations` + budgets |
| Time | Request pile-up / timeout storm | Per-chat() + per-tool timeouts |
| Memory | OOM crash | Session pool limit + TTL eviction |
| FDs / subprocesses | Resource leaks | `close()` + MCP subprocess cleanup |

## Control 1 · Context window

```python
agent = Agentao(
    max_context_tokens=128_000,   # default 200_000
)
```

Exceeding triggers **context compression** — the LLM summarizes old messages. Compression itself costs time and tokens.

**Rules of thumb**:

| Model | Suggested max_context_tokens |
|-------|-----------------------------|
| gpt-5.4 / gpt-4.1 | 100_000–128_000 |
| claude-sonnet-4 | 150_000–200_000 |
| 1M-context class | 500_000–800_000 |
| Cheap small models | 4_000–16_000 |

**Don't** set to the model's actual max — leave 20% as compression headroom.

## Control 2 · Iteration cap

```python
reply = agent.chat(msg, max_iterations=50)   # default 100
```

Pair with `on_max_iterations_callback` ([4.6](/en/part-4/6-max-iterations)) for bailout.

Agentao also tracks repeated tool-call failures inside a turn. Identical repeated tool calls trip doom-loop protection, and repeated unparseable argument payloads for the same tool are answered with a `role:tool` halt message so the next LLM request remains protocol-valid instead of spinning forever.

Tune by scenario:

| Task | max_iterations |
|------|---------------|
| Simple Q&A | 20–30 |
| Code editing / analysis | 80–150 |
| Research / deep task | 200–500 |

## Control 3 · Per-turn timeout

Agentao **has no built-in `chat()` timeout** — it runs forever. Enforce at the host:

```python
import asyncio

async def chat_with_timeout(agent, msg, timeout_s: float = 120):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(agent.chat, msg),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        if agent._current_token:
            agent._current_token.cancel("timeout")
        return "[Timeout: agent took too long]"
```

**Tiers**:

- Interactive: 60–120s (past that, users leave)
- Background: 5–30 min
- Batch: per-task

## Control 4 · Tool timeout

Custom tools **must** time out:

```python
class ApiCallTool(Tool):
    def execute(self, **kwargs) -> str:
        try:
            r = httpx.get(kwargs["url"], timeout=10.0)
            return r.text
        except httpx.TimeoutException:
            return "Request timed out"
```

MCP tool timeout comes from the `timeout` field:

```json
{"mcpServers": {"x": {"command": "...", "timeout": 30}}}
```

## Concurrency: session pools

### Pattern A · One instance per request (naive)

```python
@app.post("/chat")
async def chat(req):
    workdir = Path(f"/tmp/ephemeral-{uuid.uuid4()}")
    try:
        agent = Agentao(working_directory=workdir)
        return await asyncio.to_thread(agent.chat, req.message)
    finally:
        agent.close()
        shutil.rmtree(workdir)
```

**Fits**: stateless (each question independent, no cross-turn context), low QPS.
**Doesn't fit**: stateful conversations, MCP-heavy stacks (MCP init is expensive).

### Pattern B · Session pool + TTL eviction

```python
from time import monotonic
from asyncio import Lock

class AgentPool:
    def __init__(self, max_sessions: int = 500, ttl_s: float = 1800):
        self._pool: dict = {}
        self._global_lock = Lock()
        self.max_sessions = max_sessions
        self.ttl_s = ttl_s

    async def get(self, session_id: str, workdir: Path) -> tuple[Agentao, Lock]:
        async with self._global_lock:
            now = monotonic()
            self._evict_expired(now)
            while len(self._pool) >= self.max_sessions:
                self._evict_lru()
            if session_id not in self._pool:
                agent = Agentao(working_directory=workdir)
                self._pool[session_id] = (agent, Lock(), now)
            entry = list(self._pool[session_id])
            entry[2] = now
            self._pool[session_id] = tuple(entry)
            return entry[0], entry[1]

    def _evict_expired(self, now):
        for sid, (a, _, last) in list(self._pool.items()):
            if now - last > self.ttl_s:
                a.close()
                del self._pool[sid]

    def _evict_lru(self):
        victim = min(self._pool.items(), key=lambda kv: kv[1][2])
        victim[1][0].close()
        del self._pool[victim[0]]
```

**Key points**:

- `Lock()` guarantees **serial** turns within a session (agents aren't thread-safe — see [2.3](/en/part-2/3-lifecycle))
- TTL eviction + LRU cap together to prevent unbounded growth
- Remember `agent.close()` during eviction to reap MCP

### Pattern C · ACP subprocess pool

ACP gives you one subprocess per session — natural process isolation. Good for:

- Multi-tenant SaaS
- Strict compliance
- Tenants needing different Python deps

Cost: cold start ~1–2 s, tens of MB per process.

## Token budgets

Cap per-user/tenant total token burn:

```python
class TokenBudget:
    def __init__(self, daily_limit: int):
        self.daily_limit = daily_limit
        self.used_today: dict = {}

    def try_reserve(self, user_id: str, tokens: int) -> bool:
        used = self.used_today.get(user_id, 0)
        if used + tokens > self.daily_limit:
            return False
        self.used_today[user_id] = used + tokens
        return True

budget = TokenBudget(daily_limit=1_000_000)

def on_event(ev):
    if ev.type == EventType.LLM_TEXT:
        budget.used_today[current_user] = \
            budget.used_today.get(current_user, 0) + len(ev.data["chunk"]) // 4
```

Exact counting: `tiktoken` via `pip install 'agentao[tokenizer]'`.

## Memory footprint

Per-agent memory:

| Component | ~ size |
|-----------|--------|
| Agent core + tool registry | 3–5 MB |
| Conversation history (full context) | 0.5–2 MB |
| Memory DB (open SQLite) | < 1 MB |
| MCP subprocess (stdio) | 50–200 MB per child |

**Formula**: `total ≈ sessions × 10 MB + MCP × 100 MB`

500 sessions + 3 resident MCPs ≈ 5 GB.

## Graceful shutdown

```python
import signal, sys

pool = AgentPool(...)

def shutdown(*_):
    print("Shutting down...")
    for sid, (agent, _, _) in list(pool._pool.items()):
        try:
            agent.close()
        except Exception as e:
            print(f"Error closing {sid}: {e}")
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)
```

Without this, SIGTERM leaves MCP subprocesses orphaned.

## Pre-load-test checklist

- [ ] Max concurrent sessions per user/tenant
- [ ] Daily token budget per session/user
- [ ] Per-chat() timeout
- [ ] Sensible max_iterations defaults
- [ ] Session pool TTL and max_sessions
- [ ] Graceful shutdown
- [ ] Monitoring: active sessions, MCP subprocesses, memory per session

→ [6.8 Deployment, Canary & Rollback](./8-deployment)
