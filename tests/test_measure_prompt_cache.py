"""``scripts/measure_prompt_cache.py`` — the kept form of the cache comparison.

The script spends money when it runs for real, so everything about it that can
be wrong without a network is pinned here: that it sends nothing without
``--yes``, that each arm's cache is isolated from the first byte, that the
arithmetic is the documented formula, and that an endpoint which reports no
cache fields is *not* rendered as one that cached nothing.

The session runs through a real ``Agentao`` and the real ``anthropic`` SDK over
a scripted socket.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from tests.support.anthropic_wire import (
    Wire, attach, message_end, message_start, stream_of, text_block,
)

pytestmark = pytest.mark.usefixtures("isolated_cwd")

_SPEC = importlib.util.spec_from_file_location(
    "measure_prompt_cache",
    Path(__file__).resolve().parent.parent / "scripts" / "measure_prompt_cache.py",
)
script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(script)

TURNS = ["first question", "second question", "third question"]


def _answer(**usage: int) -> bytes:
    return stream_of(message_start(**usage), text_block(0, "ok"),
                     message_end("end_turn", output_tokens=5))


def _factory(wires, *bodies):
    """``make_agent`` that builds the script's own agent, then scripts its socket."""
    def make_agent(workdir, **kwargs):
        agent = script._default_agent(workdir, **kwargs)
        wires.append(attach(agent.llm, Wire(*bodies)))
        return agent
    return make_agent


CACHED = (
    dict(input_tokens=1000, cache_creation_input_tokens=9000),
    dict(input_tokens=100, cache_read_input_tokens=9000, cache_creation_input_tokens=900),
    dict(input_tokens=100, cache_read_input_tokens=9900, cache_creation_input_tokens=500),
)


def _run(wires, usages=CACHED, **kwargs):
    return script.run_arm(
        "c", api_key="k", base_url="https://api.example.test", model="claude-test",
        turns=TURNS, make_agent=_factory(wires, *[_answer(**u) for u in usages]), **kwargs)


def test_nothing_is_sent_without_yes(monkeypatch, capsys):
    monkeypatch.setattr(script, "run_arm", lambda *a, **k: pytest.fail("ran an arm"))
    monkeypatch.setattr(script, "Agentao", lambda *a, **k: pytest.fail("built an agent"))
    assert script.main(["--arms", "a,c"]) == 0
    out = capsys.readouterr().out
    assert "spends money" in out and "--yes" in out


def test_yes_without_a_key_stops_before_any_arm(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(script, "run_arm", lambda *a, **k: pytest.fail("ran an arm"))
    assert script.main(["--yes"]) == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_the_rows_and_the_cost_are_the_documented_arithmetic():
    wires = []
    result = _run(wires)
    assert [row["turn"] for row in result["requests"]] == [1, 2, 3]
    assert result["totals"] == {
        "prompt_tokens": 10000 + 10000 + 10500, "completion_tokens": 15,
        "cache_read_tokens": 18900, "cache_creation_tokens": 10400,
    }
    # uncached + 1.25 × written + 0.1 × read, per request.
    assert result["input_cost_units"] == round(
        (1000 + 1.25 * 9000) + (100 + 1.25 * 900 + 0.1 * 9000) + (100 + 1.25 * 500 + 0.1 * 9900))
    assert result["cache_fields_reported"] is True


def test_nothing_is_left_open_in_the_directory_the_arm_deletes(monkeypatch):
    """The arm's working directory is temporary. ``Agentao`` left to its default
    opens ``agentao.log`` there and keeps it open; POSIX deletes an open file
    without complaint, **Windows raises WinError 32 from the cleanup** — which
    is how this reached CI red on Windows only. Asserted at the moment the
    arm's agent closes, so it fails on every platform, not just that one."""
    seen = []
    real_close = script.Agentao.close

    def close(self):
        seen.append(sorted(p.name for p in Path(self.working_directory).iterdir()))
        return real_close(self)

    monkeypatch.setattr(script.Agentao, "close", close)
    _run([])
    (names,) = seen
    assert "agentao.log" not in names
    assert {"inventory.md", "orders.csv", "policy.md"} <= set(names)  # looked in the right place


def test_each_arm_is_isolated_from_the_first_byte_of_the_cached_prefix():
    """The registry emits tools alphabetically, so it is the nonce tool's
    *name* that has to sort first. (Written assuming registration order; this
    test is what said otherwise — ``activate_skill`` came out ahead of it.)"""
    first, second = [], []
    _run(first)
    _run(second)
    a, b = first[0].requests[0]["tools"], second[0].requests[0]["tools"]
    assert a[0]["name"] == b[0]["name"] == script.NONCE_TOOL_NAME
    assert a[0]["description"] != b[0]["description"]
    assert a[1:] == b[1:]                       # and the nonce is the only difference
    assert len(a) > 5                           # the real tools are still there


def test_the_native_arm_really_asks_for_breakpoints():
    wires = []
    _run(wires)
    body = wires[0].requests[-1]
    assert "cache_control" in body["tools"][-1]


def test_the_control_arm_is_the_same_wire_with_no_breakpoints():
    """A gateway that caches prefixes on its own reports reads whether or not
    it read a marker, so arm c means nothing there without this beside it."""
    wires = []
    script.run_arm(
        "d", api_key="k", base_url="https://api.example.test", model="claude-test",
        turns=TURNS, make_agent=_factory(wires, *[_answer(**u) for u in CACHED]))
    body = wires[0].requests[-1]
    assert "/v1/messages" in wires[0].urls[-1]
    assert "cache_control" not in json.dumps(body)


def test_an_endpoint_that_reports_no_cache_fields_is_not_rendered_as_no_caching():
    wires = []
    silent = _run(wires, usages=[dict(input_tokens=10000)] * 3)
    assert silent["cache_fields_reported"] is False
    assert silent["input_cost_units"] is None   # not 30000: that would read as measured
    table = script.render([silent])
    assert "not reported" in table and "see the bill" in table


def test_the_saving_is_each_arm_against_its_own_full_price():
    """Not against another arm: the first live run had one arm make 19
    requests and the others 11, so a cross-arm delta measured the model."""
    wires = []
    cached = _run(wires)
    full = cached["totals"]["prompt_tokens"]
    expected = (cached["input_cost_units"] - full) / full
    assert f"({expected:+.0%} vs its own full price)" in script.render([cached])


def test_differing_request_counts_are_called_out():
    wires = []
    short = script.run_arm(
        "c", api_key="k", base_url="https://api.example.test", model="claude-test",
        turns=TURNS[:2], make_agent=_factory(wires, *[_answer(**u) for u in CACHED[:2]]))
    table = script.render([short, _run(wires)])
    assert "Request counts differ between arms (c: 2, c: 3)" in table
    assert "not comparable" in table
    assert "differ between arms" not in script.render([_run(wires), _run(wires)])


def test_an_activation_records_how_much_history_a_prefix_move_would_rewrite():
    wires = []
    result = _run(wires, activate_skill="no-such-skill", at_turn=3)
    act = result["activation"]
    assert act["before_turn"] == 3 and act["requests_so_far"] == 2
    assert act["history_messages"] == len(TURNS[:2]) * 2   # two user turns, two answers
    assert act["history_tokens_est"] > 0
    assert act["activated"] is False
    assert "NOT activated" in script.render([result])


@pytest.mark.parametrize("argv", [
    ["--arms", "a,z"], ["--arms", ""], ["--activate-skill", "pdf"], ["--at-turn", "2"],
    ["--activate-skill", "pdf", "--at-turn", "99"],
])
def test_a_bad_command_line_is_refused_before_anything_runs(argv):
    with pytest.raises(SystemExit) as raised:
        script.main(argv)
    assert raised.value.code == 2
