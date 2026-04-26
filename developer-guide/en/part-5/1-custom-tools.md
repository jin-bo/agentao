# 5.1 Custom Tools

**Custom tools are the best way to let the agent call your business APIs.** Handing the LLM your OpenAPI spec and asking it to craft HTTP requests is fragile; a typed `Tool` subclass is reliable, auditable, and safe.

## The Tool base class

Source: `agentao/tools/base.py:11-115`

```python
from abc import ABC, abstractmethod
from typing import Dict, Any
from agentao.tools.base import Tool

class MyTool(Tool):
    @property
    def name(self) -> str:
        return "unique_tool_name"          # globally unique

    @property
    def description(self) -> str:
        return "One-line description for the LLM — decides whether it calls this tool."

    @property
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema for parameters (passed straight to LLM function calling)."""
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "..."},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return True     # True for writes / network / shell

    @property
    def is_read_only(self) -> bool:
        return False    # Pure reads → True; helps the permission engine

    def execute(self, **kwargs) -> str:
        """Real logic. Return a string — the LLM reads it for its next move."""
        query = kwargs["query"]
        limit = kwargs.get("limit", 10)
        ...
        return f"Found {len(results)} items: {results}"
```

**Six essentials**:

| Attribute/method | Required | Purpose |
|------------------|----------|---------|
| `name` | ✅ | Globally unique; collisions are overwritten with a warning |
| `description` | ✅ | **The LLM's only decision input** — say "when to use, what params mean, what comes back" |
| `parameters` | ✅ | JSON Schema; anything OpenAI function-calling supports |
| `execute(**kwargs) -> str` | ✅ | Returns a plain string; no dicts, no bytes |
| `requires_confirmation` | ❌ | True for side-effecting tools → routes through `confirm_tool` |
| `is_read_only` | ❌ | True for pure reads; permission engine / Plan mode can optimize |

## Why must `execute` return a string?

The tool result is injected into the LLM's message history as a `role:tool` message (OpenAI function calling). Non-string results aren't compatible. Correct pattern:

```python
def execute(self, **kwargs) -> str:
    data = call_my_api(kwargs)
    return json.dumps({
        "status": "ok",
        "data": data,
        "count": len(data),
    }, ensure_ascii=False)
```

Large responses (> a few dozen KB) should be **truncated or paginated** first, or they'll blow out the context window.

## Tool-call normalization

Before a tool call is written back into conversation history or executed, Agentao normalizes the model's function-call payload:

- argument strings are parsed and re-emitted as compact JSON when a safe repair is possible
- near-miss tool names can be repaired to a registered tool name
- lone UTF-16 surrogate characters are sanitized before outbound assistant/tool messages reach strict provider APIs
- every assistant `tool_call_id` is answered with a `role:tool` message, including parse errors and loop-protection halts

This is a resilience layer, not a substitute for a clear schema. Keep `parameters` precise, keep descriptions unambiguous, and validate dangerous or business-critical fields inside `execute()` before taking side effects.

## Writing a description the LLM can actually use

This matters more than the code. Bad descriptions cause misuse; good ones teach the LLM when to use, when not to, and how to handle the return.

### ❌ Bad

```python
description = "Get orders"
```

The LLM has no idea what an "order" is, whose, what params, or the return shape.

### ✅ Good

```python
description = """
Query this tenant's customer orders. Use when the user asks about "my orders",
"recent order", "order details".

Args:
- `customer_id` (required): the customer ID from the user's session context
- `status`: filter by status ("pending" / "shipped" / "delivered" / "all"), default "all"
- `limit`: max results, default 10, max 50

Returns: JSON with `orders` array; each has id/status/total/created_at.

Rules:
- Never expose customer_id to the user in your reply
- If orders is empty, tell the user "no orders found"
"""
```

Rule of thumb: **write it to the LLM itself** — "when the user says X, call me."

## Path resolution helpers

The `Tool` base class provides two helpers for path handling:

```python
class MyFileTool(Tool):
    def execute(self, path: str, **kw) -> str:
        # _resolve_path: expands ~; absolute passes through; relative joins working_directory
        p = self._resolve_path(path)
        return p.read_text()
```

`self.working_directory` is auto-bound by Agentao at registration time, so in **multi-instance deployments** each agent's tools resolve paths against that agent's root. Using these helpers (not `Path(raw)`) gives you tenant isolation for free.

## Registering tools

Agentao does not take custom tools at construction time. **Registration pattern**:

```python
from pathlib import Path
from agentao import Agentao
from agentao.transport import SdkTransport

# 1. Construct the agent (built-ins get registered during __init__)
agent = Agentao(
    working_directory=Path("/tmp/session-x"),
    transport=SdkTransport(),
)

# 2. Register your tool
my_tool = MyTool()
my_tool.working_directory = agent.working_directory   # bind explicitly
agent.tools.register(my_tool)

# 3. Use as usual
agent.chat("Look up customer 123's orders")
```

⚠️ Notes:

- `agent.tools` is a public `ToolRegistry` instance — you can call `register()` any time
- Do it **before** the first `chat()` so the LLM sees the tool list
- On name collision the later registration wins; a warning is logged

## Full example: calling a business API

```python
"""Your SaaS backend exposes order queries to the agent."""
import json
from typing import Dict, Any
from agentao.tools.base import Tool

class GetCustomerOrdersTool(Tool):
    def __init__(self, backend_client, tenant_id: str):
        super().__init__()
        self.backend = backend_client
        self.tenant_id = tenant_id      # bound per session

    @property
    def name(self) -> str:
        return "get_customer_orders"

    @property
    def description(self) -> str:
        return (
            "Query this tenant's customer orders. "
            "Use when the user asks about 'my orders', 'order status', etc. "
            "Args: customer_id (required), status (optional: pending/shipped/delivered/all, default all), "
            "limit (optional int, max 50, default 10). "
            "Returns JSON: {status, orders:[{id, status, total, created_at}]}. "
            "Never expose the internal tenant_id or api tokens in your reply."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "shipped", "delivered", "all"],
                    "default": "all",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["customer_id"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return False    # read-only API, no extra confirm needed

    @property
    def is_read_only(self) -> bool:
        return True

    def execute(self, **kwargs) -> str:
        try:
            orders = self.backend.list_orders(
                tenant_id=self.tenant_id,
                customer_id=kwargs["customer_id"],
                status=kwargs.get("status", "all"),
                limit=min(kwargs.get("limit", 10), 50),
            )
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})
        return json.dumps({
            "status": "ok",
            "orders": [o.to_dict() for o in orders],
        }, ensure_ascii=False)


# --- In your web handler ---
def make_agent_for_tenant(tenant, backend):
    agent = Agentao(
        working_directory=Path(f"/tmp/{tenant.id}"),
        transport=SdkTransport(...),
    )
    agent.tools.register(GetCustomerOrdersTool(backend, tenant.id))
    agent.tools.register(CreateRefundTool(backend, tenant.id))
    agent.tools.register(SendEmailTool(backend, tenant.id))
    return agent
```

## Common pitfalls

### ❌ Raising inside `execute`

```python
def execute(self, **kwargs) -> str:
    return self.backend.create_invoice(...)   # what if HTTPError?
```

An uncaught exception kills the whole `chat()` call. **Catch and return an error string** so the LLM can see it and adapt:

```python
def execute(self, **kwargs) -> str:
    try:
        result = self.backend.create_invoice(...)
        return json.dumps({"status": "ok", "id": result.id})
    except BackendError as e:
        return json.dumps({"status": "error", "message": str(e)})
```

### ❌ Description too vague

`"Do things with customer data"` — the LLM will call it everywhere. **One tool, one job, one focused description.**

### ❌ Forgetting `requires_confirmation=True`

Writes, refunds, emails, shell, deletes — **anything with side effects** deserves confirmation. Without it you hand the LLM a loaded gun with no safety.

### ❌ No argument bounds

The LLM may pass `limit=99999`. Always clamp in your tool:

```python
limit = min(max(1, kwargs.get("limit", 10)), 50)
```

### ❌ Oversized responses

```python
return json.dumps(all_1000_orders)   # may be 500KB
```

Blows the context window and slows the LLM. **Truncate, paginate, summarize** and let the LLM fetch the next page if it wants:

```python
return json.dumps({
    "status": "ok",
    "orders": orders[:10],
    "total_count": len(orders),
    "has_more": len(orders) > 10,
    "next_cursor": cursor if len(orders) > 10 else None,
})
```

## Tool vs Skill vs MCP: how to pick

| Need | Use |
|------|-----|
| Call HTTP API / database / in-memory object | **Tool** (this section) |
| Teach the LLM "do things our way" | **Skill** ([5.2](./2-skills)) |
| Integrate an existing third-party tool service (GitHub, filesystem, DB) | **MCP** ([5.3](./3-mcp)) |

Production products usually use all three: tools for business logic, MCP for integrations, skills for style.

→ Next: [5.2 Skills & Plugins](./2-skills)
