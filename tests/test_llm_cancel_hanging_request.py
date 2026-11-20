"""A cancel stops a model request that hangs on the network.

The adapters looked at the token only between stream events. A provider that
never answers, or a stream that stalls, held the thread in a socket read
until the request timed out: a headless run ignored its first SIGINT /
SIGTERM, and an ACP cancel did nothing. Here the server is a real socket that
stops talking, and the requests go through the real SDKs.

Each call runs on a thread that the test joins with a deadline, so a
regression fails the test instead of hanging the suite.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any, List, Optional

import pytest

from agentao.cancellation import CancellationToken
from agentao.llm._cancellable import CancellableStream, call_cancellable, CancelledWhileWaiting
from agentao.llm.client import LLMClient
from tests.support import anthropic_wire as aw
from tests.support import openai_responses_wire as rw

pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]
DEADLINE_S = 5.0


class _HangingServer:
    """Accepts one request, sends ``prefix`` (or nothing), then goes quiet.

    ``prefix=None`` hangs before the response headers. Otherwise the headers
    and ``prefix`` go out as a chunked SSE body, and the body never ends.
    :attr:`closed_by_client` is set when the client closes the connection.
    """

    def __init__(self, prefix: Optional[bytes]) -> None:
        self.prefix = prefix
        self.closed_by_client = threading.Event()
        self._stop = threading.Event()
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _serve(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(0.2)
                if self._read_request(conn).startswith(b"GET "):
                    # The anthropic wire's Models API lookup (``prepare``),
                    # bounded and not cancellable by design: a 404 ends it.
                    conn.sendall(
                        b"HTTP/1.1 404 Not Found\r\ncontent-type: application/json\r\n"
                        b"content-length: 2\r\nconnection: close\r\n\r\n{}"
                    )
                    continue
                if self.prefix is not None:
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
                        b"transfer-encoding: chunked\r\n\r\n"
                        + f"{len(self.prefix):x}\r\n".encode() + self.prefix + b"\r\n"
                    )
                while not self._stop.is_set():
                    try:
                        if conn.recv(65536) == b"":
                            self.closed_by_client.set()
                            break
                    except socket.timeout:
                        continue
                    except OSError:
                        self.closed_by_client.set()
                        break

    @staticmethod
    def _read_request(conn: socket.socket) -> bytes:
        data = b""
        deadline = time.monotonic() + 2
        while b"\r\n\r\n" not in data and time.monotonic() < deadline:
            try:
                data += conn.recv(65536)
            except socket.timeout:
                continue
        head, _, body = data.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        while len(body) < length and time.monotonic() < deadline:
            try:
                body += conn.recv(65536)
            except socket.timeout:
                continue
        return head

    def stop(self) -> None:
        self._stop.set()
        self._sock.close()


@pytest.fixture
def hanging():
    servers: List[_HangingServer] = []

    def make(prefix: Optional[bytes]) -> _HangingServer:
        server = _HangingServer(prefix)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.stop()


def _client(api_format: str, base_url: str) -> LLMClient:
    kwargs: dict = {}
    if api_format == "anthropic-messages":
        kwargs["max_tokens"] = 1024
    else:
        base_url += "/v1"
    llm = LLMClient(
        api_key="k", base_url=base_url, model="m", api_format=api_format,
        logger=logging.getLogger("test.cancel"), **kwargs,
    )
    return llm


def _completions_chunk(text: str) -> bytes:
    return b"data: " + json.dumps({
        "id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }).encode() + b"\n\n"


#: The start of an answer that says "hi" and never finishes, per wire.
_PARTIAL = {
    "openai-completions": lambda: _completions_chunk("hi"),
    "anthropic-messages": lambda: aw.stream_of(aw.message_start(), aw.text_block(0, "hi")[:2]),
    "openai-responses": lambda: rw.stream_of(rw.created(), rw.text_events(0, "hi")[:3]),
}


def _run_with_deadline(call) -> Any:
    box: dict = {}

    def target():
        try:
            box["value"] = call()
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(DEADLINE_S)
    assert not worker.is_alive(), "the cancel did not stop the request"
    box["elapsed"] = time.monotonic() - started
    if "error" in box:
        raise box["error"]
    return box


@pytest.mark.parametrize("api_format", sorted(_PARTIAL))
def test_a_cancel_stops_a_request_that_never_answers(hanging, api_format):
    server = hanging(None)
    llm = _client(api_format, server.url)
    token = CancellationToken()
    threading.Timer(0.3, token.cancel, ("test",)).start()

    box = _run_with_deadline(lambda: llm.chat_stream(HELLO, cancellation_token=token))

    response = box["value"]
    assert response.finish_reason_reported is False
    assert not response.choices[0].message.content


@pytest.mark.parametrize("api_format", sorted(_PARTIAL))
def test_a_cancel_stops_a_stalled_stream_and_keeps_what_arrived(hanging, api_format):
    server = hanging(_PARTIAL[api_format]())
    llm = _client(api_format, server.url)
    token = CancellationToken()
    shown: List[str] = []

    def on_chunk(text):
        shown.append(text)
        threading.Timer(0.3, token.cancel, ("test",)).start()

    before = set(threading.enumerate())
    box = _run_with_deadline(
        lambda: llm.chat_stream(HELLO, on_text_chunk=on_chunk, cancellation_token=token),
    )

    # The worker blocked in the read is woken and ends: closing the socket
    # from another thread would not wake it, and it would stay until the
    # SDK's 600 s read timeout.
    workers = [t for t in set(threading.enumerate()) - before
               if t.name.startswith("agentao-llm-stream")]
    for worker in workers:
        worker.join(DEADLINE_S)
    assert not [t for t in workers if t.is_alive()]

    response = box["value"]
    assert shown == ["hi"]
    assert response.choices[0].message.content == "hi"
    assert response.finish_reason_reported is False
    # The stream is closed, so the worker is not left reading it.
    assert server.closed_by_client.wait(DEADLINE_S)


def test_a_cancel_stops_a_non_streaming_request(hanging):
    """``chat()``: the Gemini bypass, the streaming fallback, the summarizer."""
    server = hanging(None)
    llm = _client("openai-completions", server.url)
    token = CancellationToken()
    threading.Timer(0.3, token.cancel, ("test",)).start()

    box = _run_with_deadline(lambda: llm.chat(HELLO, cancellation_token=token))

    assert box["value"].finish_reason_reported is False
    assert not box["value"].choices[0].message.content


@pytest.mark.parametrize("api_format", ["anthropic-messages", "openai-responses"])
def test_a_cancelled_chat_closes_the_stream_on_a_streaming_wire(hanging, api_format):
    """``chat()`` streams on these wires: a cancel closes the stream too.

    Without the token reaching the adapter, the worker read the abandoned
    stream to its end, and the server billed the whole answer.
    """
    server = hanging(_PARTIAL[api_format]())
    llm = _client(api_format, server.url)
    token = CancellationToken()
    threading.Timer(0.3, token.cancel, ("test",)).start()

    box = _run_with_deadline(lambda: llm.chat(HELLO, cancellation_token=token))

    assert box["value"].finish_reason_reported is False
    assert server.closed_by_client.wait(DEADLINE_S)


def test_a_cancelled_chat_still_counts_the_usage_already_reported(hanging):
    """``message_start`` stated the input; the worker records it after the
    caller stopped waiting, and the attempt is still counted, once."""
    server = hanging(_PARTIAL["anthropic-messages"]())
    llm = _client("anthropic-messages", server.url)
    token = CancellationToken()
    threading.Timer(0.3, token.cancel, ("test",)).start()

    _run_with_deadline(lambda: llm.chat(HELLO, cancellation_token=token))

    assert server.closed_by_client.wait(DEADLINE_S)
    deadline = time.monotonic() + DEADLINE_S
    while llm.total_prompt_tokens == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    assert llm.total_prompt_tokens == 10


def test_without_a_token_the_stream_is_read_on_the_calling_thread():
    threads = []

    def items():
        threads.append(threading.current_thread())
        yield 1
        yield 2

    assert list(CancellableStream(items, None)) == [1, 2]
    assert threads == [threading.current_thread()]


def test_the_handling_stays_on_the_calling_thread():
    """The adapter's callbacks and accumulator see the thread they saw before."""
    readers = []

    def items():
        readers.append(threading.current_thread())
        yield from (1, 2)

    consumer = []
    for item in CancellableStream(items, CancellationToken()):
        consumer.append(threading.current_thread())
    assert set(consumer) == {threading.current_thread()}
    assert readers[0] is not threading.current_thread()


def test_an_error_from_the_request_reaches_the_caller():
    def items():
        yield 1
        raise ValueError("boom")

    stream = CancellableStream(items, CancellationToken())
    seen = []
    with pytest.raises(ValueError, match="boom"):
        for item in stream:
            seen.append(item)
    assert seen == [1]


def test_stopping_early_closes_the_stream():
    closed = []

    class Stream:
        def __iter__(self):
            yield from range(10)

        def close(self):
            closed.append(True)

    for item in CancellableStream(Stream, CancellationToken()):
        break
    assert closed


def test_an_already_cancelled_token_opens_nothing():
    opened = []
    token = CancellationToken()
    token.cancel("test")
    assert list(CancellableStream(lambda: opened.append(1) or [1], token)) == []
    with pytest.raises(CancelledWhileWaiting):
        call_cancellable(lambda: opened.append(1), token)
    assert opened == []
