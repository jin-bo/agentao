# 2.1 Install & Import

## Install

Pick the install line that matches your usage. Starting in 0.4.0,
`pip install agentao` ships an embedding-only core — CLI / web fetch /
Chinese tokenization are opt-in extras.

```bash
# Embedding host (`from agentao import Agentao`) — minimum closure
pip install 'agentao>=0.4.0'

# Need the web_fetch / web_search tools — adds beautifulsoup4
pip install 'agentao[web]>=0.4.0'

# Need Chinese-text memory recall — adds jieba
pip install 'agentao[i18n]>=0.4.0'

# CLI users only — adds rich/prompt-toolkit/readchar/pygments
pip install 'agentao[cli]>=0.4.0'

# Upgrading from 0.3.x and want zero behaviour change
pip install 'agentao[full]>=0.4.0'
```

Extras matrix is documented in [1.5 Requirements](/en/part-1/5-requirements).
0.3.x → 0.4.0 migration guide:
[`docs/migration/0.3.x-to-0.4.0.md`](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.3.x-to-0.4.0.md).

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
| `bs4` / `httpx` | `WebFetchTool.execute()` / `WebSearchTool.execute()` — note: post-0.4.0 these tools are skipped at registration if `bs4` is absent (the `[web]` extra is opt-in), so the model never sees a tool whose execute would fail |
| `jieba` | CJK-bearing query through `MemoryRetriever` — pure-Latin queries skip jieba entirely; if `[i18n]` is absent, CJK recall degrades to empty with a one-time warning |
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
print(agentao.__version__)   # "0.4.0"
```

In production, verify at startup:

```python
import agentao
from packaging.version import Version

MIN = Version("0.4.0")
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
