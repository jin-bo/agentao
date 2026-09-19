"""Embed Agentao over OpenAI's Responses API.

Agentao speaks OpenAI Chat Completions by default. ``api_format`` names a
different wire protocol for the endpoint — it is **never inferred** from the
base URL, the provider name or the model name, so the same ``api.openai.com``
key keeps working on Chat Completions until you say otherwise.

What differs on this wire, and where it shows in ``build_agent``:

* ``base_url`` is the one Chat Completions takes; the SDK appends
  ``/responses``. No new dependency — it is the same ``openai`` SDK.
* ``extra_body`` keys are per-protocol. Thinking depth here is
  ``reasoning.effort``; a top-level ``reasoning_effort`` is rejected by this
  API. Reasoning *summaries* — the only reasoning text this API shows — are
  off unless asked for.
* Requests are **stateless** (``store: false``): Agentao's history stays the
  single source of truth, and a reasoning model's reasoning is carried across
  requests as encrypted items on the assistant message.

Run it live::

    OPENAI_API_KEY=sk-... uv run python -m src.wire "Say hello in five words."

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

OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.4"


def build_agent(
    working_directory: Path,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = OPENAI_BASE_URL,
    effort: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Agentao:
    """One ``Agentao`` on the ``openai-responses`` wire.

    ``effort`` sets ``reasoning.effort`` and asks for summaries beside it, so
    there is reasoning text to show. ``logger`` is the embedding host's own:
    given one, Agentao installs no file handler; left ``None`` it opens
    ``<working_directory>/agentao.log`` and keeps it open — which a temporary
    working directory cannot survive on Windows (see ``main``).
    """
    extra_body: Optional[Dict[str, Any]] = None
    if effort is not None:
        extra_body = {"reasoning": {"effort": effort, "summary": "auto"}}
    return Agentao(
        working_directory=working_directory,
        api_key=api_key,
        base_url=base_url,
        model=model,
        api_format="openai-responses",  # keyword-only; sub-agents inherit it
        logger=logger,
        extra_body=extra_body,
    )


def usage_report(agent: Agentao) -> Dict[str, int]:
    """What the session's requests cost, in the quantities a price list needs.

    ``prompt_tokens`` is the **whole** input; the two cache counts are parts
    *of* it, billed at other rates. ``cache_creation_tokens`` is the API's
    ``cache_write_tokens``, which the ``openai`` SDK reads from 3.x on — on an
    older SDK, or an endpoint that does not state it, it stays 0.
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
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not set. Export it to run this example live; "
              "`uv run pytest tests/` exercises the same code offline.")
        return 2
    prompt = " ".join(argv) or "Say hello in five words."
    with tempfile.TemporaryDirectory(prefix="agentao-openai-responses-wire-") as workdir:
        # A logger of our own, because ``workdir`` is deleted on the way out:
        # an open ``agentao.log`` inside it is a PermissionError on Windows.
        agent = build_agent(Path(workdir), api_key=api_key,
                            model=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
                            base_url=os.environ.get("OPENAI_BASE_URL", OPENAI_BASE_URL),
                            logger=logging.getLogger("openai_responses_wire_example"))
        try:
            print(agent.chat(prompt))
            print(usage_report(agent))
        finally:
            agent.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
