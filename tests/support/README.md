# tests/support/

Shared test scaffolding — fake servers, agent doubles, factory helpers.

**Scope:** Helpers that are (or would be) duplicated across 2+ test files.
Things that belong: configurable fake subprocess servers, reusable `FakeAgent`
base classes, common param builders (`initialize_params()`, `_prompt_params`).

**Out of scope:** Test-file-specific fixtures, mocks of business types with
only one caller, anything that requires a flag to behave differently per call
site. If an abstraction needs more than two optional knobs, it is probably
better left inline.

**Import style:** Both forms are in use and either is fine, but match the
file you are editing — the ACP suites use relative
(`from .support.acp_server import X`), the rest use absolute
(`from tests.support.memory import X`). Helpers here are **plain functions /
classes**, not pytest fixtures — construction is explicit at the call site.

## Modules

| Module | Holds |
|---|---|
| `acp_agents.py` | `FakeAgent` and friends — duck-typed `Agentao` replacements for ACP handler tests. |
| `acp_client.py` | Fake ACP subprocess servers (`JSONRPC_MOCK_SCRIPT`, `INTERACTION_SERVER_SCRIPT`) + manager/handle builders. |
| `acp_server.py` | In-memory `AcpServer` builders, plus the `BlockingStdin` / `CapturingStdout` / `RecordingServer` stream doubles. |
| `host_events.py` | Host event-stream capture. |
| `mcp.py` | MCP client/tool doubles. |
| `memory.py` | Memory-store builders. |
| `permissions.py` | Permission-rule builders. |
| `stop_precompact.py` | Runner + capture-script setup for the Stop / PreCompact hook suites. |
| `tool_calls.py` | The OpenAI SDK *wire* `tool_call` shape. |
| `tools.py` | Registrable tool doubles (`NamedTool`) and `make_dummy_agent`. |
| `wheel.py` | Built-wheel discovery for the `slow` clean-install tests. |
