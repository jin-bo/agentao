"""Public Pydantic models for host-facing ACP payloads.

This module defines the host-facing ACP wire shapes — request/response
payloads for ``initialize``, ``session/new``, ``session/prompt``,
``session/cancel``, plus the ``request_permission`` and ``ask_user``
notifications. The dataclasses in :mod:`agentao.acp.models` stay in
charge of internal JSON-RPC envelope dispatch; these models are the
schema contract for hosts that integrate over ACP.

Pydantic models here intentionally do **not** replace the runtime
dataclasses; they document and protect the public payload shape. Tests
in ``tests/test_acp_schema.py`` regenerate the schema from these models
and compare against ``docs/schema/harness.acp.v1.json``.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------


class AcpClientCapabilities(BaseModel):
    """Capabilities the client advertises during ``initialize``.

    Kept open (extra="allow") because ACP defines a flexible capability
    namespace; hosts may attach private capability flags that the agent
    ignores. The runtime only inspects ``fs`` and ``terminal``.
    """

    fs: Optional[Dict[str, Any]] = None
    terminal: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")


class AcpAgentCapabilities(BaseModel):
    """Capabilities Agentao advertises in the ``initialize`` response."""

    loadSession: bool = True
    promptCapabilities: Dict[str, bool] = Field(
        default_factory=lambda: {"image": False, "audio": False, "embeddedContext": False}
    )
    mcpCapabilities: Dict[str, bool] = Field(
        default_factory=lambda: {"http": False, "sse": True}
    )

    model_config = ConfigDict(extra="allow")


class AcpAgentInfo(BaseModel):
    name: str
    title: Optional[str] = None
    version: str

    model_config = ConfigDict(extra="forbid")


class AcpClientInfo(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    version: Optional[str] = None

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


class AcpInitializeRequest(BaseModel):
    """``initialize`` request params (one handshake per stdio connection)."""

    protocolVersion: int
    clientCapabilities: AcpClientCapabilities
    clientInfo: Optional[AcpClientInfo] = None

    model_config = ConfigDict(extra="forbid")


class AcpInitializeExtension(BaseModel):
    """One advertised extension method on the ``initialize`` response.

    Agentao advertises ``_agentao.cn/ask_user`` so hosts know they may be
    asked free-form questions over the same JSON-RPC channel. The shape
    is intentionally open (extra="allow") so future extensions can add
    fields without breaking existing schema-following clients.
    """

    method: str
    description: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class AcpInitializeResponse(BaseModel):
    """``initialize`` response result."""

    protocolVersion: int
    agentCapabilities: AcpAgentCapabilities
    authMethods: List[Dict[str, Any]] = Field(default_factory=list)
    agentInfo: AcpAgentInfo
    # ``handle_initialize`` returns an ``extensions`` array advertising
    # ``_agentao.cn/ask_user`` (and any future runtime extensions).
    # Without this field, schema-following hosts would reject every
    # successful initialize response that includes the extension list.
    extensions: List[AcpInitializeExtension] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# session/new
# ---------------------------------------------------------------------------


class AcpMcpEnvVar(BaseModel):
    name: str
    value: str

    model_config = ConfigDict(extra="forbid")


class AcpMcpHeader(BaseModel):
    name: str
    value: str

    model_config = ConfigDict(extra="forbid")


class AcpMcpServer(BaseModel):
    """A session-scoped MCP server config passed in ``session/new``.

    ``type`` discriminates ``stdio`` (command/args/env) vs ``sse``
    (url/headers). The agent ignores ``http`` because Agentao's MCP
    client only supports ``stdio`` and ``sse`` in v1. ``type`` defaults
    to ``"stdio"`` because the runtime parser treats a missing ``type``
    field as stdio for compatibility with the established wire form
    that historical clients send (just ``{name, command}``); making
    the schema strict here would reject runtime-valid payloads.
    """

    name: str
    type: Literal["stdio", "sse"] = "stdio"
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[List[AcpMcpEnvVar]] = None
    url: Optional[str] = None
    headers: Optional[List[AcpMcpHeader]] = None

    model_config = ConfigDict(extra="forbid")


class AcpSessionNewRequest(BaseModel):
    # ``mcpServers`` is required at runtime: ``_parse_mcp_servers``
    # raises ``TypeError`` (mapped to ``-32602``) when the field is
    # missing or ``None``. Hosts must pass an explicit list (empty is
    # fine) — schema is required here to match the contract and stop
    # generated clients from sending payloads that fail at runtime.
    cwd: str
    mcpServers: List[AcpMcpServer]

    model_config = ConfigDict(extra="forbid")


class AcpSessionNewResponse(BaseModel):
    sessionId: str
    modes: Optional[Dict[str, Any]] = None
    models: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# session/prompt
# ---------------------------------------------------------------------------


class AcpTextContentBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str

    model_config = ConfigDict(extra="forbid")


class AcpResourceLinkBlock(BaseModel):
    type: Literal["resource_link"] = "resource_link"
    uri: str
    name: Optional[str] = None
    title: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


AcpPromptContentBlock = Annotated[
    Union[AcpTextContentBlock, AcpResourceLinkBlock],
    Field(discriminator="type"),
]


class AcpSessionPromptRequest(BaseModel):
    sessionId: str
    prompt: List[AcpPromptContentBlock]

    model_config = ConfigDict(extra="forbid")


class AcpSessionPromptResponse(BaseModel):
    """``session/prompt`` response result.

    ``stopReason`` is the ACP enum: ``end_turn``, ``cancelled``,
    ``max_turn_requests``, ``refusal``. The agent currently emits only
    ``end_turn`` and ``cancelled``; the other values are listed in the
    enum so hosts that consume the schema can rely on the closed set.
    """

    stopReason: Literal["end_turn", "cancelled", "max_turn_requests", "refusal"]

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# session/load
# ---------------------------------------------------------------------------


class AcpSessionLoadRequest(BaseModel):
    """``session/load`` request params.

    Mirrors ``session/new`` (cwd + mcpServers) plus the persisted
    ``sessionId`` to reload. The advertised ``loadSession: true``
    capability documents support; the schema pins the wire shape so
    schema-following hosts can validate before sending. ``mcpServers``
    is required for the same reason as ``session/new`` — the runtime
    parser rejects ``None``.
    """

    sessionId: str
    cwd: str
    mcpServers: List[AcpMcpServer]

    model_config = ConfigDict(extra="forbid")


class AcpSessionLoadResponse(BaseModel):
    """``session/load`` returns an empty result object.

    The handler emits the persisted history as ``session/update``
    notifications before responding, so the response itself carries
    no payload — callers consume the replay stream instead.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# session/set_model + session/set_mode + session/list_models
# ---------------------------------------------------------------------------


class AcpSessionSetModelRequest(BaseModel):
    """``session/set_model`` request params.

    Constraints mirror ``handle_session_set_model``: at least one of
    ``model`` / ``contextLength`` / ``maxTokens`` must be set; ``model``
    when present must be a non-empty string; ``contextLength`` and
    ``maxTokens`` when present must be positive integers. Without
    these constraints schema-validating clients could generate
    payloads (empty body, empty model string, zero token caps) that
    the runtime rejects with ``-32602``.
    """

    sessionId: str
    model: Optional[str] = Field(default=None, min_length=1)
    contextLength: Optional[int] = Field(default=None, gt=0)
    maxTokens: Optional[int] = Field(default=None, gt=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _at_least_one_knob(self) -> "AcpSessionSetModelRequest":
        if self.model is None and self.contextLength is None and self.maxTokens is None:
            raise ValueError(
                "session/set_model requires at least one of "
                "model / contextLength / maxTokens"
            )
        return self


class AcpSessionSetModelResponse(BaseModel):
    """Post-update knob values; lets the front end confirm what landed."""

    model: str
    contextLength: int
    maxTokens: int

    model_config = ConfigDict(extra="forbid")


class AcpSessionSetModeRequest(BaseModel):
    """``session/set_mode`` request params.

    ``mode`` is constrained to the PermissionMode values the runtime
    actually accepts; ``handle_session_set_mode`` parses any other
    string into a ``-32602`` error, so the schema must reflect the
    closed set or schema-following clients would generate requests
    that fail at runtime. New presets require both a runtime change
    and a snapshot bump (a load-bearing pairing).
    """

    sessionId: str
    mode: Literal["read-only", "workspace-write", "full-access", "plan"]

    model_config = ConfigDict(extra="forbid")


class AcpSessionSetModeResponse(BaseModel):
    """The active mode after the update — confirms which preset is live."""

    mode: Literal["read-only", "workspace-write", "full-access", "plan"]

    model_config = ConfigDict(extra="forbid")


class AcpSessionListModelsRequest(BaseModel):
    """``session/list_models`` request params (sessionId only)."""

    sessionId: str

    model_config = ConfigDict(extra="forbid")


class AcpModelInfo(BaseModel):
    """One entry in ``session/list_models`` response.

    Open shape (extra="allow") — providers add fields like
    ``contextLength``, ``displayName``, etc. that the runtime
    forwards verbatim.
    """

    id: str

    model_config = ConfigDict(extra="allow")


class AcpSessionListModelsResponse(BaseModel):
    """Available-models catalog. ``warning`` is set when the underlying
    provider lookup failed and the runtime returned its cached list."""

    models: List[AcpModelInfo] = Field(default_factory=list)
    warning: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# session/update notification (server → client)
# ---------------------------------------------------------------------------


class AcpSessionUpdateContentBlock(BaseModel):
    """Inner content block carried on most session/update variants."""

    type: Literal["text"] = "text"
    text: str

    model_config = ConfigDict(extra="forbid")


class AcpSessionUpdateMessageChunk(BaseModel):
    """``agent_message_chunk`` / ``agent_thought_chunk`` /
    ``user_message_chunk`` carry a single content block."""

    sessionUpdate: Literal[
        "agent_message_chunk",
        "agent_thought_chunk",
        "user_message_chunk",
    ]
    content: AcpSessionUpdateContentBlock
    schema_version: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class AcpSessionUpdateToolCall(BaseModel):
    """``tool_call`` notifies the host that a tool is starting.

    Shape mirrors :class:`AcpToolCallSummary` so hosts can render with
    a single widget for both ``session/update`` and the
    ``request_permission`` confirmation dialog.
    """

    sessionUpdate: Literal["tool_call"] = "tool_call"
    toolCallId: str
    title: str
    kind: Literal["read", "edit", "search", "execute", "fetch", "other"]
    status: Literal["pending", "in_progress", "completed", "failed"]
    rawInput: Optional[Dict[str, Any]] = None
    schema_version: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class AcpSessionUpdateToolCallUpdate(BaseModel):
    """``tool_call_update`` carries incremental output and terminal status."""

    sessionUpdate: Literal["tool_call_update"] = "tool_call_update"
    toolCallId: str
    status: Literal["pending", "in_progress", "completed", "failed"]
    content: Optional[List[AcpToolCallContentEntry]] = None
    schema_version: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


AcpSessionUpdate = Annotated[
    Union[
        AcpSessionUpdateMessageChunk,
        AcpSessionUpdateToolCall,
        AcpSessionUpdateToolCallUpdate,
    ],
    Field(discriminator="sessionUpdate"),
]


class AcpSessionUpdateParams(BaseModel):
    """Top-level params for the ``session/update`` notification."""

    sessionId: str
    update: AcpSessionUpdate

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# session/cancel
# ---------------------------------------------------------------------------


class AcpSessionCancelRequest(BaseModel):
    sessionId: str

    model_config = ConfigDict(extra="forbid")


class AcpSessionCancelResponse(BaseModel):
    """``session/cancel`` returns an empty result object."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# request_permission (server → client) and ask_user
# ---------------------------------------------------------------------------


class AcpPermissionOption(BaseModel):
    optionId: str
    name: str
    kind: Literal[
        "allow_once",
        "reject_once",
        "allow_always",
        "reject_always",
    ]

    model_config = ConfigDict(extra="forbid")


class AcpToolCallTextBlock(BaseModel):
    """Inner content block for ``AcpToolCallContentEntry``."""

    type: Literal["text"] = "text"
    text: str

    model_config = ConfigDict(extra="forbid")


class AcpToolCallContentEntry(BaseModel):
    """One entry in ``AcpToolCallSummary.content``.

    Mirrors the runtime shape ``{"type": "content", "content":
    {"type": "text", "text": ...}}`` produced by ``confirm_tool`` when
    the tool has a description to render in the host's confirmation
    dialog.
    """

    type: Literal["content"] = "content"
    content: AcpToolCallTextBlock

    model_config = ConfigDict(extra="forbid")


class AcpToolCallSummary(BaseModel):
    """Tool-call payload mirrored into ``request_permission``.

    Hosts render this with the same widget as ``session/update`` tool
    call notifications.
    """

    toolCallId: str
    title: str
    kind: Literal["read", "edit", "search", "execute", "fetch", "other"]
    status: Literal["pending", "in_progress", "completed", "failed"]
    rawInput: Optional[Dict[str, Any]] = None
    # ``confirm_tool`` attaches a description as a content array when
    # the tool exposes one, so schema-following clients must accept the
    # field. Optional — tools without a description still produce a
    # bare summary.
    content: Optional[List[AcpToolCallContentEntry]] = None

    model_config = ConfigDict(extra="forbid")


class AcpRequestPermissionParams(BaseModel):
    sessionId: str
    toolCall: AcpToolCallSummary
    options: List[AcpPermissionOption]

    model_config = ConfigDict(extra="forbid")


class AcpRequestPermissionSelected(BaseModel):
    outcome: Literal["selected"] = "selected"
    optionId: str

    model_config = ConfigDict(extra="forbid")


class AcpRequestPermissionCancelled(BaseModel):
    outcome: Literal["cancelled"] = "cancelled"

    model_config = ConfigDict(extra="forbid")


AcpRequestPermissionOutcome = Annotated[
    Union[AcpRequestPermissionSelected, AcpRequestPermissionCancelled],
    Field(discriminator="outcome"),
]


class AcpRequestPermissionResponse(BaseModel):
    outcome: AcpRequestPermissionOutcome

    model_config = ConfigDict(extra="forbid")


class AcpAskUserParams(BaseModel):
    sessionId: str
    question: str

    model_config = ConfigDict(extra="forbid")


class AcpAskUserAnswered(BaseModel):
    """``ask_user`` reply when the user typed an answer."""

    outcome: Literal["answered"] = "answered"
    text: str

    model_config = ConfigDict(extra="forbid")


class AcpAskUserCancelled(BaseModel):
    """``ask_user`` reply when the user dismissed the prompt."""

    outcome: Literal["cancelled"] = "cancelled"

    model_config = ConfigDict(extra="forbid")


# ``ACPTransport.ask_user`` accepts ``{"outcome": "answered", "text":
# ...}`` or ``{"outcome": "cancelled"}``. Anything else is logged as an
# unknown outcome and resolved to the unavailable sentinel — the schema
# pins the contract so schema-following hosts cannot send the runtime
# payloads it does not know how to interpret.
AcpAskUserResponse = Annotated[
    Union[AcpAskUserAnswered, AcpAskUserCancelled],
    Field(discriminator="outcome"),
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AcpError(BaseModel):
    """JSON-RPC error payload — ``code``/``message``/``data``."""

    code: int
    message: str
    data: Optional[Any] = None

    model_config = ConfigDict(extra="forbid")


__all__ = [
    "AcpAgentCapabilities",
    "AcpAgentInfo",
    "AcpAskUserAnswered",
    "AcpAskUserCancelled",
    "AcpAskUserParams",
    "AcpAskUserResponse",
    "AcpClientCapabilities",
    "AcpClientInfo",
    "AcpError",
    "AcpInitializeExtension",
    "AcpInitializeRequest",
    "AcpInitializeResponse",
    "AcpMcpEnvVar",
    "AcpMcpHeader",
    "AcpMcpServer",
    "AcpModelInfo",
    "AcpPermissionOption",
    "AcpPromptContentBlock",
    "AcpRequestPermissionCancelled",
    "AcpRequestPermissionOutcome",
    "AcpRequestPermissionParams",
    "AcpRequestPermissionResponse",
    "AcpRequestPermissionSelected",
    "AcpResourceLinkBlock",
    "AcpSessionCancelRequest",
    "AcpSessionCancelResponse",
    "AcpSessionListModelsRequest",
    "AcpSessionListModelsResponse",
    "AcpSessionLoadRequest",
    "AcpSessionLoadResponse",
    "AcpSessionNewRequest",
    "AcpSessionNewResponse",
    "AcpSessionPromptRequest",
    "AcpSessionPromptResponse",
    "AcpSessionSetModelRequest",
    "AcpSessionSetModelResponse",
    "AcpSessionSetModeRequest",
    "AcpSessionSetModeResponse",
    "AcpSessionUpdate",
    "AcpSessionUpdateContentBlock",
    "AcpSessionUpdateMessageChunk",
    "AcpSessionUpdateParams",
    "AcpSessionUpdateToolCall",
    "AcpSessionUpdateToolCallUpdate",
    "AcpTextContentBlock",
    "AcpToolCallContentEntry",
    "AcpToolCallSummary",
    "AcpToolCallTextBlock",
]
