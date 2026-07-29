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
import time
from types import SimpleNamespace

import pytest

from agentao.security.url_policy import UrlPolicyError
from agentao.tools import web as web_mod
from agentao.tools.web import WebFetchTool


#: Long enough that paying it would be unmistakable in the wall-clock
#: assertion below, short enough not to drag the suite if the fix regresses.
_STALL_SECONDS = 3.0


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
    assert web_mod._read_fallback_setting()[0] == expected


def test_retired_crawl4ai_value_maps_to_playwright_and_warns(monkeypatch, caplog):
    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", "crawl4ai")
    with caplog.at_level(logging.WARNING, logger="agentao.tools.web"):
        assert web_mod._read_fallback_setting() == ("playwright", "crawl4ai")

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
    assert _tool(monkeypatch, None)._run_fallback("https://x.test", reason="t") == (None, None)


def test_playwright_fallback_renders_and_extracts_text(monkeypatch):
    async def fake_render(url, **kwargs):
        assert url == "https://x.test/"
        return 200, (
            "<html><head><style>.a{color:red}</style></head>"
            "<body><script>var x=1</script><p>Hydrated content</p></body></html>"
        )

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    out, error = _tool(monkeypatch, "playwright")._run_fallback(
        "https://x.test/", reason="JS rendering detected"
    )
    assert error is None

    assert "Fallback: playwright (local headless browser)" in out
    assert "Hydrated content" in out
    # _extract_text drops script/style — the fallback reuses it rather than
    # carrying a second extraction implementation.
    assert "var x=1" not in out
    assert "color:red" not in out


def test_jina_still_dispatches_to_jina(monkeypatch):
    monkeypatch.setattr(web_mod, "_fetch_via_jina", lambda url: "# proxied")
    out, error = _tool(monkeypatch, "jina")._run_fallback("https://x.test/", reason="t")
    assert error is None
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

    body, error = _tool(monkeypatch, "playwright")._run_fallback(
        "https://x.test/", reason="t"
    )
    assert body is None
    assert "agentao[playwright]" in error
    assert "playwright install chromium" in error


def test_missing_browser_binary_is_reported_not_swallowed(monkeypatch):
    # Package present, Chromium never downloaded — Playwright raises at launch,
    # long after the import succeeded.
    async def fake_render(url, **kwargs):
        raise RuntimeError(
            "Executable doesn't exist at /root/.cache/ms-playwright/chromium-1234"
        )

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    body, error = _tool(monkeypatch, "playwright")._run_fallback(
        "https://x.test/", reason="t"
    )
    assert body is None
    assert "Executable doesn't exist" in error


def test_fallback_runs_from_inside_a_running_event_loop(monkeypatch):
    """An async host driving this sync tool must not deadlock or crash.

    ``asyncio.run`` refuses to nest, so `_run_async` hands the coroutine to a
    worker thread with its own loop.
    """
    async def fake_render(url, **kwargs):
        return 200, "<html><body><p>Rendered under a live loop</p></body></html>"

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    tool = _tool(monkeypatch, "playwright")

    async def host():
        return tool._run_fallback("https://x.test/", reason="t")

    out, error = asyncio.run(host())
    assert error is None
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

    async def fake_render(url, **kwargs):
        calls.append(url)
        return 200, "<html><body>should never happen</body></html>"

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
    """Assert every upstream name and literal `_render_with_playwright` uses.

    Kwarg names alone are not enough: the call site also depends on
    `Playwright.chromium`, `Page.content()`, `Page.route()`, `Route.abort/
    continue_`, `Request.is_navigation_request()`, `Response.status`, and on the
    string *values* "domcontentloaded" and "networkidle" still being accepted —
    Playwright already marks the latter DISCOURAGED, and its removal would be
    swallowed by the settle handler while CI stayed green.

    Skipped when the optional extra isn't installed; CI installs it in the test
    job and in both publish workflows so this cannot quietly retire.
    """
    async_api = pytest.importorskip("playwright.async_api")

    assert hasattr(async_api, "async_playwright")
    assert hasattr(async_api, "TimeoutError")
    assert isinstance(getattr(async_api.Playwright, "chromium", None), property)

    launch = inspect.signature(async_api.BrowserType.launch).parameters
    assert "headless" in launch
    assert "env" in launch  # credential scrubbing depends on this

    new_context = inspect.signature(async_api.Browser.new_context).parameters
    assert "accept_downloads" in new_context
    assert "ignore_https_errors" in new_context

    goto = inspect.signature(async_api.Page.goto).parameters
    assert "wait_until" in goto
    assert "timeout" in goto

    wait_for_load_state = inspect.signature(async_api.Page.wait_for_load_state).parameters
    assert "state" in wait_for_load_state
    assert "timeout" in wait_for_load_state

    # The literals, not just the parameter names.
    assert "domcontentloaded" in str(goto["wait_until"].annotation)
    assert "networkidle" in str(wait_for_load_state["state"].annotation)

    assert callable(getattr(async_api.BrowserContext, "route_web_socket", None)), (
        "route_web_socket landed in Playwright 1.48 and the extra floors there;"
        " without it a page's WebSockets bypass the outbound guard"
    )
    for owner, name in [
        (async_api.WebSocketRoute, "connect_to_server"),
        (async_api.WebSocketRoute, "close"),
        (async_api.Page, "content"),
        (async_api.Page, "goto"),
        (async_api.Browser, "new_context"),
        (async_api.BrowserContext, "route"),  # context-level: covers popups
        (async_api.BrowserContext, "new_page"),
        (async_api.Browser, "close"),
        (async_api.Route, "abort"),
        (async_api.Route, "continue_"),
        (async_api.Request, "is_navigation_request"),
    ]:
        assert callable(getattr(owner, name, None)), f"{owner.__name__}.{name}"

    assert isinstance(getattr(async_api.Response, "status", None), property)


# --------------------------------------------------------------------------
# the wrapper body itself — executed, not mocked away
# --------------------------------------------------------------------------


class _FakeRoute:
    def __init__(self, url, navigation=True):
        self.request = SimpleNamespace(
            url=url, is_navigation_request=lambda: navigation
        )
        self.action = None

    async def continue_(self):
        self.action = "continue"

    async def abort(self):
        self.action = "abort"


class _FakePage:
    def __init__(self, *, html="<html><body>ok</body></html>", status=200):
        self._html = html
        self._status = status
        self.goto_kwargs = None
        self.settle_kwargs = None
        self.route_handler = None
        self.ws_route_handler = None
        self.content_calls = 0

    async def route(self, pattern, handler):
        self.route_pattern = pattern
        self.route_handler = handler

    async def goto(self, url, **kwargs):
        self.goto_kwargs = kwargs
        return SimpleNamespace(status=self._status)

    async def wait_for_load_state(self, state, **kwargs):
        self.settle_kwargs = {"state": state, **kwargs}

    async def content(self):
        self.content_calls += 1
        return self._html


class _FakeContext:
    def __init__(self, page):
        self._page = page
        self.route_pattern = None
        self.route_handler = None
        self.ws_route_pattern = None
        self.ws_route_handler = None
        self.new_page_kwargs = None

    async def route(self, pattern, handler):
        self.route_pattern = pattern
        self.route_handler = handler
        # Mirror Playwright: a context route covers every page in the context,
        # popups included. The tests read the handler off the page for
        # convenience, so publish it there too.
        self._page.route_handler = handler

    async def route_web_socket(self, pattern, handler):
        self.ws_route_pattern = pattern
        self.ws_route_handler = handler
        self._page.ws_route_handler = handler

    async def new_page(self, **kwargs):
        self.new_page_kwargs = kwargs
        return self._page


class _FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False
        self.context = _FakeContext(page)
        self.new_page_kwargs = None

    async def new_context(self, **kwargs):
        self.new_context_kwargs = kwargs
        return self.context

    async def new_page(self, **kwargs):  # must NOT be used: bypasses the guard
        raise AssertionError(
            "browser.new_page() creates an implicit context, leaving popups"
            " unguarded; use new_context() + context.new_page()"
        )

    async def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = SimpleNamespace(launch=self._launch)
        self._browser = browser
        self.launch_kwargs = None

    async def _launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self._browser


class _FakeDriverProc:
    def __init__(self):
        self.pid = 4242
        self.killed = False

    def kill(self):
        self.killed = True


class _FakeCtx:
    """Mirrors `PlaywrightContextManager`: the driver handle hangs off the
    *context manager*, not off the object `__aenter__` returns."""

    def __init__(self, playwright, *, enter=None, exit=None):
        self._playwright = playwright
        self._enter = enter
        self._exit = exit
        self.driver_proc = _FakeDriverProc()
        self._connection = SimpleNamespace(
            _transport=SimpleNamespace(_proc=self.driver_proc)
        )

    async def __aenter__(self):
        if self._enter is not None:
            await self._enter()
        return self._playwright

    async def __aexit__(self, *exc):
        if self._exit is not None:
            await self._exit()
        return False


def _install_fake_playwright(monkeypatch, playwright, *, enter=None, exit=None):
    ctx = _FakeCtx(playwright, enter=enter, exit=exit)
    module = SimpleNamespace(
        async_playwright=lambda: ctx,
        TimeoutError=type("PlaywrightTimeoutError", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "playwright", SimpleNamespace(async_api=module))
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    return ctx


def test_render_wrapper_executes_and_scrubs_child_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-reach-chromium")
    page = _FakePage(html="<html><body><p>Rendered</p></body></html>", status=200)
    browser = _FakeBrowser(page)
    pw = _FakePlaywright(browser)
    _install_fake_playwright(monkeypatch, pw)

    status, html = asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    assert (status, "Rendered" in html) == (200, True)
    assert page.goto_kwargs["wait_until"] == "domcontentloaded"
    assert page.settle_kwargs["state"] == "networkidle"
    assert browser.closed is True
    # The one child that runs attacker-controlled JS must not inherit
    # agentao's provider credentials.
    assert "OPENAI_API_KEY" not in pw.launch_kwargs["env"]
    # And the malformed httpx UA must not be forced onto a real browser.
    assert "user_agent" not in browser.context.new_page_kwargs


def test_hanging_driver_startup_is_bounded_and_the_driver_killed(monkeypatch):
    """A driver that spawns but never handshakes must not hang __aenter__.

    Nothing else covers this: the total deadline wraps _render_page, which a
    stalled startup never reaches.
    """
    monkeypatch.setattr(web_mod, "_BROWSER_STARTUP_TIMEOUT_S", 0.05)

    async def never_finishes():
        await asyncio.sleep(30)

    ctx = _install_fake_playwright(
        monkeypatch, _FakePlaywright(_FakeBrowser(_FakePage())), enter=never_finishes
    )

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    assert ctx.driver_proc.killed is True


def test_hanging_driver_teardown_is_bounded_and_the_driver_killed(monkeypatch):
    """Bounding browser.close() alone leaves __aexit__ waiting on the same
    wedged driver — the exact case the budget exists for."""
    monkeypatch.setattr(web_mod, "_BROWSER_CLOSE_TIMEOUT_S", 0.05)

    async def never_finishes():
        await asyncio.sleep(30)

    page = _FakePage()
    ctx = _install_fake_playwright(
        monkeypatch, _FakePlaywright(_FakeBrowser(page)), exit=never_finishes
    )

    status, _ = asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    # The render still succeeds — teardown trouble must not destroy the result.
    assert status == 200
    assert ctx.driver_proc.killed is True


def test_driver_handle_lives_on_the_context_manager_not_the_playwright_object():
    """Guards the private-attribute walk against the shape it actually has.

    `Playwright` (what __aenter__ returns) carries only _impl_obj / _loop /
    _wrap_handler; the connection belongs to PlaywrightContextManager. A walk
    starting from the returned object stops at its first hop and silently
    never kills anything.
    """
    async_api = pytest.importorskip("playwright.async_api")
    from playwright.async_api._context_manager import PlaywrightContextManager

    assert not hasattr(async_api.Playwright, "_connection")
    assert "_connection" in inspect.getsource(PlaywrightContextManager.__init__)


def test_render_wrapper_closes_browser_when_navigation_fails(monkeypatch):
    page = _FakePage()

    async def boom(url, **kwargs):
        raise RuntimeError("navigation exploded")

    page.goto = boom
    browser = _FakeBrowser(page)
    _install_fake_playwright(monkeypatch, _FakePlaywright(browser))

    with pytest.raises(RuntimeError, match="navigation exploded"):
        asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    assert browser.closed is True


def test_guard_is_installed_on_the_context_so_popups_are_covered(monkeypatch):
    """A route registered on one Page does not cover a popup that page opens
    with window.open — the popup's first request would reach an internal
    target before any policy ran. _FakeBrowser.new_page() raises to keep the
    implicit-context shortcut from creeping back in."""
    page = _FakePage()
    browser = _FakeBrowser(page)
    _install_fake_playwright(monkeypatch, _FakePlaywright(browser))

    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    assert browser.context.route_pattern == "**/*"
    assert browser.context.route_handler is not None
    # accept_downloads defaults to true upstream; web_fetch never consumes a
    # download, so leaving it on only lets a hostile page write to our disk.
    assert browser.new_context_kwargs["accept_downloads"] is False
    # A deliberate divergence from crawl4ai, which defaulted this to True. The
    # fallback runs *after* the certificate-verifying httpx path failed, so
    # ignoring cert errors here would render an expired or attacker-supplied
    # certificate's page and hand it to the model as though it were fine.
    assert browser.new_context_kwargs["ignore_https_errors"] is False


def test_unexpected_settle_failure_is_not_returned_as_a_successful_render(
    monkeypatch,
):
    """An unhydrated DOM must not come back as a success.

    The settle step absorbs its own timeout by design. Anything else — a
    crashed renderer, or Playwright dropping the "networkidle" literal — is a
    real failure, and continuing to page.content() would hand back exactly the
    unhydrated page this fallback exists to avoid.
    """
    page = _FakePage()

    async def bad_settle(state, **kwargs):
        raise ValueError('state: expected one of (load|domcontentloaded)')

    page.wait_for_load_state = bad_settle
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))

    with pytest.raises(ValueError):
        asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    assert page.content_calls == 0


def test_settle_timeout_is_still_absorbed(monkeypatch):
    """The documented case: long-poll pages never go idle and their DOM is
    already usable, so the expected timeout must not fail the render."""
    page = _FakePage(html="<html><body><p>Usable anyway</p></body></html>")
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    timeout_cls = sys.modules["playwright.async_api"].TimeoutError

    async def timing_out(state, **kwargs):
        raise timeout_cls("Timeout 5000ms exceeded")

    page.wait_for_load_state = timing_out

    status, html = asyncio.run(web_mod._render_with_playwright("https://x.test/"))
    assert (status, "Usable anyway" in html) == (200, True)


@pytest.mark.parametrize("stall_at", ["launch", "new_context", "route", "new_page"])
def test_a_wedged_driver_during_setup_is_bounded_and_the_browser_closed(
    monkeypatch, stall_at
):
    """launch / new_context / route / new_page are driver channel calls with no
    timeout of their own. Wrapping only _render_page left every one of them
    able to hang web_fetch with the browser left running."""
    monkeypatch.setattr(web_mod, "_BROWSER_TOTAL_TIMEOUT_S", 0.05)
    page = _FakePage()
    browser = _FakeBrowser(page)
    pw = _FakePlaywright(browser)

    async def stall(*args, **kwargs):
        await asyncio.sleep(30)

    if stall_at == "launch":
        pw._launch = stall
        pw.chromium = SimpleNamespace(launch=stall)
    elif stall_at == "new_context":
        browser.new_context = stall
    elif stall_at == "route":
        browser.context.route = stall
    else:
        browser.context.new_page = stall

    _install_fake_playwright(monkeypatch, pw)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    # A browser that did get launched must still be closed on the way out.
    assert browser.closed is (stall_at != "launch")


def test_render_wrapper_reports_http_status(monkeypatch):
    page = _FakePage(html="<html><body>404 not found</body></html>", status=404)
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))

    status, _ = asyncio.run(web_mod._render_with_playwright("https://x.test/gone"))
    assert status == 404


def test_teardown_failure_does_not_mask_the_render_error(monkeypatch):
    page = _FakePage()

    async def boom(url, **kwargs):
        raise RuntimeError("the real failure")

    page.goto = boom
    browser = _FakeBrowser(page)

    async def bad_close():
        raise RuntimeError("teardown noise")

    browser.close = bad_close
    _install_fake_playwright(monkeypatch, _FakePlaywright(browser))

    # The navigation error must survive; the teardown error is logged.
    with pytest.raises(RuntimeError, match="the real failure"):
        asyncio.run(web_mod._render_with_playwright("https://x.test/"))


def test_in_browser_navigation_to_blocked_target_is_aborted(monkeypatch):
    """Chromium chases redirects itself — the URL policy must re-apply."""
    page = _FakePage()
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    blocked = _FakeRoute("http://169.254.169.254/latest/meta-data/")
    asyncio.run(page.route_handler(blocked))
    assert blocked.action == "abort"

    # A globally-routable IP literal, so the assertion does not depend on the
    # DNS the test host happens to have — a fake-IP proxy (Clash/V2Ray maps
    # every name into 198.18.0.0/15) would otherwise make a hostname here
    # resolve to a non-public address and flip this to "abort".
    allowed = _FakeRoute("https://1.1.1.1/next")
    asyncio.run(page.route_handler(allowed))
    assert allowed.action == "continue"


def test_route_guard_fails_closed_on_an_unexpected_validator_error(monkeypatch):
    """An error that is not a policy verdict must block, not wave through."""
    page = _FakePage()
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    def exploding_validator(url, **kwargs):
        raise OSError("resolver blew up")

    monkeypatch.setattr(web_mod, "validate_outbound_url", exploding_validator)
    route = _FakeRoute("https://1.1.1.1/next")
    asyncio.run(page.route_handler(route))
    assert route.action == "abort"


def test_route_action_failure_does_not_escape_the_handler(monkeypatch):
    """A page torn down mid-verdict makes abort/continue_ raise; an exception
    escaping the handler leaves the request hanging until the nav timeout."""
    page = _FakePage()
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    route = _FakeRoute("https://1.1.1.1/next")

    async def dead_continue():
        raise RuntimeError("Target page, context or browser has been closed")

    route.continue_ = dead_continue
    asyncio.run(page.route_handler(route))  # must not raise


def test_a_stalled_dns_lookup_blocks_and_does_not_freeze_the_loop(monkeypatch):
    """`validate_outbound_url` calls socket.getaddrinfo, which blocks.

    Run on the event-loop thread it would stall the very timer enforcing the
    render ceiling, so a page-controlled hostname that resolves slowly could
    defeat it. The check runs in a worker thread with its own budget, and a
    stall fails closed.
    """
    monkeypatch.setattr(web_mod, "_POLICY_CHECK_TIMEOUT_S", 0.05)
    page = _FakePage()
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    def stalling_validator(url, **kwargs):
        time.sleep(_STALL_SECONDS)

    monkeypatch.setattr(web_mod, "validate_outbound_url", stalling_validator)

    async def drive():
        # The loop must stay responsive while the lookup stalls: this ticker
        # cannot advance if the resolution is running on the loop thread.
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        spinner = asyncio.create_task(ticker())
        route = _FakeRoute("https://slow-dns.test/asset.js", navigation=False)
        await page.route_handler(route)
        spinner.cancel()
        return route.action, ticks

    started = time.monotonic()
    action, ticks = asyncio.run(drive())
    elapsed = time.monotonic() - started

    assert action == "abort"
    assert ticks > 0, "the event loop was blocked by the DNS lookup"
    # And the stalled lookup must not hold up the way out either. asyncio.run
    # joins the *default* executor at loop shutdown, so `asyncio.to_thread`
    # would pay the full stall here even though wait_for already returned —
    # cancelling the future does not cancel the thread. A daemon thread is
    # abandoned instead.
    assert elapsed < _STALL_SECONDS / 2, (
        f"loop shutdown waited on the stalled lookup ({elapsed:.2f}s)"
    )


def test_concurrent_requests_to_one_origin_share_a_single_lookup(monkeypatch):
    """Dedup must cover in-flight lookups, not just finished verdicts.

    A page can schedule a hundred requests to one origin before the first
    resolution returns; a cache holding only completed verdicts would start a
    hundred threads for them.
    """
    page = _FakePage()
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    calls = []

    def slow_validator(url, **kwargs):
        calls.append(url)
        time.sleep(0.05)

    monkeypatch.setattr(web_mod, "validate_outbound_url", slow_validator)

    async def drive():
        routes = [
            _FakeRoute(f"https://cdn.example/a{i}.js", navigation=False)
            for i in range(25)
        ]
        await asyncio.gather(*(page.route_handler(r) for r in routes))
        return routes

    routes = asyncio.run(drive())
    assert all(r.action == "continue" for r in routes)
    assert len(calls) == 1, f"one origin should cost one lookup, got {len(calls)}"


def test_a_flood_of_distinct_origins_cannot_exhaust_threads(monkeypatch):
    """Randomised hostnames miss the per-origin cache by construction.

    Since a timed-out lookup thread is deliberately abandoned, an unbounded
    spray would accumulate threads until the process died. Over the cap the
    check is refused, and a refusal blocks the request.
    """
    monkeypatch.setattr(web_mod, "_POLICY_THREAD_HARD_CAP", 4)
    monkeypatch.setattr(web_mod, "_POLICY_CHECK_CONCURRENCY", 64)
    monkeypatch.setattr(web_mod, "_POLICY_CHECK_TIMEOUT_S", 0.05)
    page = _FakePage()
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    peak = 0

    def stalling_validator(url, **kwargs):
        nonlocal peak
        peak = max(peak, web_mod._live_policy_threads)
        time.sleep(0.3)

    monkeypatch.setattr(web_mod, "validate_outbound_url", stalling_validator)

    async def drive():
        routes = [
            _FakeRoute(f"https://r{i}.evil.test/x.js", navigation=False)
            for i in range(40)
        ]
        await asyncio.gather(*(page.route_handler(r) for r in routes))
        return routes

    routes = asyncio.run(drive())

    assert peak <= 4, f"live lookup threads exceeded the cap: {peak}"
    assert all(r.action == "abort" for r in routes)


def test_subresource_requests_are_guarded_too(monkeypatch):
    """"It never reaches the model" is false for subresources.

    A permissive-CORS or JSONP endpoint lets the page's own JavaScript read an
    internal response and write it into the DOM — which is precisely what
    page.content() returns — and a no-CORS request still hits an internal
    service as a side effect even when the response is opaque.
    """
    page = _FakePage()
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    subresource = _FakeRoute("http://127.0.0.1/img.png", navigation=False)
    asyncio.run(page.route_handler(subresource))
    assert subresource.action == "abort"

    xhr = _FakeRoute("http://169.254.169.254/latest/meta-data/", navigation=False)
    asyncio.run(page.route_handler(xhr))
    assert xhr.action == "abort"


class _FakeWebSocketRoute:
    def __init__(self, url):
        self.url = url
        self.action = None

    def connect_to_server(self):  # plain method in Playwright's async API
        self.action = "connect"

    async def close(self, **kwargs):
        self.action = "close"


def test_websockets_are_guarded_by_their_own_route(monkeypatch):
    """WebSockets do not pass through `context.route`.

    Playwright gives them a separate interception API, so without
    `route_web_socket` rendered JavaScript could open ws://169.254.169.254/,
    reach an internal endpoint, and copy accepted frames into the DOM that
    page.content() returns.
    """
    page = _FakePage()
    browser = _FakeBrowser(page)
    _install_fake_playwright(monkeypatch, _FakePlaywright(browser))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    assert browser.context.ws_route_pattern == "**/*"
    handler = browser.context.ws_route_handler
    assert handler is not None

    blocked = _FakeWebSocketRoute("ws://169.254.169.254/")
    asyncio.run(handler(blocked))
    assert blocked.action == "close"

    allowed = _FakeWebSocketRoute("wss://1.1.1.1/live")
    asyncio.run(handler(allowed))
    assert allowed.action == "connect"


def test_websocket_scheme_is_mapped_for_the_http_only_validator(monkeypatch):
    """`validate_outbound_url` only speaks http(s); ws/wss must be translated
    rather than rejected outright (or, worse, waved through)."""
    page = _FakePage()
    browser = _FakeBrowser(page)
    _install_fake_playwright(monkeypatch, _FakePlaywright(browser))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    seen = []

    def recording_validator(url, **kwargs):
        seen.append(url)

    monkeypatch.setattr(web_mod, "validate_outbound_url", recording_validator)
    asyncio.run(
        browser.context.ws_route_handler(_FakeWebSocketRoute("wss://host.test:8443/s"))
    )
    assert seen == ["https://host.test:8443/s"]


def test_non_network_schemes_are_not_run_through_the_url_policy(monkeypatch):
    """data:/blob:/about: never leave the machine, and the policy rejects any
    non-http(s) scheme — passing them through it would break normal rendering.
    """
    page = _FakePage()
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    for url in (
        "data:image/png;base64,iVBORw0KGgo=",
        "blob:https://x.test/9f3c",
        "about:blank",
    ):
        route = _FakeRoute(url, navigation=False)
        asyncio.run(page.route_handler(route))
        assert route.action == "continue", url


def test_policy_verdicts_are_cached_per_origin(monkeypatch):
    """A page pulling many assets from one origin must cost one DNS lookup."""
    page = _FakePage()
    _install_fake_playwright(monkeypatch, _FakePlaywright(_FakeBrowser(page)))
    asyncio.run(web_mod._render_with_playwright("https://x.test/"))

    seen = []

    def counting_validator(url, **kwargs):
        seen.append(url)

    monkeypatch.setattr(web_mod, "validate_outbound_url", counting_validator)
    for path in ("/a.js", "/b.css", "/c.png"):
        route = _FakeRoute(f"https://cdn.example/{path}", navigation=False)
        asyncio.run(page.route_handler(route))
        assert route.action == "continue"

    assert len(seen) == 1, seen

    # A different port is a different origin and must be validated again.
    other = _FakeRoute("https://cdn.example:8443/d.js", navigation=False)
    asyncio.run(page.route_handler(other))
    assert len(seen) == 2, seen
