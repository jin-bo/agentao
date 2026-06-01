"""Smoke tests for Jina tool injection — fully offline.

Proves the two injection surfaces are real and that the injected tools
hit the right Jina endpoints, without any network call: an
``httpx.MockTransport`` records every request and returns a canned body.

Contracts covered:
1. ``web_fetch`` is injected at construction and is the Jina tool.
2. It calls ``r.jina.ai`` with the target URL.
3. ``web_search`` is NOT the Jina tool until injected at runtime.
4. Once injected, ``web_search`` calls ``s.jina.ai`` with the query.
5. Injected tools inherit capability binding (wd / filesystem / shell).
6. ``JINA_API_KEY`` is sent as an ``Authorization: Bearer`` header.
"""

from __future__ import annotations

import httpx

from src.jina_tools import (
    JinaWebFetchTool,
    JinaWebSearchTool,
    inject_search_at_runtime,
    make_agent,
)


def _recording_transport(record: list) -> httpx.MockTransport:
    """An httpx transport that records requests and returns canned text."""

    def handler(request: httpx.Request) -> httpx.Response:
        record.append(request)
        return httpx.Response(200, text=f"CONTENT for {request.url}")

    return httpx.MockTransport(handler)


# ── Construction-time injection: web_fetch via r.jina.ai ──────────────────


def test_web_fetch_injected_at_construction(tmp_path):
    agent = make_agent(tmp_path)
    try:
        tool = agent.tools.get("web_fetch")
        # Drop-in: same name, but it's our Jina tool, not the built-in.
        assert tool.name == "web_fetch"
        assert isinstance(tool, JinaWebFetchTool)
    finally:
        agent.close()


def test_web_fetch_calls_jina_reader(tmp_path):
    record: list = []
    agent = make_agent(tmp_path, transport=_recording_transport(record))
    try:
        out = agent.tools.get("web_fetch").execute(url="https://example.com/docs")
        assert "CONTENT for" in out
        assert len(record) == 1
        url = str(record[0].url)
        assert url.startswith("https://r.jina.ai/")
        assert "example.com/docs" in url
    finally:
        agent.close()


# ── Runtime injection: web_search via s.jina.ai ───────────────────────────


def test_web_search_absent_until_runtime(tmp_path):
    agent = make_agent(tmp_path)
    try:
        # Before runtime injection: either absent, or the built-in — but
        # never the Jina tool. ``not isinstance(None, X)`` holds either way.
        before = agent.tools.tools.get("web_search")
        assert not isinstance(before, JinaWebSearchTool)

        inject_search_at_runtime(agent)

        after = agent.tools.get("web_search")
        assert isinstance(after, JinaWebSearchTool)
    finally:
        agent.close()


def test_web_search_calls_jina_search(tmp_path):
    record: list = []
    transport = _recording_transport(record)
    agent = make_agent(tmp_path)
    try:
        inject_search_at_runtime(agent, transport=transport)
        out = agent.tools.get("web_search").execute(query="agentao embedded harness")
        assert "CONTENT for" in out
        assert len(record) == 1
        url = str(record[0].url)
        assert url.startswith("https://s.jina.ai/")
        # Query is URL-encoded into the path.
        assert "agentao" in url and "%20" in url
    finally:
        agent.close()


# ── Capability binding + auth header ──────────────────────────────────────


def test_injected_tools_inherit_capability_binding(tmp_path):
    agent = make_agent(tmp_path)
    try:
        inject_search_at_runtime(agent)
        for name in ("web_fetch", "web_search"):
            tool = agent.tools.get(name)
            assert tool.working_directory == agent._working_directory
            assert tool.filesystem is agent.filesystem
            assert tool.shell is agent.shell
    finally:
        agent.close()


def test_api_key_sent_as_bearer(tmp_path):
    record: list = []
    agent = make_agent(
        tmp_path, jina_api_key="secret-key", transport=_recording_transport(record)
    )
    try:
        agent.tools.get("web_fetch").execute(url="https://example.com")
        assert record[0].headers.get("Authorization") == "Bearer secret-key"
    finally:
        agent.close()
