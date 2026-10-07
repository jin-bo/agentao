"""Blueprint E — nightly scheduled digest job.

Runs unattended. Produces a digest.md and emits exactly one machine-parseable
line on stdout:

    RESULT: {"path": "digest.md", "items": N}

Exit codes: 0 = ok, 2 = agent error / no RESULT: line.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import traceback
from contextlib import aclosing
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from agentao import Agentao
from agentao.embedding import build_from_environment
from agentao.host import TextDelta


def run() -> None:
    load_dotenv()

    today = date.today().isoformat()
    workdir = Path(__file__).resolve().parent.parent / "runs" / today
    workdir.mkdir(parents=True, exist_ok=True)

    # Copy the skill into the run's workdir so SkillManager picks it up.
    src_skill = Path(__file__).resolve().parent.parent / ".agentao" / "skills" / "daily-digest"
    dst_skill = workdir / ".agentao" / "skills" / "daily-digest"
    if not dst_skill.exists():
        dst_skill.parent.mkdir(parents=True, exist_ok=True)
        dst_skill.symlink_to(src_skill)

    agent = build_from_environment(
        working_directory=workdir,
        max_context_tokens=64_000,
    )
    agent.skill_manager.activate_skill(
        "daily-digest",
        task_description="Produce today's digest per the SKILL.md contract.",
    )

    try:
        reply, tokens_used = asyncio.run(stream_turn(
            agent,
            "Produce today's digest. End with a line "
            "`RESULT: {\"path\": \"...\", \"items\": N}` "
            "so the scheduler can consume it.",
        ))
        parsed = parse_result(reply)
        print(json.dumps({
            "status": "ok",
            "date": today,
            "tokens_est": tokens_used,
            **parsed,
        }))
    finally:
        agent.close()


async def stream_turn(agent: Agentao, prompt: str) -> tuple[str, int]:
    """Run the turn; return its final text and a rough token estimate.

    The estimate counts every streamed delta, narration before tool calls
    included. The reply is the outcome's text, not the joined deltas.
    """
    tokens_used = 0
    async with aclosing(agent.astream(prompt, max_iterations=40)) as stream:
        async for item in stream:
            if isinstance(item, TextDelta):
                tokens_used += len(item.text) // 4
            else:
                return item.text, tokens_used
    raise RuntimeError("the stream ended without a TurnOutcome")


def parse_result(reply: str) -> dict:
    m = re.search(r"RESULT:\s*(\{.*\})\s*$", reply, re.MULTILINE)
    if not m:
        raise SystemExit(f"agent did not emit RESULT: line; got:\n{reply[-500:]}")
    return json.loads(m.group(1))


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)
