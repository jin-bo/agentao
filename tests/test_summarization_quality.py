"""PR-6: what the summarizer is fed, and how much of the tail survives.

Three separate items, in the plan's risk order:

1. A **token budget for the kept-verbatim tail** — off by default, because
   the right value has to come from measurement and nothing here has measured
   it. It is aimed at "still heavy *after* compaction", not at the summary
   input: the tail never reaches the summarizer, it is spliced verbatim into
   the result, so a heavy tail re-crosses the threshold immediately and the
   next iteration compacts again.
2. The **carried summary out of the eviction pool**. It used to be a block
   inside the newest-first allocator, where it is by construction the oldest
   block — three local patches existed to stop it being evicted first. Out of
   band, that guarantee is structural rather than patched.
3. **P3 partial mitigations** — a reserve for the originating request, and an
   injectable charge for images. Both are explicitly partial; see below.
"""

from __future__ import annotations

from unittest.mock import Mock

from agentao.context_manager import ContextManager


def _cm(max_tokens=200_000):
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    return ContextManager(llm, Mock(), max_tokens=max_tokens)


def _carry(text="everything that happened before"):
    return {
        "role": "system",
        "content": (
            f"[Conversation Summary]\n{text}\n"
            f"{ContextManager.SUMMARY_END_MARKER}\n(framing note)"
        ),
    }


# ---------------------------------------------------------------------------
# 1. The token-budgeted recency window
# ---------------------------------------------------------------------------

def test_the_tail_budget_is_off_by_default():
    cm = _cm()
    msgs = [{"role": "user", "content": "x" * 40_000} for _ in range(30)]
    assert cm._apply_keep_token_budget(msgs, count_start=10) == 10


def test_a_heavy_tail_keeps_fewer_messages_when_the_budget_is_on():
    cm = _cm(200_000)
    cm.keep_recent_token_ratio = 0.02  # 4_000 tokens
    msgs = [{"role": "user", "content": "x" * 40_000} for _ in range(30)]  # ~10k tok each

    start = cm._apply_keep_token_budget(msgs, count_start=10)

    assert start > 10, "a heavy tail must tighten the search start"


def test_a_light_tail_is_unaffected():
    """The budget can only *tighten* — it never keeps more than the counts do."""
    cm = _cm(200_000)
    cm.keep_recent_token_ratio = 0.5
    msgs = [{"role": "user", "content": "short"} for _ in range(30)]

    assert cm._apply_keep_token_budget(msgs, count_start=10) == 10


def test_the_combination_is_max_not_min():
    """Taking the earlier start would simply violate the budget on a heavy
    tail, which is the thing this exists to fix."""
    cm = _cm(200_000)
    cm.keep_recent_token_ratio = 0.001  # 200 tokens — almost nothing fits
    msgs = [{"role": "user", "content": "x" * 4_000} for _ in range(30)]

    start = cm._apply_keep_token_budget(msgs, count_start=10)

    assert start >= 10
    assert len(msgs) - start < 4, "fewer than 4 kept is the accepted consequence"


def test_dropping_below_four_kept_is_logged():
    cm = _cm(200_000)
    cm.keep_recent_token_ratio = 0.001
    msgs = [{"role": "user", "content": "x" * 4_000} for _ in range(30)]

    cm._apply_keep_token_budget(msgs, count_start=10)

    logged = " ".join(str(c) for c in cm.llm_client.logger.info.call_args_list)
    assert "below the nominal 4" in logged


# ---------------------------------------------------------------------------
# 2. The carried summary is out of the eviction pool
# ---------------------------------------------------------------------------

def test_the_carried_summary_is_rendered_out_of_band():
    cm = _cm()
    out = cm._format_for_summary([_carry("PRIOR-HISTORY"), {"role": "user", "content": "next"}])

    assert out.startswith("<previous-summary>")
    assert "PRIOR-HISTORY" in out.split("</previous-summary>")[0]
    # The end marker and framing note are stripped on rehydration, so they do
    # not accumulate when an old summary is re-summarized.
    assert ContextManager.SUMMARY_END_MARKER not in out


def test_the_carried_summary_survives_a_tail_that_would_have_evicted_it():
    """The defect the three deleted patches existed to prevent.

    A rehydrated summary is by construction the *oldest* block in the window,
    so plain newest-first spending drops it first — and every compaction after
    the first would amputate the entire accumulated history.
    """
    cm = _cm(20_000)
    msgs = [_carry("PRIOR-HISTORY")] + [
        {"role": "user", "content": f"MARK{i} " + "x" * 8_000} for i in range(40)
    ]

    out = cm._format_for_summary(msgs)

    assert "PRIOR-HISTORY" in out
    assert "earlier message(s) omitted" in out, "the live half really was clipped"


def test_the_two_budgets_cannot_exceed_the_whole():
    """The replacement ceiling is mandatory, not optional.

    Both texts go into one provider request, so "its own budget" only changes
    the bookkeeping — the provider-level competition is unchanged.
    """
    for max_tokens in (8_000, 20_000, 200_000):
        cm = _cm(max_tokens)
        total = cm._summary_input_budget()
        msgs = [_carry("P" * 200_000)] + [
            {"role": "user", "content": f"m{i} " + "y" * 20_000} for i in range(30)
        ]

        out = cm._format_for_summary(msgs)
        carry = out.split("</previous-summary>")[0]

        assert cm.count_tokens_in_text(carry) <= total // 2 + 50, max_tokens
        assert cm.count_tokens_in_text(out) <= total + 200, max_tokens


def test_the_newest_carried_summary_wins():
    cm = _cm()
    out = cm._format_for_summary([
        _carry("OLDER"), _carry("NEWER"), {"role": "user", "content": "live"},
    ])
    assert "NEWER" in out
    assert "OLDER" not in out.split("</previous-summary>")[0]


def test_the_prompt_asks_for_an_update_not_a_fresh_summary():
    assert "<previous-summary>" in ContextManager._SUMMARIZE_SYSTEM_PROMPT
    assert "UPDATED" in ContextManager._SUMMARIZE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 3a. The originating request — a partial mitigation
# ---------------------------------------------------------------------------

def test_the_originating_request_is_restated_when_it_would_be_evicted():
    cm = _cm(8_000)
    msgs = [{"role": "user", "content": "THE-ORIGINAL-ASK build the parser"}] + [
        {"role": "tool", "name": "read_file", "content": f"r{i} " + "z" * 20_000}
        for i in range(20)
    ]

    out = cm._format_for_summary(msgs)

    assert "<originating-request>" in out
    assert "THE-ORIGINAL-ASK" in out.split("</originating-request>")[0]


def test_no_restatement_when_it_survived_on_its_own():
    """The common case must be byte-identical to not having the feature."""
    cm = _cm(200_000)
    msgs = [
        {"role": "user", "content": "small ask"},
        {"role": "assistant", "content": "sure"},
    ]

    out = cm._format_for_summary(msgs)

    assert "<originating-request>" not in out


def test_the_restatement_stays_inside_the_budget():
    cm = _cm(8_000)
    total = cm._summary_input_budget()
    msgs = [{"role": "user", "content": "ASK " + "q" * 100_000}] + [
        {"role": "tool", "name": "read_file", "content": f"r{i} " + "z" * 20_000}
        for i in range(20)
    ]

    out = cm._format_for_summary(msgs)

    assert cm.count_tokens_in_text(out) <= total + 200


# ---------------------------------------------------------------------------
# 3b. Images — injectable, and zero by default
# ---------------------------------------------------------------------------

def test_images_are_charged_zero_by_default():
    """Unchanged, and stated: an image-bearing history under-estimates."""
    cm = _cm()
    msg = {"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}
    assert cm._count_message_tokens(msg) == cm.count_tokens_in_text("look")


def test_an_injected_estimator_charges_images():
    cm = _cm()
    seen = []

    def _charge(block):
        seen.append(block)
        return 1_200

    cm.image_token_estimator = _charge
    msg = {"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}

    assert cm._count_message_tokens(msg) == cm.count_tokens_in_text("look") + 1_200
    assert seen and seen[0]["type"] == "image_url"


def test_a_raising_estimator_does_not_break_the_estimate():
    cm = _cm()
    cm.image_token_estimator = lambda _b: (_ for _ in ()).throw(ValueError("boom"))
    msg = {"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "x"}},
    ]}
    assert cm._count_message_tokens(msg) == cm.count_tokens_in_text("look")


# ---------------------------------------------------------------------------
# The tail budget has a structural floor, and the transcript costs lazily
# ---------------------------------------------------------------------------

def test_the_tail_budget_never_starts_past_the_last_message():
    """The floor of 1 is enforced here — it is not inherited.

    When even the newest message alone busts the budget the backwards scan
    stops at ``len(messages)``. ``_find_split_index`` takes that as a search
    *start* and scans forward, so the range is empty, it returns ``None``, and
    that is a counted ``no_safe_split`` failure — three of them open the
    circuit breaker and disable automatic compaction for the rest of the
    session, on exactly the oversized-tail history this knob exists to shrink.
    """
    cm = _cm(10_000)
    cm.keep_recent_token_ratio = 0.01  # 100 tokens — nothing fits
    msgs = [{"role": "user", "content": "x" * 20_000} for _ in range(8)]

    start = cm._apply_keep_token_budget(msgs, count_start=6)

    assert start <= len(msgs) - 1


def test_an_oversized_tail_does_not_open_the_breaker():
    """End to end: the knob's own worst case still compacts."""
    cm = _cm(10_000)
    cm.keep_recent_token_ratio = 0.01
    cm._summarize_formatted = lambda _f: "a summary"
    msgs = [{"role": "user", "content": "x" * 20_000} for _ in range(8)]

    for _ in range(cm.CIRCUIT_BREAKER_LIMIT):
        outcome = cm._run_compaction(msgs, is_auto=True, reason="compression_threshold")
        assert outcome.status == "success", outcome.detail

    assert cm.compaction_circuit_open is False


def test_the_transcript_costs_only_the_blocks_it_looks_at():
    """The cost memo is filled on demand, not precomputed.

    ``_join_within_budget`` spends newest-first and stops at the first block
    that does not fit, so on a long history it never looks at most of the
    list. Costing every block up front — to save the second allocation that
    only runs when the originating request did not survive — would encode
    hundreds of blocks the first pass alone would have skipped, and
    ``_block_cost`` is a full tiktoken encode of a whole rendered message.
    """
    cm = _cm(20_000)
    msgs = [{"role": "user", "content": f"m{i} " + "x" * 8_000} for i in range(200)]
    calls = []
    real = cm._block_cost
    cm._block_cost = lambda block: (calls.append(1), real(block))[1]

    cm._format_for_summary(msgs)

    assert len(calls) < len(msgs) // 2, (
        f"costed {len(calls)} of {len(msgs)} blocks — the memo is being "
        "precomputed, not filled on demand"
    )
