"""Host tool injection demo — Jina-backed ``web_fetch`` / ``web_search``.

Shows the two host-facing injection surfaces of the embedded contract,
both replacing a built-in by *name* (drop-in override, same name + same
parameter schema, so the model's existing tool calls keep working):

1. **Construction-time** via ``Agentao(extra_tools=[...])`` —
   :class:`JinaWebFetchTool` (``web_fetch``) backed by Jina Reader
   (``https://r.jina.ai/{url}``). Registered as the final pass, so it
   overrides the built-in ``web_fetch`` if present. See ``make_agent``.

2. **Runtime** via ``agent.add_tool(...)`` — :class:`JinaWebSearchTool`
   (``web_search``) backed by Jina Search (``https://s.jina.ai/{query}``).
   Added *after* construction, e.g. once the host learns a Jina key is
   available mid-session. Visible to the model on the next ``chat()`` /
   ``arun()`` call (the schema is snapshotted per call, never mid-turn).
   See ``inject_search_at_runtime``.

Both tools take an optional ``transport`` (an ``httpx`` transport) so the
smoke tests can drive them fully offline; in production it is ``None`` and
``httpx`` uses its real network transport.

See ``docs/design/host-tool-injection.md`` (extra_tools / add_tool) and
``docs/api/host.md``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
from urllib.parse import quote
from unittest.mock import MagicMock

from agentao import Agentao
from agentao.host import Tool

# Jina endpoints. Reader turns a URL into clean, LLM-ready text; Search
# turns a query into ranked result text. Both accept an optional
# ``Authorization: Bearer <JINA_API_KEY>`` header for higher rate limits.
JINA_READER_BASE = "https://r.jina.ai/"
JINA_SEARCH_BASE = "https://s.jina.ai/"


def _jina_get(
    url: str,
    *,
    api_key: Optional[str],
    transport: Optional[Any],
    timeout: float = 30.0,
) -> str:
    """GET ``url`` through Jina and return the response body as text.

    ``transport`` is forwarded to ``httpx.Client`` — pass an
    ``httpx.MockTransport`` in tests, leave it ``None`` in production.
    """
    import httpx

    headers = {"Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with httpx.Client(
        transport=transport, follow_redirects=True, timeout=timeout
    ) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response.text


class JinaWebFetchTool(Tool):
    """``web_fetch`` backed by Jina Reader (``r.jina.ai``).

    Same name + parameter schema as the built-in ``web_fetch`` so it is a
    true drop-in replacement.
    """

    def __init__(
        self, *, api_key: Optional[str] = None, transport: Optional[Any] = None
    ) -> None:
        self._api_key = api_key or os.getenv("JINA_API_KEY")
        self._transport = transport

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch a URL and return clean, readable text via Jina Reader. "
            "The URL is sent to https://r.jina.ai and rendered server-side."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "extract_text": {
                    "type": "boolean",
                    "description": "Accepted for drop-in parity; Jina always "
                    "returns extracted text.",
                    "default": True,
                },
            },
            "required": ["url"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return True

    def execute(self, url: str, extract_text: bool = True) -> str:
        endpoint = f"{JINA_READER_BASE}{url}"
        try:
            return _jina_get(
                endpoint, api_key=self._api_key, transport=self._transport
            )
        except Exception as exc:  # noqa: BLE001 — surface a clean tool error
            return f"Error fetching {url} via Jina Reader: {exc}"


class JinaWebSearchTool(Tool):
    """``web_search`` backed by Jina Search (``s.jina.ai``).

    Same name + parameter schema as the built-in ``web_search``.
    """

    def __init__(
        self, *, api_key: Optional[str] = None, transport: Optional[Any] = None
    ) -> None:
        self._api_key = api_key or os.getenv("JINA_API_KEY")
        self._transport = transport

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web and return ranked result text via Jina Search "
            "(https://s.jina.ai)."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num_results": {
                    "type": "integer",
                    "description": "Advisory; Jina returns a server-chosen set.",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    @property
    def requires_confirmation(self) -> bool:
        return True

    def execute(self, query: str, num_results: int = 5) -> str:
        endpoint = f"{JINA_SEARCH_BASE}{quote(query)}"
        try:
            return _jina_get(
                endpoint, api_key=self._api_key, transport=self._transport
            )
        except Exception as exc:  # noqa: BLE001 — surface a clean tool error
            return f"Error searching '{query}' via Jina Search: {exc}"


def _fake_llm_client() -> MagicMock:
    """Mimic the ``LLMClient`` shape Agentao reads at construction.

    Lets the demo + smoke tests build a real ``Agentao`` with no network
    and no API key. Replace with a real client (or pass ``api_key=`` /
    ``base_url=`` / ``model=``) to drive actual turns.
    """
    fake = MagicMock(name="FakeLLMClient")
    fake.logger = MagicMock(name="FakeLLMLogger")
    fake.model = "fake-model"
    fake.api_key = "fake-key"
    fake.base_url = "http://localhost:1"
    fake.temperature = 0.0
    fake.max_tokens = 256
    fake.total_prompt_tokens = 0
    fake.total_completion_tokens = 0
    return fake


def make_agent(
    working_directory,
    *,
    jina_api_key: Optional[str] = None,
    transport: Optional[Any] = None,
    llm_client: Optional[Any] = None,
) -> Agentao:
    """Construct an ``Agentao`` with Jina ``web_fetch`` injected at build time.

    This is the **construction-time** surface: ``extra_tools=`` registers
    :class:`JinaWebFetchTool` as the final pass, overriding the built-in
    ``web_fetch`` (``r.jina.ai``). ``web_search`` is *not* injected here —
    that happens at runtime, see :func:`inject_search_at_runtime`.
    """
    return Agentao(
        working_directory=working_directory,
        llm_client=llm_client or _fake_llm_client(),
        extra_tools=[JinaWebFetchTool(api_key=jina_api_key, transport=transport)],
    )


def inject_search_at_runtime(
    agent: Agentao,
    *,
    jina_api_key: Optional[str] = None,
    transport: Optional[Any] = None,
) -> None:
    """Add Jina ``web_search`` after construction (the **runtime** surface).

    ``replace=True`` so it works whether or not a built-in ``web_search``
    is already registered (it is when the ``[web]`` extra is installed):
    present → override (silent, INFO-audited); absent → plain add. Becomes
    model-visible on the next ``chat()`` / ``arun()`` call.
    """
    agent.add_tool(
        JinaWebSearchTool(api_key=jina_api_key, transport=transport),
        replace=True,
    )


__all__ = [
    "JinaWebFetchTool",
    "JinaWebSearchTool",
    "make_agent",
    "inject_search_at_runtime",
    "JINA_READER_BASE",
    "JINA_SEARCH_BASE",
]
