"""Which wire protocol an :class:`~agentao.llm.client.LLMClient` speaks.

``api_format`` is **not** the provider. ``LLM_PROVIDER`` names a credential
block (``{PROVIDER}_API_KEY`` / ``_BASE_URL`` / ``_MODEL``); ``api_format``
names the protocol spoken to that block's endpoint. One base URL can serve
several protocols, and a provider called ``ANTHROPIC`` pointing at an
OpenAI-compatible gateway is a working configuration today — so the format is
configured, never inferred from a URL, a provider name or a model name
(``docs/design/llm-api-adapters.md`` §3, §9).

Chosen at construction; afterwards only a provider switch that names another
format changes it (``LLMClient.reconfigure(api_format=)``). Per-model overrides
are stage 3 of that design and are not built.
"""

from __future__ import annotations

from typing import Optional

OPENAI_COMPLETIONS = "openai-completions"
ANTHROPIC_MESSAGES = "anthropic-messages"
OPENAI_RESPONSES = "openai-responses"

#: The formats this build implements, in the order they are listed to a user
#: who misspelled one. A format the design names but nothing implements yet
#: (``gemini-api``) is deliberately absent: accepting it would mean silently
#: speaking some other protocol.
API_FORMATS = (OPENAI_COMPLETIONS, ANTHROPIC_MESSAGES, OPENAI_RESPONSES)

DEFAULT_API_FORMAT = OPENAI_COMPLETIONS


def resolve_api_format(value: Optional[str]) -> str:
    """Normalise a configured ``api_format``; fail closed on anything else.

    ``None`` and an empty / whitespace-only string mean "unset" and resolve to
    the default, which keeps every existing deployment on Chat Completions.
    An unknown value raises and lists the valid ones — a typo here must not
    quietly fall back to the default wire, because the request would then go
    to an endpoint that speaks something else.
    """
    if value is None:
        return DEFAULT_API_FORMAT
    if not isinstance(value, str):
        raise TypeError("api_format must be a string or None.")
    normalized = value.strip().lower()
    if not normalized:
        return DEFAULT_API_FORMAT
    if normalized not in API_FORMATS:
        raise ValueError(
            f"Unknown api_format {value!r}. Valid values: "
            + ", ".join(API_FORMATS)
            + "."
        )
    return normalized
