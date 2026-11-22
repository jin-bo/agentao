# MCP — rules and invariants

This file loads when Claude works under this folder. It was moved verbatim from the root `CLAUDE.md`.

## MCP

- Config: `.agentao/mcp.json` (project) and `<home>/.agentao/mcp.json` (global).
- Key files: `agentao/mcp/config.py`, `client.py`, `tool.py`, `_compat.py`, `resources.py`, `resource_tools.py`, `skills.py`, `skill_tools.py`.
- CLI: `/mcp list`, `/mcp add [--http|--sse] <name> <command|url>`, `/mcp remove <name>`, `/mcp resources [server]`, `/mcp verbose [server]`.
- **`/mcp verbose` hides secrets by rule, not by guarantee** (`cli/commands/mcp_verbose.py`, module docstring). Env and header values are masked. A URL shows only `scheme://host[:port]`. Args come from the raw `mcp.json` entry only when it expands to exactly the live args, because the loaded config holds expanded `${VAR}` values, and then go through `mask_args`. Never print the live (expanded) args.

**Transports** (`config.py::resolve_transport`, fail-closed):
- `command` gives stdio. `url` gives **Streamable HTTP by default**. Add `"type": "sse"` for legacy SSE.
- A bare `url` used to mean SSE (a breaking change). Tools register as `mcp_{server}_{tool}`.

**Threading:**
- `McpClientManager` runs one loop on its own thread (`agentao-mcp-loop`). Sync callers submit to it with `run_coroutine_threadsafe` (#241).
- A call to the manager *from* that thread raises. Each `McpClient` holds its connection in an **owner task**. That task opens and closes the transport (#243).
- `disconnect_all(timeout=)` is final and bounded.

**SDK versions:**
- Agentao supports both SDK majors (`mcp>=1.26.0,<3`). `_compat.py` absorbs the 2.0 breaks. It **probes the installed SDK and never parses a version string.** Read it before you touch `client.py` or `tool.py`.
- **Tests must use real `mcp.types` models.** Never use `SimpleNamespace` or `MagicMock` fakes.

**Protocol era** (`McpClient._negotiate()`):
- Send `initialize` first. Escalate to `server/discover` only on a protocol rejection (`-32022` or `-32601`).
- **This is deliberately the reverse of upstream `mode='auto'`** (`docs/design/mcp-streamable-http.md` §5.8.1).
- Exception: a server with `"skills": true` starts with `server/discover`. Tripwire test: `test_a_dual_era_server_is_left_on_the_handshake_era`.
- `McpClient.protocol_version` is a ceiling. Gate on `>=`.

**OAuth** (`docs/design/mcp-oauth.md`):
- Every URL server gets `StoredTokenAuth`, except stdio, `"oauth": false`, or a server with its own `Authorization` header.
- Only `/mcp login` or `agentao mcp login` opens a browser. `agentao run` and ACP never do.
- Records are in `~/.agentao/mcp-oauth/`. The key is the canonical URL plus the optional `oauth.profile`.
- **With no profile, the key is the URL alone. Do not "simplify" the key.**
- A server that ACP supplies is always `"oauth": false`.

**Resources** (`docs/design/mcp-resources.md`):
- Three read-only tools are in `mcp/resource_tools.py`, not `tools/`, to prevent an import cycle. They register only when a server declares `resources`.
- Each call checks **config, then reconnect, then live capability**, in that order.
- The tool saves a binary under its own bound `working_directory`. There is no fallback to the process cwd.
- Their names are in `MCP_RESOURCE_TOOL_NAMES`, outside `BUILTIN_TOOL_NAMES`. `disable_tools` and `enabled_tools` accept them.
