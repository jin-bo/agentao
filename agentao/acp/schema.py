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
        default_factory=lambda: {"image": True, "audio": False, "embeddedContext": False}
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


class AcpInitializeMeta(BaseModel):
    """The ``_meta`` object on the ``initialize`` response.

    ACP advertises extensions through ``_meta`` rather than a top-level
    ``extensions`` array, so ``handle_initialize`` namespaces its extension
    list under the vendor key ``_agentao.cn/extensions``. The object is open
    (extra="allow") so other extensions can attach their own namespaced
    payloads to the same ``_meta`` without breaking schema-following clients.
    """

    agentao_extensions: List[AcpInitializeExtension] = Field(
        default_factory=list, alias="_agentao.cn/extensions"
    )

    # Alias-only: the wire key is ``_agentao.cn/extensions``; the Python field
    # name is never an accepted input key (no ``populate_by_name``), so the
    # validation surface stays identical to the published JSON Schema.
    model_config = ConfigDict(extra="allow")


class AcpInitializeResponse(BaseModel):
    """``initialize`` response result."""

    protocolVersion: int
    agentCapabilities: AcpAgentCapabilities
    authMethods: List[Dict[str, Any]] = Field(default_factory=list)
    agentInfo: AcpAgentInfo
    # ``handle_initialize`` advertises ``_agentao.cn/ask_user`` (and any future
    # runtime extensions) through ``_meta._agentao.cn/extensions`` — ACP's
    # standard channel for extension data — rather than a non-standard
    # top-level ``extensions`` array. Without this field, schema-following
    # hosts would reject every successful initialize response.
    meta: Optional[AcpInitializeMeta] = Field(default=None, alias="_meta")

    # Alias-only (no ``populate_by_name``): only the wire key ``_meta`` is an
    # accepted input key, so ``extra="forbid"`` is not weakened by also
    # accepting the bare field name ``meta`` — the handler never emits it.
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


class AcpSessionMode(BaseModel):
    """One selectable ACP session mode (``SessionMode``).

    agentao surfaces each :class:`~agentao.permissions.PermissionMode`
    preset as one of these: ``id`` is the preset's string value (the ACP
    ``modeId`` a client passes to ``session/set_mode``), ``name`` is a
    human label, ``description`` an optional one-liner.
    """

    id: str
    name: str
    description: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class AcpSessionModeState(BaseModel):
    """``SessionModeState`` — advertised on the ``session/new`` response.

    ``currentModeId`` is the active preset; ``availableModes`` is the full
    set a client can switch between via ``session/set_mode``.
    """

    currentModeId: str
    availableModes: List[AcpSessionMode]

    model_config = ConfigDict(extra="forbid")


class AcpSessionNewResponse(BaseModel):
    sessionId: str
    modes: Optional[AcpSessionModeState] = None
    models: Optional[Dict[str, Any]] = None
    # Advertised so clients can switch model/provider via
    # ``session/set_config_option`` without a follow-up list call. Default
    # catalog is the single current ``provider/model``.
    configOptions: Optional[List["AcpConfigOption"]] = None

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


class AcpImageContentBlock(BaseModel):
    """An ACP image content block (inline base64).

    The agent surfaces ``data``/``mimeType`` to the LLM as an OpenAI
    ``image_url`` data-URL part. The image wire deliberately carries only
    inline content — ``{data, mimeType}`` — and never a by-reference
    ``uri``: with ``extra="forbid"`` an image block that includes ``uri``
    (e.g. ``file:///etc/passwd``) is rejected outright, so the handler can
    never be coaxed into dereferencing a host path or secret. The spec's
    optional by-reference field is intentionally unsupported in v1.
    """

    type: Literal["image"] = "image"
    data: str
    mimeType: str

    model_config = ConfigDict(extra="forbid")


AcpPromptContentBlock = Annotated[
    Union[AcpTextContentBlock, AcpResourceLinkBlock, AcpImageContentBlock],
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
    """``session/load`` result.

    The handler emits the persisted history as ``session/update``
    notifications before responding, so the response carries no message
    payload — callers consume the replay stream instead. It does advertise
    the model ``configOptions`` (same as ``session/new``) so a reloaded
    session exposes model/provider switching without a follow-up round trip.
    """

    configOptions: Optional[List["AcpConfigOption"]] = None

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


class AcpConfigOptionChoice(BaseModel):
    """One selectable choice in a ``configOptions`` entry.

    For the ``model`` option the ``value`` is the Agentao convention
    ``provider/model`` (an **Agentao value convention, not an ACP standard**).
    Open shape (extra="allow") so a host-injected catalog can attach extra
    descriptive fields without breaking schema validation.
    """

    value: str
    name: Optional[str] = None
    description: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class AcpConfigOption(BaseModel):
    """A single ACP config option advertised in ``session/new`` / ``load``.

    Agentao advertises exactly one option today (``id="model"``,
    ``category="model"``, ``type="select"``). ``currentValue`` and the
    ``options`` values both use the ``provider/model`` convention. Open shape
    so future categories / fields don't require a snapshot churn for every
    host extension.
    """

    id: str
    name: str
    category: Literal["mode", "model", "thought_level"]
    type: Literal["select"] = "select"
    currentValue: Optional[str] = None
    options: List[AcpConfigOptionChoice] = Field(default_factory=list)
    description: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class AcpSessionSetConfigOptionRequest(BaseModel):
    """``session/set_config_option`` request params.

    ``extra="forbid"`` is load-bearing security: it is one of the two
    mechanisms (the other is the handler whitelist) that reject any
    credential-bearing field (``apiKey`` / ``baseUrl`` / ``_meta``). The wire
    carries only the ``provider/model`` *identifier*; credentials resolve
    server-side via the host-injectable ``provider_resolver``.

    Agentao supports ``configId="model"`` only. ``value`` is
    ``"provider/model"`` (split on the first ``/``; a bare value with no
    ``/`` is a model-only switch that keeps the current provider).
    """

    sessionId: str
    configId: str
    value: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class AcpSessionSetConfigOptionResponse(BaseModel):
    """Post-switch ``configOptions`` state (the updated ``currentValue``).

    No ``config_option_update`` notification is emitted — a successful switch
    returns the refreshed state in the response only.
    """

    configOptions: List[AcpConfigOption] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class AcpAgentaoSetModelRequest(BaseModel):
    """``_agentao.cn/set_model`` request params — free-form, secret-free.

    The vendor free-form path: ``{sessionId, model}`` only. ``extra="forbid"``
    keeps it secret-free (no ``apiKey`` / ``baseUrl`` / ``_meta``). Model-only
    switch — the provider is unchanged. Reuses the ``model`` field name so a
    DeepChat-style adapter maps its UI ``modelId`` → ``model``.
    """

    sessionId: str
    model: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class AcpAgentaoSetModelResponse(BaseModel):
    """The active model after the vendor free-form switch."""

    model: str

    model_config = ConfigDict(extra="forbid")


class AcpSessionSetModeRequest(BaseModel):
    """``session/set_mode`` request params.

    The field is the ACP-standard ``modeId`` (not ``mode``), and it is an
    **open string**, not a closed enum: a ``modeId`` is a UI/behavioural
    selector that need not map to an Agentao permission preset. The handler
    applies a permission preset only on an exact match
    (``read-only`` / ``workspace-write`` / ``full-access`` / ``plan``) and
    otherwise persists the value unchanged — so a client mode like ``code`` /
    ``ask`` round-trips instead of being rejected.
    """

    sessionId: str
    modeId: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class AcpSessionSetModeResponse(BaseModel):
    """The active ``modeId`` after the update (echoes the persisted value)."""

    modeId: str

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


class AcpPlanEntry(BaseModel):
    """One entry in a ``plan`` update (``PlanEntry``).

    agentao's ``todo_write`` carries only ``content`` + ``status``; ACP
    requires ``priority`` too, so the transport synthesizes ``"medium"``.
    """

    content: str
    priority: Literal["high", "medium", "low"]
    status: Literal["pending", "in_progress", "completed"]

    model_config = ConfigDict(extra="forbid")


class AcpSessionUpdatePlan(BaseModel):
    """``plan`` — the agent's task checklist; the client replaces the whole
    plan on each update."""

    sessionUpdate: Literal["plan"] = "plan"
    entries: List[AcpPlanEntry]
    schema_version: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class AcpSessionUpdateCurrentMode(BaseModel):
    """``current_mode_update`` — the active session mode changed.

    Emitted by the ``session/set_mode`` handler (the ACP standard set_mode
    response is empty; the change is communicated via this notification)."""

    sessionUpdate: Literal["current_mode_update"] = "current_mode_update"
    currentModeId: str
    schema_version: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


AcpSessionUpdate = Annotated[
    Union[
        AcpSessionUpdateMessageChunk,
        AcpSessionUpdateToolCall,
        AcpSessionUpdateToolCallUpdate,
        AcpSessionUpdatePlan,
        AcpSessionUpdateCurrentMode,
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
    """Params the agent sends to the client for ``_agentao.cn/ask_user``.

    ``question`` is always present. The remaining fields are optional
    structured hints a client may render as a choice prompt; a client may
    ignore them and prompt with plain text. The reply is always a single
    ``text`` string (see :class:`AcpAskUserAnswered`) — for ``multiple``
    selections the client joins them itself.
    """

    sessionId: str
    question: str
    header: Optional[str] = None
    options: Optional[List[str]] = None
    multiple: bool = False
    allowCustom: bool = True

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
    "AcpAgentaoSetModelRequest",
    "AcpAgentaoSetModelResponse",
    "AcpAskUserAnswered",
    "AcpAskUserCancelled",
    "AcpAskUserParams",
    "AcpAskUserResponse",
    "AcpClientCapabilities",
    "AcpClientInfo",
    "AcpConfigOption",
    "AcpConfigOptionChoice",
    "AcpError",
    "AcpInitializeExtension",
    "AcpInitializeMeta",
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
    "AcpSessionSetConfigOptionRequest",
    "AcpSessionSetConfigOptionResponse",
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
