"""Regression table for is_context_too_long_error provider coverage + guard.

Covers the two-tier classifier in agentao/context_manager.py: positive provider
patterns must be detected as overflow; throttling/rate-limit errors must NOT be,
even when they contain a fallback overflow phrase ("too many tokens").
"""

import pytest

from agentao.context_manager import is_context_too_long_error


# (label, error_message) — each must be classified as a context overflow.
OVERFLOW_CASES = [
    ("anthropic_tokens", "prompt is too long: 213462 tokens > 200000 maximum"),
    ("anthropic_413", '413 {"error":{"type":"request_too_large","message":"Request exceeds the maximum size"}}'),
    ("openai_window", "Your input exceeds the context window of this model"),
    ("openai_maxlen_tokens", "Requested token count exceeds the model's maximum context length of 131072 tokens"),
    ("openai_maxlen_paren", "Input length (265330) exceeds model's maximum context length (262144)."),
    ("generic_code", "Error code: context_length_exceeded"),
    ("bedrock", "input is too long for requested model"),
    ("google", "The input token count (1196265) exceeds the maximum number of tokens allowed (1048575)"),
    ("xai", "This model's maximum prompt length is 131072 but the request contains 537812 tokens"),
    ("groq", "Please reduce the length of the messages or completion"),
    ("openrouter", "This endpoint's maximum context length is 32768 tokens. However, you requested more"),
    ("poolside", "Input length 5000 exceeds the maximum allowed input length of 4096 tokens."),
    ("together", "The input (9000 tokens) is longer than the model's context length (8192 tokens)."),
    ("mistral", "Prompt contains 40000 tokens, too large for model with 32768 maximum context length"),
    ("llama_cpp", "the request exceeds the available context size, try increasing it"),
    ("lm_studio", "tokens to keep from the initial prompt is greater than the context length"),
    ("kimi", "Your request exceeded model token limit: 131072 (requested: 200000)"),
    ("ollama", "prompt too long; exceeded max context length by 1200 tokens"),
    ("dashscope_range", "Range of input length should be ..."),
    ("dashscope_code", "InternalError.Algo.InvalidParameter: ..."),
    # Broad forms the old substring matcher caught — must not regress to stricter regex.
    ("reduce_length_prompt", "Please reduce the length of the prompt and try again"),
    ("maxlen_no_digits", "Your request exceeds the maximum context length for this model"),
]

# (label, error_message) — transient/non-overflow errors that must classify False.
NON_OVERFLOW_CASES = [
    ("bedrock_throttle", "ThrottlingException: Too many tokens, please wait before trying again."),
    ("rate_limit", "429 rate limit exceeded, please retry"),
    ("too_many_requests", "Too Many Requests"),
    ("service_unavailable", "Service unavailable: please retry later"),
    ("unrelated", "Invalid API key provided"),
]


@pytest.mark.parametrize("label,msg", OVERFLOW_CASES, ids=[c[0] for c in OVERFLOW_CASES])
def test_detected_as_overflow(label, msg):
    assert is_context_too_long_error(Exception(msg)) is True


@pytest.mark.parametrize("label,msg", NON_OVERFLOW_CASES, ids=[c[0] for c in NON_OVERFLOW_CASES])
def test_not_overflow(label, msg):
    assert is_context_too_long_error(Exception(msg)) is False
