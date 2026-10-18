"""Schema export helpers for the public ``agentao.host`` contract.

Each release ships a JSON schema snapshot derived from the public
Pydantic models. Tests assert that the generated schema matches the
checked-in snapshot using normalized JSON, so a model change that
shifts the wire form is caught at PR review.

Snapshots live under ``docs/schema/host.events.v2.json`` and
``docs/schema/host.acp.v1.json``. The models are closed
(``extra="forbid"``, so ``additionalProperties: false``), so a JSON
validator built from a snapshot rejects any field the snapshot does not
list. Adding a field to an event model is therefore a new snapshot
version, as removing or renaming one is. A published snapshot is never
rewritten: :data:`FROZEN_SNAPSHOTS` pins its digest, and it keeps
describing the events of the releases that wrote it.

- ``host.events.v1.json``: frozen. Up to 0.5.14.
- ``host.events.v2.json``: ``SubagentUsage.reasoning_tokens`` (#550).
"""

from __future__ import annotations

import hashlib
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


#: The published snapshots that never change again, by SHA-256 of their text
#: with ``\n`` line ends (:func:`snapshot_digest`).
FROZEN_SNAPSHOTS: Dict[str, str] = {
    "host.events.v1.json": "2d86de8fbe893d86065094f8ca4a5aa661f666310acf9c558af9a6aa9e9325ad",
}

#: The current host event snapshot: what :func:`export_host_event_json_schema`
#: produces.
HOST_EVENTS_SNAPSHOT = "host.events.v2.json"


def snapshot_digest(text: str) -> str:
    """SHA-256 of a snapshot's text, line ends normalized to ``\n``.

    Normalized so a Windows checkout with CRLF line ends hashes the same.
    """
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


__all__ = [
    "FROZEN_SNAPSHOTS",
    "HOST_EVENTS_SNAPSHOT",
    "export_host_acp_json_schema",
    "export_host_event_json_schema",
    "normalized_schema_json",
    "snapshot_digest",
]
