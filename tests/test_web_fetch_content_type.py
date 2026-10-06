"""``web_fetch`` returns non-markup text as it arrived.

Every body used to go through ``html.parser``: ``#include <stdio.h>`` lost its
header name and ``if (a<b && c>d)`` became ``if (ad)``.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agentao.tools import web as web_mod

C_SOURCE = "#include <stdio.h>\nint main(void) { if (a<b && c>d) return 1; }\n"


@pytest.fixture
def serve(monkeypatch):
    real = httpx.AsyncClient

    def install(body: str, content_type):
        headers = {"content-type": content_type} if content_type else {}

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(200, content=body.encode("utf-8"), headers=headers)
            )
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    return install


@pytest.mark.parametrize("content_type", [
    "text/plain; charset=utf-8",
    "text/x-c",
    "application/json",
    "application/vnd.github+json",
])
def test_non_markup_text_is_returned_verbatim(serve, content_type):
    serve(C_SOURCE, content_type)
    result = asyncio.run(web_mod.WebFetchTool().async_execute("https://8.8.8.8/main.c"))
    assert "#include <stdio.h>" in result
    assert "if (a<b && c>d)" in result


@pytest.mark.parametrize("content_type", ["text/html; charset=utf-8", None])
def test_html_and_unlabelled_bodies_are_still_parsed(serve, content_type):
    serve("<html><body><p>Hello <b>world</b></p>" + "<p>x</p>" * 50 + "</body></html>", content_type)
    result = asyncio.run(web_mod.WebFetchTool().async_execute("https://8.8.8.8/"))
    assert "Hello" in result and "<b>" not in result


def test_is_raw_text():
    assert web_mod._is_raw_text("text/markdown")
    assert not web_mod._is_raw_text("text/html")
    assert not web_mod._is_raw_text("application/xhtml+xml")
    assert not web_mod._is_raw_text("image/png")
    assert not web_mod._is_raw_text(None)
