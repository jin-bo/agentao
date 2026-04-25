"""Replay schema generator + drift detection.

These tests are the contract that makes the generator useful. The
generator only matters if (a) committed files cannot drift from the
code, and (b) the structural promises of the schema policy hold.
Without these tests, ``schemas/`` is just another way for the format to
fall out of sync.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentao.replay.events import EventKind, SCHEMA_VERSION
from agentao.replay.schema import (
    SUPPORTED_VERSIONS,
    build_event_schema,
    render,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


# ---------------------------------------------------------------------------
# Drift detection: committed files must equal the generator output.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_committed_schema_matches_generator(version: str) -> None:
    path = SCHEMAS_DIR / f"replay-event-{version}.json"
    assert path.exists(), (
        f"{path.relative_to(REPO_ROOT)} is missing. "
        "Run: uv run python scripts/write_replay_schema.py"
    )
    expected = render(version)
    actual = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"Schema drift for v{version}. "
        "Run: uv run python scripts/write_replay_schema.py"
    )


def test_render_is_deterministic() -> None:
    for version in SUPPORTED_VERSIONS:
        first = render(version)
        second = render(version)
        assert first == second


# ---------------------------------------------------------------------------
# Structural invariants of the emitted schema.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_kind_enum_matches_eventkind(version: str) -> None:
    schema = build_event_schema(version)
    enum_kinds = set(schema["properties"]["kind"]["enum"])
    expected = EventKind.V1_0 if version == "1.0" else EventKind.V1_1
    assert enum_kinds == set(expected)


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_oneof_covers_every_kind(version: str) -> None:
    schema = build_event_schema(version)
    enum_kinds = set(schema["properties"]["kind"]["enum"])
    variant_kinds = {
        variant["properties"]["kind"]["const"] for variant in schema["oneOf"]
    }
    assert variant_kinds == enum_kinds


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_envelope_is_strict(version: str) -> None:
    schema = build_event_schema(version)
    assert schema["additionalProperties"] is False
    required = set(schema["required"])
    assert required == {
        "event_id",
        "session_id",
        "instance_id",
        "seq",
        "ts",
        "kind",
        "payload",
    }


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_payload_remains_lenient_until_per_kind_modeling(version: str) -> None:
    """Until per-kind payload schemas land, payload stays lenient.

    This test is intentionally a tripwire: the day we model a kind's
    payload, this test changes shape (or splits per-kind). Better to
    notice that explicitly than to silently regress to a free-form
    payload everywhere.
    """
    schema = build_event_schema(version)
    payload = schema["properties"]["payload"]
    assert payload == {"type": "object", "additionalProperties": True}


def test_v11_is_superset_of_v10() -> None:
    """Backward-compat promise: 1.0 vocabulary survives into 1.1."""
    assert EventKind.V1_0 <= EventKind.V1_1


def test_schema_version_constant_matches_latest_supported() -> None:
    """``SCHEMA_VERSION`` must always name the highest supported version."""
    assert SCHEMA_VERSION == SUPPORTED_VERSIONS[-1]


# ---------------------------------------------------------------------------
# JSON validity: each schema file is parseable and references its own URN.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_committed_schema_is_valid_json(version: str) -> None:
    path = SCHEMAS_DIR / f"replay-event-{version}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["$id"] == f"urn:agentao:schema:replay-event:{version}"
    assert "oneOf" in data
