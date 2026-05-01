"""Schema export helpers for the public harness contract.

Each release ships a JSON schema snapshot derived from the public
Pydantic models. Tests assert that the generated schema matches the
checked-in snapshot using normalized JSON, so a model change that
shifts the wire form is caught at PR review.

PR 1 ships the event-and-permission snapshot; PR 2 adds the ACP schema
under :func:`export_harness_acp_json_schema`.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from pydantic import TypeAdapter

from ..acp import schema as _acp_schema_models
from .models import ActivePermissions, HarnessEvent


def export_harness_event_json_schema() -> Dict[str, Any]:
    """Return the JSON schema for public harness events + active permissions.

    The schema is built from a small composite ``definitions`` object so
    a single snapshot file covers ``HarnessEvent`` (the discriminated
    union) and ``ActivePermissions`` together. Hosts that consume
    individual models can still pick them out by ``$defs`` name.
    """
    # ``HarnessEvent`` is an ``Annotated[Union[...], Field(...)]`` alias, not
    # a class, so its ``TypeAdapter`` resolves through Pydantic's runtime
    # introspection. Annotate as ``TypeAdapter[Any]`` to satisfy mypy --strict;
    # the runtime schema is unchanged.
    event_adapter: TypeAdapter[Any] = TypeAdapter(HarnessEvent)
    perms_adapter: TypeAdapter[ActivePermissions] = TypeAdapter(ActivePermissions)
    event_schema = event_adapter.json_schema(ref_template="#/$defs/{model}")
    perms_schema = perms_adapter.json_schema(ref_template="#/$defs/{model}")

    defs: Dict[str, Any] = {}
    defs.update(event_schema.pop("$defs", {}))
    defs.update(perms_schema.pop("$defs", {}))
    defs["ActivePermissions"] = perms_schema

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AgentaoHarnessEvents",
        "description": (
            "Public harness payload models: HarnessEvent (discriminated union "
            "of tool/subagent/permission lifecycle events) plus the "
            "ActivePermissions snapshot."
        ),
        "oneOf": event_schema.get("oneOf", []),
        "discriminator": event_schema.get("discriminator"),
        "$defs": defs,
    }


# ACP host-facing payload models exported via the harness schema surface.
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


def export_harness_acp_json_schema() -> Dict[str, Any]:
    """Return the JSON schema for host-facing ACP payload models.

    The schema groups every public ACP request/response/notification
    model under a single ``$defs`` block so a release snapshot is one
    file. Hosts that consume individual models can pick them out by
    ``$defs`` name.

    Kept in a separate snapshot from
    :func:`export_harness_event_json_schema` so a payload change on one
    surface does not flap the other.
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
        "title": "AgentaoHarnessACP",
        "description": (
            "Public host-facing ACP payload models: initialize, session/* "
            "request/response, request_permission, ask_user, and the shared "
            "error envelope."
        ),
        "$defs": defs,
    }


def normalized_schema_json(schema: Dict[str, Any]) -> str:
    """Canonical JSON form of a schema for snapshot comparison.

    Pydantic's schema generator may reorder ``$defs`` and inline
    references slightly between patch releases. Sorting keys gives us a
    stable diff target without fighting upstream ordering.
    """
    return json.dumps(schema, sort_keys=True, indent=2) + "\n"


__all__ = [
    "export_harness_acp_json_schema",
    "export_harness_event_json_schema",
    "normalized_schema_json",
]
