"""`web_fetch`'s local headless-browser fallback (Playwright).

The predecessor of this path (crawl4ai, 0.4.7–0.4.17) shipped with **zero**
tests — the only reference to it anywhere under ``tests/`` was a
``monkeypatch.delenv`` clearing its env var. This file covers the swap:
selection, dispatch, the two distinct setup failures, and the SSRF invariant
that a blocked target must never reach a fallback.

``test_playwright_api_matches_our_call_sites`` deliberately probes the *real*
installed Playwright instead of a fake. Hand-written fakes answer whatever the
code under test asks them, so they can only confirm what the author already
believed — the same blind spot that let an SDK change a tuple's arity under
agentao without a single test going red.
"""

import asyncio
import inspect
import logging
import sys

import pytest

from agentao.security.url_policy import UrlPolicyError
from agentao.tools import web as web_mod
from agentao.tools.web import WebFetchTool


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)
    monkeypatch.delenv("AGENTAO_WEB_FETCH_ALLOW_CIDRS", raising=False)


def _tool(monkeypatch, fallback: str | None) -> WebFetchTool:
    if fallback is not None:
        monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", fallback)
    return WebFetchTool()


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "none"),
        ("", "none"),
        ("none", "none"),
        ("jina", "jina"),
        ("playwright", "playwright"),
        ("PlayWright", "playwright"),
        ("  playwright  ", "playwright"),
        ("bogus", "none"),
    ],
)
def test_fallback_setting_parsing(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", raw)
    assert web_mod._read_fallback_setting() == expected


def test_retired_crawl4ai_value_maps_to_playwright_and_warns(monkeypatch, caplog):
    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "crawl4ai")
    with caplog.at_level(logging.WARNING, logger="agentao.tools.web"):
        assert web_mod._read_fallback_setting() == "playwright"

    # The substitution must be loud: the operator configured one engine and is
    # getting another. Silence here would be the same class of bug as a silent
    # third-party proxy.
    message = caplog.text
    assert "crawl4ai" in message
    assert "retired" in message
    assert "playwright" in message


def test_crawl4ai_is_not_advertised_as_a_valid_value():
    assert "crawl4ai" not in web_mod._VALID_FALLBACKS


# --------------------------------------------------------------------------
# description — the audit surface the LLM and the operator both read
# --------------------------------------------------------------------------


def test_description_discloses_local_rendering(monkeypatch):
    description = _tool(monkeypatch, "playwright").description
    assert "headless browser" in description
    assert "not sent to any third party" in description


def test_description_discloses_jina_is_third_party(monkeypatch):
    assert "r.jina.ai" in _tool(monkeypatch, "jina").description


def test_description_states_when_no_fallback_configured(monkeypatch):
    assert "No JS-rendering fallback" in _tool(monkeypatch, None).description


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


def test_none_returns_none_so_caller_keeps_primary_result(monkeypatch):
    assert _tool(monkeypatch, None)._run_fallback("https://x.test", reason="t") is None


def test_playwright_fallback_renders_and_extracts_text(monkeypatch):
    async def fake_render(url):
        assert url == "https://x.test/"
        return (
            "<html><head><style>.a{color:red}</style></head>"
            "<body><script>var x=1</script><p>Hydrated content</p></body></html>"
        )

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    out = _tool(monkeypatch, "playwright")._run_fallback(
        "https://x.test/", reason="JS rendering detected"
    )

    assert "Fallback: playwright (local headless browser)" in out
    assert "Hydrated content" in out
    # _extract_text drops script/style — the fallback reuses it rather than
    # carrying a second extraction implementation.
    assert "var x=1" not in out
    assert "color:red" not in out


def test_jina_still_dispatches_to_jina(monkeypatch):
    monkeypatch.setattr(web_mod, "_fetch_via_jina", lambda url: "# proxied")
    out = _tool(monkeypatch, "jina")._run_fallback("https://x.test/", reason="t")
    assert "r.jina.ai" in out
    assert "# proxied" in out


# --------------------------------------------------------------------------
# the two setup failures, which are separate and both actionable
# --------------------------------------------------------------------------


def test_missing_playwright_package_names_both_install_steps(monkeypatch):
    # Force the import to fail regardless of whether the extra is installed in
    # the venv running this test.
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)

    out = _tool(monkeypatch, "playwright")._run_fallback("https://x.test/", reason="t")
    assert "agentao[playwright]" in out
    assert "playwright install chromium" in out


def test_missing_browser_binary_is_reported_not_swallowed(monkeypatch):
    # Package present, Chromium never downloaded — Playwright raises at launch,
    # long after the import succeeded.
    async def fake_render(url):
        raise RuntimeError(
            "Executable doesn't exist at /root/.cache/ms-playwright/chromium-1234"
        )

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    out = _tool(monkeypatch, "playwright")._run_fallback("https://x.test/", reason="t")
    assert "Error:" in out
    assert "Executable doesn't exist" in out


def test_fallback_runs_from_inside_a_running_event_loop(monkeypatch):
    """An async host driving this sync tool must not deadlock or crash.

    ``asyncio.run`` refuses to nest, so `_run_async` hands the coroutine to a
    worker thread with its own loop.
    """
    async def fake_render(url):
        return "<html><body><p>Rendered under a live loop</p></body></html>"

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    tool = _tool(monkeypatch, "playwright")

    async def host():
        return tool._run_fallback("https://x.test/", reason="t")

    out = asyncio.run(host())
    assert "Rendered under a live loop" in out


# --------------------------------------------------------------------------
# SSRF invariant
# --------------------------------------------------------------------------


def test_blocked_target_never_reaches_the_headless_browser(monkeypatch):
    """A URL rejected by the outbound policy must not be handed to Chromium.

    The browser follows redirects and JS navigation on its own, so letting a
    blocked target through here would reopen exactly the hole `url_policy`
    closes on the httpx path.
    """
    calls = []

    async def fake_render(url):
        calls.append(url)
        return "<html><body>should never happen</body></html>"

    def boom(client, url, **kwargs):
        raise UrlPolicyError("resolves to loopback")

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    monkeypatch.setattr(web_mod, "guarded_get", boom)

    out = _tool(monkeypatch, "playwright").execute("http://169.254.169.254/latest/")

    assert calls == []
    assert "blocked outbound request" in out


# --------------------------------------------------------------------------
# API-drift guard against the real dependency
# --------------------------------------------------------------------------


def test_playwright_api_matches_our_call_sites():
    """Assert the kwargs `_render_with_playwright` passes still exist upstream.

    Skipped when the optional extra isn't installed; CI's playwright job is
    what makes this meaningful.
    """
    async_api = pytest.importorskip("playwright.async_api")

    assert hasattr(async_api, "async_playwright")

    goto = inspect.signature(async_api.Page.goto).parameters
    assert "wait_until" in goto
    assert "timeout" in goto

    wait_for_load_state = inspect.signature(async_api.Page.wait_for_load_state).parameters
    assert "state" in wait_for_load_state
    assert "timeout" in wait_for_load_state

    assert "user_agent" in inspect.signature(async_api.Browser.new_page).parameters
    assert "headless" in inspect.signature(async_api.BrowserType.launch).parameters
