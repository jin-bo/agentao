"""Schema export helpers for the public ``agentao.host`` contract.

Each release ships a JSON schema snapshot derived from the public
Pydantic models. Tests assert that the generated schema matches the
checked-in snapshot using normalized JSON, so a model change that
shifts the wire form is caught at PR review.

Snapshots live under ``docs/schema/host.events.v1.json`` and
``docs/schema/host.acp.v1.json``. The ``v1`` designation is wire
lineage — adding optional fields stays in v1; removing or renaming a
field requires a v2 bump and a release note.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from pydantic import TypeAdapter

from .models import ActivePermissions, HostEvent


def export_host_event_json_schema() -> Dict[str, Any]:
    """Return the JSON schema for ``agentao.host`` events + active permissions.

    The schema is built from a small composite ``definitions`` object so
    a single snapshot file covers ``HostEvent`` (the discriminated
    union) and ``ActivePermissions`` together. Hosts that consume
    individual models can still pick them out by ``$defs`` name.
    """
    # ``HostEvent`` is an ``Annotated[Union[...], Field(...)]`` alias, not
    # a class, so its ``TypeAdapter`` resolves through Pydantic's runtime
    # introspection. Annotate as ``TypeAdapter[Any]`` to satisfy mypy --strict;
    # the runtime schema is unchanged.
    event_adapter: TypeAdapter[Any] = TypeAdapter(HostEvent)
    perms_adapter: TypeAdapter[ActivePermissions] = TypeAdapter(ActivePermissions)
    event_schema = event_adapter.json_schema(ref_template="#/$defs/{model}")
    perms_schema = perms_adapter.json_schema(ref_template="#/$defs/{model}")

    defs: Dict[str, Any] = {}
    defs.update(event_schema.pop("$defs", {}))
    defs.update(perms_schema.pop("$defs", {}))
    defs["ActivePermissions"] = perms_schema

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AgentaoHostEvents",
        "description": (
            "Public host payload models: HostEvent (discriminated union "
            "of tool/subagent/permission lifecycle events) plus the "
            "ActivePermissions snapshot."
        ),
        "oneOf": event_schema.get("oneOf", []),
        "discriminator": event_schema.get("discriminator"),
        "$defs": defs,
    }


def export_host_acp_json_schema() -> Dict[str, Any]:
    """Return the JSON schema for host-facing ACP payload models.

    The schema groups every public ACP request/response/notification
    model under a single ``$defs`` block so a release snapshot is one
    file. Hosts that consume individual models can pick them out by
    ``$defs`` name.

    Kept in a separate snapshot from
    :func:`export_host_event_json_schema` so a payload change on one
    surface does not flap the other.
    """
    from agentao.acp.schema_export import build_host_acp_json_schema

    return build_host_acp_json_schema()


def normalized_schema_json(schema: Dict[str, Any]) -> str:
    """Canonical JSON form of a schema for snapshot comparison.

    Pydantic's schema generator may reorder ``$defs`` and inline
    references slightly between patch releases. Sorting keys gives us a
    stable diff target without fighting upstream ordering.
    """
    return json.dumps(schema, sort_keys=True, indent=2) + "\n"


__all__ = [
    "export_host_acp_json_schema",
    "export_host_event_json_schema",
    "normalized_schema_json",
]
