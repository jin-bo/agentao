"""The context window can be validated against the provider, and self-heal.

`max_tokens` is a **documented host-owned knob** on four surfaces, and this
does not take that ownership away: it stays exactly what the host wrote and
reads back unchanged. What is new is a second, lower ceiling learned from the
provider's own overflow errors — `effective_max_tokens = min(configured,
observed)` — which every internal budget is now denominated in.

The hard part is the parse, not the plumbing. Of the 21 detection patterns
roughly half carry no number, and most that do carry **two**: Anthropic's
"213462 tokens > 200000 maximum" has the request size *and* the limit.
Adopting the wrong one permanently shrinks the window until the next model
switch — a silent degradation with no warning, which is the exact failure
class this work exists to remove. So the parse is provider-asserted, and
**when it is not certain it adopts nothing**.

State plainly what this cannot do: an overflow error is its only input, so
**the first fall into the recovery ladder is its input, not something it can
prevent**. It reduces how often you fall in again.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentao.context_manager import (
    ContextManager,
    parse_observed_context_limit,
)


def _cm(max_tokens=200_000):
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    return ContextManager(llm, Mock(), max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------

# Every row is a real provider string from the detection regression table,
# paired with the number that is genuinely **the limit** — never the request
# size, which is the adjacent number in most of them.
PARSEABLE = [
    ("anthropic", "prompt is too long: 213462 tokens > 200000 maximum", 200_000),
    ("openai_of", "Requested token count exceeds the model's maximum context "
                  "length of 131072 tokens", 131_072),
    ("openai_paren", "Input length (265330) exceeds model's maximum context "
                     "length (262144).", 262_144),
    ("google", "The input token count (1196265) exceeds the maximum number of "
               "tokens allowed (1048575)", 1_048_575),
    ("xai", "This model's maximum prompt length is 131072 but the request "
            "contains 537812 tokens", 131_072),
    ("openrouter", "This endpoint's maximum context length is 32768 tokens. "
                   "However, you requested more", 32_768),
    ("poolside", "Input length 5000 exceeds the maximum allowed input length "
                 "of 4096 tokens.", 4_096),
    ("together", "The input (9000 tokens) is longer than the model's context "
                 "length (8192 tokens).", 8_192),
    ("mistral", "Prompt contains 40000 tokens, too large for model with 32768 "
                "maximum context length", 32_768),
    ("kimi", "Your request exceeded model token limit: 131072 (requested: 200000)",
     131_072),
]


@pytest.mark.parametrize("label,msg,expected", PARSEABLE, ids=[c[0] for c in PARSEABLE])
def test_the_limit_is_read_not_the_request_size(label, msg, expected):
    parsed = parse_observed_context_limit(Exception(msg))
    assert parsed is not None, msg
    assert parsed[0] == expected, f"{label}: picked the wrong number"


# Errors that carry no usable limit. The Ollama row is the one that proves
# the rule: 1200 is a *delta*, and a bare number scrape would adopt it.
UNPARSEABLE = [
    ("ollama_delta", "prompt too long; exceeded max context length by 1200 tokens"),
    ("generic_code", "Error code: context_length_exceeded"),
    ("groq", "Please reduce the length of the messages or completion"),
    ("lm_studio", "tokens to keep from the initial prompt is greater than the "
                  "context length"),
    ("bedrock", "input is too long for requested model"),
    ("dashscope", "Range of input length should be ..."),
    ("anthropic_413", '413 {"error":{"type":"request_too_large"}}'),
]


@pytest.mark.parametrize("label,msg", UNPARSEABLE, ids=[c[0] for c in UNPARSEABLE])
def test_an_uncertain_parse_adopts_nothing(label, msg):
    assert parse_observed_context_limit(Exception(msg)) is None


def test_two_patterns_disagreeing_adopts_nothing():
    """The strongest guard, and the one a number scrape gets wrong silently."""
    msg = ("maximum context length is 32768 tokens; "
           "this model's maximum prompt length is 131072")
    assert parse_observed_context_limit(Exception(msg)) is None


@pytest.mark.parametrize("value", ["12", "999999999999"])
def test_a_value_outside_the_sanity_bounds_adopts_nothing(value):
    msg = f"maximum context length is {value} tokens"
    assert parse_observed_context_limit(Exception(msg)) is None


def test_thousands_separators_are_tolerated():
    parsed = parse_observed_context_limit(
        Exception("maximum context length is 131,072 tokens")
    )
    assert parsed is not None and parsed[0] == 131_072


# ---------------------------------------------------------------------------
# Ownership: configured is the host's, effective is derived
# ---------------------------------------------------------------------------

def test_the_configured_window_is_never_overwritten():
    cm = _cm(200_000)
    cm.observe_overflow_error(Exception("maximum context length is 32768 tokens"))

    assert cm.max_tokens == 200_000, "the host's knob reads back what the host wrote"
    assert cm.observed_limit == 32_768
    assert cm.effective_max_tokens == 32_768


def test_an_observed_limit_can_only_narrow():
    """A provider rejecting at N is evidence about N, not permission to
    exceed the host's own ceiling."""
    cm = _cm(32_768)
    cm.observe_overflow_error(Exception("maximum context length is 200000 tokens"))

    assert cm.observed_limit == 200_000
    assert cm.effective_max_tokens == 32_768


def test_internal_budgets_use_the_effective_window():
    cm = _cm(200_000)
    msgs = [{"role": "user", "content": "x" * 400_000}]  # ~100k tokens
    assert cm.needs_compression(msgs) is False

    cm.observe_overflow_error(Exception("maximum context length is 32768 tokens"))

    assert cm.needs_compression(msgs) is True
    assert cm._summary_input_budget() < 200_000 * cm._SUMMARY_INPUT_BUDGET_RATIO


def test_usage_percent_uses_the_effective_window():
    """Or /context reports 70% while the API is already rejecting."""
    cm = _cm(200_000)
    msgs = [{"role": "user", "content": "x" * 40_000}]
    before = cm.get_usage_stats(msgs)["usage_percent"]

    cm.observe_overflow_error(Exception("maximum context length is 32768 tokens"))
    after = cm.get_usage_stats(msgs)["usage_percent"]

    assert after > before


def test_usage_stats_keeps_max_tokens_meaning_configured():
    cm = _cm(200_000)
    cm.observe_overflow_error(Exception("maximum context length is 32768 tokens"))
    stats = cm.get_usage_stats([{"role": "user", "content": "hi"}])

    # The old key does not change meaning — old hosts are unaffected.
    assert stats["max_tokens"] == 200_000
    assert stats["effective_max_tokens"] == 32_768
    assert stats["observed_limit"] == 32_768
    assert stats["observed_limit_provenance"] == "maximum_context_length_is"


# ---------------------------------------------------------------------------
# Clear on switch
# ---------------------------------------------------------------------------

def test_a_model_switch_discards_the_observed_limit(tmp_path):
    """It describes the model that rejected the request. The next model's
    window is simply unverified — and it is never silently overwritten."""
    from agentao.runtime.model import set_model
    from tests.support.stop_precompact import make_bare_agent

    agent = make_bare_agent(tmp_path)
    cm = agent.context_manager
    cm.observe_overflow_error(Exception("maximum context length is 32768 tokens"))
    assert cm.effective_max_tokens == 32_768

    set_model(agent, "another-model")

    assert cm.observed_limit is None
    assert cm.effective_max_tokens == cm.max_tokens


def test_a_credential_rotation_leaves_it_alone(tmp_path):
    """Same model, same endpoint — nothing about the window changed."""
    from agentao.runtime.model import set_provider
    from tests.support.stop_precompact import make_bare_agent

    agent = make_bare_agent(tmp_path)
    cm = agent.context_manager
    cm.observe_overflow_error(Exception("maximum context length is 32768 tokens"))

    set_provider(agent, api_key="rotated")

    assert cm.observed_limit == 32_768


def test_an_endpoint_switch_discards_it(tmp_path):
    """The same model name behind a different deployment is a different
    deployment, and deployments differ in what they accept."""
    from agentao.runtime.model import set_provider
    from tests.support.stop_precompact import make_bare_agent

    agent = make_bare_agent(tmp_path)
    cm = agent.context_manager
    cm.observe_overflow_error(Exception("maximum context length is 32768 tokens"))

    set_provider(agent, api_key="k", base_url="https://other.local/v1")

    assert cm.observed_limit is None


def test_the_ladder_records_what_the_provider_said(tmp_path, monkeypatch):
    """The observation point is the overflow branch — its only input."""
    from agentao.cancellation import CancellationToken
    from agentao.compaction.types import CompactionOutcome
    from agentao.runtime.chat_loop import ChatLoopRunner
    from tests.support.stop_precompact import make_bare_agent

    agent = make_bare_agent(tmp_path)
    agent.messages = [{"role": "user", "content": f"m{i}"} for i in range(6)]
    agent._last_session_summary_id = None
    monkeypatch.setattr(agent, "_build_system_prompt", lambda: "")
    monkeypatch.setattr(agent, "_emit_session_summary_if_new", lambda *_a, **_k: None)
    monkeypatch.setattr(
        agent.context_manager, "_run_compaction",
        lambda msgs, **kw: CompactionOutcome(
            status="failed", trigger="auto", kind="full",
            reason="api_overflow", messages=msgs, detail="summary_empty",
        ),
    )

    def _overflow(*_a, **_k):
        raise RuntimeError("prompt is too long: 213462 tokens > 200000 maximum")

    monkeypatch.setattr(agent, "_llm_call", _overflow)
    ChatLoopRunner(agent)._call_llm_with_overflow_recovery(
        [{"role": "system", "content": ""}] + agent.messages, "", [], CancellationToken(),
    )

    assert agent.context_manager.observed_limit == 200_000
    assert agent.context_manager.observed_limit_provenance == "anthropic:tokens>maximum"
