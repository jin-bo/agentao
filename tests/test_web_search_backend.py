"""Tests for WebSearchTool backend selection, the jina backend, and the
auto-mode fallback chain (jina → bocha → duckduckgo).

The chain/selection logic is tested directly off ``_chain`` / ``_provider``;
the fallback loop is tested by monkeypatching ``_dispatch`` so no network is
needed; jina response parsing is tested against a faked ``httpx.Client``.
"""

from __future__ import annotations

import httpx
import pytest

from agentao.tools.web import WebSearchTool


def _clear_keys(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)


# ── Backend resolution / chain construction ────────────────────────────────


def test_no_keys_chain_is_duckduckgo_only(monkeypatch):
    _clear_keys(monkeypatch)
    tool = WebSearchTool()
    assert tool._chain == ["duckduckgo"]
    assert tool._provider == "duckduckgo"


def test_bocha_key_only_chain(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("BOCHA_API_KEY", "b")
    tool = WebSearchTool()
    assert tool._chain == ["bocha", "duckduckgo"]
    assert tool._provider == "bocha"


def test_jina_key_only_chain(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("JINA_API_KEY", "j")
    tool = WebSearchTool()
    assert tool._chain == ["jina", "duckduckgo"]
    assert tool._provider == "jina"


def test_both_keys_full_chain(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("JINA_API_KEY", "j")
    monkeypatch.setenv("BOCHA_API_KEY", "b")
    tool = WebSearchTool()
    assert tool._chain == ["jina", "bocha", "duckduckgo"]
    assert tool._provider == "jina"


def test_explicit_backend_has_no_autofallback(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("BOCHA_API_KEY", "b")
    tool = WebSearchTool(backend="bocha")
    assert tool._chain == ["bocha"]  # no ddg appended for an explicit pin


def test_explicit_jina_needs_no_key(monkeypatch):
    _clear_keys(monkeypatch)
    tool = WebSearchTool(backend="jina")  # keyless jina is allowed
    assert tool._chain == ["jina"]
    assert tool._jina_api_key is None


def test_explicit_jina_key_arg_beats_env(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "from-env")
    tool = WebSearchTool(jina_api_key="explicit")
    assert tool._jina_api_key == "explicit"


def test_empty_jina_key_forces_unset(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "from-env")
    _clear_keys_bocha = monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    tool = WebSearchTool(jina_api_key="")  # force "no key" → jina out of auto chain
    assert tool._jina_api_key == ""
    assert "jina" not in tool._chain
    assert tool._chain == ["duckduckgo"]


def test_unknown_backend_message_lists_jina(monkeypatch):
    with pytest.raises(ValueError, match="jina"):
        WebSearchTool(backend="brave")


# ── Fallback loop (execute) ────────────────────────────────────────────────


def _tool_with_chain(monkeypatch, chain):
    _clear_keys(monkeypatch)
    tool = WebSearchTool()
    tool._chain = list(chain)
    tool._provider = chain[0]
    return tool


def test_primary_success_no_annotation(monkeypatch):
    tool = _tool_with_chain(monkeypatch, ["jina", "bocha", "duckduckgo"])
    monkeypatch.setattr(tool, "_dispatch", lambda b, q, n: f"OK from {b}")
    out = tool.execute("hi")
    assert out == "OK from jina"
    assert "Note:" not in out


def test_falls_back_and_annotates(monkeypatch):
    tool = _tool_with_chain(monkeypatch, ["jina", "bocha", "duckduckgo"])

    def fake_dispatch(backend, query, n):
        if backend in ("jina", "bocha"):
            raise httpx.HTTPError(f"{backend} down")
        return "OK from duckduckgo"

    monkeypatch.setattr(tool, "_dispatch", fake_dispatch)
    out = tool.execute("hi")
    assert "OK from duckduckgo" in out
    assert "jina, bocha failed" in out
    assert "fallback backend 'duckduckgo'" in out


def test_all_backends_fail(monkeypatch):
    tool = _tool_with_chain(monkeypatch, ["jina", "duckduckgo"])

    def fake_dispatch(backend, query, n):
        raise httpx.HTTPError(f"{backend} boom")

    monkeypatch.setattr(tool, "_dispatch", fake_dispatch)
    out = tool.execute("hi")
    assert out.startswith("Error: all web_search backends failed")
    assert "jina: jina boom" in out
    assert "duckduckgo: duckduckgo boom" in out


def test_no_results_is_terminal_not_a_fallback(monkeypatch):
    # A valid "no results" answer from the primary must NOT trigger fallback.
    tool = _tool_with_chain(monkeypatch, ["jina", "duckduckgo"])
    calls = []

    def fake_dispatch(backend, query, n):
        calls.append(backend)
        if backend == "jina":
            return "No search results found for: hi"
        return "OK from duckduckgo"

    monkeypatch.setattr(tool, "_dispatch", fake_dispatch)
    out = tool.execute("hi")
    assert out == "No search results found for: hi"
    assert calls == ["jina"]  # ddg never reached


# ── Jina response parsing ──────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


class _FakeClient:
    last_kwargs: dict = {}

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, headers=None):
        _FakeClient.last_kwargs = {"url": url, "params": params, "headers": headers}
        return _FakeResp(
            {
                "code": 200,
                "data": [
                    {"title": "T1", "url": "https://a", "description": "snip one"},
                    {"title": "T2", "url": "https://b", "content": "x" * 500},
                ],
            }
        )


def test_jina_parsing_and_request(monkeypatch):
    _clear_keys(monkeypatch)
    _FakeClient.last_kwargs = {}  # isolate from any prior test's capture
    monkeypatch.setenv("JINA_API_KEY", "jk")
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    tool = WebSearchTool(backend="jina")
    out = tool._search_jina("hello world", num_results=5)

    assert "1. T1" in out and "https://a" in out and "snip one" in out
    assert "2. T2" in out
    assert "…" in out  # long content truncated
    # request shape: s.jina.ai with ?q= and a bearer header
    kw = _FakeClient.last_kwargs
    assert kw["url"] == "https://s.jina.ai/"
    assert kw["params"] == {"q": "hello world"}
    assert kw["headers"]["Authorization"] == "Bearer jk"
    assert kw["headers"]["X-Respond-With"] == "no-content"


def test_jina_keyless_omits_auth_header(monkeypatch):
    _clear_keys(monkeypatch)
    _FakeClient.last_kwargs = {}
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    tool = WebSearchTool(backend="jina")
    tool._search_jina("q", num_results=3)
    assert "Authorization" not in _FakeClient.last_kwargs["headers"]


# ── review-driven hardening ────────────────────────────────────────────────


def test_description_chain_vs_single(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("JINA_API_KEY", "j")
    monkeypatch.setenv("BOCHA_API_KEY", "b")
    chain_tool = WebSearchTool()
    assert "Backend order (falls back on failure): jina → bocha → duckduckgo" in chain_tool.description

    single = WebSearchTool(backend="duckduckgo")
    assert "Backend: duckduckgo." in single.description
    assert "falls back" not in single.description


def test_single_backend_error_message_is_plain(monkeypatch):
    tool = _tool_with_chain(monkeypatch, ["bocha"])

    def boom(b, q, n):
        raise httpx.HTTPError("bad gateway")

    monkeypatch.setattr(tool, "_dispatch", boom)
    out = tool.execute("hi")
    assert out == "Error performing bocha search: bad gateway"
    assert "all web_search backends failed" not in out


@pytest.mark.parametrize("bad", [0, -3, "x", None])
def test_num_results_is_coerced_and_clamped(monkeypatch, bad):
    tool = _tool_with_chain(monkeypatch, ["duckduckgo"])
    captured = {}

    def fake(b, q, n):
        captured["n"] = n
        return "OK"

    monkeypatch.setattr(tool, "_dispatch", fake)
    tool.execute("hi", num_results=bad)
    assert captured["n"] >= 1  # never 0 / negative / non-int


def test_jina_non_list_data_raises_and_falls_back(monkeypatch):
    # A soft-error 200 with {"data": null} must NOT be reported as "no results";
    # it raises so the chain falls back.
    class _BadJina(_FakeClient):
        def get(self, url, params=None, headers=None):
            return _FakeResp({"code": 200, "data": None, "message": "rate limited"})

    _clear_keys(monkeypatch)
    monkeypatch.setattr(httpx, "Client", _BadJina)
    tool = WebSearchTool(backend="jina")
    with pytest.raises(Exception):
        tool._search_jina("q", num_results=5)


def test_jina_empty_list_is_terminal_no_results(monkeypatch):
    class _EmptyJina(_FakeClient):
        def get(self, url, params=None, headers=None):
            return _FakeResp({"code": 200, "data": []})

    _clear_keys(monkeypatch)
    monkeypatch.setattr(httpx, "Client", _EmptyJina)
    tool = WebSearchTool(backend="jina")
    assert tool._search_jina("q", num_results=5) == "No search results found for: q"


def test_bocha_non_dict_root_raises(monkeypatch):
    class _BadBocha(_FakeClient):
        def post(self, url, json=None, headers=None):
            return _FakeResp(["not", "an", "object"])

    monkeypatch.setattr(httpx, "Client", _BadBocha)
    tool = WebSearchTool(backend="bocha", api_key="k")
    with pytest.raises(Exception):
        tool._search_bocha("q", num_results=5)


def test_format_results_coerces_non_string_snippet_and_caps():
    from agentao.tools.web import _format_search_results

    items = [
        {"title": "T", "url": "u", "snippet": ["a", "b"]},   # non-string → no crash
        {"title": "T2", "url": "u2", "snippet": "x" * 500},  # long → capped
    ]
    out = _format_search_results("q", items)
    assert "['a', 'b']" in out
    assert "…" in out and "x" * 301 not in out


# ── DuckDuckGo bot-challenge guard ─────────────────────────────────────────
#
# html.duckduckgo.com answers a captcha page (HTTP 202, no results container)
# instead of results. Parsing it yields zero ``div.result`` nodes, which used
# to be formatted as a confident "No search results found" — a silent failure
# of the whole search surface, since duckduckgo is last in every auto chain.


#: Abridged from a live capture (2026-07-22). No ``#links`` container.
_DDG_CHALLENGE = """<!DOCTYPE html><html lang="en"><head><title>DuckDuckGo</title></head>
<body><a href="/"></a><center id="lite_wrapper">
Unfortunately, bots use DuckDuckGo too. Please complete the following challenge
to confirm this search was made by a human. Select all squares containing a duck:
</center><img src="x"></body></html>"""

#: A real zero-hit SERP still renders the ``#links`` container.
_DDG_NO_RESULTS = """<!DOCTYPE html><html><body>
<div class="results" id="links"><div class="no-results">No results.</div></div>
</body></html>"""

#: A real SERP with one hit.
_DDG_ONE_HIT = """<!DOCTYPE html><html><body><div class="results" id="links">
<div class="result"><a class="result__a" href="https://ex.com">Title</a>
<a class="result__snippet">Snip.</a></div></div></body></html>"""


class _HtmlResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        return None  # 2xx (incl. 202) never raises here


def _ddg_client(text, status_code=200):
    class _C(_FakeClient):
        def get(self, url, params=None, headers=None):
            return _HtmlResp(text, status_code)

    return _C


def test_ddg_challenge_page_raises_not_empty(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setattr(httpx, "Client", _ddg_client(_DDG_CHALLENGE, 202))
    tool = WebSearchTool(backend="duckduckgo")
    with pytest.raises(ValueError, match="bot-challenge"):
        tool._search_duckduckgo("q", num_results=5)


def test_ddg_challenge_body_raises_even_on_200(monkeypatch):
    # Don't lean on the 202 alone — a challenge served as 200 is still not a
    # result set, and the missing ``#links`` container is the semantic signal.
    _clear_keys(monkeypatch)
    monkeypatch.setattr(httpx, "Client", _ddg_client(_DDG_CHALLENGE, 200))
    tool = WebSearchTool(backend="duckduckgo")
    with pytest.raises(ValueError, match="bot-challenge"):
        tool._search_duckduckgo("q", num_results=5)


def test_ddg_genuine_no_results_does_not_raise(monkeypatch):
    # The guard must not swallow a real zero-hit answer: that is terminal, not
    # an error, and must still read as "No search results found".
    _clear_keys(monkeypatch)
    monkeypatch.setattr(httpx, "Client", _ddg_client(_DDG_NO_RESULTS, 200))
    tool = WebSearchTool(backend="duckduckgo")
    assert tool._search_duckduckgo("q", num_results=5) == "No search results found for: q"


def test_ddg_normal_results_still_parse(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setattr(httpx, "Client", _ddg_client(_DDG_ONE_HIT, 200))
    tool = WebSearchTool(backend="duckduckgo")
    out = tool._search_duckduckgo("q", num_results=5)
    assert "1. Title" in out and "https://ex.com" in out and "Snip." in out


def test_ddg_challenge_surfaces_as_error_when_sole_backend(monkeypatch):
    # The end-to-end consequence: keyless auto mode is a one-element chain, so
    # the challenge must reach the caller as an error rather than "no results".
    _clear_keys(monkeypatch)
    monkeypatch.setattr(httpx, "Client", _ddg_client(_DDG_CHALLENGE, 202))
    tool = WebSearchTool()
    assert tool._chain == ["duckduckgo"]
    out = tool.execute("q")
    assert out.startswith("Error performing duckduckgo search:")
    assert "bot-challenge" in out
    assert "No search results found" not in out
