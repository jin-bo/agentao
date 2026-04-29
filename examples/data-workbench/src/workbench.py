"""Blueprint D — data analyst workbench.

Run once to seed a fake parquet dataset and answer a natural-language question:

    uv run python -m src.workbench "which 3 products had the largest revenue?"

The agent runs `duckdb` / `python` via the shell tool inside its per-user
workdir, then prints `[CHART] <path>` when a matplotlib PNG is produced.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from agentao.embedding import build_from_environment
from agentao.transport import SdkTransport
from agentao.transport.events import EventType


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WORKDIR = ROOT / "workspaces" / "demo"

CHART_RE = re.compile(r"\[CHART\]\s+(\S+)")


def seed_fake_data() -> None:
    """Create a small parquet file so the agent has something to query."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / "sales.parquet"
    if out.exists():
        return
    import pandas as pd
    df = pd.DataFrame({
        "product": ["Widget", "Gadget", "Sprocket", "Flange", "Widget",
                    "Gadget", "Sprocket", "Flange", "Widget", "Gadget"],
        "region":  ["NA", "NA", "EU", "EU", "APAC", "APAC", "NA", "EU", "NA", "APAC"],
        "units":   [120, 45, 30, 80, 200, 60, 25, 90, 150, 70],
        "revenue": [2400, 1800, 900, 2400, 4000, 2400, 750, 2700, 3000, 2800],
    })
    df.to_parquet(out)


def prepare_workspace() -> Path:
    """Copy (symlink) the skills into the per-session workdir."""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    src_skills_root = ROOT / ".agentao" / "skills"
    dst_skills_root = WORKDIR / ".agentao" / "skills"
    dst_skills_root.mkdir(parents=True, exist_ok=True)

    for skill in ("duckdb-analyst", "matplotlib-charts"):
        dst = dst_skills_root / skill
        if not dst.exists():
            dst.symlink_to(src_skills_root / skill)

    # Make the data dir visible as ./data inside the workdir.
    data_link = WORKDIR / "data"
    if not data_link.exists():
        data_link.symlink_to(DATA_DIR)

    return WORKDIR


def run(question: str) -> None:
    load_dotenv()
    os.environ.setdefault("MPLBACKEND", "Agg")

    seed_fake_data()
    workdir = prepare_workspace()

    charts: list[str] = []

    def on_event(ev):
        if ev.type is EventType.LLM_TEXT:
            chunk = ev.data.get("chunk", "")
            for m in CHART_RE.finditer(chunk):
                charts.append(m.group(1))

    transport = SdkTransport(on_event=on_event)
    agent = build_from_environment(working_directory=workdir, transport=transport)

    agent.skill_manager.activate_skill(
        "duckdb-analyst",
        task_description=f"Answer the analytical question: {question}",
    )
    # Pre-activate the chart skill too so the agent can produce a PNG
    # without a second LLM round-trip just to discover the skill exists.
    agent.skill_manager.activate_skill(
        "matplotlib-charts",
        task_description="Render a single PNG summarizing the answer.",
    )

    try:
        reply = agent.chat(question, max_iterations=30)
        print(reply)
        if charts:
            print("\nGenerated charts:")
            for c in charts:
                resolved = workdir / c if not Path(c).is_absolute() else Path(c)
                print(f"  - {resolved}")
    finally:
        agent.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "question",
        nargs="?",
        default="Which 3 products had the largest total revenue? Render a bar chart.",
        help="Natural-language question.",
    )
    args = parser.parse_args()
    run(args.question)


if __name__ == "__main__":
    main()
