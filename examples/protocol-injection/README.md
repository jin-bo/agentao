# protocol-injection · replace every host IO surface

Runnable companion to the `agentao.host.protocols` extension points.
Each of the four protocols — `FileSystem`, `ShellExecutor`,
`MCPRegistry`, `MemoryStore` — is replaced with a small adapter that
records what Agentao does, so the smoke suite can prove the slots are
actually wired through.

## Try it

```bash
cd examples/protocol-injection
uv sync --extra dev
PYTHONPATH=. uv run pytest tests/ -v
```

## What's in the box

`src/protocol_demo.py` defines four reference adapters and a `make_agent`
factory that hands them to `Agentao(...)`:

| Protocol | Adapter | What it shows |
|----------|---------|---------------|
| `FileSystem` | `InMemoryFileSystem` | dict-backed, no real disk |
| `ShellExecutor` | `AuditingShellExecutor` | logs every command, refuses `run_background` |
| `MCPRegistry` | `RecordingMCPRegistry` | counts `list_servers()` reads |
| `MemoryStore` | `DictMemoryStore` | full CRUD + soft-delete + review queue |

Each smoke test exercises one slot end-to-end (`read_file`,
`run_shell_command`, `Agentao()` construction, `save_memory`) and asserts
the adapter saw the call.

## Use in your project

```python
from agentao import Agentao
from agentao.memory import MemoryManager

agent = Agentao(
    working_directory=cwd,
    llm_client=my_llm,
    filesystem=MyDockerFileSystem(),       # FileSystem
    shell=MyRemoteShellExecutor(),         # ShellExecutor
    mcp_registry=MyPluginRegistry(),       # MCPRegistry
    memory_manager=MemoryManager(          # MemoryStore wrapped in a manager
        project_store=MyRedisMemoryStore(),
    ),
)
```

The Protocol surface is enforced structurally — implement every method
and Agentao will route through your adapter without subclassing.
