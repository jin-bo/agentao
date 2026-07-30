"""`web_fetch` as an `AsyncToolBase` — the port off the nested-loop bridge.

Until 0.4.18 ``WebFetchTool`` was a sync ``Tool`` that drove its own event loop
to reach the async Playwright API. From inside a caller's running loop that is
impossible to do without blocking it: a synchronous return value cannot be
produced while yielding, so the helper submitted the coroutine to a worker
thread and blocked on ``Future.result()`` on the loop thread. Nothing else on
that loop ran, and a ``CancelledError`` could not be delivered.

The fix is not a better bridge — there isn't one — it is to stop needing a
bridge. These tests pin the properties that follow from the tool being async:
the render happens on the *caller's* loop, the loop stays responsive, and the
blocking resolver is the only thing still on a thread.

The sync ``execute`` wrapper is kept for non-async embedders and is tested here
too, including the fact that it still blocks — that cost is the reason
``async_execute`` exists, so it is asserted rather than left implied.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentao.security import url_policy as url_policy_mod
from agentao.security.url_policy import UrlPolicyError
from agentao.tools import web as web_mod
from agentao.tools.base import AsyncToolBase, Tool
from agentao.tools.web import WebFetchTool

#: Long enough that paying it would be unmistakable in a wall-clock assertion,
#: short enough not to drag the suite.
_SLOW_S = 0.3

#: Carries a `_JS_MARKERS` entry, so `_needs_js_rendering` fires and the primary
#: path hands off to the fallback.
_JS_SHELL = '<html><body><script id="__NEXT_DATA__">{}</script></body></html>'


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)
    monkeypatch.delenv("AGENTAO_WEB_FETCH_ALLOW_CIDRS", raising=False)


class _FakeResponse:
    def __init__(self, text=_JS_SHELL, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        return None


def _tool(monkeypatch, fallback="playwright") -> WebFetchTool:
    monkeypatch.setenv("AGENTAO_WEB_FETCH_FALLBACK", fallback)
    return WebFetchTool()


def _stub_primary(monkeypatch, response=None):
    """Answer the httpx leg so tests can focus on what follows it."""
    async def fake_get(client, url, **kwargs):
        return response if response is not None else _FakeResponse()

    monkeypatch.setattr(web_mod, "guarded_get_async", fake_get)


async def _ticks_while(body) -> tuple[object, int]:
    """Run ``body()`` with a 10ms ticker alongside; return its result and ticks.

    The ticker is the measurement: it can only advance if the loop is free.
    """
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    spinner = asyncio.create_task(ticker())
    try:
        result = await body()
    finally:
        spinner.cancel()
    return result, ticks


# --------------------------------------------------------------------------
# the class contract — what puts the tool on the bridged dispatch path
# --------------------------------------------------------------------------


def test_web_fetch_is_an_async_tool():
    """`ToolExecutor` picks its dispatch path with `isinstance(tool,
    AsyncToolBase)`; being a sync `Tool` is what forced the nested loop."""
    tool = WebFetchTool()
    assert isinstance(tool, AsyncToolBase)
    assert not isinstance(tool, Tool)
    assert inspect.iscoroutinefunction(tool.async_execute)


def test_the_metadata_surface_is_unchanged_by_the_port():
    """`AsyncToolBase` and `Tool` share `_BaseTool`, so none of this moved —
    a schema or confirmation regression here would be silent in production."""
    tool = WebFetchTool()
    assert tool.name == "web_fetch"
    assert tool.requires_confirmation is True
    assert tool.is_read_only is True
    schema = tool.to_openai_format()
    assert schema["function"]["name"] == "web_fetch"
    assert set(schema["function"]["parameters"]["properties"]) == {"url", "extract_text"}


def test_web_search_deliberately_stays_synchronous():
    """Scoped, not overlooked. `web_search` never drove its own loop; it is
    sync-blocking in exactly the way every other built-in tool is, and singling
    it out would be arbitrary. Recorded so the asymmetry reads as a decision."""
    assert isinstance(web_mod.WebSearchTool(), Tool)


def test_the_registry_registers_the_async_tool(monkeypatch):
    from agentao.tools.base import ToolRegistry

    registry = ToolRegistry()
    registry.register(WebFetchTool())
    assert isinstance(registry.get("web_fetch"), AsyncToolBase)
    # And it serializes for the LLM exactly like a sync tool.
    assert any(s["function"]["name"] == "web_fetch" for s in registry.to_openai_format())


# --------------------------------------------------------------------------
# the point of the port
# --------------------------------------------------------------------------


def test_the_render_runs_on_the_callers_loop_not_a_nested_one(monkeypatch):
    """The direct proof that the bridge is gone.

    The predecessor submitted the coroutine to a worker thread running its own
    `asyncio.run`, so the render observed a different thread *and* a different
    loop than the caller. Awaiting means both are the caller's.
    """
    observed: dict[str, object] = {}

    async def fake_render(url, **kwargs):
        observed["thread"] = threading.current_thread()
        observed["loop"] = asyncio.get_running_loop()
        return 200, "<html><body><p>rendered</p></body></html>"

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    _stub_primary(monkeypatch)
    tool = _tool(monkeypatch)

    async def host():
        out = await tool.async_execute("https://x.test/")
        return out, threading.current_thread(), asyncio.get_running_loop()

    out, caller_thread, caller_loop = asyncio.run(host())

    assert "rendered" in out
    assert observed["thread"] is caller_thread
    assert observed["loop"] is caller_loop


def test_async_execute_leaves_the_loop_free_but_the_sync_wrapper_does_not(monkeypatch):
    """The regression this port exists to prevent, and its counter-example.

    Both halves matter. `async_execute` awaiting a slow render must let the rest
    of the host's loop run — an ACP session servicing another request, a
    cancellation arriving, a heartbeat. The sync `execute` wrapper cannot: it
    owes its caller a return value with no way to yield. Asserting ``== 0``
    there keeps that cost documented instead of forgotten, and keeps this test
    honest about what it is measuring.
    """
    async def slow_render(url, **kwargs):
        await asyncio.sleep(_SLOW_S)
        return 200, "<html><body><p>rendered</p></body></html>"

    monkeypatch.setattr(web_mod, "_render_with_playwright", slow_render)
    _stub_primary(monkeypatch)
    tool = _tool(monkeypatch)

    async def via_async():
        return await _ticks_while(lambda: tool.async_execute("https://x.test/"))

    (out, async_ticks) = asyncio.run(via_async())
    assert "rendered" in out
    assert async_ticks > 0, "awaiting async_execute starved the caller's loop"

    async def via_sync():
        async def call():
            return tool.execute("https://x.test/")

        return await _ticks_while(call)

    (sync_out, sync_ticks) = asyncio.run(via_sync())
    assert "rendered" in sync_out
    assert sync_ticks == 0, (
        "the sync wrapper is expected to block the loop; if it no longer does, "
        "this test is no longer measuring anything"
    )


#: Big enough to take a measurable fraction of a second in `html.parser`, which
#: is what a remote page can hand us: httpx reads the whole body into memory
#: before anything caps it.
_BIG_DOM = (
    "<html><body>"
    + "".join(f"<div class='r'><p>row {i}</p></div>" for i in range(60000))
    + "</body></html>"
)


def test_parsing_a_large_dom_does_not_freeze_the_loop(monkeypatch):
    """Decode + parse + JS-sniff is CPU-bound on unbounded remote input.

    Measured on this DOM (~2.2MB, ~0.7s to parse): run inline on the loop, a
    10ms heartbeat ticks **0** times; on a worker thread it ticks ~31. CPython
    yields the GIL every `sys.getswitchinterval()` (5ms) between bytecodes, so a
    worker shares with the loop instead of locking it out, and cancellation stays
    deliverable. An earlier comment in `web.py` claimed the opposite — that pure
    Python holds the GIL for its whole run — and the numbers above are why it no
    longer does.

    This is also what the sync tool had for free: agentao dispatched it on an
    executor thread, so the parse was never on the loop. Leaving it inline would
    have made the port a regression on the one axis it exists to improve.

    `extract_text=False` on purpose — it is what makes this measure the *parse*.
    With extraction enabled, its own off-loop hop would supply the ticks and this
    would still pass with the parse moved back onto the loop.
    """
    _stub_primary(monkeypatch, _FakeResponse(text=_BIG_DOM))
    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)
    tool = WebFetchTool()

    async def drive():
        return await _ticks_while(
            lambda: tool.async_execute("https://x.test/", extract_text=False)
        )

    (out, ticks) = asyncio.run(drive())

    assert "Status: 200" in out
    assert ticks > 0, "the event loop was frozen for the whole parse"


def test_text_extraction_is_also_off_the_loop(monkeypatch):
    """The second hop, isolated by making only *it* slow.

    A blocking `time.sleep` is the probe rather than a big DOM: it freezes a loop
    thread outright and cannot be starved of the GIL, so the tick count answers
    "was this call dispatched off the loop" and nothing else.
    """
    def slow_extract(soup):
        time.sleep(_SLOW_S)
        return "extracted"

    _stub_primary(monkeypatch)
    monkeypatch.setattr(web_mod, "_extract_text", slow_extract)
    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)
    tool = WebFetchTool()

    async def drive():
        return await _ticks_while(lambda: tool.async_execute("https://x.test/"))

    (out, ticks) = asyncio.run(drive())

    assert "extracted" in out
    assert ticks > 0, "text extraction ran on the loop thread"


def test_a_rendered_dom_is_also_extracted_off_the_loop(monkeypatch):
    """The fallback's DOM is the largest, least bounded HTML this tool parses."""
    async def fake_render(url, **kwargs):
        return 200, _BIG_DOM

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    _stub_primary(monkeypatch)
    tool = _tool(monkeypatch)

    async def drive():
        return await _ticks_while(lambda: tool.async_execute("https://x.test/"))

    (out, ticks) = asyncio.run(drive())

    assert "Fallback: playwright" in out
    assert ticks > 0, "the event loop was frozen extracting the rendered DOM"


def test_the_html_pool_is_not_the_loops_default_executor(monkeypatch):
    """A fetch must not need a worker from the pool `arun` is sitting in.

    `Agentao.arun` parks the whole `chat()` turn on the loop's **default**
    executor (`agent.py`: `loop.run_in_executor(None, self.chat, ...)`), and that
    worker then blocks in `tool_executor._run_async_tool` waiting on this very
    coroutine. Asking the same pool for a parse worker is a pool-exhaustion
    deadlock: measured, a default executor with one free worker hangs the fetch
    outright, and `max_workers` concurrent turns reproduce it on a normal pool.

    Driven here by starving the default executor completely — every worker busy
    and staying busy — and asserting the fetch still finishes.
    """
    async def fake_render(url, **kwargs):
        return 200, "<html><body><p>done anyway</p></body></html>"

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    _stub_primary(monkeypatch)
    tool = _tool(monkeypatch)

    running = threading.Event()
    release = threading.Event()

    def hog():
        running.set()
        release.wait(timeout=30)

    async def drive():
        loop = asyncio.get_running_loop()
        starved = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(starved)
        # Occupy the one and only default-executor worker — this stands in for
        # the `chat()` turn `arun` parks there — and wait until it is genuinely
        # running before the fetch begins.
        hogged = loop.run_in_executor(None, hog)
        while not running.is_set():
            await asyncio.sleep(0.005)
        try:
            return await asyncio.wait_for(
                tool.async_execute("https://x.test/"), timeout=10
            )
        finally:
            release.set()
            await hogged
            starved.shutdown(wait=True)

    out = asyncio.run(drive())
    assert "done anyway" in out


def test_the_html_pool_is_bounded_and_shared(monkeypatch):
    """One small pool for the process, not one per tool instance."""
    monkeypatch.setattr(web_mod, "_cpu_pool", None)
    pool = web_mod._get_cpu_pool()
    try:
        assert pool is web_mod._get_cpu_pool(), "a second call built a second pool"
        assert pool._max_workers == web_mod._CPU_POOL_MAX_WORKERS  # noqa: SLF001
        assert pool._max_workers <= 8, "an unbounded parse pool defeats the point"
    finally:
        pool.shutdown(wait=False)
        web_mod._cpu_pool = None


def test_arun_does_not_run_chat_on_the_default_executor():
    """The other half of the same deadlock, and the half we cannot route around.

    A turn holds its worker for the whole turn and then blocks waiting on a tool
    coroutine on the host loop. Anything on that loop needing a *default*
    executor worker while turns are in flight can therefore never get one —
    including `loop.getaddrinfo`, which is how every `httpx.AsyncClient` connect
    to a hostname resolves. Measured with proxies disabled: one
    `run_in_executor` call against the default executor per request, resolving on
    an `asyncio_N` thread. No amount of pool choice inside `web_fetch` fixes
    that, so `arun` had to stop occupying the default executor.
    """
    from agentao import agent as agent_mod

    # `arun` only reaches `self.chat`, so a stand-in is enough to observe where
    # the turn lands — and avoids building a whole Agentao for a threading fact.
    class _Stub:
        arun = agent_mod.Agentao.arun

        def chat(self, *args, **kwargs):
            return threading.current_thread().name

    running = threading.Event()
    release = threading.Event()

    def hog():
        running.set()
        release.wait(timeout=30)

    async def drive():
        loop = asyncio.get_running_loop()
        starved = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(starved)
        hogged = loop.run_in_executor(None, hog)
        while not running.is_set():
            await asyncio.sleep(0.005)
        try:
            # If the turn needs a default-executor worker, this never returns.
            return await asyncio.wait_for(_Stub().arun("hi"), timeout=10)
        finally:
            release.set()
            await hogged
            starved.shutdown(wait=True)

    thread_name = asyncio.run(drive())
    assert thread_name.startswith("agentao-arun"), thread_name

    pool = agent_mod._get_arun_pool()
    assert pool is agent_mod._get_arun_pool(), "a second call built a second pool"
    # Same capacity asyncio's own default executor would have given, so moving
    # off it costs no concurrency.
    assert pool._max_workers == min(32, (os.cpu_count() or 1) + 4)  # noqa: SLF001


def test_httpx_resolves_on_the_default_executor():
    """Pins the fact the fix above rests on, against the real httpx.

    If a future httpx/anyio moves DNS onto its own workers, `arun`'s dedicated
    pool stops being load-bearing for this reason — and the comment saying it is
    becomes a lie. `trust_env=False` is essential: with proxy env vars set httpx
    connects to the proxy by address and never resolves at all, which is how an
    earlier attempt at this measurement "proved" the opposite.
    """
    import httpx

    seen = []

    async def probe():
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor

        def spy(executor, func, *args):
            if executor is None:
                seen.append(getattr(func, "__name__", str(func)))
            return original(executor, func, *args)

        loop.run_in_executor = spy
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                try:
                    await client.get("https://example.com/")
                except Exception:
                    pass  # a network failure still tells us where DNS ran
        finally:
            loop.run_in_executor = original

    asyncio.run(probe())

    if not seen:
        pytest.skip("no DNS observed — sandboxed or cached resolver")
    assert any("getaddrinfo" in name for name in seen), seen


def test_browser_teardown_fits_inside_the_dispatcher_ack_budget():
    """Cancelled teardown runs twice (browser, driver), so the pair must fit.

    Spending the normal 10s budget on each would report `TOOL_COMPLETE` with
    Chromium still shutting down — the same defect as an over-long parse drain,
    one layer down.
    """
    from agentao.runtime import tool_executor

    assert (
        2 * web_mod._BROWSER_CANCEL_CLOSE_TIMEOUT_S
        < tool_executor._ASYNC_CANCEL_ACK_TIMEOUT_S
    ), (
        f"2 x {web_mod._BROWSER_CANCEL_CLOSE_TIMEOUT_S}s teardown >= dispatcher "
        f"ack {tool_executor._ASYNC_CANCEL_ACK_TIMEOUT_S}s"
    )
    # And the non-cancelled path keeps the generous one — a slow-but-working
    # close should not be turned into a killed driver.
    assert web_mod._BROWSER_CANCEL_CLOSE_TIMEOUT_S < web_mod._BROWSER_CLOSE_TIMEOUT_S


def test_a_queued_parse_is_cancelled_rather_than_drained(monkeypatch):
    """With the pool saturated, a cancelled fetch's job may not have started.

    Draining it would keep the response alive and then run work nobody wants,
    which defeats the bound the pool exists to provide. A not-yet-started job is
    dropped from the queue instead.
    """
    monkeypatch.setattr(web_mod, "_cpu_pool", None)
    monkeypatch.setattr(web_mod, "_CPU_POOL_MAX_WORKERS", 1)
    release = threading.Event()
    running = threading.Event()
    ran = []

    def occupy(*_a):
        running.set()
        release.wait(timeout=30)
        return "occupier"

    def queued(*_a):
        ran.append("queued")
        return "queued"

    async def drive():
        hog = asyncio.ensure_future(web_mod._in_worker(occupy, None))
        while not running.is_set():
            await asyncio.sleep(0.005)
        # The single worker is busy, so this one can only be queued.
        waiter = asyncio.ensure_future(web_mod._in_worker(queued, None))
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        assert await hog == "occupier"

    try:
        asyncio.run(drive())
    finally:
        release.set()
        if web_mod._cpu_pool is not None:
            web_mod._cpu_pool.shutdown(wait=True)
        web_mod._cpu_pool = None

    assert ran == [], "a cancelled fetch's queued job ran anyway"


def test_the_drain_fits_inside_the_dispatcher_ack_budget():
    """The drain must finish before the dispatcher stops waiting for it.

    `_run_async_tool` waits `_ASYNC_CANCEL_ACK_TIMEOUT_S` for this coroutine's
    cleanup, then emits `TOOL_COMPLETE` regardless. A drain longer than that
    budget produces exactly the detached work the drain exists to prevent, with
    the invocation already reported complete — so the relation is asserted rather
    than left to a comment. Not an import in `web.py`: tools do not depend on the
    runtime, so this test is the coupling.
    """
    from agentao.runtime import tool_executor

    assert (
        web_mod._CPU_CANCEL_DRAIN_TIMEOUT_S
        < tool_executor._ASYNC_CANCEL_ACK_TIMEOUT_S
    ), (
        f"drain {web_mod._CPU_CANCEL_DRAIN_TIMEOUT_S}s >= dispatcher ack "
        f"{tool_executor._ASYNC_CANCEL_ACK_TIMEOUT_S}s"
    )


# --------------------------------------------------------------------------
# cancellation must not orphan the CPU work
# --------------------------------------------------------------------------


def test_cancelling_a_fetch_does_not_orphan_the_parse(monkeypatch):
    """`to_thread` cancels the awaiter, never the worker.

    Nothing can interrupt a running Python call from outside it, so the parse
    finishes either way. Returning immediately would leave it burning CPU and
    holding a multi-megabyte DOM with nobody waiting on the result, and a host
    that cancels repeatedly could stack several up. Waiting costs the canceller
    latency it was going to pay regardless.
    """
    finished = threading.Event()
    started = threading.Event()

    def slow_extract(soup):
        started.set()
        time.sleep(_SLOW_S)
        finished.set()
        return "extracted"

    _stub_primary(monkeypatch)
    monkeypatch.setattr(web_mod, "_extract_text", slow_extract)
    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)
    tool = WebFetchTool()

    async def drive():
        task = asyncio.create_task(tool.async_execute("https://x.test/"))
        # Let the fetch reach the extraction, then cancel mid-flight.
        while not started.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The cancellation is only allowed to surface once the worker is done.
        return finished.is_set()

    assert asyncio.run(drive()) is True, (
        "the cancelled fetch returned while its parse was still running"
    )


def _cancel_a_wedged_fetch(monkeypatch, drain_budget: float):
    """Cancel a fetch whose extraction is wedged; return the drain latency.

    The measurement stops when `CancelledError` reaches the canceller — *not*
    when `asyncio.run` returns. Those are different numbers on purpose: the
    budget bounds the former, while loop shutdown joins the default executor
    regardless of it. Conflating the two is how the first draft of this test
    "measured" 30s and concluded the budget was ignored.
    """
    monkeypatch.setattr(web_mod, "_CPU_CANCEL_DRAIN_TIMEOUT_S", drain_budget)
    release = threading.Event()
    started = threading.Event()

    def wedged_extract(soup):
        started.set()
        release.wait(timeout=30)
        return "eventually"

    _stub_primary(monkeypatch)
    monkeypatch.setattr(web_mod, "_extract_text", wedged_extract)
    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)
    tool = WebFetchTool()

    async def drive():
        task = asyncio.create_task(tool.async_execute("https://x.test/"))
        while not started.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        t0 = time.monotonic()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            # Before unwinding, so loop shutdown does not join a 30s sleeper.
            release.set()
        return time.monotonic() - t0

    return asyncio.run(drive())


def test_a_wedged_parse_cannot_hold_a_cancellation_open_forever(monkeypatch):
    """The drain is bounded, mirroring the AsyncTool dispatcher's cleanup-ack.

    Waiting for the worker is right; waiting for it *without limit* would let one
    pathological document make a cancellation un-cancellable on a host loop that
    never shuts down.
    """
    drain_s = _cancel_a_wedged_fetch(monkeypatch, 0.05)
    assert drain_s < 2.0, f"the drain budget was not enforced ({drain_s:.2f}s)"


def test_the_drain_says_so_when_it_gives_up(monkeypatch, caplog):
    """Falling back to orphaning must be loud, not silent."""
    with caplog.at_level(logging.WARNING, logger="agentao.tools.web"):
        _cancel_a_wedged_fetch(monkeypatch, 0.05)

    assert "cancelled fetch left" in caplog.text
    assert "wedged_extract" in caplog.text
    assert "holds CPU and memory" in caplog.text


def test_the_primary_fetch_keeps_the_loop_free_during_resolution(monkeypatch):
    """The httpx leg blocks too, and for longer than anyone expects.

    `validate_outbound_url` calls `getaddrinfo`; the fallback was never the only
    thing on this path that would freeze a host's loop. It runs off-thread and
    bounded now, and a stall is a rejection.
    """
    monkeypatch.setattr(web_mod, "_HTTP_TIMEOUT_S", 0.05)

    def stalling_resolver(host, port):
        time.sleep(_SLOW_S * 10)
        return set()

    monkeypatch.setattr(url_policy_mod, "_resolve_host_addresses", stalling_resolver)
    tool = _tool(monkeypatch)

    async def drive():
        return await _ticks_while(lambda: tool.async_execute("https://slow-dns.test/"))

    started = time.monotonic()
    (out, ticks) = asyncio.run(drive())
    elapsed = time.monotonic() - started

    assert "blocked outbound request" in out
    assert ticks > 0, "the event loop was blocked by the DNS lookup"
    assert elapsed < _SLOW_S * 5, f"the stalled lookup was joined ({elapsed:.2f}s)"


def test_a_stalled_lookup_does_not_leak_the_url_to_the_fallback(monkeypatch):
    """A stall now surfaces as `UrlPolicyError`, which must keep the existing
    no-fallback-on-a-blocked-target rule — otherwise widening the rejection
    would have quietly opened a path that sends the URL to a third party."""
    monkeypatch.setattr(web_mod, "_HTTP_TIMEOUT_S", 0.05)
    monkeypatch.setattr(
        url_policy_mod, "_resolve_host_addresses",
        lambda host, port: (time.sleep(_SLOW_S * 10), set())[1],
    )
    reached = []

    async def fake_jina(url):
        reached.append(url)
        return "# proxied"

    monkeypatch.setattr(web_mod, "_fetch_via_jina", fake_jina)
    tool = _tool(monkeypatch, "jina")

    out = asyncio.run(tool.async_execute("https://slow-dns.test/"))

    assert reached == [], "a rejected target was sent to the third-party reader"
    assert "blocked outbound request" in out


# --------------------------------------------------------------------------
# the sync wrapper stays a working surface
# --------------------------------------------------------------------------


def test_sync_and_async_surfaces_return_the_same_thing(monkeypatch):
    async def fake_render(url, **kwargs):
        return 200, "<html><body><p>same either way</p></body></html>"

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    _stub_primary(monkeypatch)
    tool = _tool(monkeypatch)

    assert tool.execute("https://x.test/") == asyncio.run(
        tool.async_execute("https://x.test/")
    )


def test_the_sync_wrapper_works_off_a_loop_thread(monkeypatch):
    """The ordinary case: a sync embedder with no loop at all."""
    async def fake_render(url, **kwargs):
        return 200, "<html><body><p>no loop here</p></body></html>"

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    _stub_primary(monkeypatch)

    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()  # precondition: nothing running
    assert "no loop here" in _tool(monkeypatch).execute("https://x.test/")


def test_the_sync_wrapper_does_not_deadlock_inside_a_loop(monkeypatch):
    """It blocks — asserted above — but it must not deadlock or raise the
    "asyncio.run() cannot be called from a running event loop" error."""
    async def fake_render(url, **kwargs):
        return 200, "<html><body><p>nested but alive</p></body></html>"

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    _stub_primary(monkeypatch)
    tool = _tool(monkeypatch)

    async def host():
        return tool.execute("https://x.test/")

    assert "nested but alive" in asyncio.run(host())


def test_a_runtime_error_from_the_coroutine_is_not_mistaken_for_nesting(monkeypatch):
    """`_run_coroutine_blocking` decides by asking whether a loop is running.

    Guessing from `except RuntimeError` around `asyncio.run` — the shape this
    replaced — masks a genuine `RuntimeError` from the tool behind an unrelated
    event-loop message, and retries an already-closed coroutine.
    """
    async def boom():
        raise RuntimeError("the tool itself failed")

    with pytest.raises(RuntimeError, match="the tool itself failed"):
        web_mod._run_coroutine_blocking(boom())

    async def host():
        return web_mod._run_coroutine_blocking(boom())

    with pytest.raises(RuntimeError, match="the tool itself failed"):
        asyncio.run(host())


# --------------------------------------------------------------------------
# the fallback contract still holds through the async path
# --------------------------------------------------------------------------


def test_a_failed_fallback_does_not_discard_the_static_shell(monkeypatch):
    """The distinction `_run_fallback` returns `(body, error)` for: a host that
    installed the extra but never ran `playwright install chromium` must not
    turn every JS-flagged fetch into a hard failure."""
    async def fake_render(url, **kwargs):
        raise RuntimeError("Executable doesn't exist")

    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)
    _stub_primary(monkeypatch)

    out = asyncio.run(_tool(monkeypatch).async_execute("https://x.test/"))
    assert "configured fallback failed" in out
    assert "Executable doesn't exist" in out
    assert "Status: 200" in out


def test_an_http_error_falls_back_and_reports_both_failures(monkeypatch):
    import httpx

    async def failing_get(client, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    async def fake_render(url, **kwargs):
        raise RuntimeError("no browser either")

    monkeypatch.setattr(web_mod, "guarded_get_async", failing_get)
    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)

    out = asyncio.run(_tool(monkeypatch).async_execute("https://x.test/"))
    assert "connection refused" in out
    assert "Fallback also failed" in out
    assert "no browser either" in out


def test_a_rendered_error_page_is_not_served_as_the_document(monkeypatch):
    """`page.goto` does not raise on 4xx and this path is reached *from* an
    httpx error, so without the status check a 404 body reads as the article."""
    import httpx

    async def failing_get(client, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    async def fake_render(url, **kwargs):
        return 404, "<html><body><p>Not found</p></body></html>"

    monkeypatch.setattr(web_mod, "guarded_get_async", failing_get)
    monkeypatch.setattr(web_mod, "_render_with_playwright", fake_render)

    out = asyncio.run(_tool(monkeypatch).async_execute("https://x.test/"))
    assert "Not found" not in out
    assert "HTTP 404" in out


def test_no_fallback_configured_keeps_the_shell_and_says_how_to_enable_one(monkeypatch):
    _stub_primary(monkeypatch)
    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)

    out = asyncio.run(WebFetchTool().async_execute("https://x.test/"))
    assert "AGENTAO_WEB_FETCH_FALLBACK=jina" in out
    assert "playwright" in out


# --------------------------------------------------------------------------
# API-drift guard against the real dependency
# --------------------------------------------------------------------------


def test_httpx_async_call_sites_match_the_installed_httpx():
    """The sync client's kwargs do not imply the async client's."""
    import httpx

    params = inspect.signature(httpx.AsyncClient.__init__).parameters
    assert "follow_redirects" in params
    assert "timeout" in params
    assert hasattr(httpx.AsyncClient, "__aenter__")
    assert hasattr(httpx.AsyncClient, "__aexit__")


def test_a_response_body_survives_the_client_closing():
    """`async_execute` reads `.text` *after* the `async with` block exits.

    That works only because a non-streamed response is fully read during
    `get()`. Asserting it against the real httpx rather than a fake: a fake
    response answers `.text` whatever the client did, so it could never catch
    httpx making the body lazy.
    """
    import httpx

    def handler(request):
        return httpx.Response(200, text="body read after close")

    async def drive():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=False, timeout=1.0
        ) as client:
            return await client.get("https://x.test/")

    response = asyncio.run(drive())
    assert response.text == "body read after close"


def test_the_async_tool_base_contract_is_what_we_implement():
    """Probe the base class rather than trusting the method name: the executor
    calls `async_execute(**kwargs)`, and a rename upstream would leave
    `WebFetchTool` abstract-but-instantiable-looking."""
    assert "async_execute" in AsyncToolBase.__abstractmethods__
    assert not WebFetchTool.__abstractmethods__
    sig = inspect.signature(WebFetchTool.async_execute)
    assert list(sig.parameters) == ["self", "url", "extract_text"]
