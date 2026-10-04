"""Measure what tool definitions cost per request, as agentao declares them.

``docs/design/tool-search.md`` defers ``tool_search`` until tool definitions
put measurable pressure on the token budget. This measures that cost so the
trigger can be checked against numbers, not guessed at.

It makes **no model request** and spends nothing. It builds an ``Agentao`` in a
temporary directory twice: once with no MCP servers, once with the servers in
``SERVERS`` declared in that directory's ``.agentao/mcp.json``. Each time it
counts the tool list that ``ToolRegistry.to_openai_format()`` returns, which is
what goes on the wire, so MCP tools are counted in their ``mcp_<server>_<tool>``
wrappers. The servers are public packages fetched by ``npx`` / ``uvx`` at the
pinned versions below, so the first run needs network access and node.

Tokens are ``tiktoken`` ``o200k_base`` over the JSON of each definition — the
encoding ``ContextManager`` uses for current OpenAI models. Other providers
tokenize differently and add their own per-tool framing, so read the numbers as
relative sizes, not as a bill.

Usage::

    uv run python scripts/measure_tool_tokens.py              # built-ins + all servers
    uv run python scripts/measure_tool_tokens.py --builtin-only
    uv run python scripts/measure_tool_tokens.py --json out.json

What it cannot tell you: whether the model picks the wrong tool more often as
the list grows (the second trigger). That needs an eval against a real model.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import tiktoken

from agentao.agent import Agentao

ENC = tiktoken.get_encoding("o200k_base")

#: Public MCP servers, pinned. ``{wd}`` is replaced with the temporary
#: working directory. ``server-github`` is the archived npm server; GitHub's
#: current server is a separate Go binary with more tools, so this is a floor.
SERVERS: Dict[str, Dict[str, Any]] = {
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem@2026.8.31", "{wd}"]},
    "memory": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory@2026.8.31"]},
    "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github@2025.4.8"]},
    "playwright": {"command": "npx", "args": ["-y", "@playwright/mcp@0.0.83"]},
    "seqthink": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking@2026.8.31"]},
    "fetch": {"command": "uvx", "args": ["mcp-server-fetch==2026.8.18"]},
}


def tokens(obj: Any) -> int:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return len(ENC.encode(text))


def measure(label: str, servers: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        config = {}
        for name, entry in servers.items():
            entry = json.loads(json.dumps(entry).replace("{wd}", str(wd)))
            config[name] = {**entry, "trust": True, "timeout": {"startup": 180}}
        (wd / ".agentao").mkdir()
        (wd / ".agentao" / "mcp.json").write_text(json.dumps({"mcpServers": config}))
        agent = Agentao(
            working_directory=wd, api_key="unused",
            base_url="https://unused.invalid/v1", model="gpt-4o",
        )
        try:
            tools = agent.tools.to_openai_format()
            per_tool = {t["function"]["name"]: tokens(t) for t in tools}
            status = agent.mcp_manager.get_server_status() if agent.mcp_manager else []
            system = tokens(agent._build_system_prompt())
        finally:
            agent.close()

    groups: Dict[str, Dict[str, int]] = {}
    for name, n in per_tool.items():
        owner = next((s for s in servers if name.startswith(f"mcp_{s}_")), "builtin")
        group = groups.setdefault(owner, {"tools": 0, "tokens": 0})
        group["tools"] += 1
        group["tokens"] += n
    mcp = [n for name, n in per_tool.items() if name.startswith("mcp_")]
    return {
        "label": label,
        "tool_count": len(tools),
        "tools_tokens": tokens(tools),
        "system_prompt_tokens": system,
        "mcp_tool_count": len(mcp),
        "mcp_tool_median_tokens": statistics.median(mcp) if mcp else 0,
        "groups": groups,
        "per_tool": per_tool,
        "servers": [
            {"name": s.get("name"), "status": s.get("status"), "tools": s.get("tools")}
            for s in status
        ],
    }


def report(result: Dict[str, Any]) -> None:
    print(
        f"{result['label']}: {result['tool_count']} tools, "
        f"{result['tools_tokens']:,} tool tokens, "
        f"system prompt {result['system_prompt_tokens']:,} tokens"
    )
    for name, g in sorted(result["groups"].items(), key=lambda kv: -kv[1]["tokens"]):
        print(f"  {name:<12} {g['tools']:>3} tools  {g['tokens']:>6,} tokens")
    for s in result["servers"]:
        if s["status"] != "connected":
            print(f"  ! {s['name']}: {s['status']}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--builtin-only", action="store_true")
    parser.add_argument("--json", type=Path, help="write the full per-tool results here")
    args = parser.parse_args()

    results = [measure("builtin", {})]
    if not args.builtin_only:
        results.append(measure("mcp", SERVERS))
    for result in results:
        report(result)
    if args.json:
        args.json.write_text(json.dumps(results, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
