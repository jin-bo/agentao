"""The ``deepseek`` fallback of ``web_fetch`` (``AGENTAO_WEB_FETCH_FALLBACK``).

Requests run through a real ``httpx.AsyncClient`` over ``httpx.MockTransport``,
so the request DeepSeek would see and httpx's own ``raise_for_status`` /
``.json()`` are what the assertions read.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

import agentao.tools.web as web_mod
from agentao.tools.web import WebFetchTool

_OFFICIAL = "https://api.deepseek.com/v1"
_JS_SHELL = '<html><body><script id="__NEXT_DATA__">{}</script></body></html>'


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "AGENTAO_WEB_FETCH_FALLBACK",
        "AGENTAO_WEB_FETCH_ALLOW_CIDRS",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _tool(monkeypatch, *, key="dk", base_url=_OFFICIAL) -> WebFetchTool:
    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "deepseek")
    if key is not None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    if base_url is not None:
        monkeypatch.setenv("DEEPSEEK_BASE_URL", base_url)
    return WebFetchTool()


def _route(monkeypatch, handler):
    """Send every ``httpx.AsyncClient`` through ``handler``; return the log."""
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def _handler(request):
        seen.append(request)
        return handler(request)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


def _fallback(tool, url):
    return asyncio.run(tool._run_fallback(url, reason="test"))


# ── selection and the audit surface ────────────────────────────────────────


def test_deepseek_is_a_valid_fallback_value(monkeypatch):
    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "DeepSeek")
    assert web_mod._read_fallback_setting() == ("deepseek", None)


def test_the_description_says_where_the_url_goes(monkeypatch):
    tool = _tool(monkeypatch)
    assert tool._fallback == "deepseek"
    assert "https://api.deepseek.com" in tool.description
    assert "NOTE" not in tool.description


@pytest.mark.parametrize(
    "key,base_url",
    [
        (None, _OFFICIAL),                        # no key at all
        ("dk", None),                             # key, but no base URL
        ("dk", "https://my-gateway.example/v1"),  # key for some other endpoint
    ],
)
def test_without_a_usable_key_there_is_no_fallback_and_the_description_says_so(
    monkeypatch, key, base_url,
):
    """The model must not be told the URL may go to DeepSeek when nothing
    will be sent there."""
    tool = _tool(monkeypatch, key=key, base_url=base_url)
    assert tool._fallback == "none"
    assert "falls back to DeepSeek" not in tool.description
    assert "AGENTAO_WEB_FETCH_FALLBACK=deepseek is set but no DeepSeek key resolves" in tool.description
    assert _fallback(tool, "https://x.test/") == (None, None)


def test_a_gateway_base_url_is_logged_once_for_web_fetch(monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="agentao.tools.web"):
        _tool(monkeypatch, base_url="https://my-gateway.example/v1")
        _tool(monkeypatch, base_url="https://my-gateway.example/v1")
    notes = [r.getMessage() for r in caplog.records if "DEEPSEEK_BASE_URL" in r.getMessage()]
    assert notes == [
        "web_fetch: DEEPSEEK_API_KEY is set but DEEPSEEK_BASE_URL is not "
        "https://api.deepseek.com; the deepseek fetch fallback stays off."
    ]
    assert "my-gateway" not in notes[0]  # names only, never values


def test_the_js_shell_hint_names_the_deepseek_option(monkeypatch):
    async def fake_get(client, url, **kwargs):
        return httpx.Response(
            200, text=_JS_SHELL, headers={"content-type": "text/html"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(web_mod, "guarded_get_async", fake_get)
    out = asyncio.run(WebFetchTool().async_execute("https://spa.test/"))
    assert "=deepseek (sends URL to api.deepseek.com)" in out


def test_the_js_shell_hint_does_not_ask_to_set_deepseek_when_it_is_set(monkeypatch):
    """With ``=deepseek`` set but no usable key, the hint names the missing key
    rather than telling the model to set what is already set."""
    async def fake_get(client, url, **kwargs):
        return httpx.Response(
            200, text=_JS_SHELL, headers={"content-type": "text/html"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(web_mod, "guarded_get_async", fake_get)
    out = asyncio.run(_tool(monkeypatch, key=None).async_execute("https://spa.test/"))
    assert "AGENTAO_WEB_FETCH_FALLBACK=deepseek is set but no DeepSeek key resolves" in out
    assert "Set AGENTAO_WEB_FETCH_FALLBACK" not in out


# ── the request ────────────────────────────────────────────────────────────


def test_the_request_and_the_result(monkeypatch):
    seen = _route(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"title": "Example", "content": "# Example\n\nBody text."}
        ),
    )
    body, error = _fallback(_tool(monkeypatch, key="dk-secret"), "https://x.test/page")

    assert error is None
    assert body.startswith("URL: https://x.test/page\nFallback: deepseek fetch (https://api.deepseek.com/v1/tools/fetch)\n\n")
    assert "Title: Example\n\n# Example\n\nBody text." in body
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://api.deepseek.com/v1/tools/fetch"
    assert req.headers["authorization"] == "Bearer dk-secret"
    assert json.loads(req.content) == {"url": "https://x.test/page"}
    assert "dk-secret" not in body


def test_an_empty_title_adds_no_title_line(monkeypatch):
    _route(monkeypatch, lambda r: httpx.Response(200, json={"title": "", "content": "text"}))
    body, _ = _fallback(_tool(monkeypatch), "https://x.test/")
    assert "Title:" not in body and body.endswith("\n\ntext")


def test_the_fetch_url_is_fixed_whatever_the_base_url_path(monkeypatch):
    seen = _route(monkeypatch, lambda r: httpx.Response(200, json={"title": "", "content": "x"}))
    _fallback(_tool(monkeypatch, base_url="https://api.deepseek.com/anthropic"), "https://x.test/")
    assert str(seen[0].url) == "https://api.deepseek.com/v1/tools/fetch"


@pytest.mark.parametrize(
    "status,expected",
    [
        (404, "HTTP 404 — DeepSeek could not get usable content"),
        (403, "403"),
        (429, "429"),
        (504, "504"),
    ],
)
def test_an_http_error_is_a_failed_fallback(monkeypatch, status, expected):
    _route(monkeypatch, lambda r: httpx.Response(status, json={"error": {"message": "x"}}))
    body, error = _fallback(_tool(monkeypatch), "https://x.test/")
    assert body is None
    assert error.startswith("deepseek fetch (https://api.deepseek.com/v1/tools/fetch): ")
    assert expected in error


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>Sign in to the network</html>"),
        httpx.Response(200, json={"title": "t"}),
        httpx.Response(200, json={"title": "t", "content": None}),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"title": "t", "content": "  \n"}),
    ],
    ids=["html", "no-content", "null-content", "list", "empty-content"],
)
def test_a_malformed_200_is_a_failed_fallback(monkeypatch, response):
    _route(monkeypatch, lambda r: response)
    body, error = _fallback(_tool(monkeypatch), "https://x.test/")
    assert body is None and error


def test_a_network_failure_is_a_failed_fallback(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("simulated", request=request)

    _route(monkeypatch, handler)
    body, error = _fallback(_tool(monkeypatch), "https://x.test/")
    assert body is None and "simulated" in error


def test_a_timeout_is_reported_by_name(monkeypatch):
    """``asyncio.TimeoutError`` has an empty ``str``; the error is never blank."""
    async def fake_fetch(url, api_key):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(web_mod, "_fetch_via_deepseek", fake_fetch)
    body, error = _fallback(_tool(monkeypatch), "https://x.test/")
    assert body is None and error.endswith("TimeoutError")


def test_an_unparsable_url_is_never_sent(monkeypatch):
    seen = _route(monkeypatch, lambda r: httpx.Response(200, json={"title": "", "content": "x"}))
    body, error = _fallback(_tool(monkeypatch), "https://[::1/")
    assert body is None and "cannot be parsed" in error
    assert seen == []


def test_a_url_with_credentials_is_never_sent(monkeypatch):
    seen = _route(monkeypatch, lambda r: httpx.Response(200, json={"title": "", "content": "x"}))
    body, error = _fallback(_tool(monkeypatch), "https://user:pw@x.test/")
    assert body is None and "credentials" in error
    assert seen == []


def test_an_over_long_url_is_refused_before_the_request(monkeypatch):
    seen = _route(monkeypatch, lambda r: httpx.Response(200, json={"title": "", "content": "x"}))
    url = "https://x.test/" + "a" * 8200
    body, error = _fallback(_tool(monkeypatch), url)
    assert body is None and "at most 8192" in error
    assert seen == []


def test_the_response_is_capped(monkeypatch):
    monkeypatch.setattr(web_mod, "_MAX_BODY_BYTES", 4096)
    big = json.dumps({"title": "", "content": "x" * 20_000})
    _route(monkeypatch, lambda r: httpx.Response(200, text=big))
    body, error = _fallback(_tool(monkeypatch), "https://x.test/")
    assert body is None and "4096 byte limit" in error


def test_the_cap_is_the_shared_helper():
    import inspect

    assert "aread_capped(" in inspect.getsource(web_mod._fetch_via_deepseek)


# ── through the whole tool ─────────────────────────────────────────────────


def test_a_js_page_comes_back_from_deepseek(monkeypatch):
    async def fake_get(client, url, **kwargs):
        return httpx.Response(
            200, text=_JS_SHELL, headers={"content-type": "text/html"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(web_mod, "guarded_get_async", fake_get)
    _route(monkeypatch, lambda r: httpx.Response(200, json={"title": "App", "content": "Hydrated"}))
    out = asyncio.run(_tool(monkeypatch).async_execute("https://spa.test/"))
    assert "Fallback: deepseek fetch" in out and "Hydrated" in out


def test_a_blocked_url_never_reaches_deepseek(monkeypatch):
    """An internal target must not be handed to a third party to fetch."""
    seen = _route(monkeypatch, lambda r: httpx.Response(200, json={"title": "", "content": "x"}))
    out = asyncio.run(_tool(monkeypatch).async_execute("http://127.0.0.1:8080/admin"))
    assert out.startswith("Error: blocked outbound request")
    assert not any(r.url.host == "api.deepseek.com" for r in seen)


def test_a_failed_fallback_keeps_the_direct_fetch_error(monkeypatch):
    async def fake_get(client, url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(503, text="down", request=request)

    monkeypatch.setattr(web_mod, "guarded_get_async", fake_get)
    _route(monkeypatch, lambda r: httpx.Response(404, json={}))
    out = asyncio.run(_tool(monkeypatch).async_execute("https://x.test/"))
    assert out.startswith("Error fetching URL: Server error '503")
    assert "Fallback also failed: deepseek fetch" in out
    assert "could not get usable content" in out
