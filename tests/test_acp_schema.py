"""Snapshot + validation tests for the public ACP payload schema (PR 2).

Hosts integrating Agentao over ACP rely on these payloads as the wire
contract. Schema changes must update both the Pydantic model and the
checked-in snapshot in ``docs/schema/host.acp.v1.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentao.acp.schema import (
    AcpAskUserResponse,
    AcpInitializeRequest,
    AcpInitializeResponse,
    AcpRequestPermissionParams,
    AcpRequestPermissionResponse,
    AcpSessionCancelRequest,
    AcpSessionListModelsRequest,
    AcpSessionListModelsResponse,
    AcpSessionLoadRequest,
    AcpSessionLoadResponse,
    AcpSessionNewRequest,
    AcpSessionNewResponse,
    AcpSessionPromptRequest,
    AcpSessionPromptResponse,
    AcpSessionSetModeRequest,
    AcpSessionSetModeResponse,
    AcpSessionSetModelRequest,
    AcpSessionSetModelResponse,
    AcpSessionUpdateParams,
)
from agentao.host.schema import (
    export_host_acp_json_schema,
    normalized_schema_json,
)


SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs" / "schema" / "host.acp.v1.json"
)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_acp_schema_matches_snapshot():
    generated = normalized_schema_json(export_host_acp_json_schema())
    snapshot = SNAPSHOT_PATH.read_text()
    snapshot_norm = normalized_schema_json(json.loads(snapshot))
    assert generated == snapshot_norm, (
        "Generated harness ACP schema diverged from "
        f"{SNAPSHOT_PATH}. Regenerate via "
        "agentao.host.schema.export_host_acp_json_schema() and re-run."
    )


def test_acp_schema_is_independent_from_event_schema():
    """The ACP snapshot must not reuse keys from the event snapshot.

    Both snapshots share a $defs namespace inside their own files, but
    the public surface keeps them separate so a payload change on one
    does not flap the other test.
    """
    event_path = SNAPSHOT_PATH.parent / "host.events.v1.json"
    event = json.loads(event_path.read_text())
    acp = json.loads(SNAPSHOT_PATH.read_text())
    event_defs = set(event.get("$defs", {}).keys())
    acp_defs = set(acp.get("$defs", {}).keys())
    # Allow shared *primitive* names defensively, but every ACP payload
    # model must live only in the ACP snapshot.
    acp_payload_models = {n for n in acp_defs if n.startswith("Acp")}
    assert acp_payload_models, "ACP snapshot is missing public payload models"
    assert acp_payload_models.isdisjoint(event_defs), (
        f"ACP payload models leaked into event snapshot: "
        f"{acp_payload_models & event_defs}"
    )


# ---------------------------------------------------------------------------
# Representative payload validation
# ---------------------------------------------------------------------------


def test_initialize_request_round_trip():
    req = AcpInitializeRequest.model_validate({
        "protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": True}},
        "clientInfo": {"name": "host-app", "version": "0.1.0"},
    })
    assert req.protocolVersion == 1


def test_initialize_response_round_trip():
    """The runtime advertises ``_agentao.cn/ask_user`` through
    ``_meta._agentao.cn/extensions`` — ACP's standard channel for
    extension data — not a non-standard top-level ``extensions`` array.
    The schema must accept the ``_meta`` object; pre-fix ``extra="forbid"``
    rejected it and any host validating the response saw every successful
    handshake as malformed."""
    resp = AcpInitializeResponse.model_validate({
        "protocolVersion": 1,
        "agentCapabilities": {
            "loadSession": True,
            "promptCapabilities": {"image": True, "audio": False, "embeddedContext": False},
            "mcpCapabilities": {"http": False, "sse": True},
        },
        "authMethods": [],
        "agentInfo": {"name": "agentao", "version": "0.3.1.dev0"},
        "_meta": {
            "_agentao.cn/extensions": [
                {
                    "method": "_agentao.cn/ask_user",
                    "description": "Request free-form text input from the user.",
                },
            ],
        },
    })
    assert resp.agentInfo.name == "agentao"
    assert resp.meta is not None
    assert len(resp.meta.agentao_extensions) == 1
    assert resp.meta.agentao_extensions[0].method == "_agentao.cn/ask_user"


def test_initialize_response_rejects_top_level_extensions():
    """The non-standard top-level ``extensions`` array is gone: with
    ``extra="forbid"`` a payload that still carries it is rejected, so a
    client cannot keep relying on the old shape."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AcpInitializeResponse.model_validate({
            "protocolVersion": 1,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {"image": True, "audio": False, "embeddedContext": False},
                "mcpCapabilities": {"http": False, "sse": True},
            },
            "authMethods": [],
            "agentInfo": {"name": "agentao", "version": "0.3.1.dev0"},
            "extensions": [
                {"method": "_agentao.cn/ask_user"},
            ],
        })


def test_initialize_response_meta_is_alias_only():
    """``_meta`` is the only accepted input key — the bare Python field name
    ``meta`` is rejected (no ``populate_by_name``), so the validation surface
    matches the published JSON Schema exactly and ``extra="forbid"`` is not
    weakened by silently accepting an off-spec ``meta`` key."""
    import pytest
    from pydantic import ValidationError

    base = {
        "protocolVersion": 1,
        "agentCapabilities": {
            "loadSession": True,
            "promptCapabilities": {"image": True, "audio": False, "embeddedContext": False},
            "mcpCapabilities": {"http": False, "sse": True},
        },
        "authMethods": [],
        "agentInfo": {"name": "agentao", "version": "0.3.1.dev0"},
    }
    # The wire alias is accepted.
    ok = AcpInitializeResponse.model_validate(
        {**base, "_meta": {"_agentao.cn/extensions": []}}
    )
    assert ok.meta is not None
    # The bare field name is not — it is off-spec and the handler never emits it.
    with pytest.raises(ValidationError):
        AcpInitializeResponse.model_validate({**base, "meta": {}})


def test_session_new_validates_mcp_server_discriminator():
    req = AcpSessionNewRequest.model_validate({
        "cwd": "/tmp/proj",
        "mcpServers": [
            {"name": "fs", "type": "stdio", "command": "npx", "args": ["a"]},
            {"name": "remote", "type": "sse", "url": "https://x.example/sse"},
        ],
    })
    assert len(req.mcpServers) == 2

    with pytest.raises(ValidationError):
        AcpSessionNewRequest.model_validate({
            "cwd": "/tmp/proj",
            "mcpServers": [{"name": "x", "type": "websocket"}],  # not in v1
        })


def test_session_new_requires_mcp_servers_field():
    """The runtime ``_parse_mcp_servers`` raises on ``None`` — schema
    must require the field so generated clients can't send a
    payload the runtime will reject with ``-32602``."""
    with pytest.raises(ValidationError):
        AcpSessionNewRequest.model_validate({"cwd": "/tmp/proj"})


def test_mcp_server_type_defaults_to_stdio():
    """Runtime ``_parse_mcp_servers`` treats a missing ``type`` field
    as ``stdio`` so existing ``{name, command}`` payloads continue to
    work. The schema must accept the same shape."""
    from agentao.acp.schema import AcpMcpServer
    server = AcpMcpServer.model_validate({
        "name": "fs", "command": "/usr/local/bin/mcp-fs",
    })
    assert server.type == "stdio"
    # Whole request validates without an explicit ``type`` field.
    req = AcpSessionNewRequest.model_validate({
        "cwd": "/tmp/proj",
        "mcpServers": [{"name": "fs", "command": "/bin/true"}],
    })
    assert req.mcpServers[0].type == "stdio"


def test_session_new_response_minimal():
    resp = AcpSessionNewResponse.model_validate({"sessionId": "s-1"})
    assert resp.sessionId == "s-1"


def test_session_prompt_round_trip():
    req = AcpSessionPromptRequest.model_validate({
        "sessionId": "s-1",
        "prompt": [
            {"type": "text", "text": "hello"},
            {"type": "resource_link", "uri": "file:///x"},
        ],
    })
    assert len(req.prompt) == 2

    resp = AcpSessionPromptResponse.model_validate({"stopReason": "end_turn"})
    assert resp.stopReason == "end_turn"


def test_session_prompt_accepts_inline_image_block():
    req = AcpSessionPromptRequest.model_validate({
        "sessionId": "s-1",
        "prompt": [
            {"type": "text", "text": "what is this?"},
            {"type": "image", "data": "QUJD", "mimeType": "image/png"},
        ],
    })
    assert len(req.prompt) == 2


def test_image_block_rejects_uri_field():
    """The image wire carries only {data, mimeType} — a by-reference ``uri``
    (a host path / secret vector) must be rejected by extra='forbid', not
    silently accepted and ignored."""
    with pytest.raises(ValidationError):
        AcpSessionPromptRequest.model_validate({
            "sessionId": "s-1",
            "prompt": [{
                "type": "image",
                "data": "QUJD",
                "mimeType": "image/png",
                "uri": "file:///etc/passwd",
            }],
        })


def test_session_prompt_response_rejects_unknown_stop_reason():
    with pytest.raises(ValidationError):
        AcpSessionPromptResponse.model_validate({"stopReason": "stopped"})


def test_session_cancel_request_minimal():
    req = AcpSessionCancelRequest.model_validate({"sessionId": "s-1"})
    assert req.sessionId == "s-1"


def test_request_permission_params_options_validate_kind_enum():
    params = AcpRequestPermissionParams.model_validate({
        "sessionId": "s-1",
        "toolCall": {
            "toolCallId": "tc-1",
            "title": "run_shell_command",
            "kind": "execute",
            "status": "pending",
            "rawInput": {"command": "ls"},
        },
        "options": [
            {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
            {"optionId": "reject_once", "name": "Reject once", "kind": "reject_once"},
        ],
    })
    assert params.toolCall.kind == "execute"

    with pytest.raises(ValidationError):
        AcpRequestPermissionParams.model_validate({
            "sessionId": "s-1",
            "toolCall": {
                "toolCallId": "tc-1",
                "title": "x",
                "kind": "execute",
                "status": "pending",
            },
            "options": [
                {"optionId": "x", "name": "x", "kind": "maybe"},  # not in enum
            ],
        })


def test_request_permission_response_discriminator():
    selected = AcpRequestPermissionResponse.model_validate({
        "outcome": {"outcome": "selected", "optionId": "allow_once"},
    })
    assert selected.outcome.outcome == "selected"

    cancelled = AcpRequestPermissionResponse.model_validate({
        "outcome": {"outcome": "cancelled"},
    })
    assert cancelled.outcome.outcome == "cancelled"


def test_ask_user_response_round_trip():
    """``ACPTransport.ask_user`` accepts only the discriminated outcome
    shape. Pre-fix the schema declared ``{"answer": str}``; clients
    generated from that schema sent payloads the runtime rejected as
    "unknown outcome" and the call resolved to the unavailable
    sentinel."""
    from pydantic import TypeAdapter

    adapter = TypeAdapter(AcpAskUserResponse)
    answered = adapter.validate_python(
        {"outcome": "answered", "text": "yes"},
    )
    assert answered.outcome == "answered"
    assert answered.text == "yes"

    cancelled = adapter.validate_python({"outcome": "cancelled"})
    assert cancelled.outcome == "cancelled"

    # The legacy ``{"answer": ...}`` shape is now rejected; this is the
    # whole point of the contract realignment.
    with pytest.raises(ValidationError):
        adapter.validate_python({"answer": "yes"})


def test_session_load_request_round_trip():
    """``loadSession: true`` is advertised on initialize; the schema
    must include the matching request shape so hosts generated from
    the snapshot can talk to that capability."""
    req = AcpSessionLoadRequest.model_validate({
        "sessionId": "11111111-1111-1111-1111-111111111111",
        "cwd": "/tmp/proj",
        "mcpServers": [],
    })
    assert req.sessionId.startswith("1111")
    # Empty body is the documented response shape.
    AcpSessionLoadResponse.model_validate({})


def test_session_set_model_round_trip_partial_fields():
    """Independent knobs: a request with only ``model`` must validate."""
    req = AcpSessionSetModelRequest.model_validate({
        "sessionId": "s-1",
        "model": "claude-sonnet-4-6",
    })
    assert req.contextLength is None and req.maxTokens is None
    resp = AcpSessionSetModelResponse.model_validate({
        "model": "claude-sonnet-4-6",
        "contextLength": 200000,
        "maxTokens": 8192,
    })
    assert resp.contextLength == 200000


def test_session_set_model_rejects_payloads_runtime_rejects():
    """Schema must reject the same payloads ``handle_session_set_model``
    rejects, so schema-following clients can't generate requests that
    fail at runtime."""
    # Empty body — handler requires at least one knob.
    with pytest.raises(ValidationError):
        AcpSessionSetModelRequest.model_validate({"sessionId": "s-1"})
    # Empty model string.
    with pytest.raises(ValidationError):
        AcpSessionSetModelRequest.model_validate({
            "sessionId": "s-1", "model": "",
        })
    # Non-positive contextLength.
    with pytest.raises(ValidationError):
        AcpSessionSetModelRequest.model_validate({
            "sessionId": "s-1", "contextLength": 0,
        })
    with pytest.raises(ValidationError):
        AcpSessionSetModelRequest.model_validate({
            "sessionId": "s-1", "maxTokens": -1,
        })


def test_session_set_mode_round_trip():
    req = AcpSessionSetModeRequest.model_validate({
        "sessionId": "s-1", "modeId": "read-only",
    })
    assert req.modeId == "read-only"
    resp = AcpSessionSetModeResponse.model_validate({"modeId": "read-only"})
    assert resp.modeId == "read-only"


def test_session_set_mode_accepts_open_mode_ids():
    """``modeId`` is the ACP-standard field and an OPEN string — a UI mode
    that has no Agentao permission meaning (DeepChat's ``code`` / ``ask``)
    must validate, not be rejected. Permission presets are still valid
    values; the handler applies a preset only on an exact match."""
    for value in (
        "read-only", "workspace-write", "full-access", "plan",  # presets
        "code", "ask", "acceptEdits",                            # UI-only modes
    ):
        req = AcpSessionSetModeRequest.model_validate({
            "sessionId": "s-1", "modeId": value,
        })
        assert req.modeId == value
    # Still rejects an empty modeId and the legacy ``mode`` field name.
    with pytest.raises(ValidationError):
        AcpSessionSetModeRequest.model_validate({"sessionId": "s-1", "modeId": ""})
    with pytest.raises(ValidationError):
        AcpSessionSetModeRequest.model_validate({
            "sessionId": "s-1", "mode": "read-only",
        })


def test_session_list_models_round_trip_with_warning():
    """The list-models handler returns a cached list plus ``warning``
    when the provider lookup fails. Schema must accept that shape."""
    req = AcpSessionListModelsRequest.model_validate({"sessionId": "s-1"})
    assert req.sessionId == "s-1"
    resp = AcpSessionListModelsResponse.model_validate({
        "models": [{"id": "claude-sonnet-4-6", "displayName": "Sonnet 4.6"}],
        "warning": "Could not fetch model list: provider timeout",
    })
    assert resp.warning is not None
    # Provider-specific extras (e.g. ``displayName``) flow through.
    assert resp.models[0].id == "claude-sonnet-4-6"


def test_session_update_notification_discriminator():
    """The ``session/update`` notification carries one of several
    ``sessionUpdate`` variants — schema must select the right one."""
    chunk = AcpSessionUpdateParams.model_validate({
        "sessionId": "s-1",
        "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "hello"},
            "schema_version": "v1",
        },
    })
    assert chunk.update.sessionUpdate == "agent_message_chunk"

    tool_call = AcpSessionUpdateParams.model_validate({
        "sessionId": "s-1",
        "update": {
            "sessionUpdate": "tool_call",
            "toolCallId": "tc-1",
            "title": "run_shell_command",
            "kind": "execute",
            "status": "pending",
            "rawInput": {"command": "ls"},
        },
    })
    assert tool_call.update.sessionUpdate == "tool_call"

    update = AcpSessionUpdateParams.model_validate({
        "sessionId": "s-1",
        "update": {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "tc-1",
            "status": "completed",
        },
    })
    assert update.update.sessionUpdate == "tool_call_update"


def test_request_permission_params_accepts_content_block():
    """``confirm_tool`` attaches a ``content`` array to ``toolCall``
    when the tool exposes a description — schema-following hosts must
    accept the field, not reject it as an unknown extra."""
    params = AcpRequestPermissionParams.model_validate({
        "sessionId": "s-1",
        "toolCall": {
            "toolCallId": "tc-1",
            "title": "run_shell_command",
            "kind": "execute",
            "status": "pending",
            "rawInput": {"command": "ls"},
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": "Run shell command"},
                },
            ],
        },
        "options": [
            {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
        ],
    })
    assert params.toolCall.content is not None
    assert params.toolCall.content[0].content.text == "Run shell command"
