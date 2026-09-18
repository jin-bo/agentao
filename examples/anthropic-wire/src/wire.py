"""Embed Agentao over Anthropic's native Messages API.

Agentao speaks OpenAI Chat Completions by default. ``api_format`` names a
different wire protocol for the endpoint — it is **never inferred** from the
base URL, the provider name or the model name, so an Anthropic model behind an
OpenAI-compatible gateway keeps working on the default until you say otherwise.

Three things differ on this wire, and all three are in ``build_agent``:

* ``base_url`` is the API **root** (``https://api.anthropic.com``) — the SDK
  appends ``/v1/messages`` itself.
* ``extra_body`` keys are per-protocol. Thinking depth here is
  ``output_config.effort``; ``reasoning_effort`` is rejected by this API.
* ``temperature`` is not sent at all (the ``anthropic`` SDK has no such
  parameter on ``messages.create``).

Run it live::

    ANTHROPIC_API_KEY=sk-ant-... uv run python -m src.wire "Say hello in five words."

Without a key it exits with instructions; the offline smoke is ``tests/``.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from agentao import Agentao

ANTHROPIC_API_ROOT = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-5"


def build_agent(
    working_directory: Path,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = ANTHROPIC_API_ROOT,
    effort: Optional[str] = None,
    prompt_cache: bool = False,
    logger: Optional[logging.Logger] = None,
) -> Agentao:
    """One ``Agentao`` on the ``anthropic-messages`` wire.

    ``effort`` turns on extended thinking the way current models take it
    (``adaptive`` + ``output_config.effort``). ``prompt_cache`` opts into
    explicit cache breakpoints — off by default because whether an endpoint
    honours them is the endpoint's to say. ``logger`` is the embedding host's
    own: given one, Agentao installs no file handler; left ``None`` it opens
    ``<working_directory>/agentao.log`` and keeps it open — which a temporary
    working directory cannot survive on Windows (see ``main``).
    """
    extra_body: Optional[Dict[str, Any]] = None
    if effort is not None:
        extra_body = {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
    return Agentao(
        working_directory=working_directory,
        api_key=api_key,
        base_url=base_url,
        model=model,
        api_format="anthropic-messages",  # keyword-only; sub-agents inherit it
        logger=logger,
        extra_body=extra_body,
        prompt_cache="anthropic" if prompt_cache else None,
    )


def usage_report(agent: Agentao) -> Dict[str, int]:
    """What the session's requests cost, in the four quantities a price list needs.

    ``prompt_tokens`` is the **whole** input on this wire. The two cache counts
    are parts *of* it, billed at other rates — so cost is
    ``(prompt - read - write) × input + read × cache_read + write × cache_write
    + completion × output``. Agentao reports the quantities; the prices are yours.
    """
    llm = agent.llm
    return {
        "prompt_tokens": llm.total_prompt_tokens,
        "completion_tokens": llm.total_completion_tokens,
        "cache_read_tokens": llm.total_cache_read_tokens,
        "cache_creation_tokens": llm.total_cache_creation_tokens,
    }


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set. Export it to run this example live; "
              "`uv run pytest tests/` exercises the same code offline.")
        return 2
    prompt = " ".join(argv) or "Say hello in five words."
    with tempfile.TemporaryDirectory(prefix="agentao-anthropic-wire-") as workdir:
        # A logger of our own, because ``workdir`` is deleted on the way out:
        # an open ``agentao.log`` inside it is a PermissionError on Windows.
        agent = build_agent(Path(workdir), api_key=api_key,
                            model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
                            logger=logging.getLogger("anthropic_wire_example"))
        try:
            print(agent.chat(prompt))
            print(usage_report(agent))
        finally:
            agent.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
