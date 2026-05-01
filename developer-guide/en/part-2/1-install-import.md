# 2.1 Install & Import

## Install

```bash
# Recommended: pin an exact version
pip install 'agentao==0.2.14'

# uv users
uv add 'agentao==0.2.14'

# With optional extras
pip install 'agentao[pdf,excel,tokenizer]==0.2.14'
```

Extras are listed in [1.5 Requirements](/en/part-1/5-requirements).

## The two imports you always need

```python
from agentao import Agentao                  # main agent class
from agentao.transport import SdkTransport    # callback bridge; preferred for embedding
```

Those two lines are essentially the price of admission. Everything else is opt-in:

```python
from agentao.transport import (
    Transport,         # Protocol base (implement for a custom transport)
    AgentEvent,        # event object
    EventType,         # event enum
    NullTransport,     # silent transport (tests)
)

from agentao.permissions import PermissionEngine, PermissionMode
from agentao.cancellation import CancellationToken, AgentCancelledError
from agentao.tools.base import Tool, ToolRegistry  # when authoring custom tools
```

## Lazy loading

`agentao/__init__.py` uses **PEP 562 `__getattr__`** to defer heavy imports, and since 0.3.4 (P0.5) the deferral covers a much wider set of libraries — `from agentao import Agentao` no longer pulls the OpenAI SDK, BeautifulSoup, jieba, filelock, rich, prompt_toolkit, readchar, click, pygments, starlette, or uvicorn.

What stays lazy now (load on first runtime use):

| Library | First triggered by |
|---|---|
| `openai` | `LLMClient(...)` construction (default LLM client only — hosts that inject `llm_client=` never load it) |
| `bs4` / `httpx` | `WebFetchTool.execute()` / `WebSearchTool.execute()` |
| `jieba` | first call into `MemoryRetriever` recall scoring |
| `filelock` | `SkillRegistry.save()` (CLI / `agentao plugin install`) |
| `mcp` SDK (`McpClientManager`, `McpTool`) | first MCP server attach (`init_mcp` or hosts that pass `mcp_manager=`) |
| `rich`, `prompt_toolkit`, `readchar`, `click`, `pygments` | only loaded by `agentao/cli/*` — never on the embed path |

For embedders:

- `import agentao` is **cheap** — none of the above touch your import-time graph.
- Accessing `agentao.Agentao` triggers the agent module, which still avoids the deferred libs above.
- Hosts that pass their own `llm_client=` / `mcp_registry=` / file-system / shell never pay for the OpenAI SDK or the MCP SDK at all.
- `from agentao.memory import ...` stays standalone — no LLM stack loaded.
- The invariant is enforced by `tests/test_no_cli_deps_in_core.py` (AST walk; fails if a top-level import of a deferred module slips outside `agentao/cli/`) and `tests/test_import_cost.py` (`python -X importtime` subprocess; fails if any of the deferred names appear in the trace of `import agentao`).

You can treat Agentao as a "conditional dependency": import cost only when you actually use it.

```python
# Top of your FastAPI module
import agentao  # lightweight, no side effects

def get_agent():
    from agentao import Agentao  # agent module loads, but openai/bs4/jieba do not
    return Agentao(...)
```

## Version check

```python
import agentao
print(agentao.__version__)   # "0.2.14"
```

In production, verify at startup:

```python
import agentao
from packaging.version import Version

MIN = Version("0.2.14")
if Version(agentao.__version__) < MIN:
    raise RuntimeError(f"Need agentao >= {MIN}, got {agentao.__version__}")
```

## Minimal embedding skeleton

```python
"""Bare-minimum embedding boilerplate."""
from pathlib import Path
from agentao import Agentao
from agentao.transport import SdkTransport

def make_agent(workdir: Path, on_token=None) -> Agentao:
    transport = SdkTransport(
        on_event=lambda ev: on_token(ev.data.get("chunk", ""))
                             if on_event_is_text(ev) else None,
    )
    return Agentao(transport=transport, working_directory=workdir)

def on_event_is_text(ev) -> bool:
    return ev.type.name == "LLM_TEXT"
```

Parameter meanings are covered next.

→ [2.2 Constructor Reference](./2-constructor-reference)
