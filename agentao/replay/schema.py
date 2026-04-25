"""JSON Schema generator for replay events.

Code is the source of truth: :class:`agentao.replay.events.EventKind`
declares the kind vocabulary per schema version, and :class:`ReplayEvent`
declares the envelope shape. This module emits the on-disk schema files
under ``schemas/`` from those declarations.

Design notes:

- The schema is a discriminated union from day one — every kind gets its
  own ``oneOf`` variant pinning ``kind`` to a ``const``. Per-kind payload
  shapes will be tightened in follow-ups; on the first cut every variant
  carries the same lenient ``payload`` (``object``, additional properties
  allowed) so the structural skeleton is in place without blocking
  emission sites that have not been audited yet.
- The envelope is strict (``additionalProperties: false``). Adding a new
  envelope field is a breaking change; payload extensibility is what
  makes the format forward-compatible.
- Each schema version pins a separate file. v1.0 schema accepts only the
  v1.0 kind enum; v1.1 schema accepts the full v1.1 vocabulary. A 1.0
  replay file must keep validating against ``schemas/replay-event-1.0.json``
  forever — that is the backward-compatibility promise documented in
  ``docs/replay/schema-policy.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, FrozenSet, Iterable

from .events import EventKind


SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _kinds_for_version(version: str) -> FrozenSet[str]:
    if version == "1.0":
        return EventKind.V1_0
    if version == "1.1":
        return EventKind.V1_1
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


def _kind_variant(kind: str) -> dict:
    """One ``oneOf`` branch — pins ``kind`` to a constant string.

    Payload remains lenient at this stage. When per-kind payload shapes
    are added, this function (or a successor that takes a per-kind
    payload schema) is the only place that needs to change.
    """
    return {
        "type": "object",
        "properties": {
            "kind": {"const": kind},
        },
        "required": ["kind"],
    }


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


SUPPORTED_VERSIONS: tuple = ("1.0", "1.1")


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
