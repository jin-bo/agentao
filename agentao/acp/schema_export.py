"""ACP schema-export implementation.

Lives under ``agentao.acp`` because the function inspects ACP wire
models and produces a JSON schema for them — that's an ACP concern, not
a host-package concern. The public entry point is
:func:`agentao.host.schema.export_host_acp_json_schema`, which is a
thin lazy delegate to :func:`build_host_acp_json_schema` here.

This module exists so ``agentao.host`` does not eagerly import
``agentao.acp`` at package load. After the planned ``acp/`` wheel split
(see Phase 6 of ``docs/design/core-boundary-review.md``),
``agentao-core`` will not declare ``agentao-acp`` as a hard dependency;
the lazy delegate raises ``ImportError`` with a clear install hint when
ACP is not installed.
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
    """Build the JSON schema for host-facing ACP payload models.

    Implementation behind
    :func:`agentao.host.schema.export_host_acp_json_schema`. Kept here so
    ``agentao.host`` can stay free of an eager ACP import.
    """
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
