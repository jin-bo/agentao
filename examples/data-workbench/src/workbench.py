"""Blueprint D — data analyst workbench.

Run once to seed a fake parquet dataset and answer a natural-language question:

    uv run python -m src.workbench "which 3 products had the largest revenue?"

The agent runs `duckdb` / `python` via the shell tool inside its per-user
workdir, then prints `[CHART] <path>` when a matplotlib PNG is produced.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
from contextlib import aclosing
from pathlib import Path

from dotenv import load_dotenv

from agentao import Agentao
from agentao.embedding import build_from_environment
from agentao.host import TextDelta


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WORKDIR = ROOT / "workspaces" / "demo"

CHART_RE = re.compile(r"\[CHART\]\s+(\S+?\.png)")  # ends at .png: joined deltas from two LLM calls have no separator


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

    agent = build_from_environment(working_directory=workdir)

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
        reply, charts = asyncio.run(stream_turn(agent, question))
        print(reply)
        if charts:
            print("\nGenerated charts:")
            for c in charts:
                resolved = workdir / c if not Path(c).is_absolute() else Path(c)
                print(f"  - {resolved}")
    finally:
        agent.close()


async def stream_turn(agent: Agentao, question: str) -> tuple[str, list[str]]:
    """Run the turn; return its final text and the chart paths it announced.

    Charts are read from everything the model streamed, because a ``[CHART]``
    line can come in narration before a later tool call, which is not part of
    the final text. The deltas are joined first: one line can span chunks.
    """
    streamed: list[str] = []
    async with aclosing(agent.astream(question, max_iterations=30)) as stream:
        async for item in stream:
            if isinstance(item, TextDelta):
                streamed.append(item.text)
            else:
                charts = [m.group(1) for m in CHART_RE.finditer("".join(streamed))]
                return item.text, charts
    raise RuntimeError("the stream ended without a TurnOutcome")


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
