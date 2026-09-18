"""The Models API on the ``anthropic-messages`` wire: ``GET /v1/models/{id}``.

The 200 body is what ``api.anthropic.com`` returned for ``claude-sonnet-5`` on
2026-09-18, trimmed of capabilities this code never reads; the 404 is what an
Anthropic-compatible gateway answered the same day. Both go through the real
SDK, so parsing and exception types are the SDK's own.
"""

from unittest.mock import MagicMock

import pytest

from agentao.context_manager import ContextManager
from agentao.llm.client import LLMClient
from tests.support.anthropic_wire import (
    Wire, attach, message_end, message_start, stream_of, text_block,
)

SONNET_5 = {
    "id": "claude-test", "type": "model", "display_name": "Claude Test",
    "created_at": "2026-06-29T00:00:00Z",
    "max_input_tokens": 1_000_000, "max_tokens": 128_000,
    "capabilities": {
        "batch": {"supported": True}, "citations": {"supported": True},
        "code_execution": {"supported": True},
        "context_management": {"supported": True},
        "effort": {"supported": True, "high": {"supported": True},
                   "low": {"supported": True}, "max": {"supported": True},
                   "medium": {"supported": True}},
        "image_input": {"supported": True}, "pdf_input": {"supported": True},
        "structured_outputs": {"supported": True},
        "thinking": {"supported": True, "types": {
            "adaptive": {"supported": True}, "enabled": {"supported": False}}},
    },
}


def _ok() -> bytes:
    return stream_of(message_start(input_tokens=5), text_block(0, "ok"), message_end())


# ``LLMClient`` opens ``agentao.log`` in the process cwd: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _llm(**kwargs) -> LLMClient:
    return LLMClient(api_key="k", base_url="https://api.example.test", model="claude-test",
                     api_format="anthropic-messages", **kwargs)


HELLO = [{"role": "user", "content": "hi"}]


def test_the_reported_output_cap_is_used_before_any_rejection():
    llm = _llm(max_tokens=500_000)
    wire = attach(llm, Wire(_ok(), models={"claude-test": SONNET_5}))
    llm.chat(HELLO)
    # One Messages request, already under the cap: no 400 was spent learning it.
    assert [r["max_tokens"] for r in wire.requests] == [128_000]
    assert llm.model_input_limit == 1_000_000
    assert llm.model_capabilities["thinking"]["types"]["enabled"] == {"supported": False}


def test_a_smaller_request_is_left_alone():
    llm = _llm()
    wire = attach(llm, Wire(_ok(), models={"claude-test": SONNET_5}))
    llm.chat(HELLO, max_tokens=300)
    assert wire.requests[0]["max_tokens"] == 300


def test_the_lookup_happens_once_per_model_and_again_after_a_switch():
    llm = _llm()
    wire = attach(llm, Wire(_ok(), _ok(), _ok(), models={"claude-test": SONNET_5}))
    llm.chat(HELLO)
    llm.chat(HELLO)
    assert wire.model_lookups == ["claude-test"]

    llm.model = "claude-other"          # what set_model does, then:
    llm.reset_capability_latches()
    assert llm.model_input_limit is None and llm.model_capabilities is None
    llm.chat(HELLO, max_tokens=500_000)
    assert wire.model_lookups == ["claude-test", "claude-other"]
    # The other model is unknown to the endpoint: nothing of the first one's
    # limits survives to clamp it.
    assert wire.requests[-1]["max_tokens"] == 500_000
    assert llm.model_input_limit is None


_ERR = {"type": "error", "error": {"type": "api_error", "message": "boom"}}


@pytest.mark.parametrize("answer, lookups", [
    (None, 1),                  # 404, a compatible gateway: a definite no
    ((403, _ERR), 1),
    ((200, {"unexpected": "shape"}), 1),
    ((429, _ERR), 2),           # transient: one more try, then stop asking
    ((529, _ERR), 2),
])
def test_an_endpoint_that_gives_nothing_changes_nothing(answer, lookups):
    llm = _llm(max_tokens=70_000)
    wire = attach(llm, Wire(_ok(), _ok(), _ok(),
                            models={"claude-test": answer} if answer else None))
    assert llm.chat(HELLO).choices[0].message.content == "ok"
    llm.chat(HELLO)
    llm.chat(HELLO)
    assert [r["max_tokens"] for r in wire.requests] == [70_000] * 3
    assert wire.model_lookups == ["claude-test"] * lookups
    assert llm.model_input_limit is None and llm.model_capabilities is None


def test_a_blip_on_the_first_turn_does_not_cost_the_session_its_limits():
    llm = _llm(max_tokens=500_000)
    answers = [(529, _ERR), SONNET_5]
    wire = Wire(_ok(), _ok())
    wire._models = type("Seq", (dict,), {"get": lambda self, key: answers.pop(0)})()
    attach(llm, wire)
    llm.chat(HELLO)
    llm.chat(HELLO)
    assert [r["max_tokens"] for r in wire.requests] == [500_000, 128_000]
    assert llm.model_input_limit == 1_000_000


def test_the_log_records_the_max_tokens_that_was_sent(caplog):
    """Adoption runs before the request is built, so the first request's log
    entry is already clamped — not the configured value that never went out."""
    llm = _llm(max_tokens=500_000)
    wire = attach(llm, Wire(_ok(), models={"claude-test": SONNET_5}))
    with caplog.at_level("INFO", logger=llm.logger.name):
        llm.chat(HELLO)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert wire.requests[0]["max_tokens"] == 128_000
    assert "128000" in logged and "500000" not in logged


def test_building_a_request_never_touches_the_network():
    llm = _llm(max_tokens=500_000)
    wire = attach(llm, Wire(models={"claude-test": SONNET_5}))
    llm._build_request_kwargs(HELLO, None, None, stream=True)
    assert wire.model_lookups == []


@pytest.mark.parametrize("field, value", [
    ("max_tokens", None), ("max_tokens", 0), ("max_input_tokens", None),
])
def test_a_field_the_endpoint_leaves_out_is_not_adopted(field, value):
    llm = _llm(max_tokens=500_000)
    wire = attach(llm, Wire(_ok(), models={"claude-test": {**SONNET_5, field: value}}))
    llm.chat(HELLO)
    if field == "max_tokens":
        assert wire.requests[0]["max_tokens"] == 500_000
        assert llm.model_input_limit == 1_000_000
    else:
        assert wire.requests[0]["max_tokens"] == 128_000
        assert llm.model_input_limit is None


def test_the_reported_window_narrows_the_context_budget_and_never_widens_it():
    llm = _llm()
    cm = ContextManager(llm, memory_tool=None, max_tokens=200_000)
    assert (cm.effective_max_tokens, cm.reported_limit) == (200_000, None)

    llm.model_input_limit = 64_000
    assert (cm.effective_max_tokens, cm.reported_limit) == (64_000, 64_000)
    assert cm.max_tokens == 200_000                          # the host's knob reads back
    assert cm.get_usage_stats([])["reported_limit"] == 64_000

    llm.model_input_limit = 1_000_000                         # larger than configured
    assert cm.effective_max_tokens == 200_000

    llm.reset_capability_latches()                            # a model switch
    assert cm.effective_max_tokens == 200_000 and cm.reported_limit is None


def test_a_mock_client_reports_no_window():
    """``MagicMock`` answers any attribute; only a positive ``int`` counts."""
    cm = ContextManager(MagicMock(), memory_tool=None, max_tokens=200_000)
    assert cm.reported_limit is None and cm.effective_max_tokens == 200_000


def test_the_streaming_entry_point_adopts_too():
    llm = _llm(max_tokens=500_000)
    wire = attach(llm, Wire(_ok(), models={"claude-test": SONNET_5}))
    llm.chat_stream(HELLO, on_text_chunk=lambda _chunk: None)
    assert wire.requests[0]["max_tokens"] == 128_000
