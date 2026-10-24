"""An uninformative HTTP 413 gets one compaction and one retry (#572).

GitHub Copilot answers an oversized request with a 413 whose body says
nothing ("failed to parse request"). Such a body cannot tell a whole request
that is too large from one image that is, so the policy is bounded:

* a recognised token overflow keeps the full ladder (unchanged);
* an explicit image rejection keeps the image-removal path (unchanged);
* a real ``int`` 413 with no more to go on gets one full compaction, then one
  retry, and whatever that retry raises ends the turn — no minimal history,
  no image removal on the strength of the 413.

The errors are real SDK exceptions, as in ``test_image_rejection_history.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import anthropic
import httpx
import openai
import pytest

from agentao.context_manager import is_context_too_long_error, is_request_too_large_error
from agentao.transport import EventType, NullTransport

pytestmark = pytest.mark.usefixtures("isolated_cwd")

_PNG_B64 = "iVBORw0KGgoAAAAA"


def _openai_status(status: int, body, cls=openai.APIStatusError):
    req = httpx.Request("POST", "https://api.githubcopilot.com/chat/completions")
    return cls(f"Error code: {status} - {body}",
               response=httpx.Response(status, request=req, json=body), body=body)


def _copilot_413():
    """The body Copilot returned, as pi-mono 73f8de80b recorded it."""
    return _openai_status(413, {"message": "failed to parse request", "code": ""})


def _anthropic_413():
    body = {"type": "error", "error": {"type": "request_too_large",
                                       "message": "Request exceeds the maximum size"}}
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIStatusError("Error code: 413 - " + str(body),
                                    response=httpx.Response(413, request=req, json=body),
                                    body=body)


def _image_413():
    """A 413 that names the image: explicit image-rejection evidence."""
    return _openai_status(413, {"error": {"message": "The image is too large to process.",
                                          "type": "invalid_request_error"}})


def _image_400():
    body = {"error": {"message": "You uploaded an unsupported image. Please make sure your "
                                 "image is valid.", "type": "invalid_request_error",
                      "param": None, "code": "image_parse_error"}}
    return _openai_status(400, body, cls=openai.BadRequestError)


def _overflow_400():
    body = {"error": {"message": "This model's maximum context length is 1000 tokens. However, "
                                 "your messages resulted in 5000 tokens.",
                      "type": "invalid_request_error", "param": None,
                      "code": "context_length_exceeded"}}
    return _openai_status(400, body, cls=openai.BadRequestError)


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------

def test_copilots_413_is_a_request_too_large_error():
    e = _copilot_413()
    assert not is_context_too_long_error(e)  # the miss #572 is about
    assert is_request_too_large_error(e)


def test_a_recognised_413_overflow_stays_on_the_overflow_ladder():
    e = _anthropic_413()
    assert is_context_too_long_error(e)
    assert not is_request_too_large_error(e)


@pytest.mark.parametrize("status", ["413", True, 413.0, None])
def test_the_status_must_be_a_real_int(status):
    e = Exception("Error code: 413 - request entity too large")
    e.status_code = status
    assert not is_request_too_large_error(e)


@pytest.mark.parametrize("status", [400, 422, 500])
def test_a_body_that_only_mentions_413_is_not_one(status):
    e = _openai_status(status, {"message": "upstream said 413 request entity too large"})
    assert not is_request_too_large_error(e)


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


def _sends(agent, *outcomes):
    sent = []
    queue = list(outcomes)

    def call(messages, tools, token):
        sent.append(list(messages))
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    agent._llm_call = Mock(side_effect=call)
    return sent


def _compactions(agent, *statuses):
    """Fake the coordinator; record each request it was asked to run."""
    runs = []
    queue = list(statuses)

    def fake_run(request, *, system_prompt, messages_with_system=None, **_):
        runs.append(request)
        return SimpleNamespace(
            outcome=SimpleNamespace(status=queue.pop(0), detail=None),
            system_prompt=system_prompt,
            messages_with_system=[{"role": "system", "content": system_prompt}] + agent.messages,
        )
    agent.compaction_coordinator.run = fake_run
    return runs


def _has_image(messages) -> bool:
    return any(
        isinstance(m.get("content"), list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
        for m in messages
    )


def _image_events(transport):
    return [e for e in transport.events if e.type == EventType.IMAGES_REMOVED]


def test_a_413_on_a_request_without_images_compacts_once_and_retries():
    agent = _make_agent()
    sent = _sends(agent, _copilot_413(), _ok())
    runs = _compactions(agent, "success")

    assert agent.chat("hello") == "ok"
    assert len(sent) == 2
    assert [(r.kind, r.reason) for r in runs] == [("full", "api_overflow")]


def test_a_413_with_images_and_no_image_evidence_compacts_and_keeps_them():
    # The boundary #572 admits: nothing in the body says the image is why,
    # so the request is not guessed to be an image rejection.
    transport = _Recording()
    agent = _make_agent(transport)
    sent = _sends(agent, _copilot_413(), _ok())
    runs = _compactions(agent, "success")

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert out == "ok"
    assert len(sent) == 2 and len(runs) == 1
    assert _has_image(agent.messages)
    assert not _image_events(transport)


def test_a_413_that_names_the_image_takes_the_image_path_and_does_not_compact():
    transport = _Recording()
    agent = _make_agent(transport)
    sent = _sends(agent, _image_413())
    agent.compaction_coordinator.run = Mock(side_effect=AssertionError("compaction ran"))

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert out.startswith("[LLM API error:")
    assert "history was removed" in out
    assert len(sent) == 1
    assert not _has_image(agent.messages)
    assert len(_image_events(transport)) == 1


def test_a_413_that_persists_after_compaction_returns_the_error():
    transport = _Recording()
    agent = _make_agent(transport)
    sent = _sends(agent, _copilot_413(), _copilot_413())
    runs = _compactions(agent, "success")

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert out.startswith("[LLM API error:") and "failed to parse request" in out
    assert len(sent) == 2
    assert [r.kind for r in runs] == ["full"]  # no minimal-history rung
    assert "history was removed" not in out
    assert _has_image(agent.messages)
    assert not _image_events(transport)


@pytest.mark.parametrize("status", ["cancelled", "skipped", "failed"])
def test_a_compaction_that_did_not_succeed_does_not_resend(status):
    agent = _make_agent()
    sent = _sends(agent, _copilot_413())
    runs = _compactions(agent, status)

    out = agent.chat("hello")
    assert out.startswith("[LLM API error:") and "failed to parse request" in out
    assert len(sent) == 1 and len(runs) == 1


def test_a_retry_that_turns_into_an_image_rejection_takes_the_image_path():
    transport = _Recording()
    agent = _make_agent(transport)
    sent = _sends(agent, _copilot_413(), _image_400())
    _compactions(agent, "success")

    out = agent.chat("what is this?", images=[{"data": _PNG_B64, "mimeType": "image/png"}])
    assert len(sent) == 2
    assert "history was removed" in out
    assert not _has_image(agent.messages)
    assert len(_image_events(transport)) == 1


def test_a_retry_that_turns_into_an_overflow_is_not_compacted_again():
    agent = _make_agent()
    sent = _sends(agent, _copilot_413(), _overflow_400())
    runs = _compactions(agent, "success")

    out = agent.chat("hello")
    assert out.startswith("[LLM API error:") and "maximum context length" in out
    assert len(sent) == 2 and len(runs) == 1


def test_a_string_413_status_is_not_recovered():
    agent = _make_agent()
    e = Exception("Error code: 413 - failed to parse request")
    e.status_code = "413"
    sent = _sends(agent, e)
    agent.compaction_coordinator.run = Mock(side_effect=AssertionError("compaction ran"))

    assert agent.chat("hello").startswith("[LLM API error:")
    assert len(sent) == 1


# ---------------------------------------------------------------------------
# The breaker, through the real coordinator
# ---------------------------------------------------------------------------

def _seed_history(agent, n=12):
    for i in range(n):
        agent.messages.append({"role": "user", "content": f"user {i} " + "x" * 400})
        agent.messages.append({"role": "assistant", "content": f"assistant {i} " + "y" * 400})


def test_a_413_after_a_good_summary_does_not_charge_the_breaker():
    agent = _make_agent()
    _seed_history(agent)
    cm = agent.context_manager
    cm._summarize_formatted = lambda _f, **_: "A summary of the earlier conversation."
    before = len(agent.messages)
    _sends(agent, _copilot_413(), _copilot_413())

    out = agent.chat("hello")
    assert out.startswith("[LLM API error:")
    assert len(agent.messages) < before  # the compaction really ran
    assert cm.circuit_breaker_failures == 0


def test_a_failed_summary_still_counts_as_before():
    agent = _make_agent()
    _seed_history(agent)
    cm = agent.context_manager
    cm._summarize_formatted = lambda _f, **_: ""
    sent = _sends(agent, _copilot_413())

    out = agent.chat("hello")
    assert out.startswith("[LLM API error:")
    assert len(sent) == 1  # a failed compaction does not resend
    assert cm.circuit_breaker_failures == 1
