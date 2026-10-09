"""A provider-rejected image must not stay in history (#480).

The image message enters ``agent.messages`` before the model is called. When
the provider refused it, every later request carried it again and failed the
same way, for the rest of the session and after a resume. These tests drive
the runner with **real SDK exceptions** carrying the error bodies measured on
2026-10-09 (Anthropic, OpenAI Chat Completions and Responses, Gemini and Qwen
through their OpenAI-compatible endpoints), not hand-written strings.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import anthropic
import httpx
import openai
import pytest

from agentao.embedding.sessions import load_session, save_session
from agentao.llm._retry import _is_image_rejection
from agentao.replay import ReplayAdapter, ReplayReader, ReplayRecorder
from agentao.transport import EventType, NullTransport

from .support.anthropic_wire import Wire, attach, message_end, message_start, stream_of, text_block

# Agentao here writes to the process cwd: see ``isolated_cwd`` in conftest.py.
pytestmark = pytest.mark.usefixtures("isolated_cwd")

_PNG_B64 = "iVBORw0KGgoAAAAA"
_JPEG_B64 = "/9j/4AAAAAAAAAAA"


# ---------------------------------------------------------------------------
# Real error bodies, as the providers returned them
# ---------------------------------------------------------------------------

def _openai_error(status: int, message: str, code=None, param=None, cls=None):
    body = {"error": {"message": message, "type": "invalid_request_error",
                      "param": param, "code": code}}
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    cls = cls or {400: openai.BadRequestError, 401: openai.AuthenticationError,
                  429: openai.RateLimitError}.get(status, openai.APIStatusError)
    return cls(f"Error code: {status} - {body}",
               response=httpx.Response(status, request=req, json=body), body=body)


def _anthropic_error(message: str):
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(f"Error code: 400 - {body}",
                                     response=httpx.Response(400, request=req, json=body),
                                     body=body)


REJECTIONS = {
    "anthropic-media-type-mismatch": lambda: _anthropic_error(
        "messages.0.content.1.image.source.base64: The image was specified using the "
        "image/jpeg media type, but the image appears to be a image/png image"),
    "anthropic-not-an-image": lambda: _anthropic_error("Could not process image"),
    "anthropic-svg": lambda: _anthropic_error(
        "messages.0.content.1.image.source.base64.media_type: Input should be "
        "'image/jpeg', 'image/png', 'image/gif' or 'image/webp'"),
    "openai-completions-format": lambda: _openai_error(
        400, "You uploaded an unsupported image. Please make sure your image has of one "
        "the following formats: ['png', 'jpeg', 'gif', 'webp'].", code="invalid_image_format"),
    "openai-completions-corrupt": lambda: _openai_error(
        400, "You uploaded an unsupported image. Please make sure your image is valid.",
        code="image_parse_error"),
    "openai-responses": lambda: _openai_error(
        400, "The image data you provided does not represent a valid image. Please check "
        "your input and try again.", code="invalid_value", param="input"),
    "gemini-compat": lambda: _openai_error(
        400, "Unable to process input image. Please retry or report in "
        "https://developers.generativeai.google/guide/troubleshooting"),
    "qwen-format": lambda: _openai_error(
        400, "<400> InternalError.Algo.InvalidParameter: The image format is illegal and "
        "cannot be opened", code="invalid_parameter_error"),
    "qwen-too-small": lambda: _openai_error(
        400, "<400> InternalError.Algo.InvalidParameter: The image length and width do not "
        "meet the model restrictions. [height:8 or width:8 must be larger than 10]",
        code="invalid_parameter_error"),
}

# The probe's one unrelated 400, sent with an image in the request.
UNRELATED_400 = lambda: _openai_error(  # noqa: E731
    400, "Could not finish the message because max_tokens or model output limit was "
    "reached. Please try again with higher max_tokens.")


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(REJECTIONS))
def test_every_measured_rejection_is_classified(name):
    assert _is_image_rejection(REJECTIONS[name]()) is True


@pytest.mark.parametrize("error", [
    UNRELATED_400(),
    _openai_error(401, "Invalid API key; this key cannot use image input."),
    _openai_error(403, "Image input is not enabled for this project.", cls=openai.PermissionDeniedError),
    _openai_error(429, "Rate limit reached for image requests."),
    _openai_error(500, "Internal error while processing image.", cls=openai.InternalServerError),
    # An unrelated 400 that only echoes an identifier containing "image".
    _openai_error(400, "Invalid schema for function 'mcp_figma_get_image': "
                       "'required' is required to be supplied."),
    _openai_error(400, "Invalid 'messages[1].content[0].image_url_detail': unknown parameter."),
    # A validating proxy echoing the request's data URL in an unrelated 400.
    _openai_error(400, "1 validation error: temperature must be <= 2 "
                       "(input: {'url': 'data:image/png;base64,iVBORw0K'})"),
    # A tool whose schema has a parameter called "image".
    _anthropic_error("tools.0.input_schema.properties.image: Input should be a valid dictionary"),
    _openai_error(400, "Invalid schema for function 'render': 'image' is a required property."),
], ids=["unrelated-400", "auth-401", "permission-403", "rate-429", "server-500",
        "tool-name", "field-name", "echoed-data-url", "tool-schema-anthropic",
        "tool-schema-openai"])
def test_other_failures_are_not_image_rejections(error):
    assert _is_image_rejection(error) is False


def test_status_must_be_a_real_int():
    """An object that only *answers* ``status_code`` is not a 400."""
    fake = MagicMock()
    fake.__str__ = lambda self: "image"
    assert _is_image_rejection(fake) is False
    assert _is_image_rejection(Exception("Error code: 400 - image")) is False


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

class _Recording(NullTransport):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, event):
        self.events.append(event)


def _ok():
    r = MagicMock()
    r.choices[0].message.tool_calls = None
    r.choices[0].message.content = "ok"
    r.choices[0].message.reasoning_content = None
    return r


def _make_agent(transport=None):
    with patch("agentao.agent.LLMClient") as cls:
        llm = Mock()
        llm.logger = Mock()
        llm.model = "m"
        cls.return_value = llm
        from agentao.agent import Agentao
        return Agentao(working_directory=Path.cwd(), logger=Mock(), transport=transport,
                       api_key="x", base_url="http://x", model="m")


def _has_image(messages) -> bool:
    return any(
        isinstance(m.get("content"), list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
        for m in messages
    )


def _sends(agent, *outcomes):
    """Script ``_llm_call``; record what each request carried."""
    sent = []
    queue = list(outcomes)

    def call(messages, tools, token):
        sent.append([dict(m) for m in messages])
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    agent._llm_call = Mock(side_effect=call)
    return sent


def _note_texts(agent):
    return [
        p["text"] for m in agent.messages if isinstance(m.get("content"), list)
        for p in m["content"] if p.get("type") == "text" and p["text"].startswith("[Image removed")
    ]


@pytest.mark.parametrize("name", sorted(REJECTIONS))
def test_a_rejected_image_is_removed_and_the_next_turn_works(name):
    transport = _Recording()
    agent = _make_agent(transport)
    sent = _sends(agent, REJECTIONS[name](), _ok())

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])

    assert out.startswith("[LLM API error:")
    assert "Image content in this conversation's history was removed" in out
    assert not _has_image(agent.messages)
    # The user's text is still there, beside the note.
    user = next(m for m in agent.messages if m["role"] == "user" and isinstance(m["content"], list))
    assert "what is this?" in user["content"][0]["text"]
    assert user["content"][1]["text"].startswith("[Image removed from the conversation history after")
    # The turn stopped: one request, no silent retry without the image.
    assert len(sent) == 1

    assert agent.chat("hello again") == "ok"
    assert not _has_image(sent[1])

    removed = [e for e in transport.events if e.type == EventType.IMAGES_REMOVED]
    assert len(removed) == 1
    assert removed[0].data["reason"] == "provider_rejected"
    assert removed[0].data["images_removed"] == 1
    assert _PNG_B64 not in json.dumps(removed[0].data)


@pytest.mark.parametrize("error", [
    UNRELATED_400(),
    _openai_error(401, "Invalid API key; this key cannot use image input."),
], ids=["unrelated-400", "auth-401-mentioning-image"])
def test_other_errors_leave_history_alone(error):
    transport = _Recording()
    agent = _make_agent(transport)
    _sends(agent, error)

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])

    assert out.startswith("[LLM API error:")
    assert "history was removed" not in out
    assert _has_image(agent.messages)
    assert not [e for e in transport.events if e.type == EventType.IMAGES_REMOVED]


def test_every_image_in_history_goes_and_the_rest_of_each_message_stays():
    """The provider does not say which image it refused: all of them go,
    earlier turns' included. Only the image parts change."""
    agent = _make_agent()
    sent = _sends(agent, _ok(), REJECTIONS["anthropic-not-an-image"]())
    agent.chat("two pictures", images=[
        {"data": _PNG_B64, "mimeType": "image/png"},
        {"data": _JPEG_B64, "mimeType": "image/jpeg"},
    ])
    # A mixed message a host might have put in history: text after the image,
    # and a key of its own.
    agent.messages.append({
        "role": "user", "name": "host-note",
        "content": [
            {"type": "text", "text": "before"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PNG_B64}"}},
            {"type": "text", "text": "after"},
        ],
    })
    agent.chat("and this one", images=[{"data": _PNG_B64, "mimeType": "image/png"}])

    assert len(sent) == 2
    assert not _has_image(agent.messages)
    assert len(_note_texts(agent)) == 4
    mixed = next(m for m in agent.messages if m.get("name") == "host-note")
    assert [p["type"] for p in mixed["content"]] == ["text", "text", "text"]
    assert mixed["content"][0]["text"] == "before"
    assert mixed["content"][2]["text"] == "after"
    assert not any("base64" in t or "data:" in t for t in _note_texts(agent))


def test_switching_to_a_text_only_model_after_an_image_turn_recovers():
    """The old fallback was armed only on the turn that attached the image, so
    every later turn failed once the model stopped accepting images."""
    transport = _Recording()
    agent = _make_agent(transport)
    unsupported = _openai_error(400, "This model does not support image input.")
    sent = _sends(agent, _ok(), unsupported, _ok(), _ok())

    agent.chat("look", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    # Turn 2 is text only; history still carries turn 1's image.
    assert agent.chat("hello") == "ok"
    assert len(sent) == 3 and not _has_image(sent[2])
    assert agent.chat("again") == "ok"
    assert _note_texts(agent) == [
        "[Image removed from the conversation history because the current model "
        "does not accept image input. It can no longer be viewed.]"
    ]
    reasons = [e.data["reason"] for e in transport.events if e.type == EventType.IMAGES_REMOVED]
    assert reasons == ["model_unsupported"]


def test_a_tool_schema_400_that_says_images_unsupported_leaves_earlier_images():
    """The retry path's scan applies the same tool-schema guard as the
    terminal path: a 400 about a tool parameter named ``images`` is not the
    model refusing the user's picture."""
    agent = _make_agent()
    schema_400 = _openai_error(
        400, "Invalid schema for function 'gallery': tools[0].function.parameters."
        "properties.images: array type unsupported")
    sent = _sends(agent, _ok(), schema_400)
    agent.chat("look", images=[{"data": _PNG_B64, "mimeType": "image/png"}])

    out = agent.chat("hello")
    assert out.startswith("[LLM API error:")
    assert len(sent) == 2
    assert _has_image(agent.messages)
    assert not _note_texts(agent)


def test_this_turns_image_keeps_the_documented_attachment_tag():
    """Developer guide A.1: the image-turn degradation is an ``<attachment/>``
    tag. Unchanged — only images left over from earlier turns get the note.
    The rewrite is still announced: replay cannot see it otherwise."""
    transport = _Recording()
    agent = _make_agent(transport)
    unsupported = _openai_error(400, "This model does not support image input.")
    _sends(agent, unsupported, _ok())
    agent.chat("look", images=[{"data": _PNG_B64, "mimeType": "image/png", "_source": "shot.png"}])
    user = [m for m in agent.messages if m["role"] == "user"][-1]
    assert isinstance(user["content"], str)
    assert '<attachment uri="shot.png" mimetype="image/png"/>' in user["content"]
    removed = [e.data for e in transport.events if e.type == EventType.IMAGES_REMOVED]
    assert removed == [{"reason": "model_unsupported", "images_removed": 1,
                        "message_indices": [agent.messages.index(user)]}]


def test_a_rejection_on_the_retry_after_compaction_is_handled_too():
    agent = _make_agent()
    overflow = _openai_error(400, "This model's maximum context length is 1000 tokens. "
                                  "However, your messages resulted in 5000 tokens.",
                             code="context_length_exceeded")
    sent = _sends(agent, overflow, REJECTIONS["openai-completions-corrupt"]())

    def fake_run(request, *, system_prompt, messages_with_system=None, **_):
        return SimpleNamespace(
            outcome=SimpleNamespace(status="success", detail=None),
            system_prompt=system_prompt,
            messages_with_system=[{"role": "system", "content": system_prompt}] + agent.messages,
        )
    agent.compaction_coordinator.run = fake_run

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert len(sent) == 2
    assert "history was removed" in out
    assert not _has_image(agent.messages)


@pytest.mark.parametrize("name", ["qwen-format", "qwen-too-small"])
def test_a_qwen_image_rejection_is_not_treated_as_an_overflow(name):
    """DashScope uses one generic code for overflows and refused images; while
    the overflow table matched the code, a refused image ran both compaction
    rungs before failing anyway."""
    agent = _make_agent()
    sent = _sends(agent, REJECTIONS[name]())
    agent.compaction_coordinator.run = Mock(side_effect=AssertionError("compaction ran"))

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert len(sent) == 1
    assert "history was removed" in out
    assert not _has_image(agent.messages)


def test_a_qwen_parameter_error_is_not_treated_as_an_overflow():
    """#484: DashScope's generic code also heads every invalid-parameter 400.
    Read as an overflow, a bad temperature ran both compaction rungs, an LLM
    summarisation that rewrote history, and then failed anyway."""
    agent = _make_agent()
    sent = _sends(agent, _openai_error(
        400, "<400> InternalError.Algo.InvalidParameter: Temperature should be in "
        "[0.0, 2.0)", code="invalid_parameter_error"))
    agent.compaction_coordinator.run = Mock(side_effect=AssertionError("compaction ran"))

    out = agent.chat("hello")
    assert len(sent) == 1
    assert out.startswith("[LLM API error:") and "Temperature should be" in out


def test_removal_drops_the_stale_token_anchor():
    """The last response's prompt_tokens still counts the removed images."""
    agent = _make_agent()
    _sends(agent, REJECTIONS["anthropic-not-an-image"]())
    agent.context_manager.invalidate_token_anchor = Mock()
    agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    agent.context_manager.invalidate_token_anchor.assert_called_once_with()


def test_a_transport_that_raises_does_not_replace_the_provider_error():
    agent = _make_agent()
    _sends(agent, REJECTIONS["anthropic-not-an-image"]())
    real_emit = agent.transport.emit

    def emit(event):
        if event.type == EventType.IMAGES_REMOVED:
            raise RuntimeError("host transport is broken")
        return real_emit(event)
    agent.transport.emit = emit

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert out.startswith("[LLM API error:") and "history was removed" in out
    assert not _has_image(agent.messages)


def test_a_real_overflow_that_mentions_images_still_compacts():
    """An overflow phrase wins over the image heuristic: the images must not be
    removed for an error that is really about size."""
    agent = _make_agent()
    overflow = _openai_error(
        400, "This model's maximum context length is 128000 tokens. However, your "
        "messages resulted in 130000 tokens, including 3 images.",
        code="context_length_exceeded")
    assert _is_image_rejection(overflow)  # the overlap this test is about
    sent = _sends(agent, overflow, _ok())
    runs = []

    def fake_run(request, *, system_prompt, messages_with_system=None, **_):
        runs.append(request)
        return SimpleNamespace(
            outcome=SimpleNamespace(status="success", detail=None),
            system_prompt=system_prompt,
            messages_with_system=[{"role": "system", "content": system_prompt}] + agent.messages,
        )
    agent.compaction_coordinator.run = fake_run

    assert agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}]) == "ok"
    assert len(runs) == 1 and len(sent) == 2
    assert _has_image(agent.messages)


@pytest.mark.parametrize("error", [
    _openai_error(500, "vision is not supported on this deployment right now.",
                  cls=openai.InternalServerError),
    _openai_error(429, "Requests with images are not supported at this rate tier."),
], ids=["server-500", "rate-429"])
def test_earlier_images_survive_a_non_input_error_that_mentions_vision(error):
    """Removing an earlier turn's image is permanent, so it needs a
    400/413/422; a transient error that says "not supported" leaves it."""
    agent = _make_agent()
    sent = _sends(agent, _ok(), error)
    agent.chat("look", images=[{"data": _PNG_B64, "mimeType": "image/png"}])

    out = agent.chat("hello")
    assert out.startswith("[LLM API error:")
    assert len(sent) == 2
    assert _has_image(agent.messages)
    assert not _note_texts(agent)


_OVERFLOW_WITH_IMAGES = lambda: _openai_error(  # noqa: E731
    400, "Maximum context length exceeded: 140000 tokens including 3 images.",
    code="context_length_exceeded")


@pytest.mark.parametrize("script", [
    (("cancelled",), 1),                    # the host declined the overflow compaction
    (("success", "skipped"), 2),            # compacted, still too long, no smaller cut
    (("success", "success"), 3),            # minimal history, and it still overflows
], ids=["overflow-compaction-cancelled", "minimal-history-no-cut", "still-overflowing"])
def test_a_genuine_overflow_that_mentions_images_keeps_them_on_every_exit(script):
    """Codex review: an overflow that says "including 3 images" refuses no
    image. Every overflow exit returns the context-length error with history
    untouched, as a declined overflow always has."""
    from agentao.context_manager import is_context_too_long_error

    assert is_context_too_long_error(_OVERFLOW_WITH_IMAGES())
    assert _is_image_rejection(_OVERFLOW_WITH_IMAGES())  # the overlap this guards
    statuses, sends = script
    transport = _Recording()
    agent = _make_agent(transport)
    sent = _sends(agent, *[_OVERFLOW_WITH_IMAGES() for _ in range(sends)])
    queue = list(statuses)

    def fake_run(request, *, system_prompt, messages_with_system=None, **_):
        return SimpleNamespace(
            outcome=SimpleNamespace(status=queue.pop(0), detail=None),
            system_prompt=system_prompt,
            messages_with_system=[{"role": "system", "content": system_prompt}] + agent.messages,
        )
    agent.compaction_coordinator.run = fake_run

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert len(sent) == sends
    assert out.startswith("[LLM API error:") and "Maximum context length" in out
    assert "history was removed" not in out
    assert _has_image(agent.messages)
    assert not [e for e in transport.events if e.type == EventType.IMAGES_REMOVED]


def test_an_echoed_data_url_is_cut_from_the_saved_error():
    agent = _make_agent()
    echo = _openai_error(400, "Invalid image: {'url': 'data:image/png;base64,"
                              + "QUFB" * 50 + "'}")
    _sends(agent, echo)
    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert "data:image/png;base64,[…]" in out
    assert "QUFB" not in json.dumps(agent.messages)


@pytest.mark.parametrize("payload", [
    "QUFB\\/QUFB-QUFB_QUFB" * 20,   # JSON-escaped slash, URL-safe alphabet
], ids=["escaped-and-url-safe"])
def test_an_escaped_echoed_data_url_is_cut_whole(payload):
    agent = _make_agent()
    _sends(agent, _openai_error(400, 'Invalid image: {"url": "data:image\\/png;base64,'
                                     + payload + '"} end'))
    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert "QUFB" not in json.dumps(agent.messages)
    assert '"} end' in out


def test_a_saved_session_does_not_bring_the_image_back(tmp_path):
    agent = _make_agent()
    _sends(agent, REJECTIONS["gemini-compat"]())
    agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])

    save_session(agent.messages, "m", session_id="s1", project_root=tmp_path)
    messages, _, _ = load_session("s1", project_root=tmp_path)
    assert not _has_image(messages)
    assert _PNG_B64 not in json.dumps(messages)


def test_the_rewrite_is_in_the_replay(tmp_path):
    """Delta capture records only the messages a turn adds; the in-place
    rewrite is visible only through ``images_removed``."""
    agent = _make_agent()
    rec = ReplayRecorder.create("sess", tmp_path)
    adapter = ReplayAdapter(NullTransport(), rec)
    agent.transport = adapter
    _sends(agent, REJECTIONS["openai-responses"]())

    adapter.begin_turn("what is this?")
    agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    adapter.end_turn("")
    rec.close()

    events = ReplayReader(rec.path).events()
    removed = [e for e in events if e["kind"] == "images_removed"]
    assert len(removed) == 1
    assert removed[0]["payload"]["reason"] == "provider_rejected"
    assert removed[0]["payload"]["images_removed"] == 1


# ---------------------------------------------------------------------------
# End to end: the exception a real client raises is the one classified
# ---------------------------------------------------------------------------

def test_a_real_anthropic_client_rejection_clears_history():
    from agentao.agent import Agentao
    from agentao.llm.client import LLMClient

    llm = LLMClient(api_key="test-key", base_url="https://api.example.test", model="claude-test",
                    api_format="anthropic-messages", max_tokens=1024,
                    logger=logging.getLogger("test.anthropic"))
    wire = attach(llm, Wire(
        (400, {"type": "error", "error": {"type": "invalid_request_error",
                                          "message": "Could not process image"}}),
        stream_of(message_start(), text_block(0, "fine"), message_end()),
    ))
    agent = Agentao(working_directory=Path.cwd(), logger=Mock(), llm_client=llm)

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert "history was removed" in out
    assert agent.chat("hello again") == "fine"

    second = json.dumps(wire.requests[1])
    assert '"type": "image"' not in second
    assert "[Image removed from the conversation history after" in second


def test_an_image_url_the_wire_cannot_send_does_not_stick_the_session():
    """#485: a history image whose URL the anthropic-messages wire cannot
    express (here a host-written ``file:`` URL) used to raise before any
    request, on every turn. It now goes out as a note, and history keeps
    what the host wrote."""
    from agentao import Agentao
    from agentao.llm._image_parts import UNSENDABLE_IMAGE_NOTE
    from agentao.llm.client import LLMClient

    llm = LLMClient(api_key="test-key", base_url="https://api.example.test", model="claude-test",
                    api_format="anthropic-messages", max_tokens=1024,
                    logger=logging.getLogger("test.anthropic"))
    wire = attach(llm, Wire(
        stream_of(message_start(), text_block(0, "first"), message_end()),
        stream_of(message_start(), text_block(0, "second"), message_end()),
    ))
    agent = Agentao(working_directory=Path.cwd(), logger=Mock(), llm_client=llm)
    host_part = {"type": "image_url", "image_url": {"url": "file:///tmp/cat.png"}}
    agent.messages.append({"role": "user", "content": [{"type": "text", "text": "cat"}, host_part]})
    agent.messages.append({"role": "assistant", "content": "noted"})

    assert agent.chat("what was it?") == "first"
    assert agent.chat("and now?") == "second"
    assert len(wire.requests) == 2
    for request in wire.requests:
        assert UNSENDABLE_IMAGE_NOTE in json.dumps(request)
    assert host_part in agent.messages[0]["content"]


# ---------------------------------------------------------------------------
# An "image" the error only echoes back (#486)
# ---------------------------------------------------------------------------

# Constructed, as in #486: no gateway has been seen doing this.
_ECHO_400 = lambda: _openai_error(  # noqa: E731
    400, "invalid tool_call_id 'call_9' in messages[3]: {content: 'please describe this image'}")

_EVERYDAY_PROMPTS = [
    "what is this?", "describe this image", "please describe this image",
    "Can you see the image?", "is this a valid image?", "what's in these images",
    "the image appears blurry", "process image files in the folder",
]


def test_an_error_that_only_echoes_the_users_words_is_not_a_rejection():
    assert _is_image_rejection(_ECHO_400())  # the misreading this guards
    assert not _is_image_rejection(_ECHO_400(), ["please describe this image"])


@pytest.mark.parametrize("name", sorted(REJECTIONS))
def test_every_measured_rejection_survives_everyday_wording(name):
    """The provider's own "image" sits in words the user does not send."""
    assert _is_image_rejection(REJECTIONS[name](), _EVERYDAY_PROMPTS) is True


def test_one_unexplained_image_keeps_it_a_rejection():
    """An echo beside the provider's own refusal is still a refusal."""
    both = _openai_error(400, "Could not process image. Request: {content: 'describe this image'}")
    assert _is_image_rejection(both, ["describe this image"])


def test_an_echo_keeps_the_images():
    agent = _make_agent()
    sent = _sends(agent, _ECHO_400())
    out = agent.chat("please describe this image",
                     images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert out.startswith("[LLM API error:") and "history was removed" not in out
    assert len(sent) == 1
    assert _has_image(agent.messages)
    assert not _note_texts(agent)


def test_a_repeated_rejection_is_not_explained_by_the_earlier_error_reply():
    """The first refusal's ``[LLM API error: …]`` reply quotes it word for
    word. Counting assistant text would read the second, identical refusal
    of a re-attached image as an echo, and the image would stick."""
    agent = _make_agent()
    _sends(agent, REJECTIONS["anthropic-not-an-image"](), REJECTIONS["anthropic-not-an-image"]())
    image = [{"data": _PNG_B64, "mimeType": "image/png"}]
    assert "history was removed" in agent.chat("what is this?", images=image)
    assert "Could not process image" in agent.messages[-1]["content"]

    assert "history was removed" in agent.chat("try this one", images=image)
    assert not _has_image(agent.messages)
