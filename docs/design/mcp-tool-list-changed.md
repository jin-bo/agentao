# MCP `tools/list_changed`: following a server's tool list

Status: built (0.5.18). Borrowed from codex#54656 ("Honor MCP tool-list changes within a turn").

## 1. Problem

Agentao listed each server's tools once, at connect, and registered them once (`tooling/mcp_tools.py::register_mcp_tools`). No `ClientSession` had a `message_handler`, so `notifications/tools/list_changed` was dropped. A server that added a tool mid-session never offered it; one that removed or renamed a tool left the model calling a stale name. A reconnect listed the tools again into `McpClient._tools`, but the registry kept the first connection's tools.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Opt-in per server: `"refresh_tools": true`. Only `True` counts; `agentao doctor` flags a non-boolean. | A change alters the tool list in the request, which restarts the provider's prompt cache for the tools. Codex also keeps it behind a flag. |
| D2 | The `message_handler` is installed only for an opted-in server. | Without the opt-in the session is built exactly as before. |
| D3 | The handler only sets `McpClient._tools_stale`. | It runs in the session's receive loop; a request from there could wait on the loop that has to answer it. |
| D4 | The chat loop asks before **each** model request (`ChatLoopRunner._refresh_mcp_tools`), not once per turn. | A tool call can change the list, and the next request in the same turn should carry the new one. The check is a flag scan with no thread hop when nothing is stale. |
| D5 | Coalesce: the flag is a boolean, cleared when the re-list starts. | N notices before a request cost one `tools/list`. A notice that arrives during the re-list sets it again for the next request. |
| D6 | A failed re-list (timeout under `startup`, protocol error, catalogue bound) keeps the current tools and logs a warning. The flag stays cleared. | The model keeps the tools it has rather than losing them all, and a broken server is not asked again on every request. The next notice tries again. The client's current list still goes to the registry: a reconnect may have listed it since, and with the flag cleared nothing else would deliver it. A cancel that drops finished re-lists marks those servers stale again. |
| D7 | Replace per server, never the whole catalogue. | `get_all_tools()` skips a server that is reconnecting; a full re-registration would drop its tools from the model. |
| D8 | The agent, not the client, decides whether anything changed: it compares the re-listed `Tool` list with the one the server listed last (`agent._mcp_listed`). `agent._mcp_registered` (name → `(server, Tool)`) says what to remove. | After a reconnect `McpClient._tools` is already new while the registry still holds the old tools, so a client-side comparison would miss it. Comparing with the registered tools instead would count a tool refused for a name collision as a change on every refresh. An identical list leaves the registry and the request untouched. |
| D9 | A reconnect of an opted-in server marks the list stale, without asking for another `tools/list`: the handshake's list is used. | Same staleness as a notice; the reconnect's listing reaches the registry through the same path, at no extra request. |
| D13 | The first successful connect after a failed one (a server that needed login at startup) marks the list stale too, for an opted-in server only. In-session `/mcp login` refreshes at once, so `/mcp list` and `/tools` show the tools; otherwise the next request does. `agentao mcp login` from a shell is another process and cannot reach a running session. | `"refresh_tools": true` already says the session may take new tools. Without the opt-in the documented restart (`mcp-oauth.md` D8) stays. |
| D12 | The manager returns each re-listed list; the agent never reads it back from the client. | `disconnect()` empties `McpClient._tools`, and `get_all_tools()` skips a server that is not connected. A connection that drops between the re-list and the registry update would otherwise unregister every tool of that server. |
| D10 | A host's duck-typed `mcp_manager=` is never refreshed (`isinstance` on the real manager). | It has no stale flags; the host owns that manager. |
| D11 | Sub-agents are not refreshed. A running child keeps the parent's `mcp_*` instances it was spawned with. | A child has no `mcp_manager` of its own (empty `InMemoryMCPRegistry`), so it can never consume the parent's flags. A tool the server removed fails at the server, as any call to a removed tool would. |

## 3. Flow

1. Server sends `notifications/tools/list_changed`. mcp 1.x hands the handler a `ServerNotification` RootModel, 2.x the notification itself; `_compat.is_tool_list_changed` unwraps and type-checks.
2. `McpClient._on_server_message` sets `_tools_stale`.
3. Before the next request, `refresh_mcp_tools(agent)` calls `McpClientManager.refresh_stale_tools()`, which runs `McpClient.refresh_tools()` for every stale client on the MCP loop (`gather(..., return_exceptions=True)`: one server's failure keeps the others' results) and returns `{server: new list}`.
4. For each re-listed server whose `Tool` list differs from the one it listed last: unregister its names (only those whose origin is still `mcp`), register the new list against the other servers' claimed names, log `+added -removed`.
5. The runner rebuilds the request's `tools` from the registry.

The server sends the notice before the answer to the call that changed the list, and each session has one receive loop that handles messages in order, so the flag is set by the time that `call_tool` returns (probed 200/200 on mcp 1.26 and 2.0).

## 4. Not built

- `resources/list_changed` (see `mcp-resources.md`) and `prompts/list_changed`.
- A `/mcp list` marker for opted-in servers.
- Refreshing a running sub-agent's tools.
