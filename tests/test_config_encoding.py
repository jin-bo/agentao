"""F1 — startup-critical config readers survive a foreign encoding.

``UnicodeDecodeError`` subclasses **``ValueError``** — not ``OSError``,
not ``json.JSONDecodeError``. Every reader in ``docs/reference/configuration.md``
caught only the latter two, so a config file that is not valid UTF-8
raised straight through the ``except`` clause written to contain it. The
missed sibling of the P0 ``permission-hardening-plan.md`` fixed one line
lower: ``isinstance(data, dict)`` guards the *parsed shape*; the *decode*
failure happens earlier, on ``read_text``.

Two contracts, split by what dropping the file costs:

- ``permissions.json`` **fails closed** — a typed error naming the path,
  session construction aborts. Losing a deny rule is not a neutral
  degradation (see ``test_permissions_modes.py::test_invalid_json_user_config_fails_closed``).
- everything else **warns with the path and degrades** to its default.

Every input here is built from real bytes (``codecs.BOM_UTF16_LE +
body.encode("utf-16-le")``), never a hand-authored string that restates
the belief — a UTF-16 fixture written as a ``str`` would be re-encoded to
UTF-8 on write and prove nothing.
"""

from __future__ import annotations

import codecs
import json
import logging
import os
from pathlib import Path

import pytest

from agentao.embedding.permission_loader import (
    PermissionConfigError,
    load_permission_rules,
)


# ---------------------------------------------------------------------------
# Byte-level fixtures
# ---------------------------------------------------------------------------

_RULE = {"tool": "run_shell_command", "args": {"command": "^git "}, "action": "allow"}


def _body(payload: dict) -> str:
    return json.dumps(payload)


def utf8(payload: dict) -> bytes:
    return _body(payload).encode("utf-8")


def utf8_bom(payload: dict) -> bytes:
    return codecs.BOM_UTF8 + _body(payload).encode("utf-8")


def utf16le_bom(payload: dict) -> bytes:
    """What PowerShell 5.1's ``>`` and ``Out-File`` write on stock Windows."""
    return codecs.BOM_UTF16_LE + _body(payload).encode("utf-16-le")


def gbk_cjk() -> bytes:
    """Valid JSON whose CJK comment is GBK — the non-Windows path in."""
    return '{"rules": [], "note": "中文"}'.encode("gbk")


def test_the_three_fixtures_are_genuinely_different_bytes():
    """Counterfactual for the fixtures themselves.

    If ``utf16le_bom`` decoded as UTF-8 the encoding tests below would
    pass against a reader that never changed.
    """
    payload = {"rules": [_RULE]}
    assert utf8(payload) != utf8_bom(payload) != utf16le_bom(payload)
    assert utf8_bom(payload).startswith(b"\xef\xbb\xbf")
    with pytest.raises(UnicodeDecodeError):
        utf16le_bom(payload).decode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        gbk_cjk().decode("utf-8")


@pytest.fixture
def user_root(tmp_path: Path) -> Path:
    root = tmp_path / "home" / ".agentao"
    root.mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# permissions.json — fails closed
# ---------------------------------------------------------------------------


class TestPermissionsJson:
    def _load(self, tmp_path: Path, user_root: Path):
        return load_permission_rules(project_root=tmp_path, user_root=user_root)

    def test_absent_is_silent(self, tmp_path, user_root):
        rules, sources = self._load(tmp_path, user_root)
        assert (rules, sources) == ([], [])

    def test_plain_utf8_loads(self, tmp_path, user_root):
        (user_root / "permissions.json").write_bytes(utf8({"rules": [_RULE]}))
        rules, sources = self._load(tmp_path, user_root)
        assert rules == [_RULE]
        assert sources == [f"user:{user_root / 'permissions.json'}"]

    def test_bom_loads_identically_to_plain_utf8(self, tmp_path, user_root):
        """``utf-8-sig`` strips the BOM instead of discarding the file.

        Asserting the *rules* — not merely that no exception escaped —
        is what proves the BOM was consumed by the codec rather than
        smuggled into the first key.
        """
        (user_root / "permissions.json").write_bytes(utf8_bom({"rules": [_RULE]}))
        rules, sources = self._load(tmp_path, user_root)
        assert rules == [_RULE]
        assert len(sources) == 1

    @pytest.mark.parametrize(
        "name, payload",
        [
            ("utf16le", utf16le_bom({"rules": [_RULE]})),
            ("gbk", gbk_cjk()),
        ],
    )
    def test_undecodable_raises_naming_the_path(
        self, tmp_path, user_root, name, payload
    ):
        path = user_root / "permissions.json"
        path.write_bytes(payload)
        with pytest.raises(PermissionConfigError) as excinfo:
            self._load(tmp_path, user_root)
        assert excinfo.value.path == path
        assert str(path) in str(excinfo.value)
        assert "not valid UTF-8" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, UnicodeDecodeError)

    def test_invalid_json_raises_with_line_and_column(self, tmp_path, user_root):
        path = user_root / "permissions.json"
        path.write_text('{"rules": [],}', encoding="utf-8")  # genuine trailing comma
        with pytest.raises(PermissionConfigError) as excinfo:
            self._load(tmp_path, user_root)
        assert "invalid JSON" in str(excinfo.value)
        assert "line 1" in str(excinfo.value)

    def test_a_typod_top_level_key_raises_instead_of_dropping_every_rule(
        self, tmp_path, user_root
    ):
        """The document-level twin of the closed rule key set.

        ``data.get("rules", [])`` swallows the typo whole: the file parses,
        every rule vanishes, and ``loaded_sources`` still names the file —
        a silent fail-*open* in the one loader that fails closed.
        """
        (user_root / "permissions.json").write_text(
            json.dumps({"rule": [_RULE]}), encoding="utf-8",
        )
        with pytest.raises(PermissionConfigError) as excinfo:
            self._load(tmp_path, user_root)
        assert "unknown top-level key" in str(excinfo.value)
        assert "'rule'" in str(excinfo.value)

    def test_an_empty_document_is_still_a_valid_empty_policy(
        self, tmp_path, user_root
    ):
        """Only *extra* keys are rejected — ``{}`` is a real benign state."""
        (user_root / "permissions.json").write_text("{}", encoding="utf-8")
        rules, sources = self._load(tmp_path, user_root)
        assert rules == []
        assert len(sources) == 1

    def test_non_object_document_raises(self, tmp_path, user_root):
        """Document shape is the loader's job, not the rule validator's."""
        (user_root / "permissions.json").write_text("[]", encoding="utf-8")
        with pytest.raises(PermissionConfigError) as excinfo:
            self._load(tmp_path, user_root)
        assert "top-level value must be a JSON object" in str(excinfo.value)
        assert excinfo.value.errors == []

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root ignores the mode bits, so chmod(0o000) is not unreadable",
    )
    @pytest.mark.skipif(
        os.name == "nt",
        reason="chmod(0o000) does not remove read access on Windows; making a file "
               "genuinely unreadable there needs an ACL, which is the identity oracle's job",
    )
    def test_unreadable_file_raises(self, tmp_path, user_root):
        path = user_root / "permissions.json"
        path.write_bytes(utf8({"rules": []}))
        path.chmod(0o000)
        try:
            with pytest.raises(PermissionConfigError) as excinfo:
                self._load(tmp_path, user_root)
        finally:
            path.chmod(0o600)
        assert "cannot read the file" in str(excinfo.value)

    def test_invalid_rule_raises_with_index_and_reason(self, tmp_path, user_root):
        (user_root / "permissions.json").write_text(
            json.dumps({"rules": [_RULE, {"tool": "x", "action": "alow"}]}),
            encoding="utf-8",
        )
        with pytest.raises(PermissionConfigError) as excinfo:
            self._load(tmp_path, user_root)
        assert excinfo.value.errors == [
            (1, "'action' must be one of allow, deny, ask (case-insensitive), "
                "got 'alow'"),
        ]
        assert "rules[1]" in str(excinfo.value)

    def test_missing_file_is_not_an_unreadable_file(self, tmp_path, user_root):
        """The ``is_file()`` pre-check is a prerequisite, not a nicety.

        Without it the absent-file case reaches the ``OSError`` branch,
        which now raises — i.e. agentao would refuse to start for every
        user who has never written a permissions file.
        """
        assert not (user_root / "permissions.json").exists()
        assert self._load(tmp_path, user_root) == ([], [])

    def test_directory_at_the_path_is_treated_as_absent(self, tmp_path, user_root):
        (user_root / "permissions.json").mkdir()
        assert self._load(tmp_path, user_root) == ([], [])

    def test_stray_project_file_is_never_a_rule_source(self, tmp_path, caplog):
        """Project scope is deliberately unhonored — including when broken.

        Guards the boundary of the fail-closed route: a *project* file
        that cannot be parsed must still not abort startup, because the
        engine never reads it as a rule source in the first place.
        """
        cfg = tmp_path / ".agentao"
        cfg.mkdir()
        (cfg / "permissions.json").write_bytes(utf16le_bom({"rules": [_RULE]}))
        with caplog.at_level(logging.WARNING):
            rules, sources = load_permission_rules(
                project_root=tmp_path, user_root=None,
            )
        assert (rules, sources) == ([], [])
        assert "no longer honored" in caplog.text


# ---------------------------------------------------------------------------
# Everything else — warns with the path, degrades to the default
# ---------------------------------------------------------------------------


class TestWarnAndDegrade:
    def test_settings_json_factory_reader(self, tmp_path, caplog):
        from agentao.embedding.factory import _load_settings

        cfg = tmp_path / ".agentao"
        cfg.mkdir()
        path = cfg / "settings.json"
        path.write_bytes(utf16le_bom({"mode": "full-access"}))
        with caplog.at_level(logging.WARNING):
            assert _load_settings(tmp_path) == {}
        assert str(path) in caplog.text
        assert "not valid UTF-8" in caplog.text

    def test_settings_json_bom_still_loads(self, tmp_path):
        from agentao.embedding.factory import _load_settings

        cfg = tmp_path / ".agentao"
        cfg.mkdir()
        (cfg / "settings.json").write_bytes(utf8_bom({"mode": "full-access"}))
        assert _load_settings(tmp_path) == {"mode": "full-access"}

    def test_settings_json_replay_reader(self, tmp_path, caplog):
        from agentao.replay.config import _load_settings

        cfg = tmp_path / ".agentao"
        cfg.mkdir()
        path = cfg / "settings.json"
        path.write_bytes(utf16le_bom({"replay": {"enabled": True}}))
        with caplog.at_level(logging.WARNING):
            assert _load_settings(tmp_path) == {}
        assert str(path) in caplog.text

    def test_mcp_json(self, tmp_path, caplog):
        from agentao.mcp.config import load_mcp_config

        cfg = tmp_path / ".agentao"
        cfg.mkdir()
        path = cfg / "mcp.json"
        path.write_bytes(utf16le_bom({"mcpServers": {}}))
        with caplog.at_level(logging.WARNING):
            assert load_mcp_config(project_root=tmp_path, user_root=None) == {}
        assert str(path) in caplog.text
        assert "not valid UTF-8" in caplog.text

    def test_skills_config_json(self, tmp_path, caplog):
        from agentao.skills.manager import SkillManager

        cfg = tmp_path / ".agentao"
        cfg.mkdir()
        path = cfg / "skills_config.json"
        path.write_bytes(utf16le_bom({"disabled_skills": ["x"]}))
        with caplog.at_level(logging.WARNING):
            mgr = SkillManager(
                skills_dir=str(tmp_path / "nonexistent"),
                working_directory=tmp_path,
            )
        assert mgr.disabled_skills == set()
        assert str(path) in caplog.text

    def test_acp_json_raises_the_typed_error_not_a_traceback(self, tmp_path):
        """``acp.json`` already fails closed; it just failed *wrongly*.

        The whole point of ``load_acp_client_config`` is that config
        problems arrive as ``AcpConfigError``. A UTF-16 file bypassed it.
        """
        from agentao.acp_client.config import load_acp_client_config
        from agentao.acp_client.models import AcpConfigError

        cfg = tmp_path / ".agentao"
        cfg.mkdir()
        path = cfg / "acp.json"
        path.write_bytes(utf16le_bom({"servers": {}}))
        with pytest.raises(AcpConfigError) as excinfo:
            load_acp_client_config(project_root=tmp_path)
        assert str(path) in str(excinfo.value)
        assert "not valid UTF-8" in str(excinfo.value)


# ---------------------------------------------------------------------------
# agentao doctor — catches and reports, never exits early
# ---------------------------------------------------------------------------


class TestDoctorCatchesAndReports:
    """The carve-out: the moment a user needs diagnostics is exactly when
    their config is broken, so the diagnostic path must not inherit the
    runtime loader's abort. ``_collect_permissions`` mirrors the loader
    rather than calling it — these tests are what keeps the mirror from
    drifting back to describing the old contract.
    """

    @pytest.fixture
    def home(self, tmp_path, monkeypatch):
        fake = tmp_path / "fake_home"
        (fake / ".agentao").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake))
        monkeypatch.setattr(Path, "home", lambda: fake)
        return fake / ".agentao"

    def _run(self, tmp_path):
        from agentao.cli.diagnostics_cli import DiagnosticReport, _collect_permissions

        report = DiagnosticReport()
        _collect_permissions(tmp_path, report)  # must not raise
        return report

    def test_undecodable_file_is_reported_not_raised(self, tmp_path, home):
        (home / "permissions.json").write_bytes(utf16le_bom({"rules": []}))
        report = self._run(tmp_path)
        assert report.ok is False
        assert report.sections["permissions"]["user_status"] == "malformed"
        assert any("not valid UTF-8" in f.message for f in report.findings)

    def test_report_states_the_new_cost_of_a_broken_file(self, tmp_path, home):
        """Not "silently ignored" any more — startup now aborts."""
        (home / "permissions.json").write_text("{broken", encoding="utf-8")
        report = self._run(tmp_path)
        assert any(
            "will not start" in f.message and f.level == "error"
            for f in report.findings
        )

    def test_invalid_rules_are_reported_per_rule(self, tmp_path, home):
        (home / "permissions.json").write_text(
            json.dumps({"rules": [{"tool": "x", "pattern": "^git "}]}),
            encoding="utf-8",
        )
        report = self._run(tmp_path)
        assert report.ok is False
        assert report.sections["permissions"]["user_status"] == "malformed"
        assert any("rules[0]" in f.message for f in report.findings)

    def test_typod_top_level_key_is_reported_not_called_healthy(
        self, tmp_path, home
    ):
        """The mirror has to carry the loader's document check too."""
        (home / "permissions.json").write_text(
            json.dumps({"rule": [_RULE]}), encoding="utf-8",
        )
        report = self._run(tmp_path)
        assert report.ok is False
        assert report.sections["permissions"]["user_status"] == "malformed"
        assert any("unknown top-level key" in f.message for f in report.findings)

    def test_invalid_rules_also_report_the_startup_cost(self, tmp_path, home):
        """A per-rule message alone never says that startup now aborts."""
        (home / "permissions.json").write_text(
            json.dumps({"rules": [{"tool": "x", "action": "alow"}]}),
            encoding="utf-8",
        )
        report = self._run(tmp_path)
        assert any(
            "will not start" in f.message and f.level == "error"
            for f in report.findings
        )

    def test_bom_file_is_healthy(self, tmp_path, home):
        (home / "permissions.json").write_bytes(utf8_bom({"rules": [_RULE]}))
        report = self._run(tmp_path)
        assert report.ok is True
        assert report.sections["permissions"]["rule_count"] == 1


# ---------------------------------------------------------------------------
# The error text has to survive the CLI's own display boundary
# ---------------------------------------------------------------------------


class TestFatalHandlerSurvivesHostileMarkup:
    """``PermissionConfigError`` quotes the offending key verbatim.

    ``agentao``'s interactive fatal handler renders ``str(exc)`` through
    Rich markup, so a rule field literally named ``[/oops]`` — or an
    ``OSError`` stringifying as ``[Errno 13] ...`` — would raise
    ``MarkupError`` from the handler that exists to turn a crash into a
    readable message.
    """

    def _error(self, key: str):
        return PermissionConfigError(
            Path("/home/u/[wip]/permissions.json"),
            "one or more rules are invalid",
            errors=[(0, f"unknown field {key!r}")],
        )

    def test_the_payload_would_break_an_unescaped_print(self):
        from rich.console import Console
        from rich.errors import MarkupError

        with pytest.raises(MarkupError):
            Console().print(f"[red]{self._error('[/oops]')}[/red]")

    def test_the_real_handler_escapes_it(self, capsys):
        """Drives ``cli.entrypoints.main`` itself.

        Re-printing the handler's format string here would only restate
        what this file believes it says. ``main`` renders a factory
        exception through the broad handler and exits 1, so raising the
        hostile error from the factory exercises the real boundary.
        """
        pytest.importorskip("prompt_toolkit")
        from agentao.cli.entrypoints import main

        def _factory(**kwargs):
            raise self._error("[/oops]")

        with pytest.raises(SystemExit) as excinfo:
            main(agent_factory=_factory)

        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "Fatal error:" in out
        assert "[/oops]" in out
        # The handler renders the ``Path`` it was given, so the expectation is that
        # path's own string — ``\\home\\u\\[wip]\\...`` on Windows. What is under test is
        # that the brackets survive Rich's markup, not which separator the OS uses.
        assert str(Path("/home/u/[wip]/permissions.json")) in out
