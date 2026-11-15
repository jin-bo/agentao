"""Tests for WebSearchTool backend selection, the three backends, and the
auto-mode fallback chain (deepseek → tavily → jina).

The chain/selection logic is tested directly off ``_chain`` / ``backends``;
the fallback loop is tested by monkeypatching ``_dispatch`` so no network is
needed. Response parsing runs a real ``httpx.Client`` over an
``httpx.MockTransport``, so ``raise_for_status`` and ``.json()`` are httpx's
own, not a stand-in's.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from agentao.tools.web import WebSearchTool, _is_official_deepseek_base_url

_OFFICIAL = "https://api.deepseek.com/v1"


def _clear_keys(monkeypatch):
    for var in (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "TAVILY_API_KEY",
        "JINA_API_KEY",
        "BOCHA_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _deepseek_env(monkeypatch, base_url=_OFFICIAL, key="dk"):
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    if base_url is not None:
        monkeypatch.setenv("DEEPSEEK_BASE_URL", base_url)


def _mock_httpx(monkeypatch, handler):
    """Route every ``httpx.Client`` through ``handler``; return the request log."""
    seen: list[httpx.Request] = []
    real_client = httpx.Client

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _client)
    return seen


def test_deepseek_base_url_note_is_logged_once(monkeypatch, caplog):
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch, base_url="https://my-gateway.example/v1")
    with caplog.at_level(logging.INFO, logger="agentao.tools.web"):
        WebSearchTool()
        WebSearchTool()
    notes = [r for r in caplog.records if "DEEPSEEK_BASE_URL" in r.getMessage()]
    assert len(notes) == 1


@pytest.mark.parametrize("pinned", ["tavily", "jina"])
def test_a_tool_pinned_elsewhere_leaves_the_deepseek_note_for_later(
    monkeypatch, caplog, pinned,
):
    """A pin to another backend never reads the deepseek env, so it must not
    spend the once-per-process note an auto-mode tool needs later."""
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch, base_url="https://my-gateway.example/v1")
    monkeypatch.setenv("TAVILY_API_KEY", "tk")
    with caplog.at_level(logging.INFO, logger="agentao.tools.web"):
        WebSearchTool(backend=pinned)
        assert not [r for r in caplog.records if "DEEPSEEK_BASE_URL" in r.getMessage()]
        WebSearchTool()
    notes = [r for r in caplog.records if "DEEPSEEK_BASE_URL" in r.getMessage()]
    assert len(notes) == 1


# ── DeepSeek base-URL gate ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://api.deepseek.com",
        "https://api.deepseek.com/",
        "https://api.deepseek.com/v1",
        "https://api.deepseek.com/beta",
        "https://api.deepseek.com/anthropic",
        "https://API.DeepSeek.com/v1",
        "https://api.deepseek.com:443/v1",
        "  https://api.deepseek.com/v1  ",
    ],
)
def test_official_deepseek_base_urls_pass(url):
    assert _is_official_deepseek_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        "http://api.deepseek.com/v1",  # not https
        "https://api.deepseek.com.evil.example/v1",  # lookalike suffix
        "https://evil.example/api.deepseek.com",  # host in the path
        "https://proxy.api.deepseek.com/v1",  # subdomain
        "https://deepseek.com/v1",  # apex, not the API host
        "https://api.deepseek.com.:443/v1",  # trailing dot
        "https://user:pw@api.deepseek.com/v1",  # credentials in the URL
        "https://api.deepseek.com:8443/v1",  # other port
        "https://api.deepseek.com:notaport/v1",  # unparsable port
        "api.deepseek.com/v1",  # no scheme
        "https://my-gateway.example/deepseek/v1",
    ],
)
def test_non_official_deepseek_base_urls_fail(url):
    assert not _is_official_deepseek_base_url(url)


# ── Backend resolution / chain construction ────────────────────────────────


def test_no_keys_chain_is_empty(monkeypatch):
    _clear_keys(monkeypatch)
    tool = WebSearchTool()
    assert tool.backends == ()
    assert tool._provider is None
    assert "No backend is configured." in tool.description


def test_no_backend_execute_reports_configuration(monkeypatch):
    _clear_keys(monkeypatch)
    out = WebSearchTool().execute("q")
    assert out.startswith("Error: web_search has no backend configured.")
    for var in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "TAVILY_API_KEY", "JINA_API_KEY"):
        assert var in out


def test_full_chain_order(monkeypatch):
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    monkeypatch.setenv("JINA_API_KEY", "j")
    tool = WebSearchTool()
    assert tool.backends == ("deepseek", "tavily", "jina")
    assert tool._provider == "deepseek"
    assert (
        "Backend order (falls back on failure): deepseek → tavily → jina"
        in tool.description
    )


@pytest.mark.parametrize(
    "env, chain",
    [
        ({"TAVILY_API_KEY": "t"}, ("tavily",)),
        ({"JINA_API_KEY": "j"}, ("jina",)),
        ({"TAVILY_API_KEY": "t", "JINA_API_KEY": "j"}, ("tavily", "jina")),
    ],
)
def test_partial_chains(monkeypatch, env, chain):
    _clear_keys(monkeypatch)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert WebSearchTool().backends == chain


def test_deepseek_env_key_needs_official_base_url(monkeypatch, caplog):
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch, base_url="https://my-gateway.example/v1", key="sk-secret")
    with caplog.at_level(logging.INFO, logger="agentao.tools.web"):
        tool = WebSearchTool()
    assert tool.backends == ()
    assert tool._deepseek_api_key is None
    notes = [r.getMessage() for r in caplog.records if "DEEPSEEK_BASE_URL" in r.getMessage()]
    assert notes, "expected a log line explaining why deepseek is off"
    # Names only: neither the key nor the configured URL reaches the log.
    assert not any("sk-secret" in n or "my-gateway" in n for n in notes)


def test_deepseek_env_key_without_base_url_stays_off(monkeypatch):
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch, base_url=None)
    assert WebSearchTool().backends == ()


def test_deepseek_base_url_without_key_stays_off(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", _OFFICIAL)
    assert WebSearchTool().backends == ()


def test_deepseek_env_pair_enables(monkeypatch):
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch)
    tool = WebSearchTool()
    assert tool.backends == ("deepseek",)
    assert tool._deepseek_api_key == "dk"


def test_explicit_deepseek_key_skips_the_base_url_gate(monkeypatch):
    """The gate guards reuse of the chat key; a host-supplied key is a choice."""
    _clear_keys(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://my-gateway.example/v1")
    tool = WebSearchTool(deepseek_api_key="host-key")
    assert tool.backends == ("deepseek",)
    assert tool._deepseek_api_key == "host-key"


def test_explicit_keys_beat_env(monkeypatch):
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "t-env")
    monkeypatch.setenv("JINA_API_KEY", "j-env")
    tool = WebSearchTool(deepseek_api_key="d", tavily_api_key="t", jina_api_key="j")
    assert (tool._deepseek_api_key, tool._tavily_api_key, tool._jina_api_key) == ("d", "t", "j")


@pytest.mark.parametrize(
    "kwarg, backend",
    [
        ("deepseek_api_key", "deepseek"),
        ("tavily_api_key", "tavily"),
        ("jina_api_key", "jina"),
    ],
)
def test_empty_key_forces_backend_off(monkeypatch, kwarg, backend):
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    monkeypatch.setenv("JINA_API_KEY", "j")
    tool = WebSearchTool(**{kwarg: ""})
    assert backend not in tool.backends
    assert len(tool.backends) == 2


def test_explicit_backend_has_no_autofallback(monkeypatch):
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    tool = WebSearchTool(backend="tavily")
    assert tool.backends == ("tavily",)
    assert "Backend: tavily." in tool.description
    assert "falls back" not in tool.description


def test_explicit_jina_needs_no_key(monkeypatch):
    _clear_keys(monkeypatch)
    tool = WebSearchTool(backend="jina")  # keyless jina is allowed
    assert tool.backends == ("jina",)
    assert tool._jina_api_key is None


def test_pinned_deepseek_without_key_raises(monkeypatch):
    _clear_keys(monkeypatch)
    with pytest.raises(ValueError, match="DEEPSEEK_BASE_URL"):
        WebSearchTool(backend="deepseek")


def test_pinned_deepseek_with_unofficial_base_url_raises(monkeypatch):
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch, base_url="https://my-gateway.example/v1")
    with pytest.raises(ValueError, match="requires an API key"):
        WebSearchTool(backend="deepseek")


def test_pinned_tavily_without_key_raises(monkeypatch):
    _clear_keys(monkeypatch)
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        WebSearchTool(backend="tavily")


@pytest.mark.parametrize("retired", ["bocha", "duckduckgo"])
def test_retired_backends_raise_with_a_pointer(monkeypatch, retired):
    _clear_keys(monkeypatch)
    with pytest.raises(ValueError, match="retired in 0.5.21"):
        WebSearchTool(backend=retired)


def test_bocha_env_key_is_ignored(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("BOCHA_API_KEY", "b")
    assert WebSearchTool().backends == ()


def test_bocha_api_key_kwarg_is_gone(monkeypatch):
    _clear_keys(monkeypatch)
    with pytest.raises(TypeError):
        WebSearchTool(api_key="b")  # type: ignore[call-arg]


def test_unknown_backend_message_lists_the_known_ones(monkeypatch):
    with pytest.raises(ValueError, match="'deepseek', 'tavily', 'jina'"):
        WebSearchTool(backend="brave")


# ── Fallback loop (execute) ────────────────────────────────────────────────


def _tool_with_chain(monkeypatch, chain):
    _clear_keys(monkeypatch)
    tool = WebSearchTool()
    tool._chain = list(chain)
    tool._provider = chain[0]
    return tool


def test_primary_success_no_annotation(monkeypatch):
    tool = _tool_with_chain(monkeypatch, ["deepseek", "tavily", "jina"])
    monkeypatch.setattr(tool, "_dispatch", lambda b, q, n: f"OK from {b}")
    out = tool.execute("hi")
    assert out == "OK from deepseek"
    assert "Note:" not in out


def test_falls_back_and_annotates(monkeypatch):
    tool = _tool_with_chain(monkeypatch, ["deepseek", "tavily", "jina"])

    def fake_dispatch(backend, query, n):
        if backend in ("deepseek", "tavily"):
            raise httpx.HTTPError(f"{backend} down")
        return "OK from jina"

    monkeypatch.setattr(tool, "_dispatch", fake_dispatch)
    out = tool.execute("hi")
    assert "OK from jina" in out
    assert "deepseek, tavily failed" in out
    assert "fallback backend 'jina'" in out


def test_all_backends_fail(monkeypatch):
    tool = _tool_with_chain(monkeypatch, ["deepseek", "tavily"])

    def fake_dispatch(backend, query, n):
        raise httpx.HTTPError(f"{backend} boom")

    monkeypatch.setattr(tool, "_dispatch", fake_dispatch)
    out = tool.execute("hi")
    assert out.startswith("Error: all web_search backends failed")
    assert "deepseek: deepseek boom" in out
    assert "tavily: tavily boom" in out


def test_no_results_is_terminal_not_a_fallback(monkeypatch):
    # A valid "no results" answer from the primary must NOT trigger fallback.
    tool = _tool_with_chain(monkeypatch, ["deepseek", "tavily"])
    calls = []

    def fake_dispatch(backend, query, n):
        calls.append(backend)
        if backend == "deepseek":
            return "No search results found for: hi"
        return "OK from tavily"

    monkeypatch.setattr(tool, "_dispatch", fake_dispatch)
    out = tool.execute("hi")
    assert out == "No search results found for: hi"
    assert calls == ["deepseek"]


def test_single_backend_error_message_is_plain(monkeypatch):
    tool = _tool_with_chain(monkeypatch, ["tavily"])

    def boom(b, q, n):
        raise httpx.HTTPError("bad gateway")

    monkeypatch.setattr(tool, "_dispatch", boom)
    out = tool.execute("hi")
    assert out == "Error performing tavily search: bad gateway"
    assert "all web_search backends failed" not in out


@pytest.mark.parametrize("bad", [0, -3, "x", None])
def test_num_results_is_coerced_and_clamped(monkeypatch, bad):
    tool = _tool_with_chain(monkeypatch, ["jina"])
    captured = {}

    def fake(b, q, n):
        captured["n"] = n
        return "OK"

    monkeypatch.setattr(tool, "_dispatch", fake)
    tool.execute("hi", num_results=bad)
    assert captured["n"] >= 1  # never 0 / negative / non-int


# ── DeepSeek backend ───────────────────────────────────────────────────────


def _deepseek_tool(monkeypatch):
    _clear_keys(monkeypatch)
    return WebSearchTool(backend="deepseek", deepseek_api_key="dk")


def test_deepseek_request_and_parsing(monkeypatch):
    payload = {
        "results": [
            {
                "title": "T1",
                "url": "https://a",
                "snippet": "short",
                "summary": "the longer model summary",
                "date": "2026-11-01T00:00:00Z",
                "rerank_score": 2.1,
                "authority": 3,
            },
            {"title": "T2", "url": "https://b", "snippet": "only snippet", "date": None},
            "not-a-dict",
        ]
    }
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json=payload))
    out = _deepseek_tool(monkeypatch)._search_deepseek("hello world", num_results=50)

    assert "1. T1" in out and "https://a" in out
    assert "the longer model summary" in out and "short" not in out  # summary first
    assert "2. T2" in out and "only snippet" in out
    # DeepSeek's date is unreliable (a 2026 page came back as 2013, seen
    # live 2026-11-15), so it is never shown to the model.
    assert "Date" not in out and "2026-11-01" not in out

    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://api.deepseek.com/v1/tools/search"
    assert req.headers["Authorization"] == "Bearer dk"
    assert json.loads(req.content) == {"query": "hello world", "count": 50}


def test_deepseek_count_is_capped_at_50(monkeypatch):
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"results": []}))
    _deepseek_tool(monkeypatch)._search_deepseek("q", num_results=500)
    assert json.loads(seen[0].content)["count"] == 50


def test_deepseek_url_ignores_the_configured_base_url(monkeypatch):
    """The Search API URL is fixed; DEEPSEEK_BASE_URL only gates key reuse."""
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch, base_url="https://api.deepseek.com/anthropic")
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"results": []}))
    WebSearchTool(backend="deepseek")._search_deepseek("q", num_results=5)
    assert str(seen[0].url) == "https://api.deepseek.com/v1/tools/search"


def test_deepseek_empty_results_is_terminal(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"results": []}))
    out = _deepseek_tool(monkeypatch)._search_deepseek("q", num_results=5)
    assert out == "No search results found for: q"


@pytest.mark.parametrize("body", [{"results": None}, {"error": "x"}, ["not", "an", "object"]])
def test_deepseek_malformed_200_raises(monkeypatch, body):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json=body))
    with pytest.raises(ValueError, match="deepseek: unexpected response shape"):
        _deepseek_tool(monkeypatch)._search_deepseek("q", num_results=5)


def test_deepseek_http_error_raises(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(httpx.HTTPStatusError):
        _deepseek_tool(monkeypatch)._search_deepseek("q", num_results=5)


def test_deepseek_long_query_is_refused_not_cut(monkeypatch):
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"results": []}))
    tool = _deepseek_tool(monkeypatch)
    with pytest.raises(ValueError, match="129 characters"):
        tool._search_deepseek("x" * 129, num_results=5)
    assert seen == []  # never sent
    tool._search_deepseek("x" * 128, num_results=5)  # the limit itself is fine
    assert json.loads(seen[0].content)["query"] == "x" * 128


@pytest.mark.parametrize("query", ["", "   "])
def test_deepseek_blank_query_is_refused(monkeypatch, query):
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"results": []}))
    with pytest.raises(ValueError, match="empty"):
        _deepseek_tool(monkeypatch)._search_deepseek(query, num_results=5)
    assert seen == []


@pytest.mark.parametrize("query", ["", "   "])
def test_blank_query_is_refused_before_any_backend(monkeypatch, query):
    """A blank query must not fall back to a backend that sends it anyway."""
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "tk")
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"results": []}))
    out = WebSearchTool().execute(query)
    assert out.startswith("Error: web_search needs a non-empty query")
    assert seen == []


def test_long_query_falls_back_to_tavily(monkeypatch):
    """End to end: a query over DeepSeek's limit is served by the next backend."""
    _clear_keys(monkeypatch)
    _deepseek_env(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "tk")
    hit = {"results": [{"title": "TT", "url": "https://t", "content": "from tavily"}]}
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json=hit))
    out = WebSearchTool().execute("y" * 200)
    assert "fallback backend 'tavily'" in out
    assert "from tavily" in out
    assert [r.url.host for r in seen] == ["api.tavily.com"]


# ── Tavily backend ─────────────────────────────────────────────────────────


def _tavily_tool(monkeypatch):
    _clear_keys(monkeypatch)
    return WebSearchTool(backend="tavily", tavily_api_key="tk")


def test_tavily_request_and_parsing(monkeypatch):
    payload = {
        "query": "hello",
        "answer": None,
        "images": [],
        "response_time": 0.5,
        "results": [
            {"title": "T1", "url": "https://a", "content": "snip one", "score": 0.9},
            {"title": "T2", "url": "https://b", "content": "x" * 500, "score": 0.5},
        ],
    }
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json=payload))
    out = _tavily_tool(monkeypatch)._search_tavily("hello", num_results=5)

    assert "1. T1" in out and "https://a" in out and "snip one" in out
    assert "2. T2" in out and "…" in out  # long content capped

    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://api.tavily.com/search"
    assert req.headers["Authorization"] == "Bearer tk"
    assert json.loads(req.content) == {
        "query": "hello",
        "max_results": 5,
        "search_depth": "basic",
        "include_answer": False,
    }


def test_tavily_max_results_is_capped_at_20(monkeypatch):
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"results": []}))
    _tavily_tool(monkeypatch)._search_tavily("q", num_results=99)
    assert json.loads(seen[0].content)["max_results"] == 20


def test_tavily_empty_results_is_terminal(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"results": []}))
    assert _tavily_tool(monkeypatch)._search_tavily("q", num_results=5) == (
        "No search results found for: q"
    )


@pytest.mark.parametrize("body", [{"results": None}, {"detail": {"error": "x"}}, [1, 2]])
def test_tavily_malformed_200_raises(monkeypatch, body):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json=body))
    with pytest.raises(ValueError, match="tavily: unexpected response shape"):
        _tavily_tool(monkeypatch)._search_tavily("q", num_results=5)


@pytest.mark.parametrize("status", [401, 429, 432, 433])
def test_tavily_http_errors_raise(monkeypatch, status):
    _mock_httpx(
        monkeypatch,
        lambda req: httpx.Response(status, json={"detail": {"error": "limit"}}),
    )
    with pytest.raises(httpx.HTTPStatusError):
        _tavily_tool(monkeypatch)._search_tavily("q", num_results=5)


# ── Jina backend ───────────────────────────────────────────────────────────


def test_jina_parsing_and_request(monkeypatch):
    _clear_keys(monkeypatch)
    payload = {
        "code": 200,
        "data": [
            {"title": "T1", "url": "https://a", "description": "snip one"},
            {"title": "T2", "url": "https://b", "content": "x" * 500},
        ],
    }
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json=payload))
    out = WebSearchTool(backend="jina", jina_api_key="jk")._search_jina("hello world", 5)

    assert "1. T1" in out and "https://a" in out and "snip one" in out
    assert "2. T2" in out and "…" in out
    (req,) = seen
    assert req.url.host == "s.jina.ai"
    assert req.url.params["q"] == "hello world"
    assert req.headers["Authorization"] == "Bearer jk"
    assert req.headers["X-Respond-With"] == "no-content"


def test_jina_keyless_omits_auth_header(monkeypatch):
    _clear_keys(monkeypatch)
    seen = _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"data": []}))
    WebSearchTool(backend="jina")._search_jina("q", num_results=3)
    assert "Authorization" not in seen[0].headers


def test_jina_non_list_data_raises(monkeypatch):
    # A soft-error 200 with {"data": null} must NOT be reported as "no results";
    # it raises so the chain falls back.
    _clear_keys(monkeypatch)
    _mock_httpx(
        monkeypatch,
        lambda req: httpx.Response(200, json={"code": 200, "data": None, "message": "rate limited"}),
    )
    with pytest.raises(ValueError, match="jina: unexpected response shape"):
        WebSearchTool(backend="jina")._search_jina("q", num_results=5)


def test_jina_empty_list_is_terminal_no_results(monkeypatch):
    _clear_keys(monkeypatch)
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"code": 200, "data": []}))
    assert WebSearchTool(backend="jina")._search_jina("q", 5) == "No search results found for: q"


# ── Shared formatter ───────────────────────────────────────────────────────


def test_format_results_coerces_non_string_snippet_and_caps():
    from agentao.tools.web import _format_search_results

    items = [
        {"title": "T", "url": "u", "snippet": ["a", "b"]},   # non-string → no crash
        {"title": "T2", "url": "u2", "snippet": "x" * 500},  # long → capped
    ]
    out = _format_search_results("q", items)
    assert "['a', 'b']" in out
    assert "…" in out and "x" * 301 not in out
    assert "Date:" not in out


def test_a_multi_line_snippet_is_rendered_on_one_indented_line():
    """DeepSeek summaries carry newlines and Markdown headings (seen live,
    2026-11-15). Each snippet stays one indented line, so a ``# heading`` in
    a summary never reads as a line of the result layout."""
    from agentao.tools.web import _format_search_results

    snippet = "# Python 3.13.13\n\nNote: superseded.\n...\n## Pricing\tSearch $0.75"
    out = _format_search_results("q", [{"title": "T", "url": "u", "snippet": snippet}])
    assert "   # Python 3.13.13 Note: superseded. ... ## Pricing Search $0.75\n" in out
    assert not any(line.startswith("#") for line in out.splitlines())


def test_deepseek_dates_are_kept_in_the_debug_log(monkeypatch, caplog):
    """Hidden from the model, the raw date stays available for debugging."""
    payload = {"results": [{"title": "T", "url": "https://a", "snippet": "s",
                            "date": "2013-03-12T16:00:00+00:00"}]}
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json=payload))
    with caplog.at_level(logging.DEBUG, logger="agentao.tools.web"):
        out = _deepseek_tool(monkeypatch)._search_deepseek("q", num_results=3)
    assert "2013-03-12" not in out
    assert any("2013-03-12T16:00:00+00:00" in r.getMessage() for r in caplog.records)
