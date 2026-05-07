"""ACP schema-export implementation.

Public entry point is :func:`agentao.host.schema.export_host_acp_json_schema`,
which lazy-imports :func:`build_host_acp_json_schema` from here.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import TypeAdapter

from . import schema as _acp_schema_models


# ACP host-facing payload models exported via the agentao.host schema surface.
# Listed by name so the snapshot has a deterministic ``$defs`` ordering and
# a removed/renamed payload model breaks the snapshot test loudly.
_ACP_PUBLIC_MODELS = (
    "AcpInitializeRequest",
    "AcpInitializeResponse",
    "AcpSessionNewRequest",
    "AcpSessionNewResponse",
    "AcpSessionLoadRequest",
    "AcpSessionLoadResponse",
    "AcpSessionPromptRequest",
    "AcpSessionPromptResponse",
    "AcpSessionCancelRequest",
    "AcpSessionCancelResponse",
    "AcpSessionSetModelRequest",
    "AcpSessionSetModelResponse",
    "AcpSessionSetModeRequest",
    "AcpSessionSetModeResponse",
    "AcpSessionListModelsRequest",
    "AcpSessionListModelsResponse",
    "AcpSessionUpdateParams",
    "AcpRequestPermissionParams",
    "AcpRequestPermissionResponse",
    "AcpAskUserParams",
    "AcpAskUserResponse",
    "AcpError",
)


def build_host_acp_json_schema() -> Dict[str, Any]:
    """Build the JSON schema for host-facing ACP payload models."""
    defs: Dict[str, Any] = {}
    for name in _ACP_PUBLIC_MODELS:
        model = getattr(_acp_schema_models, name)
        adapter = TypeAdapter(model)
        sub = adapter.json_schema(ref_template="#/$defs/{model}")
        # Pull nested $defs (e.g. shared content-block models referenced
        # by multiple top-level payloads) up into the composite block.
        defs.update(sub.pop("$defs", {}))
        defs[name] = sub
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AgentaoHostACP",
        "description": (
            "Public host-facing ACP payload models: initialize, session/* "
            "request/response, request_permission, ask_user, and the shared "
            "error envelope."
        ),
        "$defs": defs,
    }


__all__ = ["build_host_acp_json_schema"]
