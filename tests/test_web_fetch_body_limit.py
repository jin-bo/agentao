"""``web_fetch`` holds a bounded body, for a bounded time.

Before this, ``guarded_get_async`` called ``client.get``, which reads the whole
body into memory — for every redirect hop, not only the last — and the tool
then cut the decoded text to 10,000 characters. A localhost server sending
300 MB with no ``Content-Length`` took the process to 707 MB RSS, and
``_HTTP_TIMEOUT_S`` (httpx's per-read timeout) never fired on a server that
kept sending.

Every test here drives real ``httpx`` objects over ``MockTransport``: the
streaming, decoding and close behaviour under test is httpx's own, which a
hand-written fake would only restate. Streams are generators that record how
far they were pulled, so "stopped early" is observed, not inferred. Public IP
literals keep DNS out of it.
"""

from __future__ import annotations

import asyncio
import gzip

import httpx
import pytest

from agentao.security import ResponseTooLargeError, guarded_get, guarded_get_async
from agentao.security import url_policy
from agentao.tools import web as web_mod


class _Pulled:
    """A body stream that counts the chunks actually pulled from it.

    httpx picks the stream type from the content object, so the async and sync
    clients each get their own generator.
    """

    def __init__(self, chunk: bytes, count: int):
        self.chunk = chunk
        self.count = count
        self.pulled = 0

    def sync(self):
        for _ in range(self.count):
            self.pulled += 1
            yield self.chunk

    async def aiter(self):
        for _ in range(self.count):
            self.pulled += 1
            yield self.chunk


def _async_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, timeout=5.0
    )


def _sync_client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False, timeout=5.0
    )


def _get_async(handler, url="https://8.8.8.8/", **kwargs):
    async def run():
        async with _async_client(handler) as client:
            return await guarded_get_async(client, url, **kwargs)

    return asyncio.run(run())


# --------------------------------------------------------------------------
# The reader
# --------------------------------------------------------------------------


def test_a_body_within_the_limit_reads_like_client_get():
    response = _get_async(
        lambda request: httpx.Response(200, text="hello world"),
        max_body_bytes=11,
    )
    # Exactly at the limit is allowed, and the body is readable the usual way.
    assert response.content == b"hello world"
    assert response.text == "hello world"
    assert response.is_closed


def test_a_declared_length_over_the_limit_is_refused_before_any_body_byte():
    stream = _Pulled(b"x" * 1024, 100)

    def handler(request):
        return httpx.Response(
            200, headers={"content-length": str(1024 * 100)}, content=stream.aiter()
        )

    with pytest.raises(ResponseTooLargeError) as excinfo:
        _get_async(handler, max_body_bytes=4096)
    assert stream.pulled == 0
    # The message names the limit and nothing about the body.
    assert "4096" in str(excinfo.value)
    assert excinfo.value.max_bytes == 4096


def test_an_undeclared_body_is_counted_and_the_stream_stops_at_the_limit():
    stream = _Pulled(b"x" * 1024, 10_000)  # ~10 MB if read to the end

    with pytest.raises(ResponseTooLargeError):
        _get_async(lambda request: httpx.Response(200, content=stream.aiter()), max_body_bytes=4096)
    # Five 1 KiB chunks cross 4 KiB; nothing past the crossing chunk is pulled.
    assert stream.pulled == 5


def test_decoded_bytes_are_counted_not_wire_bytes():
    """A small gzip body that inflates past the limit is still refused."""
    raw = gzip.compress(b"a" * 1_000_000)
    assert len(raw) < 8192

    def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=raw)

    with pytest.raises(ResponseTooLargeError):
        _get_async(handler, max_body_bytes=8192)

    # And a compressed body within the limit decodes normally.
    small = gzip.compress(b"b" * 100)
    response = _get_async(
        lambda request: httpx.Response(
            200, headers={"content-encoding": "gzip"}, content=small
        ),
        max_body_bytes=8192,
    )
    assert response.text == "b" * 100


def test_no_limit_reads_the_whole_body():
    """``None`` stays the default, so a host calling ``guarded_get`` sees no change."""
    response = _get_async(lambda request: httpx.Response(200, content=b"z" * 50_000))
    assert len(response.content) == 50_000


def test_a_redirect_hops_body_is_released_unread():
    hop_body = _Pulled(b"h" * 1024, 10_000)

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(
                302, headers={"location": "https://8.8.4.4/next"}, content=hop_body.aiter()
            )
        return httpx.Response(200, text="final")

    response = _get_async(handler, "https://8.8.8.8/start", max_body_bytes=4096)
    assert response.text == "final"
    assert hop_body.pulled == 0


def test_the_sync_surface_applies_the_same_limit():
    stream = _Pulled(b"x" * 1024, 10_000)
    with _sync_client(
        lambda request: httpx.Response(200, content=stream.sync())
    ) as client:
        with pytest.raises(ResponseTooLargeError):
            guarded_get(client, "https://8.8.8.8/", max_body_bytes=4096)
    assert stream.pulled == 5

    with _sync_client(lambda request: httpx.Response(200, text="ok")) as client:
        assert guarded_get(client, "https://8.8.8.8/", max_body_bytes=2).text == "ok"


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------


@pytest.fixture
def route_httpx(monkeypatch):
    """Point every ``httpx.AsyncClient`` the tool builds at ``handler``."""
    real = httpx.AsyncClient

    def install(handler):
        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    return install


def test_web_fetch_refuses_an_oversized_page_without_falling_back(
    monkeypatch, route_httpx
):
    monkeypatch.setattr(web_mod, "_MAX_BODY_BYTES", 4096)
    stream = _Pulled(b"<p>x</p>" * 128, 10_000)
    route_httpx(lambda request: httpx.Response(200, content=stream.aiter()))

    tool = web_mod.WebFetchTool()
    tool._fallback = web_mod._FALLBACK_JINA
    fallbacks: list[str] = []

    async def record_fallback(url, *, reason, extract_text=True):
        fallbacks.append(reason)
        return "fell back", None

    monkeypatch.setattr(tool, "_run_fallback", record_fallback)

    result = asyncio.run(tool.async_execute("https://8.8.8.8/big"))
    assert result.startswith("Error:")
    assert "4096 byte limit" in result
    assert fallbacks == []
    assert stream.pulled < 10


def test_web_fetch_has_a_whole_fetch_ceiling(monkeypatch, route_httpx):
    """A server that keeps sending resets httpx's per-read timeout forever."""
    monkeypatch.setattr(web_mod, "_FETCH_TOTAL_TIMEOUT_S", 0.3)

    async def trickle():
        while True:
            await asyncio.sleep(0.05)
            yield b"."

    route_httpx(lambda request: httpx.Response(200, content=trickle()))
    tool = web_mod.WebFetchTool()
    tool._fallback = web_mod._FALLBACK_NONE

    result = asyncio.run(
        asyncio.wait_for(tool.async_execute("https://8.8.8.8/slow"), timeout=5.0)
    )
    assert result == "Error: Request timed out for https://8.8.8.8/slow"


def test_the_jina_fallback_is_capped_too(monkeypatch, route_httpx):
    monkeypatch.setattr(web_mod, "_MAX_BODY_BYTES", 4096)
    stream = _Pulled(b"# md\n" * 200, 10_000)
    route_httpx(lambda request: httpx.Response(200, content=stream.aiter()))

    with pytest.raises(ResponseTooLargeError):
        asyncio.run(web_mod._fetch_via_jina("https://example.com/"))
    assert stream.pulled < 10


def test_the_limit_is_one_implementation():
    """Both surfaces and the jina path read through the shared helpers."""
    import inspect

    assert "read_capped(" in inspect.getsource(url_policy.guarded_get)
    assert "aread_capped(" in inspect.getsource(url_policy.guarded_get_async)
    assert "aread_capped(" in inspect.getsource(web_mod._fetch_via_jina)
