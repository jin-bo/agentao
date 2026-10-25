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
    FROZEN_SCHEMAS,
    LIVE_VERSIONS,
    SUPPORTED_VERSIONS,
    build_event_schema,
    render,
    schema_digest,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


def _schema(version: str) -> dict:
    """The schema at ``version``: the published file once it is frozen."""
    if version in FROZEN_SCHEMAS:
        path = SCHEMAS_DIR / f"replay-event-{version}.json"
        return json.loads(path.read_text(encoding="utf-8"))
    return build_event_schema(version)


# ---------------------------------------------------------------------------
# Drift detection: committed files must equal the generator output.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", LIVE_VERSIONS)
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


@pytest.mark.parametrize("version", sorted(FROZEN_SCHEMAS))
def test_a_published_schema_never_changes(version: str) -> None:
    """The typed payloads are closed and come from the live host models, so
    regenerating an old version would add fields it never had. A host that
    saved the published file must keep getting that contract (#550)."""
    path = SCHEMAS_DIR / f"replay-event-{version}.json"
    assert schema_digest(path.read_text(encoding="utf-8")) == FROZEN_SCHEMAS[version]


@pytest.mark.parametrize("version", sorted(FROZEN_SCHEMAS))
def test_a_frozen_version_is_not_rebuilt(version: str) -> None:
    """A rebuild would hand out a second contract under the same name."""
    with pytest.raises(ValueError, match="frozen"):
        build_event_schema(version)


def test_only_the_current_version_is_live() -> None:
    """Moving ``SCHEMA_VERSION`` on must freeze the version it leaves."""
    assert LIVE_VERSIONS == (SCHEMA_VERSION,)


def test_the_current_usage_payload_has_reasoning_tokens() -> None:
    """1.5 adds it. 1.4, as published, rejects it."""
    def usage_props(schema: dict) -> dict:
        variant = next(
            v for v in schema["oneOf"]
            if v["properties"]["kind"]["const"] == EventKind.SUBAGENT_LIFECYCLE
        )
        usage = variant["properties"]["payload"]["properties"]["usage"]["anyOf"][0]
        assert usage["additionalProperties"] is False
        return usage["properties"]

    assert "reasoning_tokens" in usage_props(_schema("1.5"))
    published = json.loads((SCHEMAS_DIR / "replay-event-1.4.json").read_text(encoding="utf-8"))
    assert "reasoning_tokens" not in usage_props(published)


def test_the_current_lifecycle_payload_has_parent_tool_call_id() -> None:
    """1.6 adds it. 1.5, as published, rejects it (#580)."""
    def payload(schema: dict) -> dict:
        variant = next(
            v for v in schema["oneOf"]
            if v["properties"]["kind"]["const"] == EventKind.SUBAGENT_LIFECYCLE
        )
        payload = variant["properties"]["payload"]
        assert payload["additionalProperties"] is False
        return payload["properties"]

    assert "parent_tool_call_id" in payload(build_event_schema(SCHEMA_VERSION))
    assert "parent_tool_call_id" not in payload(_schema("1.5"))


def test_render_is_deterministic() -> None:
    for version in LIVE_VERSIONS:
        first = render(version)
        second = render(version)
        assert first == second


# ---------------------------------------------------------------------------
# Structural invariants of the emitted schema.
# ---------------------------------------------------------------------------


_VERSION_KINDS = {
    "1.0": EventKind.V1_0,
    "1.1": EventKind.V1_1,
    "1.2": EventKind.V1_2,
    "1.3": EventKind.V1_3,
    "1.4": EventKind.V1_4,
    "1.5": EventKind.V1_5,
    "1.6": EventKind.V1_6,
}


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_kind_enum_matches_eventkind(version: str) -> None:
    schema = _schema(version)
    enum_kinds = set(schema["properties"]["kind"]["enum"])
    expected = _VERSION_KINDS[version]
    assert enum_kinds == set(expected)


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_oneof_covers_every_kind(version: str) -> None:
    schema = _schema(version)
    enum_kinds = set(schema["properties"]["kind"]["enum"])
    variant_kinds = {
        variant["properties"]["kind"]["const"] for variant in schema["oneOf"]
    }
    assert variant_kinds == enum_kinds


@pytest.mark.parametrize("version", SUPPORTED_VERSIONS)
def test_envelope_is_strict(version: str) -> None:
    schema = _schema(version)
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
    schema = _schema(version)
    payload = schema["properties"]["payload"]
    assert payload == {"type": "object", "additionalProperties": True}


def test_v11_is_superset_of_v10() -> None:
    """Backward-compat promise: 1.0 vocabulary survives into 1.1."""
    assert EventKind.V1_0 <= EventKind.V1_1


def test_v12_is_superset_of_v11() -> None:
    """Backward-compat promise: 1.1 vocabulary survives into 1.2."""
    assert EventKind.V1_1 <= EventKind.V1_2


def test_v12_adds_only_harness_projection_kinds() -> None:
    """v1.2's net-new kinds are the three harness-projection kinds."""
    assert EventKind.V1_2_NEW == frozenset({
        EventKind.TOOL_LIFECYCLE,
        EventKind.SUBAGENT_LIFECYCLE,
        EventKind.PERMISSION_DECISION,
    })


def test_v12_kinds_have_typed_payloads() -> None:
    """The v1.2 harness-projection variants must carry a typed payload schema.

    Drift tripwire: a maintainer who removes the per-kind payload
    derivation in :mod:`agentao.replay.schema` must update this test
    too — silently regressing the payload to ``{}`` would defeat the
    schema as an audit-validation contract.
    """
    schema = _schema("1.2")
    variants = {
        v["properties"]["kind"]["const"]: v for v in schema["oneOf"]
    }
    for kind in EventKind.V1_2_NEW:
        payload = variants[kind]["properties"].get("payload")
        assert payload is not None, f"v1.2 kind {kind!r} has no typed payload"
        # Pydantic-derived payloads always have ``properties`` and at
        # least the ``event_type`` discriminator field.
        assert "properties" in payload
        assert "event_type" in payload["properties"]


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
