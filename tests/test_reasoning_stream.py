"""Reasoning streams to the host as it arrives (#548).

``THINKING`` used to be one event with the whole reasoning, sent after the
call's stream had ended — so a final answer's reasoning showed *after* the
answer, and a long thinking phase showed nothing at all. Each adapter now hands
every piece to ``_StreamAccumulator.add_reasoning``, which passes it raw to
``chat_stream``'s ``on_reasoning_chunk``; ``llm_call.py`` sanitizes the stream
(``security/stream_text.py``) and turns each clean piece into one ``THINKING``.

The contract these tests hold, per the plan on #548:

- within one successful attempt the pieces join to the sanitized
  ``reasoning_content`` history keeps;
- reasoning a chunk carries goes out before the text that chunk carries;
- a stream that drops mid-reasoning is still retried, and the failed attempt's
  pieces are not taken back;
- the non-streaming paths send the whole reasoning once, before the text.

Everything goes through the real SDKs; only the socket is scripted.
"""

from __future__ import annotations

import io
import json
import logging
import random
from pathlib import Path
from typing import Any, List, Tuple
from unittest.mock import patch

import httpx
import openai
import pytest
from rich.console import Console

from agentao import Agentao
from agentao.llm import client as client_mod
from agentao.llm.client import LLMClient
from agentao.runtime.sanitize import sanitize_text_field
from agentao.security.stream_text import StreamTextSanitizer
from agentao.transport import AgentEvent, EventType, SdkTransport
from tests.support import anthropic_wire as aw
from tests.support import openai_responses_wire as rw

# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

HELLO = [{"role": "user", "content": "hi"}]
FLAG = "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"  # Scotland


def _log() -> logging.Logger:
    return logging.getLogger("test.reasoning_stream")


def _recording() -> Tuple[List[Tuple[str, str]], dict]:
    """Both callbacks into one ordered list of ``(kind, piece)``."""
    seen: List[Tuple[str, str]] = []
    return seen, {
        "on_text_chunk": lambda c: seen.append(("text", c)),
        "on_reasoning_chunk": lambda c: seen.append(("reasoning", c)),
    }


def _joined(seen: List[Tuple[str, str]], kind: str) -> str:
    return "".join(piece for k, piece in seen if k == kind)


def _through_the_runtime(llm: Any) -> List[Tuple[str, str]]:
    """One ``run_llm_call`` on *llm*: the ``THINKING``/``LLM_TEXT`` a host sees."""
    from types import SimpleNamespace

    from agentao.runtime.llm_call import run_llm_call

    events: List[AgentEvent] = []
    agent = SimpleNamespace(transport=SimpleNamespace(emit=events.append),
                            replay_manager=None, llm=llm)
    run_llm_call(agent, messages=list(HELLO), tools=[])
    kinds = {EventType.THINKING: ("reasoning", "text"), EventType.LLM_TEXT: ("text", "chunk")}
    return [(kinds[e.type][0], e.data[kinds[e.type][1]]) for e in events if e.type in kinds]


# -- the three wires --------------------------------------------------------------


def _completions_chunk(finish: Any = None, **delta: Any) -> bytes:
    return b"data: " + json.dumps({
        "id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }).encode() + b"\n\n"


def _completions(*bodies: Any, model: str = "m") -> Tuple[LLMClient, List[int]]:
    calls: List[int] = []

    def handler(request: Any) -> Any:
        calls.append(1)
        body = bodies[len(calls) - 1]
        if isinstance(body, dict):
            # ``json.dumps`` escapes a lone surrogate; httpx's ``json=`` would
            # try to encode it as UTF-8 and fail.
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=json.dumps(body).encode())
        headers = {"content-type": "text/event-stream"}
        if isinstance(body, bytes):
            return httpx.Response(200, headers=headers, content=body)
        return httpx.Response(200, headers=headers, stream=body)

    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model=model, logger=_log())
    llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return llm, calls


def test_chat_completions_streams_reasoning_and_joins_to_history():
    llm, _ = _completions(
        _completions_chunk(reasoning_content="weigh ")
        + _completions_chunk(reasoning_content="it")
        + _completions_chunk(content="ok", finish="stop") + b"data: [DONE]\n\n")
    seen, callbacks = _recording()
    message = llm.chat_stream(HELLO, **callbacks).choices[0].message
    assert seen == [("reasoning", "weigh "), ("reasoning", "it"), ("text", "ok")]
    assert _joined(seen, "reasoning") == message.reasoning_content


def test_a_chunk_with_both_fields_sends_its_reasoning_first():
    llm, _ = _completions(
        _completions_chunk(reasoning_content="then answer", content="42", finish="stop")
        + b"data: [DONE]\n\n")
    seen, callbacks = _recording()
    llm.chat_stream(HELLO, **callbacks)
    assert seen == [("reasoning", "then answer"), ("text", "42")]


def test_anthropic_streams_thinking_before_the_text_block():
    llm = LLMClient(api_key="k", base_url="https://api.example.test", model="claude-test",
                    api_format="anthropic-messages", max_tokens=4096, logger=_log())
    aw.attach(llm, aw.Wire(aw.stream_of(
        aw.message_start(),
        aw.thinking_block(0, ["weigh ", "it"], "SIG=="),
        aw.text_block(1, "ok"),
        aw.message_end(),
    )))
    seen, callbacks = _recording()
    message = llm.chat_stream(HELLO, **callbacks).choices[0].message
    assert seen == [("reasoning", "weigh "), ("reasoning", "it"), ("text", "ok")]
    assert _joined(seen, "reasoning") == message.reasoning_content


def test_responses_streams_both_summary_parts_with_their_separator():
    llm = LLMClient(api_key="k", base_url="http://wire.test/v1", model="gpt-test",
                    api_format="openai-responses", logger=_log())
    where = {"item_id": "rs_1", "output_index": 0}
    part = {"type": "summary_text", "text": ""}
    rw.attach(llm, rw.Wire(rw.stream_of(
        rw.created(),
        {"type": "response.reasoning_summary_part.added", **where, "summary_index": 0, "part": part},
        {"type": "response.reasoning_summary_text.delta", **where, "summary_index": 0, "delta": "Plan"},
        {"type": "response.reasoning_summary_part.added", **where, "summary_index": 1, "part": part},
        {"type": "response.reasoning_summary_text.delta", **where, "summary_index": 1, "delta": "Check"},
        rw.text_events(1, "ok"), rw.completed([rw.message_item("ok")]),
    )))
    seen, callbacks = _recording()
    message = llm.chat_stream(HELLO, **callbacks).choices[0].message
    assert message.reasoning_content == "Plan\n\nCheck"
    assert _joined(seen, "reasoning") == "Plan\n\nCheck"
    assert seen[-1] == ("text", "ok")


# -- sanitizing -------------------------------------------------------------------


SAMPLES = [
    "plain reasoning, nothing to clean",
    "lone \ud800 surrogate and another \udfff here",
    "smuggled \U000E0041\U000E0042 orphan tags",
    f"a flag {FLAG} kept whole",
    f"flag cut early \U0001F3F4\U000E0067\U000E0062 then text",
    "".join(f"{FLAG}x" for _ in range(11)),  # past _MAX_TAG_SEQUENCES
    f"\U0001F3F4 base at the end of a piece{FLAG}\U0001F3F4",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_streamed_sanitizing_equals_sanitizing_the_whole_text(text):
    rng = random.Random(548)
    for _ in range(200):
        cuts = sorted(rng.sample(range(1, len(text)), k=min(len(text) - 1, rng.randint(1, 8))))
        pieces = [text[i:j] for i, j in zip([0, *cuts], [*cuts, len(text)])]
        sanitizer = StreamTextSanitizer()
        streamed = "".join(sanitizer.feed(p) for p in pieces) + sanitizer.flush()
        assert streamed == sanitize_text_field(text), pieces


def test_a_lone_surrogate_reaches_the_host_as_text_replay_can_write():
    llm, _ = _completions(
        _completions_chunk(reasoning_content="bad \ud800 byte")
        + _completions_chunk(content="ok", finish="stop") + b"data: [DONE]\n\n")
    seen = _through_the_runtime(llm)
    piece = _joined(seen, "reasoning")
    assert piece == "bad � byte"
    # What ``replay/recorder.py`` does with an event payload.
    json.dumps({"text": piece}, ensure_ascii=False).encode("utf-8")


def test_a_flag_split_across_chunks_survives():
    halves = (FLAG[:3], FLAG[3:])
    llm, _ = _completions(
        _completions_chunk(reasoning_content=f"go {halves[0]}")
        + _completions_chunk(reasoning_content=f"{halves[1]} team")
        + _completions_chunk(content="ok", finish="stop") + b"data: [DONE]\n\n")
    seen = _through_the_runtime(llm)
    assert _joined(seen, "reasoning") == f"go {FLAG} team"


def test_a_held_back_tail_reaches_the_host_before_the_answer():
    llm, _ = _completions(
        _completions_chunk(reasoning_content=f"done {FLAG[:3]}")
        + _completions_chunk(content="ok", finish="stop") + b"data: [DONE]\n\n")
    seen = _through_the_runtime(llm)
    assert [k for k, _ in seen] == ["reasoning", "reasoning", "text"]
    assert _joined(seen, "reasoning") == "done \U0001F3F4"  # unclosed: tags dropped


# -- retries and the non-streaming paths ------------------------------------------


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda _d: None)
    monkeypatch.setattr(client_mod, "_interruptible_sleep", lambda _d, _t=None: True)


def test_a_drop_mid_reasoning_is_retried_and_its_pieces_stay(no_sleep):
    class Dies(httpx.SyncByteStream):
        def __iter__(self):
            yield _completions_chunk(reasoning_content="first try")
            raise httpx.RemoteProtocolError("peer closed connection")

    llm, calls = _completions(
        Dies(),
        _completions_chunk(reasoning_content="second try")
        + _completions_chunk(content="ok", finish="stop") + b"data: [DONE]\n\n")
    seen, callbacks = _recording()
    message = llm.chat_stream(HELLO, **callbacks).choices[0].message
    assert len(calls) == 2
    assert message.reasoning_content == "second try"
    # Never retracted: the host saw the failed attempt's reasoning, then this.
    assert seen == [("reasoning", "first try"), ("reasoning", "second try"), ("text", "ok")]


def test_a_parameter_repair_after_reasoning_resets_like_a_retry(no_sleep):
    """The repair re-sends without a wait, but the host saw reasoning: it gets
    the ``on_retry`` reset, and the runtime drops the held-back tail."""
    class Rejected(httpx.SyncByteStream):
        def __iter__(self):
            yield _completions_chunk(reasoning_content=f"a {FLAG[:3]}")
            raise ValueError("unsupported 'max_tokens': use 'max_completion_tokens'")

    ok = (_completions_chunk(reasoning_content="b")
          + _completions_chunk(content="ok", finish="stop") + b"data: [DONE]\n\n")
    llm, calls = _completions(Rejected(), ok)
    seen = _through_the_runtime(llm)
    assert len(calls) == 2 and llm._use_max_completion_tokens
    assert seen == [("reasoning", "a "), ("reasoning", "b"), ("text", "ok")]

    retries: List[dict] = []
    llm, _ = _completions(Rejected(), ok)
    llm.chat_stream(HELLO, on_retry=retries.append, on_reasoning_chunk=lambda _c: None)
    assert [r["reason"] for r in retries] == ["parameter_repair"]


def test_the_gemini_bypass_sends_the_whole_reasoning_once_before_the_text(no_sleep):
    body = {
        "id": "c", "object": "chat.completion", "created": 1, "model": "gemini-test",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": "ok", "reasoning_content": "weigh \ud800 it",
        }}],
    }
    llm, _ = _completions(body, body, model="gemini-test")  # the client, then the runtime
    seen, callbacks = _recording()
    llm.chat_stream(HELLO, **callbacks)
    assert seen == [("reasoning", "weigh \ud800 it"), ("text", "ok")]
    assert _through_the_runtime(llm) == [("reasoning", "weigh � it"), ("text", "ok")]


# -- the agent turn: no whole-text THINKING after the pieces ----------------------


def test_a_client_that_never_streams_reasoning_still_shows_it_once():
    """A host ``llm_client=`` whose ``chat_stream`` ignores the callback."""
    from types import SimpleNamespace

    from agentao.runtime.llm_call import run_llm_call

    def chat_stream(*, on_text_chunk, **_kwargs):
        on_text_chunk("ok")
        message = SimpleNamespace(content="ok", reasoning_content="weigh \ud800 it")
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)],
                               usage=None)

    events: List[AgentEvent] = []
    agent = SimpleNamespace(
        transport=SimpleNamespace(emit=events.append), replay_manager=None,
        llm=SimpleNamespace(model="m", temperature=0.0, max_tokens=256, chat_stream=chat_stream),
    )
    run_llm_call(agent, messages=[{"role": "user", "content": "hi"}], tools=[])
    assert [e.data["text"] for e in events if e.type == EventType.THINKING] == ["weigh � it"]


def test_a_client_that_streams_raw_reasoning_is_sanitized_on_the_way_out():
    """A host ``llm_client=`` that forwards its provider's pieces unsanitized."""
    from types import SimpleNamespace

    from agentao.runtime.llm_call import run_llm_call

    def chat_stream(*, on_text_chunk, on_reasoning_chunk, **_kwargs):
        on_reasoning_chunk("weigh \ud800 it")
        on_reasoning_chunk("\U000E0041")  # an orphan tag character only
        on_text_chunk("ok")
        message = SimpleNamespace(content="ok", reasoning_content="weigh \ud800 it\U000E0041")
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)],
                               usage=None)

    events: List[AgentEvent] = []
    agent = SimpleNamespace(
        transport=SimpleNamespace(emit=events.append), replay_manager=None,
        llm=SimpleNamespace(model="m", temperature=0.0, max_tokens=256, chat_stream=chat_stream),
    )
    run_llm_call(agent, messages=[{"role": "user", "content": "hi"}], tools=[])
    assert [e.data["text"] for e in events if e.type == EventType.THINKING] == ["weigh � it"]


def test_raw_pieces_share_one_tag_sequence_budget_across_the_stream():
    """A host's raw pieces: a split flag survives, and the cap is per stream."""
    from types import SimpleNamespace

    from agentao.runtime.llm_call import run_llm_call
    from agentao.security.unicode_tags import _MAX_TAG_SEQUENCES

    flag = "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"
    pieces = ["a" + flag[:3], flag[3:]] + [flag] * (_MAX_TAG_SEQUENCES + 2)

    def chat_stream(*, on_text_chunk, on_reasoning_chunk, **_kwargs):
        for piece in pieces:
            on_reasoning_chunk(piece)
        on_text_chunk("ok")
        message = SimpleNamespace(content="ok", reasoning_content="".join(pieces))
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)],
                               usage=None)

    events: List[AgentEvent] = []
    agent = SimpleNamespace(
        transport=SimpleNamespace(emit=events.append), replay_manager=None,
        llm=SimpleNamespace(model="m", temperature=0.0, max_tokens=256, chat_stream=chat_stream),
    )
    run_llm_call(agent, messages=[{"role": "user", "content": "hi"}], tools=[])
    shown = "".join(e.data["text"] for e in events if e.type == EventType.THINKING)
    assert shown == sanitize_text_field("".join(pieces))
    assert shown.count("\U000E007F") == _MAX_TAG_SEQUENCES


def test_a_turn_sends_each_piece_once_and_nothing_whole_after():
    llm_body = (_completions_chunk(reasoning_content="weigh ")
                + _completions_chunk(reasoning_content="it")
                + _completions_chunk(content="ok", finish="stop") + b"data: [DONE]\n\n")
    events: List[AgentEvent] = []
    agent = Agentao(api_key="k", base_url="http://wire.test/v1", model="m",
                    working_directory=Path.cwd(),
                    transport=SdkTransport(on_event=events.append))
    agent.llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      content=llm_body))))
    try:
        assert agent.chat("hi") == "ok"
    finally:
        agent.close()
    order = [(e.type, e.data.get("text") or e.data.get("chunk"))
             for e in events if e.type in (EventType.THINKING, EventType.LLM_TEXT)]
    assert order == [(EventType.THINKING, "weigh "), (EventType.THINKING, "it"),
                     (EventType.LLM_TEXT, "ok")]


# -- the CLI ----------------------------------------------------------------------


@pytest.fixture
def cli():
    with patch("agentao.cli.app.safe_load_dotenv"), patch(
        "agentao.cli.subcommands._load_and_register_plugins"
    ), patch("agentao.cli.app.Agentao"):
        from agentao.cli import AgentaoCLI

        app = AgentaoCLI()
        yield app
        # A TOOL_START with no TOOL_COMPLETE leaves the display's progress
        # timer re-arming itself until interpreter shutdown.
        for state in list(app.display._states.values()):
            app.display._cancel_progress_timer(state)
        app.display._states.clear()


@pytest.fixture
def screen(monkeypatch) -> io.StringIO:
    import agentao.cli.transport as transport

    buf = io.StringIO()
    monkeypatch.setattr(transport, "console", Console(file=buf, width=200, highlight=False))
    return buf


def _event(kind: EventType, **data: Any) -> AgentEvent:
    return AgentEvent(kind, data)


def test_the_cli_draws_one_header_per_stretch_and_keeps_whitespace(cli, screen):
    from agentao.cli import transport

    for piece in ("weigh", "\n\n", "it"):
        transport.emit_event(cli, _event(EventType.THINKING, text=piece))
    transport.emit_event(cli, _event(EventType.TOOL_START, tool="read_file", args={}, call_id="1"))
    transport.emit_event(cli, _event(EventType.THINKING, text="again"))
    transport.end_thinking(cli)
    out = screen.getvalue()
    assert out.count("Thinking") == 2
    assert "weigh\n  \n  it" in out
    assert cli._thinking_open is False


def test_whitespace_alone_opens_no_stretch(cli, screen):
    from agentao.cli import transport

    # A model with thinking off may send "\n\n" as its whole reasoning.
    transport.emit_event(cli, _event(EventType.THINKING, text="\n\n"))
    assert cli._thinking_open is False
    transport.emit_event(cli, _event(EventType.THINKING, text="\nweigh"))
    transport.end_thinking(cli)
    out = screen.getvalue()
    assert out.count("Thinking") == 1
    assert "  weigh" in out and "\n  \n" not in out


@pytest.mark.parametrize("kind", [
    EventType.LLM_TEXT, EventType.LLM_RETRY, EventType.TOOL_START,
    EventType.TOOL_CONFIRMATION, EventType.ERROR, EventType.TURN_START,
])
def test_anything_drawn_closes_the_stretch_first(cli, screen, kind):
    from agentao.cli import transport

    transport.emit_event(cli, _event(EventType.THINKING, text="weigh it"))
    assert cli._thinking_open is True
    transport.emit_event(cli, _event(kind))
    assert cli._thinking_open is False


def test_markdown_mode_shows_a_tool_call_turns_text_before_its_tools(cli, screen):
    """Markdown mode drops ``LLM_TEXT`` until the turn's end; the text before
    a tool call used to reach the screen only as ``THINKING``."""
    from agentao.cli import transport

    cli.markdown_mode = True
    transport.emit_event(cli, _event(EventType.TURN_START))
    transport.emit_event(cli, _event(EventType.LLM_TEXT, chunk="I'll read "))
    transport.emit_event(cli, _event(EventType.LLM_TEXT, chunk="the config"))
    transport.emit_event(cli, _event(EventType.TOOL_START, tool="read_file", args={}))
    assert "I'll read the config" in screen.getvalue()
    # The final answer is the turn's to render: a new call forgets it.
    transport.emit_event(cli, _event(EventType.TURN_START))
    transport.emit_event(cli, _event(EventType.LLM_TEXT, chunk="the answer"))
    transport.emit_event(cli, _event(EventType.TURN_START))
    assert "the answer" not in screen.getvalue()


def test_a_confirmation_prompt_closes_the_stretch(cli, screen, monkeypatch):
    from agentao.cli import transport

    transport.emit_event(cli, _event(EventType.THINKING, text="weigh it"))
    monkeypatch.setattr(transport.readchar, "readkey", lambda: "1")
    transport.confirm_tool_execution(cli, "write_file", "write", {})
    assert cli._thinking_open is False
    assert screen.getvalue().index("weigh it") < screen.getvalue().index("Tool Confirmation")


def test_a_tool_call_turns_text_is_sent_once_as_text_never_as_thinking():
    """Text before a tool call went out as ``LLM_TEXT`` and again as
    ``THINKING``; appended to the reasoning pieces, it glued onto them."""
    call = {"index": 0, "id": "call_1", "type": "function",
            "function": {"name": "no_such_tool", "arguments": "{}"}}
    bodies = [
        _completions_chunk(reasoning_content="check")
        + _completions_chunk(content="Running it")
        + _completions_chunk(tool_calls=[call], finish="tool_calls") + b"data: [DONE]\n\n",
        _completions_chunk(content="done", finish="stop") + b"data: [DONE]\n\n",
    ]
    events: List[AgentEvent] = []
    agent = Agentao(api_key="k", base_url="http://wire.test/v1", model="m",
                    working_directory=Path.cwd(),
                    transport=SdkTransport(on_event=events.append))
    agent.llm.client = openai.OpenAI(
        api_key="k", base_url="http://wire.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      content=bodies.pop(0)))))
    try:
        assert agent.chat("hi") == "done"
    finally:
        agent.close()
    order = [(e.type, e.data.get("text") or e.data.get("chunk"))
             for e in events if e.type in (EventType.THINKING, EventType.LLM_TEXT)]
    assert order == [(EventType.THINKING, "check"), (EventType.LLM_TEXT, "Running it"),
                     (EventType.LLM_TEXT, "done")]
