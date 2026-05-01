"""JSON Schema generator for replay events.

Code is the source of truth: :class:`agentao.replay.events.EventKind`
declares the kind vocabulary per schema version, and :class:`ReplayEvent`
declares the envelope shape. This module emits the on-disk schema files
under ``schemas/`` from those declarations.

Design notes:

- The schema is a discriminated union from day one — every kind gets its
  own ``oneOf`` variant pinning ``kind`` to a ``const``. Per-kind payload
  shapes are lenient by default; v1.2 introduces typed payloads for the
  three harness-projected kinds (``tool_lifecycle`` / ``subagent_lifecycle``
  / ``permission_decision``) by deriving them from the Pydantic models
  in :mod:`agentao.host.models`, so a payload-shape change there is
  caught here as schema drift.
- The envelope is strict (``additionalProperties: false``). Adding a new
  envelope field is a breaking change; payload extensibility is what
  makes the format forward-compatible.
- Each schema version pins a separate file. v1.0 schema accepts only the
  v1.0 kind enum; v1.1 schema accepts the full v1.1 vocabulary; v1.2
  adds the harness-projection kinds. A 1.0 replay file must keep
  validating against ``schemas/replay-event-1.0.json`` forever — that
  is the backward-compatibility promise documented in
  ``docs/replay/schema-policy.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, Optional

from pydantic import TypeAdapter

from .events import EventKind


SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _kinds_for_version(version: str) -> FrozenSet[str]:
    if version == "1.0":
        return EventKind.V1_0
    if version == "1.1":
        return EventKind.V1_1
    if version == "1.2":
        return EventKind.V1_2
    raise ValueError(f"unknown replay schema version: {version!r}")


def _envelope_properties() -> Dict[str, dict]:
    """Shared envelope properties — every event carries these regardless of kind."""
    return {
        "event_id": {"type": "string", "minLength": 1},
        "session_id": {"type": "string", "minLength": 1},
        "instance_id": {"type": "string", "minLength": 1},
        "seq": {"type": "integer", "minimum": 0},
        "ts": {"type": "string", "format": "date-time"},
        "turn_id": {"type": ["string", "null"]},
        "parent_turn_id": {"type": ["string", "null"]},
        "payload": {
            "type": "object",
            "additionalProperties": True,
        },
    }


# Property schemas for the optional sanitizer-injected fields listed in
# :data:`agentao.replay.sanitize.SANITIZER_INJECTED_FIELDS`. The two
# constants are kept side-by-side so an addition to the field set is a
# build-time mismatch, not silent schema drift.
_SANITIZE_METADATA_PROPERTIES: Dict[str, dict] = {
    "redaction_hits": {
        "type": "object",
        "additionalProperties": {"type": "integer", "minimum": 0},
    },
    "redacted": {"type": "string"},
    "redacted_fields": {
        "type": "array",
        "items": {"type": "string"},
    },
}


def _assert_sanitize_metadata_in_sync() -> None:
    """Guard against drift between the sanitizer and this generator.

    Importing the sanitizer's source-of-truth field set lazily avoids
    eager-importing the recorder dependency chain at module load.
    """
    from .sanitize import SANITIZER_INJECTED_FIELDS

    declared = frozenset(_SANITIZE_METADATA_PROPERTIES)
    if declared != SANITIZER_INJECTED_FIELDS:
        raise RuntimeError(
            "replay.schema._SANITIZE_METADATA_PROPERTIES is out of sync "
            "with replay.sanitize.SANITIZER_INJECTED_FIELDS: "
            f"declared={sorted(declared)}, actual={sorted(SANITIZER_INJECTED_FIELDS)}. "
            "Update both sides together so v1.2 schema validation keeps "
            "covering every field the sanitizer can inject."
        )


def _harness_payload_schema(model_name: str) -> dict:
    """Return a Pydantic-derived payload schema for a harness lifecycle kind.

    The harness ``$defs`` graph is inlined into the payload subschema so
    each variant is self-contained — readers that want to validate a
    single replay event do not have to resolve cross-document refs.
    Imported lazily so a v1.0 / v1.1 schema generation path never pays
    the harness import cost.

    The Pydantic models use ``extra="forbid"`` (translated to
    ``additionalProperties: false``); we extend the schema with the
    sanitizer's optional projection metadata so a redacted event still
    validates. Genuine model drift remains caught because the explicit
    property list still rejects unknown names.
    """
    from ..host.models import (  # local: avoid eager harness import
        PermissionDecisionEvent,
        SubagentLifecycleEvent,
        ToolLifecycleEvent,
    )

    models = {
        "ToolLifecycleEvent": ToolLifecycleEvent,
        "SubagentLifecycleEvent": SubagentLifecycleEvent,
        "PermissionDecisionEvent": PermissionDecisionEvent,
    }
    model = models[model_name]
    adapter: TypeAdapter[Any] = TypeAdapter(model)
    payload_schema = adapter.json_schema(ref_template="#/$defs/{model}")
    # Inline ``$defs`` (e.g. enum types referenced by the model) so the
    # variant is self-contained — readers don't need a parent document.
    if "$defs" in payload_schema:
        defs = payload_schema.pop("$defs")
        # Resolve refs locally if any subschemas reference one another.
        payload_schema["$defs"] = defs
    _assert_sanitize_metadata_in_sync()
    payload_schema.setdefault("properties", {}).update(_SANITIZE_METADATA_PROPERTIES)
    return payload_schema


# Map from v1.2 harness-projected kind to the Pydantic model whose
# ``model_json_schema()`` defines the canonical payload shape. The
# generator inlines each model's schema into the matching ``oneOf``
# variant; a model field rename / removal therefore surfaces as schema
# drift in CI rather than silently producing replays that fail
# downstream validators.
_HARNESS_PROJECTED_PAYLOADS: Dict[str, str] = {
    EventKind.TOOL_LIFECYCLE: "ToolLifecycleEvent",
    EventKind.SUBAGENT_LIFECYCLE: "SubagentLifecycleEvent",
    EventKind.PERMISSION_DECISION: "PermissionDecisionEvent",
}


def _kind_variant(kind: str) -> dict:
    """One ``oneOf`` branch — pins ``kind`` to a constant string.

    Most kinds keep a lenient payload (``object``, additional properties
    allowed) because their payload shape has not been modeled yet. The
    three v1.2 harness-projected kinds get a typed payload derived from
    :mod:`agentao.host.models` so a model change is caught as drift.
    """
    variant: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "kind": {"const": kind},
        },
        "required": ["kind"],
    }
    model_name = _HARNESS_PROJECTED_PAYLOADS.get(kind)
    if model_name is not None:
        variant["properties"]["payload"] = _harness_payload_schema(model_name)
    return variant


def build_event_schema(version: str) -> dict:
    """Return the JSON Schema document for replay events at ``version``.

    The returned dict is JSON-serializable and deterministic — repeated
    calls produce structurally identical output (kinds are enumerated in
    sorted order).
    """
    kinds = sorted(_kinds_for_version(version))
    envelope = _envelope_properties()
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": f"urn:agentao:schema:replay-event:{version}",
        "title": f"Agentao replay event (v{version})",
        "description": (
            "One JSONL line in a replay file. Source of truth: "
            "agentao/replay/events.py - regenerate via "
            "scripts/write_replay_schema.py."
        ),
        "type": "object",
        "required": [
            "event_id",
            "session_id",
            "instance_id",
            "seq",
            "ts",
            "kind",
            "payload",
        ],
        "additionalProperties": False,
        "properties": {
            **envelope,
            "kind": {
                "type": "string",
                "enum": kinds,
            },
        },
        "oneOf": [_kind_variant(kind) for kind in kinds],
    }


SUPPORTED_VERSIONS: tuple = ("1.0", "1.1", "1.2")


def render(version: str) -> str:
    """Return the canonical text form for the schema at ``version``.

    Trailing newline + 2-space indent + ``sort_keys=True`` make the file
    deterministic on disk so ``git diff --exit-code`` is meaningful.
    """
    return json.dumps(build_event_schema(version), indent=2, sort_keys=True) + "\n"


def write_all(out_dir: Path, versions: Iterable[str] = SUPPORTED_VERSIONS) -> Dict[str, Path]:
    """Write one schema file per version under ``out_dir``.

    Returns a ``{version: path}`` map of files written, in declaration
    order. Creates ``out_dir`` if missing.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}
    for version in versions:
        path = out_dir / f"replay-event-{version}.json"
        path.write_text(render(version), encoding="utf-8")
        written[version] = path
    return written
