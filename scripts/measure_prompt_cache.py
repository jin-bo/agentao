"""Measure what prompt caching is worth on a real endpoint, arm by arm.

The comparison recorded in ``docs/design/llm-api-adapters.md`` (*Live results*,
2026-09-18) was run once by hand and its script was not kept, so the numbers
could not be reproduced and the two questions it left open could not be asked
of another endpoint. This is that script, kept.

**It spends money.** Without ``--yes`` it prints the plan and makes no request.

Three arms, one scripted session each (real tool calls over three seeded
files, so the prompt grows the way an agent's does)::

    a   Chat Completions, no breakpoints          stage 0a alone
    b   Chat Completions, 3 cache_control marks   stage 0a + 0b
    c   anthropic-messages, native breakpoints    stage 1

Each arm gets a fresh nonce in the **first** tool definition, so no arm reads a
cache another arm wrote — the first byte of the cached prefix differs.

Usage::

    # Anthropic's own API, all three arms (what the design doc recorded):
    ANTHROPIC_API_KEY=sk-ant-... uv run python scripts/measure_prompt_cache.py --yes

    # A third-party gateway: does it honour the markers *and* report them?
    GATEWAY_KEY=... uv run python scripts/measure_prompt_cache.py --yes \\
        --arms a,b --api-key-env GATEWAY_KEY \\
        --base-url-compat https://gateway.example/v1 --model some-model

    # Where does an active skill's body belong? Activate one mid-session and
    # read the per-request rows on either side of it:
    ... --arms c --activate-skill pdf --at-turn 4

What it cannot tell you: on an endpoint whose ``usage`` carries no cache fields
(Anthropic's OpenAI-compatible endpoint is one), arms **a** and **b** come back
identical here and differ only on the bill. The report says so rather than
printing a zero that reads as "no caching".

Cost is in *uncached-input-token units*: ``uncached + W × written + R × read``
with ``--write-rate`` / ``--read-rate`` (defaults 1.25 / 0.1, Anthropic's 5-minute
cache). No currency: prices are the reader's.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agentao import Agentao
from agentao.tools.base import Tool
from agentao.transport import EventType

ARMS: Dict[str, Dict[str, Any]] = {
    "a": {"label": "0a only", "api_format": "openai-completions", "prompt_cache": None},
    "b": {"label": "0a + 0b", "api_format": "openai-completions", "prompt_cache": "anthropic"},
    "c": {"label": "native", "api_format": "anthropic-messages", "prompt_cache": "anthropic"},
}

SEED_FILES = {
    "inventory.md": "# Inventory\n\n" + "\n".join(
        f"- item-{i:03d}: {'widget' if i % 3 else 'gasket'} batch {i * 7 % 50}, "
        f"shelf {chr(65 + i % 6)}{i % 40}" for i in range(180)),
    "policy.md": "# Returns policy\n\n" + "\n\n".join(
        f"## Clause {i}\nA return under clause {i} must be filed within {10 + i} days, "
        f"carries a {i % 5}% restocking fee, and is approved by tier-{1 + i % 3} staff."
        for i in range(1, 60)),
    "orders.csv": "order_id,item,qty,status\n" + "\n".join(
        f"{1000 + i},item-{i * 3 % 180:03d},{1 + i % 9},{'open' if i % 4 else 'returned'}"
        for i in range(220)),
}

TURNS = [
    "Read inventory.md and tell me which shelf holds item-042.",
    "Now read policy.md. How many days does clause 17 allow?",
    "Read orders.csv. How many orders are in status 'returned'?",
    "Which item appears in order 1100, and which shelf is it on?",
    "Under which clause would a return filed after 25 days fall? Name one.",
    "Summarise the three files in three sentences.",
    "Given all of that, what is one inconsistency you would check first?",
]


#: Sorts ahead of every built-in (``activate_skill`` is the first of those).
#: ``ToolRegistry.to_openai_format`` emits tools **alphabetically by name** —
#: for cache stability — so the name, not registration order, is what puts the
#: nonce at the first byte of the cached prefix. A nonce further down leaves
#: every definition before it byte-identical across arms, which an automatic
#: prefix cache would happily share.
NONCE_TOOL_NAME = "aaa_measurement_marker"


class _CacheNonce(Tool):
    """A tool that does nothing, whose description isolates this arm's cache."""

    def __init__(self, nonce: str) -> None:
        self._nonce = nonce

    @property
    def name(self) -> str:
        return NONCE_TOOL_NAME

    @property
    def description(self) -> str:
        return f"Measurement marker {self._nonce}. Never call this tool."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> str:
        return "This tool does nothing."


def _default_agent(workdir: Path, *, api_key: str, base_url: str, model: str,
                   arm: Dict[str, Any], nonce: str) -> Agentao:
    return Agentao(
        working_directory=workdir, api_key=api_key, base_url=base_url, model=model,
        api_format=arm["api_format"], prompt_cache=arm["prompt_cache"],
        extra_tools=[_CacheNonce(nonce)],
    )


def cost_units(row: Dict[str, int], write_rate: float, read_rate: float) -> float:
    uncached = row["prompt_tokens"] - row["cache_read_tokens"] - row["cache_creation_tokens"]
    return (uncached + write_rate * row["cache_creation_tokens"]
            + read_rate * row["cache_read_tokens"])


def run_arm(
    key: str, *, api_key: str, base_url: str, model: str,
    turns: List[str] = TURNS, make_agent: Callable[..., Agentao] = _default_agent,
    activate_skill: Optional[str] = None, at_turn: Optional[int] = None,
    write_rate: float = 1.25, read_rate: float = 0.1,
) -> Dict[str, Any]:
    """Run one arm's session and return its per-request rows and totals."""
    arm = ARMS[key]
    requests: List[Dict[str, Any]] = []
    activation: Optional[Dict[str, Any]] = None
    with tempfile.TemporaryDirectory(prefix=f"agentao-cache-arm-{key}-") as tmp:
        workdir = Path(tmp)
        for name, text in SEED_FILES.items():
            (workdir / name).write_text(text, encoding="utf-8")
        agent = make_agent(workdir, api_key=api_key, base_url=base_url, model=model,
                           arm=arm, nonce=uuid.uuid4().hex)
        real_emit = agent.transport.emit
        turn_no = 0

        def emit(event: Any) -> Any:
            if event.type == EventType.LLM_CALL_COMPLETED:
                data = event.data
                requests.append({
                    "turn": turn_no, "status": data.get("status"),
                    "prompt_tokens": data.get("prompt_tokens") or 0,
                    "completion_tokens": data.get("completion_tokens") or 0,
                    "cache_read_tokens": data.get("cache_read_tokens") or 0,
                    "cache_creation_tokens": data.get("cache_creation_tokens") or 0,
                })
            return real_emit(event)

        agent.transport.emit = emit
        try:
            for turn_no, prompt in enumerate(turns, start=1):
                if activate_skill and turn_no == at_turn:
                    answer = agent.skill_manager.activate_skill(activate_skill, "measurement")
                    activation = {
                        "skill": activate_skill, "before_turn": turn_no,
                        # What the design doc asks to be recorded: how much
                        # history a prefix placement would have re-written.
                        "history_messages": len(agent.messages),
                        "history_tokens_est": agent.context_manager.estimate_tokens(agent.messages),
                        "requests_so_far": len(requests),
                        "activated": not str(answer).startswith("Error"),
                    }
                agent.chat(prompt)
        finally:
            agent.close()

    totals = {name: sum(row[name] for row in requests) for name in (
        "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_creation_tokens")}
    # An endpoint that reports no cache fields is not an endpoint that cached
    # nothing. Say which, or a zero reads as a measurement.
    reported = totals["cache_read_tokens"] > 0 or totals["cache_creation_tokens"] > 0
    return {
        "arm": key, "label": arm["label"], "api_format": arm["api_format"],
        "prompt_cache": arm["prompt_cache"], "model": model, "base_url": base_url,
        "requests": requests, "totals": totals, "cache_fields_reported": reported,
        "input_cost_units": (round(sum(cost_units(r, write_rate, read_rate) for r in requests))
                             if reported else None),
        "activation": activation,
    }


def render(results: List[Dict[str, Any]]) -> str:
    lines = ["| Arm | Wire | Requests | Prompt tokens | Cache written | Cache read | Input cost units |",
             "|---|---|---|---|---|---|---|"]
    # The saving is each arm against **its own** full price, never against
    # another arm. The model decides how many tool rounds a turn takes, and it
    # does not decide the same way twice: the first live run of this script
    # had one arm make 19 requests and the other two 11, so a cross-arm delta
    # measured the model's mood, not the cache. An arm whose endpoint reported
    # no cache fields is priced at the full rate — an upper bound, not a
    # finding that nothing was cached — and says so.
    for result in results:
        totals = result["totals"]
        full = totals["prompt_tokens"]
        if result["cache_fields_reported"]:
            written, read = f"{totals['cache_creation_tokens']:,}", f"{totals['cache_read_tokens']:,}"
            cost = f"{result['input_cost_units']:,}"
            if full:
                cost += f" ({(result['input_cost_units'] - full) / full:+.0%} vs its own full price)"
        else:
            written = read = "not reported"
            cost = f"≤ {full:,} (see the bill)"
        lines.append(
            f"| {result['arm']} — {result['label']} | `{result['api_format']}` | "
            f"{len(result['requests'])} | {full:,} | {written} | {read} | {cost} |")
    counts = [(result["arm"], len(result["requests"])) for result in results]
    if len({n for _arm, n in counts}) > 1:
        lines.append(
            "\nRequest counts differ between arms ("
            + ", ".join(f"{arm}: {n}" for arm, n in counts)
            + ") — the model took a different number of tool rounds. Totals are not "
            "comparable across those arms; the percentage in each row is.")
    for result in results:
        act = result.get("activation")
        if act:
            lines.append(
                f"\nArm {result['arm']}: `{act['skill']}` "
                f"{'activated' if act['activated'] else 'NOT activated'} before turn "
                f"{act['before_turn']}, with {act['history_messages']} messages "
                f"(~{act['history_tokens_est']:,} tokens) of history and "
                f"{act['requests_so_far']} requests already sent.")
    return "\n".join(lines)


def _parse(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--arms", default="a,b,c", help="comma-separated subset of a,b,c")
    parser.add_argument("--api-key-env", default="ANTHROPIC_API_KEY",
                        help="name of the environment variable holding the key")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--base-url-compat", default="https://api.anthropic.com/v1",
                        help="Chat Completions endpoint, for arms a and b")
    parser.add_argument("--base-url-native", default="https://api.anthropic.com",
                        help="Messages API root, for arm c")
    parser.add_argument("--activate-skill", default=None)
    parser.add_argument("--at-turn", type=int, default=None)
    parser.add_argument("--write-rate", type=float, default=1.25)
    parser.add_argument("--read-rate", type=float, default=0.1)
    parser.add_argument("--out", default=None, help="write the full JSON result here")
    parser.add_argument("--yes", action="store_true", help="actually send requests (spends money)")
    args = parser.parse_args(argv)
    args.arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    unknown = [arm for arm in args.arms if arm not in ARMS]
    if unknown or not args.arms:
        parser.error(f"--arms takes a subset of {','.join(ARMS)}; got {unknown or 'nothing'}")
    if bool(args.activate_skill) != (args.at_turn is not None):
        parser.error("--activate-skill and --at-turn go together")
    if args.at_turn is not None and not 1 <= args.at_turn <= len(TURNS):
        parser.error(f"--at-turn must be 1..{len(TURNS)}")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse(argv)
    if not args.yes:
        print(f"Plan: arms {','.join(args.arms)} × {len(TURNS)} user turns on {args.model} "
              f"(about {2 * len(TURNS) - 1} requests per arm, prompt growing past ~15k tokens).\n"
              f"  a, b → {args.base_url_compat}\n  c    → {args.base_url_native}\n"
              f"Key from ${args.api_key_env}. This spends money; re-run with --yes to send.")
        return 0
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        print(f"${args.api_key_env} is not set.", file=sys.stderr)
        return 2
    results = []
    for key in args.arms:
        base_url = args.base_url_native if key == "c" else args.base_url_compat
        print(f"arm {key} ({ARMS[key]['label']}) …", file=sys.stderr)
        results.append(run_arm(
            key, api_key=api_key, base_url=base_url, model=args.model,
            activate_skill=args.activate_skill, at_turn=args.at_turn,
            write_rate=args.write_rate, read_rate=args.read_rate))
    print(render(results))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nfull result: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
