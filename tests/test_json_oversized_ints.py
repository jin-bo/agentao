"""An integer literal longer than ``sys.int_max_str_digits`` (#495).

``json.loads`` refuses to convert one with a plain ``ValueError``, not
``JSONDecodeError``, so every ``except json.JSONDecodeError`` handler let it
through. ``agentao.json_parse`` turns it into a ``JSONDecodeError``; these
tests write the literal into a real file or stream, so each goes through the
same loader the product uses.
"""

from __future__ import annotations

import ast
import io
import json
import logging
import sys
from pathlib import Path

import pytest

from agentao import json_parse

pytestmark = pytest.mark.skipif(
    not hasattr(sys, "get_int_max_str_digits"),
    reason="this Python has no integer string conversion limit",
)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "agentao"


def _big() -> str:
    return "1" * (sys.get_int_max_str_digits() + 700)


def _settings(root: Path, text: str) -> Path:
    path = root / ".agentao" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


def test_the_stdlib_still_raises_a_plain_value_error():
    """The premise: if this starts raising JSONDecodeError, the helper is moot."""
    with pytest.raises(ValueError) as info:
        json.loads(_big())
    assert not isinstance(info.value, json.JSONDecodeError)


def test_an_oversized_integer_is_a_decode_error_at_the_literal():
    doc = '{"a": 1,\n "n": ' + _big() + "}"
    with pytest.raises(json.JSONDecodeError) as info:
        json_parse.loads(doc)
    err = info.value
    assert err.pos == doc.index("1111")
    assert (err.lineno, err.colno) == (2, 7)
    assert f"longer than {sys.get_int_max_str_digits()} digits" in err.msg


def test_the_position_skips_digits_inside_strings_and_fractions():
    doc = '{"s": "' + _big() + '", "f": 1.' + _big() + ', "n": -' + _big() + "}"
    with pytest.raises(json.JSONDecodeError) as info:
        json_parse.loads(doc)
    assert info.value.pos == doc.index("-1")


def test_utf16_bytes_report_a_position_in_the_decoded_text():
    with pytest.raises(json.JSONDecodeError) as info:
        json_parse.loads(("[" + _big() + "]").encode("utf-16"))
    assert info.value.pos == 1


def test_locating_the_literal_is_linear_in_the_document():
    """A ``\\d{N,}`` search restarts inside every shorter run: seconds here."""
    import time

    limit = sys.get_int_max_str_digits()
    doc = "[" + ",".join(["1" * limit] * 300) + "," + _big() + "]"
    start = time.monotonic()
    with pytest.raises(json.JSONDecodeError) as info:
        json_parse.loads(doc)
    assert time.monotonic() - start < 1.0
    assert info.value.pos == doc.index(_big())


def test_load_reads_a_file_object():
    with pytest.raises(json.JSONDecodeError):
        json_parse.load(io.StringIO("[" + _big() + "]"))
    assert json_parse.load(io.StringIO('{"a": [1, 2.5, null]}')) == {"a": [1, 2.5, None]}


def test_ordinary_documents_and_long_digit_strings_are_unchanged():
    assert json_parse.loads('{"id": "' + _big() + '"}') == {"id": _big()}
    assert json_parse.loads("1e309") == float("inf")
    with pytest.raises(json.JSONDecodeError):
        json_parse.loads("{")


def test_undecodable_bytes_stay_a_unicode_decode_error():
    """Several callers handle UnicodeDecodeError in a clause of its own."""
    with pytest.raises(UnicodeDecodeError):
        json_parse.loads(b'{"a": "\xff\xfe\xfa"}')


# ---------------------------------------------------------------------------
# Settings: startup, /replay on, doctor
# ---------------------------------------------------------------------------


def test_startup_ignores_a_settings_file_it_cannot_parse(tmp_path, caplog):
    from agentao.replay import load_replay_config

    _settings(tmp_path, '{"replay": {"enabled": true, "max_instances": ' + _big() + "}}")
    with caplog.at_level(logging.WARNING, logger="agentao.replay.config"):
        cfg = load_replay_config(tmp_path)
    assert (cfg.enabled, cfg.max_instances) == (False, 20)
    assert "longer than" in caplog.text


@pytest.mark.parametrize(
    "text",
    [
        '{"replay": {"enabled": false}, "other": ' + "1" * 5000 + "}",
        '{"replay": {"enabled": false}, "other": [1,}',
        '[{"replay": {"enabled": false}}]',
    ],
    ids=["oversized-int", "invalid-json", "not-an-object"],
)
def test_replay_toggle_never_overwrites_a_file_it_could_not_read(tmp_path, text):
    """Startup ignores such a file; writing ``{"replay": ...}`` over it would
    delete every other setting in it."""
    from agentao.replay import ReplaySettingsError, save_replay_enabled

    path = _settings(tmp_path, text)
    with pytest.raises(ReplaySettingsError, match="was not changed"):
        save_replay_enabled(True, tmp_path)
    assert path.read_text(encoding="utf-8") == text


def _strict(text: str):
    def refuse(token):
        raise AssertionError(f"non-standard JSON token {token}")
    return json.loads(text, parse_constant=refuse)


def test_replay_toggle_writes_a_non_finite_count_back_as_the_count_in_effect(tmp_path):
    from agentao.replay import save_replay_enabled

    path = _settings(tmp_path, '{"replay": {"enabled": false, "max_instances": 1e309}, "keep": 1}')
    cfg = save_replay_enabled(True, tmp_path)
    data = _strict(path.read_text(encoding="utf-8"))
    assert data == {"replay": {"enabled": True, "max_instances": 20}, "keep": 1}
    assert (cfg.enabled, cfg.max_instances) == (True, 20)


def test_replay_toggle_refuses_another_non_finite_number(tmp_path):
    from agentao.replay import ReplaySettingsError, save_replay_enabled

    text = '{"replay": {"enabled": false}, "elsewhere": -1e999}'
    path = _settings(tmp_path, text)
    with pytest.raises(ReplaySettingsError, match="cannot represent"):
        save_replay_enabled(True, tmp_path)
    assert path.read_text(encoding="utf-8") == text


def test_replay_toggle_command_reports_the_refusal(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agentao.cli import replay_commands

    _settings(tmp_path, "[1]")
    printed = []
    monkeypatch.setattr(replay_commands.console, "print", lambda *a, **k: printed.append(a))
    reloaded = []
    cli = SimpleNamespace(agent=SimpleNamespace(
        working_directory=tmp_path,
        reload_replay_config=lambda: reloaded.append(True),
    ))
    replay_commands._handle_toggle(cli, "on")
    assert reloaded == []
    assert "Could not persist replay setting" in str(printed[0][0])


@pytest.mark.parametrize(
    "text",
    ['{"other": ' + "1" * 5000 + "}", '{"other": [1,}', "[1]"],
    ids=["oversized-int", "invalid-json", "not-an-object"],
)
def test_mode_save_never_overwrites_a_file_it_could_not_read(tmp_path, monkeypatch, text):
    """``/mode`` writes the same file as ``/replay on|off`` and must refuse alike."""
    from types import SimpleNamespace

    from agentao.cli import app as cli_app
    from agentao.permissions import PermissionMode

    path = _settings(tmp_path, text)
    printed = []
    monkeypatch.setattr(cli_app.console, "print", lambda *a, **k: printed.append(a))
    cli = SimpleNamespace(
        _project_root=tmp_path, current_mode=PermissionMode.FULL_ACCESS,
    )
    cli_app.AgentaoCLI._save_settings(cli)
    assert path.read_text(encoding="utf-8") == text
    assert "Mode not saved" in str(printed[0][0])


def test_session_save_skips_a_neighbour_that_is_not_an_object(tmp_path):
    from agentao.embedding.sessions import _find_created_at

    (tmp_path / "a.json").write_text("[1]", encoding="utf-8")
    (tmp_path / "b.json").write_bytes(b"\xff\xfe{")
    assert _find_created_at(tmp_path, "sid") is None


def test_session_scans_skip_a_neighbour_that_is_not_utf8(tmp_path):
    from agentao.embedding.sessions import _session_dir, delete_session

    session_dir = _session_dir(tmp_path)
    session_dir.mkdir(parents=True)
    (session_dir / "a.json").write_bytes(b"\xff\xfe{")
    assert delete_session("nope", project_root=tmp_path) is False


def test_doctor_reports_invalid_json_instead_of_crashing(tmp_path):
    from agentao.cli.diagnostics.loaders import _load_json_object

    path = _settings(tmp_path, '{"replay": {"max_instances": ' + _big() + "}}")
    data, status, finding = _load_json_object(path, area="settings")
    assert (data, status) == (None, "malformed")
    assert "Invalid JSON" in finding.message
    assert "line 1, col 30" in finding.message


def test_permission_file_with_one_is_a_permission_config_error(tmp_path):
    from agentao.embedding.permission_loader import PermissionConfigError, _read_rule_file

    path = tmp_path / "permissions.json"
    path.write_text('{"rules": [], "x": ' + _big() + "}", encoding="utf-8")
    with pytest.raises(PermissionConfigError, match="invalid JSON"):
        _read_rule_file(path)


# ---------------------------------------------------------------------------
# Untrusted input: model tool arguments, hook stdout, ACP lines
# ---------------------------------------------------------------------------


def test_tool_arguments_with_one_canonicalise_to_an_empty_object():
    from agentao.runtime.sanitize import canonicalize_tool_arguments

    assert canonicalize_tool_arguments('{"n": ' + _big() + "}", tool_name="t") == "{}"


def test_tool_argument_parsing_reports_it_as_unparseable():
    """The planner turns a ``ValueError`` into the parse-failure tool result."""
    from agentao.runtime.arg_repair import parse_tool_arguments

    with pytest.raises(ValueError):
        parse_tool_arguments('{"n": ' + _big() + "}")


def test_hook_stdout_with_one_is_a_parse_error():
    from agentao.plugins.hooks._resolve import parse_stdout

    data, state, message = parse_stdout('{"continue": ' + _big() + "}", "PreToolUse")
    assert (data, state) == (None, "parse_error")
    assert "longer than" in message


def test_acp_server_answers_a_parse_error_and_keeps_going():
    from agentao.acp.server import AcpServer

    stdin = io.StringIO(
        '{"jsonrpc": "2.0", "id": 1, "method": "x", "params": {"n": ' + _big() + "}}\n"
        '{"jsonrpc": "2.0", "id": 2, "method": "no/such/method"}\n'
    )
    stdout = io.StringIO()
    AcpServer(stdin=stdin, stdout=stdout).run()
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0]["id"] is None
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["id"] == 2


# ---------------------------------------------------------------------------
# Guard: no handler catches JSONDecodeError around a raw json.load(s)
# ---------------------------------------------------------------------------


def _catches_value_error(handler: ast.ExceptHandler) -> bool:
    names = []
    for node in ast.walk(handler.type) if handler.type is not None else []:
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
    return handler.type is None or bool(
        {"ValueError", "Exception", "BaseException"} & set(names)
    )


def _raw_json_calls(body):
    for stmt in body:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("load", "loads")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
            ):
                yield node


def test_no_json_decode_error_handler_guards_a_raw_json_call():
    """A ``try`` that catches ``JSONDecodeError`` but not ``ValueError`` must
    parse through ``json_parse``, or an oversized integer escapes it again."""
    offenders = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path.name == "json_parse.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            handlers = node.handlers
            catches_decode = any(
                h.type is not None and "JSONDecodeError" in ast.unparse(h.type)
                for h in handlers
            )
            if not catches_decode or any(_catches_value_error(h) for h in handlers):
                continue
            for call in _raw_json_calls(node.body):
                rel = path.relative_to(PACKAGE_ROOT.parent).as_posix()
                offenders.append(f"{rel}:{call.lineno}")
    assert not offenders, (
        "json.load(s) guarded only by `except JSONDecodeError`; use "
        "agentao.json_parse instead:\n  " + "\n  ".join(offenders)
    )
