"""A wire switch moves the thinking level agentao wrote, and only that one.

Each wire rejects the others' thinking fields on every request, so a level
``/thinking`` or ``agentao run`` wrote for one wire would fail every request
after ``/provider`` changed the wire. The level is told apart by where it
came from (``llm/thinking.py`` records what it wrote), not by its field name:
a field the host set through ``LLM_EXTRA_BODY`` or ``extra_body=`` stays as it
was, as it always has (``docs/design/host-llm-extra-params.md`` §5).
"""

from __future__ import annotations

import pytest

from agentao.llm.client import LLMClient
from agentao.llm.thinking import clear_thinking, sent_thinking, set_thinking

# The clients here keep the default ``agentao.log``: see ``isolated_cwd``.
pytestmark = pytest.mark.usefixtures("isolated_cwd")


def _client(extra_body=None, api_format="openai-completions") -> LLMClient:
    return LLMClient(
        api_key="k", base_url="https://a.test/v1", model="m",
        api_format=api_format, extra_body=extra_body,
    )


def test_a_level_agentao_wrote_moves_to_the_new_wires_field():
    llm = _client()
    set_thinking(llm, "high")

    llm.reconfigure("k", api_format="anthropic-messages")
    assert llm.extra_body == {"output_config": {"effort": "high"}}
    assert sent_thinking(llm) == "high"

    llm.reconfigure("k", api_format="openai-responses")
    assert llm.extra_body == {"reasoning": {"effort": "high"}}

    llm.reconfigure("k", api_format="openai-completions")
    assert llm.extra_body == {"reasoning_effort": "high"}


def test_other_keys_the_host_set_beside_it_are_kept():
    llm = _client({"reasoning": {"summary": "auto"}, "top_p": 0.5},
                  api_format="openai-responses")
    set_thinking(llm, "low")

    llm.reconfigure("k", api_format="openai-completions")
    assert llm.extra_body == {
        "reasoning": {"summary": "auto"}, "top_p": 0.5, "reasoning_effort": "low",
    }


def test_a_field_the_host_set_stays_as_it_was():
    llm = _client({"reasoning_effort": "high"})

    llm.reconfigure("k", api_format="anthropic-messages")
    assert llm.extra_body == {"reasoning_effort": "high"}


def test_a_level_the_host_changed_since_is_the_hosts():
    llm = _client()
    set_thinking(llm, "high")
    llm.extra_body["reasoning_effort"] = "medium"

    llm.reconfigure("k", api_format="anthropic-messages")
    assert llm.extra_body == {"reasoning_effort": "medium"}


def test_a_level_the_new_wire_does_not_take_is_dropped(caplog):
    llm = _client()
    set_thinking(llm, "minimal")

    with caplog.at_level("INFO", logger=llm.logger.name):
        llm.reconfigure("k", api_format="anthropic-messages")
    assert llm.extra_body == {}
    assert sent_thinking(llm) is None
    assert any("'minimal' dropped" in r.getMessage() for r in caplog.records)


def test_a_cleared_level_is_not_brought_back():
    llm = _client({"reasoning_effort": "high"})
    set_thinking(llm, "low")
    clear_thinking(llm)
    llm.extra_body["reasoning_effort"] = "low"  # the host's, now

    llm.reconfigure("k", api_format="anthropic-messages")
    assert llm.extra_body == {"reasoning_effort": "low"}


def test_a_model_switch_on_the_same_wire_leaves_it():
    llm = _client()
    set_thinking(llm, "high")

    llm.reconfigure("k", model="m2")
    assert llm.extra_body == {"reasoning_effort": "high"}


def test_a_failed_switch_puts_the_level_back(monkeypatch):
    from agentao.llm import _anthropic_messages as wire_mod

    llm = _client()
    set_thinking(llm, "high")

    def boom(self):
        raise ImportError("no anthropic here")

    monkeypatch.setattr(wire_mod.AnthropicMessagesAdapter, "create_client", boom)
    with pytest.raises(ImportError):
        llm.reconfigure("k", api_format="anthropic-messages")
    assert llm.api_format == "openai-completions"
    assert llm.extra_body == {"reasoning_effort": "high"}

    # The record came back too: the next switch still moves it.
    monkeypatch.undo()
    llm.reconfigure("k", api_format="anthropic-messages")
    assert llm.extra_body == {"output_config": {"effort": "high"}}


def test_a_moved_level_is_not_called_a_previous_wires_key(caplog):
    llm = _client({"top_p": 0.5})
    set_thinking(llm, "high")

    with caplog.at_level("WARNING", logger=llm.logger.name):
        llm.reconfigure("k", api_format="anthropic-messages")
    stale = [r.getMessage() for r in caplog.records
             if "configured for the previous wire" in r.getMessage()]
    assert stale and "top_p" in stale[0] and "output_config" not in stale[0]


def test_a_switch_works_with_an_extra_body_that_cannot_be_copied():
    import threading

    llm = _client()
    llm.extra_body["opaque"] = threading.Lock()  # deepcopy raises on this
    llm.reconfigure("k", model="m2")
    assert llm.model == "m2"


def test_a_host_field_already_on_the_new_wire_wins():
    """The host added ``reasoning_effort`` for the Chat Completions switch it
    was about to make. That is the host's field: agentao's level gives way."""
    llm = _client(api_format="anthropic-messages")
    set_thinking(llm, "high")
    llm.extra_body["reasoning_effort"] = "low"

    llm.reconfigure("k", api_format="openai-completions")
    assert llm.extra_body == {"reasoning_effort": "low"}


def test_a_host_reasoning_effort_is_not_dropped_by_a_move():
    """On ``openai-responses`` ``reasoning_effort`` is not agentao's field, so
    a move away from it leaves it, as it leaves any host field."""
    llm = _client(api_format="openai-responses")
    set_thinking(llm, "high")
    llm.extra_body["reasoning_effort"] = "low"  # the host's, rejected here

    llm.reconfigure("k", api_format="anthropic-messages")
    assert llm.extra_body == {"reasoning_effort": "low", "output_config": {"effort": "high"}}
