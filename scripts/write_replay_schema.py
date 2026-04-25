"""Regenerate replay-event JSON Schema files under ``schemas/``.

Usage::

    uv run python scripts/write_replay_schema.py          # rewrite files
    uv run python scripts/write_replay_schema.py --check  # CI drift check

In ``--check`` mode the script writes nothing; it compares the generator
output to the committed file and exits non-zero on any drift, listing
which schemas need regeneration. This is the contract that turns the
emitter into a real anti-drift mechanism — without it, generation is
just another way for the schemas to fall out of date.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as ``python scripts/write_replay_schema.py`` from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentao.replay.schema import SUPPORTED_VERSIONS, render  # noqa: E402


def _schema_path(version: str) -> Path:
    return ROOT / "schemas" / f"replay-event-{version}.json"


def _check() -> int:
    drift = []
    for version in SUPPORTED_VERSIONS:
        path = _schema_path(version)
        expected = render(version)
        actual = path.read_text(encoding="utf-8") if path.exists() else ""
        if expected != actual:
            drift.append(path)
    if drift:
        print("Replay schema drift detected. Regenerate with:", file=sys.stderr)
        print("    uv run python scripts/write_replay_schema.py", file=sys.stderr)
        for path in drift:
            print(f"  - {path.relative_to(ROOT)}", file=sys.stderr)
        return 1
    print("Replay schemas up to date.")
    return 0


def _write() -> int:
    out_dir = ROOT / "schemas"
    out_dir.mkdir(parents=True, exist_ok=True)
    for version in SUPPORTED_VERSIONS:
        path = _schema_path(version)
        path.write_text(render(version), encoding="utf-8")
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
