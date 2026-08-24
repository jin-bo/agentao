"""``trigger`` provenance, driven through the **five real entry points**.

The sibling wire-shape test builds payloads by hand, so it can only
assert what ``build_pre_compact`` does with the arguments a test chooses.
This one drives the actual compaction sites — ``_maybe_microcompact``,
``_maybe_full_compress``, both rungs of ``_call_llm_with_overflow_recovery``,
and ``/compact``'s ``_dispatch_pre_compact`` — and reads the payload the
hook subprocess actually received on stdin.

That distinction is the whole point of this file. ``trigger`` was
hardcoded ``"auto"`` inside the payload builder for every entry point,
and a hand-built payload passing ``trigger="manual"`` would have looked
correct while no site on earth produced it. Only the real call sites can
falsify that.
"""

from __future__ import annotations

import json
import stat
from types import SimpleNamespace

from agentao.cancellation import CancellationToken
from agentao.cli.commands.compact import handle_compact_command
from agentao.compaction.types import CompactionOutcome
from agentao.plugins.models import ParsedHookRule
from agentao.runtime.chat_loop import ChatLoopRunner

from tests.support.host_events import CapturingTransport
from tests.support.stop_precompact import make_bare_agent

# An error string the real classifier accepts as an overflow, taken from
# tests/test_context_overflow_detection.py's Anthropic row.
_OVERFLOW = "prompt is too long: 213462 tokens > 200000 maximum"

_HISTORY = [
    {"role": "user", "content": "one"},
    {"role": "assistant", "content": "two"},
    {"role": "user", "content": "three"},
    {"role": "assistant", "content": "four"},
    {"role": "user", "content": "five"},
]


def _rule(tmp_path, name, matcher=None):
    """A PreCompact rule whose command **appends** each payload it is given.

    The shared ``write_capture_script`` truncates, which loses the first
    of the two overflow rungs — both dispatch into the same file within
    one driver call.
    """
    script = tmp_path / f"{name}.sh"
    capture = tmp_path / f"{name}.jsonl"
    script.write_text(
        "#!/bin/sh\ncat >> '" + str(capture) + "'\nprintf '\\n' >> '"
        + str(capture) + "'\nexit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR | stat.S_IWUSR)
    rule = ParsedHookRule(
        event="PreCompact",
        hook_type="command",
        command=f"sh '{script}'",
        matcher=matcher,
        plugin_name="t",
    )
    return rule, capture


def _payloads(capture):
    if not capture.exists():
        return []
    return [
        json.loads(line)
        for line in capture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _agent(tmp_path, rules):
    transport = CapturingTransport()
    agent = make_bare_agent(tmp_path, transport=transport)
    agent._plugin_hook_rules = rules
    agent.messages = list(_HISTORY)
    # ``runtime/turn.py:111`` sets this at the start of every turn; these
    # drivers call the compaction sites directly, below that layer.
    agent._last_session_summary_id = None
    return agent, transport


def _neutralize(agent, monkeypatch):
    """Stub the parts of a compaction that need an LLM or a real prompt.

    Everything up to and including the hook dispatch stays real — that is
    what is under test. Only the summarizing transform is replaced, with a
    ``failed`` outcome: history stays byte-identical, no LLM is reached, and
    the hook has already fired by the time it is consulted.
    """
    monkeypatch.setattr(agent, "_build_system_prompt", lambda: "")
    monkeypatch.setattr(agent, "_emit_session_summary_if_new", lambda *_a, **_k: None)
    monkeypatch.setattr(
        agent.context_manager,
        "_run_compaction",
        lambda msgs, *, is_auto=True, reason="compression_threshold", decide=None: (
            CompactionOutcome(
                status="failed",
                trigger="auto" if is_auto else "manual",
                kind="full",
                reason=reason,
                messages=msgs,
                detail="summary_empty",
            )
        ),
    )


# --------------------------------------------------------------------------
# The five drivers — each runs the production code path end to end.
# --------------------------------------------------------------------------

def _drive_microcompact(tmp_path, monkeypatch, rules):
    agent, transport = _agent(tmp_path, rules)
    _neutralize(agent, monkeypatch)
    cm = agent.context_manager
    monkeypatch.setattr(cm, "needs_microcompaction", lambda *_a, **_k: True)
    monkeypatch.setattr(cm, "microcompact_would_mutate", lambda *_a, **_k: True)
    runner = ChatLoopRunner(agent)
    runner._maybe_microcompact([{"role": "system", "content": ""}] + agent.messages, "")
    return transport


def _drive_threshold_full(tmp_path, monkeypatch, rules):
    agent, transport = _agent(tmp_path, rules)
    _neutralize(agent, monkeypatch)
    cm = agent.context_manager
    monkeypatch.setattr(cm, "needs_compression", lambda *_a, **_k: True)
    monkeypatch.setattr(
        type(cm), "compaction_circuit_open", property(lambda _s: False),
    )
    runner = ChatLoopRunner(agent)
    runner._maybe_full_compress([{"role": "system", "content": ""}] + agent.messages, "")
    return transport


def _drive_overflow(tmp_path, monkeypatch, rules):
    """Both ladder rungs: ``_llm_call`` raises overflow every time."""
    agent, transport = _agent(tmp_path, rules)
    _neutralize(agent, monkeypatch)

    def _always_overflow(*_a, **_k):
        raise RuntimeError(_OVERFLOW)

    monkeypatch.setattr(agent, "_llm_call", _always_overflow)
    runner = ChatLoopRunner(agent)
    runner._call_llm_with_overflow_recovery(
        [{"role": "system", "content": ""}] + agent.messages,
        "",
        [],
        CancellationToken(),
    )
    return transport


def _drive_manual(tmp_path, monkeypatch, rules):
    """The real ``/compact`` handler, through the real coordinator.

    Only the summarizing step is stubbed — as a ``failed`` outcome, so the
    hook still fires and history stays put.
    """
    agent, transport = _agent(tmp_path, rules)
    _neutralize(agent, monkeypatch)
    cli = SimpleNamespace(agent=agent, _cached_ctx_pct=0.0)
    handle_compact_command(cli, "")
    return transport


def test_each_entry_point_reports_its_own_trigger(tmp_path, monkeypatch):
    """The provenance table, read off the wire.

    Four automatic sites say ``auto``; manual ``/compact`` says
    ``manual``. Before the fix all five said ``auto``.
    """
    expected = {
        "microcompact_threshold": "auto",
        "compression_threshold": "auto",
        "api_overflow": "auto",
        "api_overflow_after_compression": "auto",
        "manual_cli": "manual",
    }

    rule, capture = _rule(tmp_path, "capture_all")
    for driver in (
        _drive_microcompact,
        _drive_threshold_full,
        _drive_overflow,
        _drive_manual,
    ):
        driver(tmp_path, monkeypatch, [rule])

    seen = {p["reason"]: p["trigger"] for p in _payloads(capture)}
    assert seen == expected, seen


def test_manual_matcher_selects_only_the_manual_entry(tmp_path, monkeypatch):
    """PR-1's acceptance: ``{"trigger": "manual"}`` fires on ``/compact``
    and on nothing else.

    Selection is asserted through ``PLUGIN_HOOK_FIRED``, which is emitted
    only when ``select_matching_rules`` returned something — so a count of
    zero means the rule was genuinely not selected, not that the script
    failed.
    """
    rule, _ = _rule(tmp_path, "manual_only", matcher={"trigger": "manual"})

    for driver in (_drive_microcompact, _drive_threshold_full, _drive_overflow):
        transport = driver(tmp_path, monkeypatch, [rule])
        assert transport.hook_fired_events("PreCompact") == [], driver.__name__

    transport = _drive_manual(tmp_path, monkeypatch, [rule])
    fired = transport.hook_fired_events("PreCompact")
    assert len(fired) == 1
    assert fired[0].data["trigger"] == "manual"


def test_alternation_rule_still_fires_at_every_entry_point(tmp_path, monkeypatch):
    """The regression guard named in the plan: a host rule written
    ``{"trigger": "manual|auto"}`` matched all five sites before the
    change and must match all five after it."""
    rule, _ = _rule(tmp_path, "alternation", matcher={"trigger": "manual|auto"})

    counts = []
    for driver in (
        _drive_microcompact,
        _drive_threshold_full,
        _drive_overflow,
        _drive_manual,
    ):
        transport = driver(tmp_path, monkeypatch, [rule])
        counts.append(len(transport.hook_fired_events("PreCompact")))

    # microcompact 1, threshold 1, overflow 2 (both rungs), manual 1.
    assert counts == [1, 1, 2, 1]
