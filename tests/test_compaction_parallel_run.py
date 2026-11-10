"""A run of parallel tool results, and a cut with nothing to shed (#625, #623).

The chat-loop tests run the real loop and the real ``read_file``. Only the model
is fake: it refuses a request over its window, as a provider does.
"""

from __future__ import annotations

import json
import os
import random
import string
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentao.compaction.request import is_kept_request, turn_reminder
from agentao.context_manager import ContextManager, PreparedCompaction
from agentao.runtime.tool_result_formatter import save_clipped_result

REMINDER = turn_reminder(datetime(2026, 11, 9, 2, 40, 4))
# Under the formatter's spill threshold once ``read_file`` numbers the lines, so
# a result is whole in history and a clip is the only thing that shortens it.
RESULT_CHARS = 32_000
WINDOW = 120_000


def _cm(max_tokens=200_000):
    llm = Mock()
    llm.logger = Mock()
    llm.model = "test-model"
    cm = ContextManager(llm, Mock(), max_tokens=max_tokens)
    cm._summarize_formatted = lambda formatted, **kwargs: "SUMMARY"
    return cm


def _batch(parallel, tag="p", content="x"):
    calls = [
        {"id": f"{tag}{i}", "type": "function",
         "function": {"name": "read_file", "arguments": "{}"}}
        for i in range(parallel)
    ]
    return [{"role": "assistant", "content": "", "tool_calls": calls}] + [
        {"role": "tool", "tool_call_id": f"{tag}{i}", "name": "read_file", "content": content}
        for i in range(parallel)
    ]


def _older_work():
    """Two finished exchanges, large enough that summarizing them saves something."""
    return _batch(1, "a", content="y" * 2_000) + _batch(1, "b", content="y" * 2_000)


def _request(text="do the batch"):
    return {"role": "user", "content": REMINDER + text}


def _prepare(cm, messages, reason="compression_threshold"):
    return cm.prepare_compaction(messages, trigger="auto", kind="full", reason=reason)


def _pairs_hold(messages):
    declared = {c["id"] for m in messages for c in m.get("tool_calls") or ()}
    answered = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
    return declared == answered


# ---------------------------------------------------------------------------
# The walk-back (#625)
# ---------------------------------------------------------------------------


def test_a_run_with_no_cut_ahead_walks_back_to_its_call():
    history = [_request()] + _older_work() + _batch(8)

    prep = _prepare(_cm(), history)

    assert isinstance(prep, PreparedCompaction)
    assert prep.split_index == 5
    assert prep.to_keep[0] is history[5]
    assert _pairs_hold(prep.to_keep)


@pytest.mark.parametrize(
    "history",
    [_batch(8), [_request()] + _batch(8)],
    ids=["call-first", "call-second"],
)
def test_a_run_after_only_the_request_stands_down(history):
    """A first request that fans out: nothing comes before the run but the request.

    Its copy would go straight back, so there is nothing to shed, and the next
    reply gives a cut. Not a counted failure: three iterations of it must not
    open the breaker.
    """
    cm = _cm()

    for _ in range(ContextManager.CIRCUIT_BREAKER_LIMIT):
        outcome = cm._run_compaction(history, is_auto=True, reason="compression_threshold")
        assert outcome.status == "skipped"
        assert outcome.detail == "nothing_to_shed"

    assert cm.circuit_breaker_failures == 0
    assert cm.compaction_circuit_open is False


@pytest.mark.parametrize(
    "history",
    [
        [{"role": "user", "content": "plain"}] + _batch(8),
        [{"role": "user", "content": "go"}]
        + [{"role": "tool", "name": "read_file", "content": "r"} for _ in range(10)],
        [{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"}, _request("a" * 500)]
        + [{"role": "tool", "name": "read_file", "content": "r"} for _ in range(10)],
        [{"role": "user", "content": "go"}, _request("a" * 500), {"role": "assistant", "content": "ok"}]
        + [{"role": "tool", "name": "read_file", "content": "r"} for _ in range(10)],
    ],
    ids=[
        "plain-user-second", "no-call-at-all",
        "orphans-after-a-user", "orphans-after-an-answer",
    ],
)
def test_a_run_with_nothing_before_it_is_still_no_safe_split(history):
    cm = _cm()

    outcome = cm._run_compaction(history, is_auto=True, reason="compression_threshold")

    assert outcome.status == "failed"
    assert outcome.detail == "no_safe_split"
    assert cm.circuit_breaker_failures == 1


def test_consecutive_large_batches_do_not_open_the_breaker():
    cm = _cm()
    history = [{"role": "user", "content": "task"}]
    for b in range(4):
        history = history + _batch(20, f"b{b}")
        cm._run_compaction(history, is_auto=True, reason="compression_threshold")

    assert cm.compaction_circuit_open is False
    assert cm.circuit_breaker_failures == 0


def test_a_keep_budget_gives_way_to_the_run():
    cm = _cm(10_000)
    cm.keep_recent_token_ratio = 0.01
    history = [_request()] + _older_work() + _batch(8, content="y" * 2_000)

    prep = _prepare(cm, history)

    assert prep.split_index == 5
    assert _pairs_hold(prep.to_keep)


# ---------------------------------------------------------------------------
# Results the model has not read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["compression_threshold", "manual_cli"])
def test_the_threshold_path_does_not_clip_unread_results(reason):
    big = "y" * 20_000
    history = [_request()] + _older_work() + _batch(16, content=big)

    prep = _prepare(_cm(), history, reason=reason)

    results = [m["content"] for m in prep.to_keep if m["role"] == "tool"]
    assert results == [big] * 16
    assert prep.clip_on_commit is False


def test_a_forward_cut_does_not_clip_unread_results_either():
    big = "y" * 20_000
    history = (
        [_request("old")] + [{"role": "assistant", "content": "a" * 2_000}] * 12
        + [_request()] + _batch(7, content=big)
    )

    prep = _prepare(_cm(), history)

    assert prep.split_index < len(history) - 8
    results = [m["content"] for m in prep.to_keep if m["role"] == "tool"]
    assert results[-7:] == [big] * 7


def test_a_user_message_after_unread_results_does_not_expose_them():
    """A ``new_instruction`` at the iteration cap: no model reply followed the run."""
    big = "y" * 20_000
    history = (
        [_request("old")] + _older_work() * 2
        + [_request("mid")] + _batch(8, content=big) + [_request("new")]
    )

    prep = _prepare(_cm(), history)

    results = [m["content"] for m in prep.to_keep if m["role"] == "tool"]
    assert results == [big] * 8


def test_results_the_model_has_read_are_still_clipped():
    big = "y" * 20_000
    read = []
    for i in range(8):
        read += _batch(1, f"r{i}", content=big)
    history = [_request()] + [{"role": "assistant", "content": "ok"}] * 8 + read + _batch(1, "n")

    prep = _prepare(_cm(), history)

    assert any(
        m["role"] == "tool" and len(m["content"]) <= ContextManager.MICROCOMPACT_TOOL_LIMIT
        and m["content"] != "x"
        for m in prep.to_keep
    )


def test_an_overflow_clips_only_in_commit():
    cm = _cm()
    saved = []
    cm.clipped_result_saver = lambda name, content: saved.append(name) or "SAVED-NOTE"
    big = "y" * 20_000
    history = [_request()] + _older_work() + _batch(16, content=big)

    prep = _prepare(cm, history, reason="api_overflow")

    assert prep.clip_on_commit is True
    assert saved == [], "a cancelled compaction must leave no file behind"
    assert prep.to_keep[1]["content"] == big

    result = cm.commit_compaction(prep, "SUMMARY")

    assert saved == ["read_file"] * 11
    clipped = [m["content"] for m in result if m.get("role") == "tool" and "SAVED-NOTE" in m["content"]]
    assert len(clipped) == 11
    assert all(len(c) <= ContextManager.MICROCOMPACT_TOOL_LIMIT for c in clipped)


@pytest.mark.parametrize(
    "saver",
    [None, "not callable", lambda n, c: None, lambda n, c: 7, lambda n, c: 1 / 0],
    ids=["none", "not-callable", "no-copy", "wrong-type", "raises"],
)
def test_a_clip_with_no_saved_copy_names_none(saver):
    cm = _cm()
    cm.clipped_result_saver = saver
    history = _batch(6, content="y" * 20_000)

    clipped = cm.microcompact_messages(history, overflow=True)[1]["content"]

    assert "chars omitted by microcompact …]" in clipped
    assert "saved" not in clipped


def test_a_note_does_not_break_the_carried_omission_count():
    cm = _cm()
    cm.clipped_result_saver = lambda n, c: save_note
    save_note = "full result saved to /tmp/a.txt, use read_file to access it"
    history = _batch(6, content="y" * 20_000)

    clipped = cm.microcompact_messages(history, overflow=True)[1]["content"]

    assert ContextManager._prior_omissions(clipped) > 0
    assert len(clipped) <= ContextManager.MICROCOMPACT_TOOL_LIMIT


def test_a_bracket_in_the_saved_path_does_not_break_the_carried_count(tmp_path):
    workdir = tmp_path / "project[1]"
    cm = _cm()
    cm.clipped_result_saver = lambda n, c: save_clipped_result(n, c, workdir)
    history = _batch(6, content="y" * 20_000)

    clipped = cm.microcompact_messages(history, overflow=True)[1]["content"]

    assert "project[1]" in clipped
    assert ContextManager._prior_omissions(clipped) > 0


def _band_history():
    return [_request()] + _batch(8, content="y" * 20_000)


def test_the_microcompaction_tier_leaves_unread_results_alone():
    cm = _cm()
    history = _band_history()

    assert cm.microcompact_would_mutate(history) is False
    assert cm.prepare_microcompact(history).tool_results_to_clip == 0
    assert cm.microcompact_messages(history) == history
    assert cm.last_microcompact_mutated is False


def test_an_overflow_counts_and_clips_the_same_unread_results():
    cm = _cm()
    history = _band_history()

    assert cm.microcompact_would_mutate(history, overflow=True) is True
    assert cm.prepare_microcompact(history, overflow=True).tool_results_to_clip == 3
    out = cm.microcompact_messages(history, overflow=True)
    assert [len(m["content"]) < 20_000 for m in out if m["role"] == "tool"] == [True] * 3 + [False] * 5


def test_a_model_reply_makes_results_old_enough_to_clip():
    cm = _cm()
    history = _band_history() + [{"role": "assistant", "content": "read them"}]

    assert cm.prepare_microcompact(history).tool_results_to_clip == 3


def test_a_user_message_after_the_results_does_not_mark_them_read():
    cm = _cm()
    history = _band_history() + [_request("a new instruction")]

    assert cm.prepare_microcompact(history).tool_results_to_clip == 0


# ---------------------------------------------------------------------------
# The saved copy
# ---------------------------------------------------------------------------


def test_a_clipped_result_is_saved_whole(tmp_path):
    note = save_clipped_result("read_file", "the whole text", tmp_path)

    path = Path(note.split("saved to ")[1].split(",")[0])
    assert path.parent == tmp_path / ".agentao" / "tool-outputs"
    assert path.read_text() == "the whole text"
    assert "]" not in note and "[" not in note


def test_a_saved_copy_is_redacted_and_says_so(tmp_path):
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    note = save_clipped_result("read_file", f"key {secret}", tmp_path)

    path = Path(note.split("saved to ")[1].split(",")[0])
    assert secret not in path.read_text()
    assert "masked" in note
    assert "]" not in note and "[" not in note


def test_mcp_skill_content_is_not_saved(tmp_path):
    assert save_clipped_result("read_skill_file", "skill text", tmp_path) is None
    assert not (tmp_path / ".agentao").exists()


def test_a_result_the_formatter_spilled_is_not_saved_again(tmp_path):
    from agentao.runtime.tool_result_formatter import _save_and_truncate

    excerpt, disk_path = _save_and_truncate(
        "z" * 50_000, "read_file", output_dir=tmp_path / "first",
    )
    assert disk_path

    assert save_clipped_result("read_file", excerpt, tmp_path) is None
    clipped = ContextManager._head_tail_clip(
        excerpt, ContextManager.MICROCOMPACT_TOOL_LIMIT, note="omitted by microcompact",
    )
    assert disk_path in clipped, "the head must keep the path the excerpt names"


def test_a_result_that_only_starts_like_an_excerpt_is_saved(tmp_path):
    note = save_clipped_result("read_file", "[Output truncated: see below] text", tmp_path)

    assert note is not None


def test_saving_a_clip_prunes_old_copies(tmp_path):
    out_dir = tmp_path / ".agentao" / "tool-outputs"
    out_dir.mkdir(parents=True)
    old = out_dir / "read_file_1_abc.txt"
    old.write_text("old")
    os.utime(old, (1, 1))

    assert save_clipped_result("read_file", "text", tmp_path) is not None

    assert not old.exists()


def test_a_failed_write_names_no_file(tmp_path):
    blocker = tmp_path / ".agentao"
    blocker.write_text("a file where the directory should be")

    assert save_clipped_result("read_file", "text", tmp_path) is None


# ---------------------------------------------------------------------------
# A cut with nothing to shed (#623)
# ---------------------------------------------------------------------------


def _summarizer_never_called(cm):
    def fail(formatted, **kwargs):
        raise AssertionError("the summarizer was called")

    cm._summarize_formatted = fail


def test_a_cut_of_only_the_request_stands_down():
    cm = _cm()
    _summarizer_never_called(cm)
    history = [_request()] + _batch(1, "a") + _batch(1, "b")

    outcome = cm._run_compaction(history, is_auto=True, reason="compression_threshold")

    assert outcome.status == "skipped"
    assert outcome.detail == "nothing_to_shed"
    assert outcome.messages is history
    assert cm.circuit_breaker_failures == 0


def test_a_cut_of_only_scaffolding_and_the_copy_stands_down():
    cm = _cm()
    first = [_request()] + _older_work() + _batch(8)
    compacted = cm._run_compaction(first, is_auto=True, reason="compression_threshold").messages
    assert any(is_kept_request(m) for m in compacted)
    _summarizer_never_called(cm)

    outcome = cm._run_compaction(compacted, is_auto=True, reason="compression_threshold")

    assert outcome.status == "skipped"
    assert outcome.detail == "nothing_to_shed"


def test_a_cut_of_only_scaffolding_stands_down_without_a_copy():
    cm = _cm()
    _summarizer_never_called(cm)
    history = [
        {"role": "system", "content": "[Compact Boundary | tokens_before=1]"},
        {"role": "system", "content": "[Conversation Summary]\nolder"},
        _request(),
    ] + _batch(1, "a") + _batch(1, "b")

    outcome = cm._run_compaction(history, is_auto=True, reason="compression_threshold")

    assert outcome.detail == "nothing_to_shed"


def test_a_request_large_enough_to_clip_still_compacts():
    cm = _cm(10_000)
    history = [_request("word " * 20_000)] + _batch(1, "a") + _batch(1, "b")

    outcome = cm._run_compaction(history, is_auto=True, reason="compression_threshold")

    assert outcome.status == "success"
    assert cm.estimate_tokens(outcome.messages) < cm.estimate_tokens(history)


def test_a_cut_that_sheds_real_messages_still_compacts():
    cm = _cm()
    history = [_request()] + _older_work() + _batch(1, "c")

    outcome = cm._run_compaction(history, is_auto=True, reason="compression_threshold")

    assert outcome.status == "success"


def test_manual_compact_explains_a_cut_with_nothing_to_shed():
    from agentao.cli.commands.compact import _FAILURE_HINTS

    assert "nothing_to_shed" in _FAILURE_HINTS


# ---------------------------------------------------------------------------
# The real chat loop
# ---------------------------------------------------------------------------


def _response(content="", calls=None, prompt_tokens=10):
    message = SimpleNamespace(content=content, tool_calls=calls, reasoning_content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=1, total_tokens=prompt_tokens + 1,
        ),
        model="test-model",
    )


def _call(i):
    function = SimpleNamespace(
        name="read_file", arguments=json.dumps({"file_path": f"f{i}.txt"}),
    )
    return SimpleNamespace(id=f"c{i}", type="function", function=function)


class _Turn:
    """A turn whose model refuses any request over ``hard_limit`` tokens."""

    def __init__(self, script, hard_limit, compaction_controller=None):
        from agentao import Agentao

        kwargs = {"compaction_controller": compaction_controller} if compaction_controller else {}
        self.agent = Agentao(
            api_key="k", base_url="https://example.test/v1", model="test-model",
            working_directory=Path.cwd(), **kwargs,
        )
        self.cm = self.agent.context_manager
        self.cm.max_tokens = WINDOW
        self.cm._summarize_formatted = lambda formatted, **k: "SUMMARY"
        self.hard_limit = hard_limit
        self.script = iter(script)
        self.requests = []
        self.refused = 0
        self.agent._llm_call = self._llm

    def _llm(self, messages, tools, token):
        est = self.cm.estimate_tokens(messages)
        if self.hard_limit is not None and est > self.hard_limit:
            self.refused += 1
            raise Exception(
                f"This model's maximum context length is {self.hard_limit} tokens. "
                f"However, your messages resulted in {est} tokens."
            )
        self.requests.append(list(messages))
        return next(self.script)

    def tool_contents(self, request):
        return [m["content"] for m in request if m.get("role") == "tool"]


def _files(count, chars=RESULT_CHARS):
    """Random words: the estimate counts repeated text as far fewer tokens."""
    rnd = random.Random(1)
    for i in range(count):
        lines = [f"file-{i}"]
        while sum(len(line) + 1 for line in lines) < chars:
            lines.append(" ".join("".join(rnd.choices(string.ascii_lowercase, k=6)) for _ in range(12)))
        Path(f"f{i}.txt").write_text("\n".join(lines))


def _batch_turn(parallel):
    return [
        _response(calls=[_call(0)]),
        _response(calls=[_call(1)]),
        _response(calls=[_call(i + 2) for i in range(parallel)]),
        _response("done"),
    ]


def test_a_real_window_at_the_limit_recovers_by_clipping(isolated_cwd):
    _files(18)
    turn = _Turn(_batch_turn(16), hard_limit=WINDOW)

    assert turn.agent.chat("do the batch") == "done"

    assert turn.refused == 1
    assert turn.cm.circuit_breaker_failures == 0
    last = turn.requests[-1]
    assert _pairs_hold(last)
    assert any(is_kept_request(m) for m in last)
    assert any("[Conversation Summary]" in str(m.get("content")) for m in last)
    clipped = [c for c in turn.tool_contents(last) if "full result saved to" in c]
    assert len(clipped) == 11
    for content in clipped:
        path = Path(content.split("saved to ")[1].split(",")[0])
        saved = path.read_text()
        assert len(saved) >= RESULT_CHARS, "the copy holds the whole result"
        assert content[:400] in saved
        assert saved.endswith(content[-1_000:])
    turn.agent.close()


def test_a_real_window_above_the_limit_gets_every_result_whole(isolated_cwd):
    _files(18)
    turn = _Turn(_batch_turn(16), hard_limit=None)

    assert turn.agent.chat("do the batch") == "done"

    assert turn.refused == 0
    results = turn.tool_contents(turn.requests[-1])
    assert len(results) == 16
    assert all("omitted by microcompact" not in c for c in results)
    assert any("[Conversation Summary]" in str(m.get("content")) for m in turn.requests[-1])
    assert turn.cm.circuit_breaker_failures == 0
    assert not list(Path(".").glob(".agentao/tool-outputs/*.txt"))
    turn.agent.close()


def test_a_batch_in_the_microcompaction_band_reaches_the_model_whole(isolated_cwd):
    """At 55-80% of the window, the next request carries every result whole."""
    _files(18)
    script = [
        _response(calls=[_call(i) for i in range(16)]),
        # What the provider counted for the request that held the batch.
        _response(calls=[_call(16)], prompt_tokens=140_000),
        _response(calls=[_call(17)], prompt_tokens=140_000),
        _response("done"),
    ]
    turn = _Turn(script, hard_limit=None)
    turn.cm.max_tokens = 200_000

    assert turn.agent.chat("do the batch") == "done"

    after_batch = turn.requests[1]
    usage = turn.cm.estimate_tokens(after_batch) / 200_000
    assert 0.55 < usage < 0.80, "the batch must land in the band, or this proves nothing"
    assert all("omitted by microcompact" not in c for c in turn.tool_contents(after_batch))

    # The model has read them now, so the next pass can take the older ones.
    assert any("omitted by microcompact" in c for c in turn.tool_contents(turn.requests[2]))
    turn.agent.close()


def test_a_first_request_that_fans_out_recovers_by_clipping(isolated_cwd):
    """No cut exists at all: ``[request, assistant, results]``."""
    _files(16)
    script = [_response(calls=[_call(i) for i in range(16)]), _response("done")]
    turn = _Turn(script, hard_limit=WINDOW)

    assert turn.agent.chat("do the batch") == "done"

    last = turn.requests[-1]
    assert last[1]["role"] == "user" and "do the batch" in last[1]["content"]
    assert not any("[Conversation Summary]" in str(m.get("content")) for m in last)
    assert len([c for c in turn.tool_contents(last) if "full result saved to" in c]) == 11
    turn.agent.close()


def test_a_host_can_refuse_the_overflow_clip(isolated_cwd):
    from agentao.compaction.types import CompactionDecision

    asked = []

    def controller(ctx):
        asked.append((ctx.kind, ctx.reason))
        if ctx.kind == "microcompact":
            return CompactionDecision(action="cancel", reason="host says no")
        return CompactionDecision(action="allow")

    _files(16)
    script = [_response(calls=[_call(i) for i in range(16)])]
    turn = _Turn(script, hard_limit=WINDOW, compaction_controller=controller)

    out = turn.agent.chat("do the batch")

    assert "maximum context length" in out
    assert ("microcompact", "api_overflow") in asked
    assert not list(Path(".").glob(".agentao/tool-outputs/*.txt"))
    turn.agent.close()


def test_a_host_cancel_of_the_full_compaction_runs_no_clip(isolated_cwd):
    from agentao.compaction.types import CompactionDecision

    asked = []

    def controller(ctx):
        asked.append((ctx.kind, ctx.reason))
        if ctx.kind == "full":
            return CompactionDecision(action="cancel", reason="no")
        return CompactionDecision(action="allow")

    _files(18)
    turn = _Turn(_batch_turn(16), hard_limit=WINDOW, compaction_controller=controller)

    out = turn.agent.chat("do the batch")

    assert "maximum context length" in out
    assert ("full", "api_overflow") in asked
    assert ("microcompact", "api_overflow") not in asked
    turn.agent.close()


def test_a_413_with_no_cut_does_not_clip(isolated_cwd):
    """A 413 does not say that tokens are the problem."""
    import httpx
    import openai

    _files(16)
    script = iter([_response(calls=[_call(i) for i in range(16)])])
    turn = _Turn([], hard_limit=None)
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    sends = []

    def llm(messages, tools, token):
        sends.append(list(messages))
        if len(sends) == 1:
            return next(script)
        raise openai.APIStatusError(
            "Error code: 413",
            response=httpx.Response(413, request=request),
            body={"message": "failed to parse request", "code": ""},
        )

    turn.agent._llm_call = llm

    out = turn.agent.chat("do the batch")

    assert out.startswith("[LLM API error:")
    assert all("omitted by microcompact" not in str(m.get("content")) for m in turn.agent.messages)
    turn.agent.close()


def test_every_refused_shape_leaves_a_valid_history(isolated_cwd):
    _files(18)
    turn = _Turn(_batch_turn(16), hard_limit=WINDOW)

    turn.agent.chat("do the batch")

    for request in turn.requests:
        assert _pairs_hold(request)
    turn.agent.close()
