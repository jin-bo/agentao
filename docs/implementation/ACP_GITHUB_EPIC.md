# ACP Support Epic

## Epic

**Title**

ACP support for Agentao via stdio JSON-RPC server

**Summary**

Implement minimum viable Agent Client Protocol (ACP) support in Agentao so ACP-compatible clients can launch Agentao over stdio, create and load sessions, send prompts, receive streaming session updates, request tool permissions, and cancel active turns.

The first milestone targets a pragmatic compatibility layer on top of existing Agentao runtime abstractions rather than a full architecture rewrite. The implementation should reuse the current transport abstraction, session persistence, permission engine, cancellation flow, and MCP support where possible.

**Goals**

- Support ACP over `stdio`
- Support `initialize`
- Support `session/new`
- Support `session/prompt`
- Support `session/cancel`
- Support `session/load`
- Emit ACP-compatible `session/update` events
- Map tool confirmation to `session/request_permission`
- Reuse existing Agentao runtime and session persistence

**Non-goals for v1**

- Full ACP transport matrix beyond `stdio`
- Full ACP client-host capability routing for `fs/*` and `terminal/*`
- MCP-over-ACP extension support
- Full coverage of all ACP optional methods and notifications

**Acceptance Criteria**

- An ACP client can start Agentao in stdio mode and complete an `initialize -> session/new -> session/prompt` flow successfully.
- Agent text and tool progress are surfaced as ACP `session/update` notifications.
- ACP permission requests can approve or reject tool execution.
- ACP cancel interrupts active turns and returns control cleanly.
- Saved sessions can be loaded and replayed via ACP.

**Dependencies / Reuse**

- Existing runtime transport abstraction: `agentao/transport/*`
- Existing chat loop and cancellation: `agentao/agent.py`
- Existing tool confirmation flow: `agentao/tool_runner.py`
- Existing session persistence: `agentao/session.py`
- Existing MCP client support: `agentao/mcp/*`

**Risks**

- Current `Path.cwd()` usage is process-global and not safe for ACP multi-session semantics.
- Existing tools assume local execution rather than ACP client capabilities.
- Protocol mapping from internal events to ACP update types may need iteration as client interoperability is tested.

## Issue 1

**Title**

Create ACP module skeleton and stdio JSON-RPC server foundation

**Problem**

Agentao does not expose any ACP-compatible server endpoint today. There is no JSON-RPC server, no ACP method dispatcher, and no ACP-specific module boundary.

**Scope**

- Create `agentao/acp/`
- Add `protocol.py`
- Add `server.py`
- Add `session_manager.py`
- Add `transport.py`
- Add `models.py`
- Implement JSON-RPC request parsing and response writing over stdio
- Implement method dispatch and standard error responses

**Implementation Checklist**

- [ ] Create `agentao/acp/__init__.py`
- [ ] Define ACP constants and supported protocol version in `agentao/acp/protocol.py`
- [ ] Implement stdio read loop in `agentao/acp/server.py`
- [ ] Implement thread-safe JSON-RPC write path in `agentao/acp/server.py`
- [ ] Implement request dispatcher by `method`
- [ ] Implement standard JSON-RPC error handling
- [ ] Ensure logs never pollute stdout JSON-RPC stream

**Acceptance Criteria**

- Process can start in ACP stdio mode without entering interactive CLI mode
- Server can parse valid JSON-RPC requests and emit valid responses
- Unknown methods return proper JSON-RPC method-not-found errors

## Issue 2

**Title**

Implement ACP `initialize` handshake and capability negotiation

**Problem**

ACP clients require an `initialize` handshake before any session methods. Agentao currently has no ACP capability advertisement layer.

**Scope**

- Implement `initialize`
- Negotiate protocol version
- Return `agentCapabilities`, `agentInfo`, and auth metadata

**Implementation Checklist**

- [ ] Parse `protocolVersion`, `clientCapabilities`, and `clientInfo`
- [ ] Define supported ACP version constant
- [ ] Return `loadSession` capability
- [ ] Return baseline prompt capabilities for v1
- [ ] Return MCP capability advertisement based on actual support
- [ ] Return `agentInfo` with name, title, version
- [ ] Store client capabilities for later session use

**Acceptance Criteria**

- ACP client can complete `initialize`
- Version mismatch behavior is deterministic and documented
- Response shape matches ACP expectations for supported fields

## Issue 3

**Title**

Add ACP session registry and per-session Agentao runtime lifecycle

**Problem**

ACP is session-based, but Agentao currently does not maintain ACP session state or a registry of runtime instances keyed by ACP session ID.

**Scope**

- Add ACP session registry
- Create `AcpSessionState`
- Manage Agentao instance lifecycle per ACP session

**Implementation Checklist**

- [ ] Define `AcpSessionState` in `agentao/acp/models.py`
- [ ] Track `session_id`, `agent`, `cwd`, `client_capabilities`, `cancel_token`
- [ ] Implement create/get/delete/close operations in `session_manager.py`
- [ ] Ensure server shutdown closes all session-owned Agentao instances
- [ ] Add protection against duplicate or missing session IDs

**Acceptance Criteria**

- Multiple ACP sessions can exist at once in the same server process
- Each ACP session has its own Agentao runtime state
- Session lookup and teardown are reliable

## Issue 4

**Title**

Implement ACP `session/new` and runtime initialization from request params

**Problem**

There is no ACP entry point for creating a new session, binding a working directory, or injecting per-session MCP configuration.

**Scope**

- Implement `session/new`
- Accept `cwd`
- Accept session-level MCP server config
- Create Agentao instance with ACP transport

**Implementation Checklist**

- [ ] Parse `cwd` from request
- [ ] Parse `mcpServers` from request
- [ ] Generate ACP `sessionId`
- [ ] Create `ACPTransport` and inject it into Agentao
- [ ] Create `PermissionEngine` for the session
- [ ] Initialize MCP connections for the session where possible
- [ ] Return `sessionId`
- [ ] Handle invalid cwd and bad MCP config cleanly

**Acceptance Criteria**

- ACP client can create a new session successfully
- Returned `sessionId` can be used in later requests
- Session creation failures return structured errors

## Issue 5

**Title**

Refactor Agentao to support per-session working directories

**Problem**

Current runtime code relies on `Path.cwd()` in several places. ACP sessions require working directory semantics that are not tied to the server process global cwd.

**Scope**

- Introduce session/runtime working directory context
- Remove direct dependence on process-global cwd in ACP execution paths

**Implementation Checklist**

- [ ] Audit all runtime `Path.cwd()` usage
- [ ] Add optional `working_directory` to `Agentao.__init__`
- [ ] Route system prompt cwd rendering through session context
- [ ] Update file, shell, memory, and session persistence paths as needed
- [ ] Define project-local `.agentao/` behavior under ACP sessions
- [ ] Add tests for multiple sessions with different cwd values

**Acceptance Criteria**

- Two ACP sessions with different cwd values do not leak state
- File and shell operations resolve relative paths against the correct session cwd
- Session metadata and prompts report the correct working directory

## Issue 6

**Title**

Implement ACP `session/prompt` with ContentBlock to Agentao message conversion

**Problem**

ACP prompt requests use structured content blocks, but Agentao currently accepts mostly string user input at its runtime boundary.

**Scope**

- Implement `session/prompt`
- Convert ACP content blocks into internal user message text
- Run Agentao chat loop for the session

**Implementation Checklist**

- [ ] Parse ACP `ContentBlock[]`
- [ ] Support `text` blocks in v1
- [ ] Support `resource_link` blocks with a documented minimal mapping
- [ ] Reject unsupported block types with a clear error
- [ ] Serialize prompt execution per session
- [ ] Create and bind a fresh cancellation token for each active turn
- [ ] Return final completion state when turn finishes

**Acceptance Criteria**

- ACP text prompts execute successfully
- Unsupported content types fail explicitly rather than silently degrading
- Concurrent prompts to the same session do not corrupt runtime state

## Issue 7

**Title**

Implement ACP transport adapter for `session/update` notifications

**Problem**

Agentao emits internal runtime events, but ACP clients expect `session/update` notifications using ACP update types.

**Scope**

- Implement `ACPTransport`
- Map internal `AgentEvent` values to ACP update notifications

**Implementation Checklist**

- [ ] Map `EventType.LLM_TEXT` to ACP agent message updates
- [ ] Map `EventType.TOOL_START` to ACP tool call updates
- [ ] Map `EventType.TOOL_OUTPUT` to incremental tool call content updates
- [ ] Map `EventType.TOOL_COMPLETE` to terminal tool call updates
- [ ] Decide v1 behavior for `EventType.THINKING`
- [ ] Decide v1 behavior for sub-agent lifecycle events
- [ ] Ensure all emitted payloads are JSON-serializable

**Acceptance Criteria**

- ACP client receives progressive `session/update` notifications during a turn
- Tool start/output/completion are visible in the ACP event stream
- Final assistant output appears through ACP update flow

## Issue 8

**Title**

Map Agentao tool confirmations to ACP `session/request_permission`

**Problem**

Agentao tool confirmation is currently synchronous and transport-local. ACP requires a protocol-level permission request flow.

**Scope**

- Implement `ACPTransport.confirm_tool`
- Translate tool confirmation to ACP request/response flow

**Implementation Checklist**

- [ ] Emit `session/request_permission` from ACP transport
- [ ] Offer at least `allow_once` and `reject_once`
- [ ] Optionally support `allow_session` in v1 if feasible
- [ ] Block until client responds or timeout occurs
- [ ] Translate selected option to ToolRunner-compatible boolean result
- [ ] If `allow_session` is supported, update session permission state
- [ ] Ensure multiple permission prompts for one session are serialized

**Acceptance Criteria**

- ACP client can approve a tool call once
- ACP client can reject a tool call once
- Timeout or disconnect produces deterministic cancellation behavior

## Issue 9

**Title**

Implement ACP `session/cancel` using existing Agentao cancellation flow

**Problem**

ACP clients must be able to cancel an active turn. Agentao already has cancellation primitives, but they are not exposed as ACP session methods.

**Scope**

- Implement `session/cancel`
- Bind ACP cancellation to `CancellationToken`

**Implementation Checklist**

- [ ] Track active turn token per ACP session
- [ ] Implement `session/cancel` handler
- [ ] Make cancellation idempotent
- [ ] Ensure cancellation propagates to LLM and tool execution
- [ ] Define consistent post-cancel completion/update behavior

**Acceptance Criteria**

- ACP client can cancel an active session turn
- Cancelled turns stop without hanging the server
- Repeated cancel requests do not crash or corrupt state

## Issue 10

**Title**

Implement ACP `session/load` and replay saved session history as ACP updates

**Problem**

Agentao can persist and load sessions locally, but ACP requires protocol-level session loading and replay through `session/update`.

**Scope**

- Implement `session/load`
- Reuse current session persistence
- Replay historical messages in ACP format

**Implementation Checklist**

- [ ] Reuse `agentao/session.py` load path
- [ ] Define mapping from persisted messages to ACP replay events
- [ ] Replay user messages as user message updates
- [ ] Replay assistant messages as agent message updates
- [ ] Decide v1 handling for persisted tool messages
- [ ] Return success only after replay completes
- [ ] Return clear error when requested session does not exist

**Acceptance Criteria**

- ACP client can load an existing session by ID
- Conversation history is replayed before the request completes
- Loaded session can continue receiving new prompts

## Issue 11

**Title**

Support session-scoped MCP server injection from ACP `session/new`

**Problem**

ACP session creation may include MCP server configuration, but Agentao currently loads MCP mostly from local config files at startup.

**Scope**

- Convert ACP-provided MCP config into Agentao MCP client config
- Attach MCP tools to the session runtime

**Implementation Checklist**

- [ ] Define config translation from ACP `mcpServers` to Agentao MCP config
- [ ] Merge or override local MCP config with session-level config
- [ ] Connect MCP servers for the session runtime
- [ ] Register discovered MCP tools for that session
- [ ] Surface MCP connection failures in a non-fatal and observable way

**Acceptance Criteria**

- ACP session can expose tools from request-provided MCP servers
- MCP failures do not necessarily kill the whole server process
- Session-level MCP config does not leak between sessions

## Issue 12

**Title**

Add ACP CLI entrypoint and runtime wiring for stdio mode

**Problem**

There is no supported way to launch Agentao as an ACP server from the command line.

**Scope**

- Add `--acp` and `--stdio` startup path
- Bypass interactive terminal UI in ACP mode

**Implementation Checklist**

- [ ] Add ACP launch mode to CLI or `main.py`
- [ ] Start stdio JSON-RPC server instead of interactive CLI
- [ ] Route logs to stderr or file only
- [ ] Ensure clean process shutdown and resource cleanup

**Acceptance Criteria**

- `agentao --acp --stdio` starts a valid ACP server
- Stdout contains only ACP protocol messages
- Shutdown cleans up MCP connections and session runtimes

## Issue 13

**Title**

Add ACP unit, integration, and end-to-end test coverage

**Problem**

ACP support spans protocol framing, session state, transport mapping, cancellation, permissions, and persistence. Without dedicated tests, regressions will be hard to detect.

**Scope**

- Add protocol-level tests
- Add transport mapping tests
- Add end-to-end stdio tests

**Implementation Checklist**

- [ ] Add `tests/test_acp_protocol.py`
- [ ] Add `tests/test_acp_initialize.py`
- [ ] Add `tests/test_acp_session_new.py`
- [ ] Add `tests/test_acp_prompt.py`
- [ ] Add `tests/test_acp_transport.py`
- [ ] Add `tests/test_acp_permissions.py`
- [ ] Add `tests/test_acp_cancel.py`
- [ ] Add `tests/test_acp_load.py`
- [ ] Add `tests/test_acp_multi_session.py`
- [ ] Add stdio subprocess end-to-end test

**Acceptance Criteria**

- Core ACP flows have automated coverage
- Event mapping and permission flows are regression-tested
- Multi-session and load/cancel behavior are covered

## Issue 14

**Title**

Document ACP support, limitations, and launch flow

**Problem**

ACP support will be non-obvious to users and contributors without dedicated documentation and clear statement of current limits.

**Scope**

- Add implementation and user-facing documentation
- Document supported ACP subset and known limitations

**Implementation Checklist**

- [ ] Add `docs/ACP.md`
- [ ] Update `README.md` with ACP launch and scope
- [ ] Document currently supported ACP methods
- [ ] Document unsupported optional features
- [ ] Add a minimal ACP client transcript example

**Acceptance Criteria**

- Contributors can understand the architecture and implementation scope
- Users can launch and test ACP mode from docs alone
- Limitations are explicit rather than implied

## Suggested Milestones

**M1: Basic ACP Connectivity**

- Issue 1
- Issue 2
- Issue 3
- Issue 4
- Issue 6

**M2: Streaming, Permissions, Cancellation**

- Issue 7
- Issue 8
- Issue 9

**M3: Persistence and MCP**

- Issue 10
- Issue 11

**M4: Hardening and Release**

- Issue 5
- Issue 12
- Issue 13
- Issue 14

