"""Tests for the /thinking command (thinking depth → reasoning_effort).

The handler only touches ``cli.agent.llm.extra_body`` (the live request-body
passthrough), so a lightweight fake CLI/agent/LLM is enough — no real provider
credentials or network. See ``agentao/cli/commands/provider.py`` and
``docs/design/host-llm-extra-params.md``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentao.cli.commands import handle_thinking_command


def _fake_cli(extra_body=None):
    """A minimal stand-in exposing ``cli.agent.llm.extra_body`` (a live dict)."""
    llm = SimpleNamespace(extra_body={} if extra_body is None else extra_body)
    return SimpleNamespace(agent=SimpleNamespace(llm=llm))


def _eb(cli):
    return cli.agent.llm.extra_body


def test_show_when_unset_does_not_mutate(capsys):
    cli = _fake_cli()
    handle_thinking_command(cli, "")
    out = capsys.readouterr().out
    assert "default" in out
    assert "reasoning_effort" not in _eb(cli)


def test_set_canonical_level():
    cli = _fake_cli()
    handle_thinking_command(cli, "high")
    assert _eb(cli)["reasoning_effort"] == "high"


def test_change_level():
    cli = _fake_cli({"reasoning_effort": "high"})
    handle_thinking_command(cli, "low")
    assert _eb(cli)["reasoning_effort"] == "low"


def test_value_is_lowercased():
    cli = _fake_cli()
    handle_thinking_command(cli, "HIGH")
    assert _eb(cli)["reasoning_effort"] == "high"


def test_off_clears_key():
    cli = _fake_cli({"reasoning_effort": "medium"})
    handle_thinking_command(cli, "off")
    assert "reasoning_effort" not in _eb(cli)


def test_off_when_unset_is_noop():
    cli = _fake_cli()
    handle_thinking_command(cli, "off")  # must not raise / KeyError
    assert "reasoning_effort" not in _eb(cli)


def test_off_preserves_other_extra_body_keys():
    cli = _fake_cli({"reasoning_effort": "high", "seed": 7})
    handle_thinking_command(cli, "off")
    assert _eb(cli) == {"seed": 7}


def test_non_standard_value_passes_through_with_note(capsys):
    cli = _fake_cli()
    handle_thinking_command(cli, "ultra")
    out = capsys.readouterr().out
    assert _eb(cli)["reasoning_effort"] == "ultra"
    assert "non-standard" in out


def test_literal_none_is_a_value_not_disable():
    # A provider whose scale includes a literal "none" effort is set explicitly;
    # only "off" clears the key.
    cli = _fake_cli()
    handle_thinking_command(cli, "none")
    assert _eb(cli)["reasoning_effort"] == "none"


def test_missing_extra_body_attr_errors_gracefully(capsys):
    cli = SimpleNamespace(agent=SimpleNamespace(llm=SimpleNamespace()))
    handle_thinking_command(cli, "high")  # must not raise
    out = capsys.readouterr().out
    assert "extra_body" in out


# ── review-driven hardening ────────────────────────────────────────────────


def test_markup_in_value_does_not_crash_and_is_stored(capsys):
    # A value containing Rich markup brackets must not raise MarkupError; the
    # printed confirmation is escaped, but the raw value is stored verbatim.
    cli = _fake_cli()
    handle_thinking_command(cli, "[/]")  # would crash if interpolated unescaped
    out = capsys.readouterr().out
    assert _eb(cli)["reasoning_effort"] == "[/]"
    assert "non-standard" in out


def test_markup_in_current_value_show_path_does_not_crash(capsys):
    cli = _fake_cli({"reasoning_effort": "[red]x[/red]"})
    handle_thinking_command(cli, "")  # show path interpolates current value
    handle_thinking_command(cli, "off")  # off path interpolates prev value
    # No exception == pass; key cleared by off.
    assert "reasoning_effort" not in _eb(cli)


def test_multi_word_value_is_rejected(capsys):
    cli = _fake_cli()
    handle_thinking_command(cli, "high please")
    out = capsys.readouterr().out
    assert "reasoning_effort" not in _eb(cli)  # nothing stored
    assert "Invalid" in out


def test_non_standard_value_preserves_case():
    # Case-sensitive provider token must not be lowercased; only known canonical
    # levels are normalized.
    cli = _fake_cli()
    handle_thinking_command(cli, "X-High")
    assert _eb(cli)["reasoning_effort"] == "X-High"


def test_none_extra_body_is_initialized():
    # An injected client defaulting extra_body to None must still be settable.
    cli = _fake_cli(extra_body=None)
    cli.agent.llm.extra_body = None  # _fake_cli maps None -> {}; force None here
    handle_thinking_command(cli, "high")
    assert cli.agent.llm.extra_body == {"reasoning_effort": "high"}


def test_explicit_none_value_is_treated_as_set():
    # reasoning_effort present but None is "set" (still sent to provider), so
    # bare show reflects it and off can clear it.
    cli = _fake_cli({"reasoning_effort": None})
    handle_thinking_command(cli, "off")
    assert "reasoning_effort" not in _eb(cli)
