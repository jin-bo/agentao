"""Regenerate ``agentao.host`` JSON Schema snapshots under ``docs/schema/``.

Usage::

    uv run python scripts/write_host_schema.py          # rewrite files
    uv run python scripts/write_host_schema.py --check  # CI drift check

In ``--check`` mode the script writes nothing; it compares the generator
output to the committed file and exits non-zero on any drift, listing
which schemas need regeneration. Mirrors ``scripts/write_replay_schema.py``
so host schemas get the same fast-fail anti-drift signal in CI Job 0
instead of waiting for the test matrix to surface a Pydantic model
divergence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as ``python scripts/write_host_schema.py`` from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentao.host.schema import (  # noqa: E402
    export_host_acp_json_schema,
    export_host_event_json_schema,
    normalized_schema_json,
)


# (snapshot-name, exporter) pairs. Add new entries here when a new
# host schema surface is frozen for release.
_SNAPSHOTS = (
    ("host.events.v1.json", export_host_event_json_schema),
    ("host.acp.v1.json", export_host_acp_json_schema),
)


def _schema_path(name: str) -> Path:
    return ROOT / "docs" / "schema" / name


def _check() -> int:
    drift = []
    for name, exporter in _SNAPSHOTS:
        path = _schema_path(name)
        expected = normalized_schema_json(exporter())
        actual = path.read_text(encoding="utf-8") if path.exists() else ""
        if expected != actual:
            drift.append(path)
    if drift:
        print("Host schema drift detected. Regenerate with:", file=sys.stderr)
        print("    uv run python scripts/write_host_schema.py", file=sys.stderr)
        for path in drift:
            print(f"  - {path.relative_to(ROOT)}", file=sys.stderr)
        return 1
    print("Host schemas up to date.")
    return 0


def _write() -> int:
    out_dir = ROOT / "docs" / "schema"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, exporter in _SNAPSHOTS:
        path = _schema_path(name)
        path.write_text(normalized_schema_json(exporter()), encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if committed schemas drift from the generator output.",
    )
    args = parser.parse_args(argv)
    return _check() if args.check else _write()


if __name__ == "__main__":
    raise SystemExit(main())
