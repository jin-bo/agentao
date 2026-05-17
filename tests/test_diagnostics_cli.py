"""Tests for ``agentao doctor`` and ``agentao config validate``.

These commands aggregate or validate existing health signals; they must not
require an instantiated agent and must not print API keys.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict

import pytest

from agentao.cli.diagnostics_cli import (
    DiagnosticReport,
    Finding,
    _collect_mcp,
    _collect_permissions,
    _collect_provider,
    _collect_replay,
    _collect_settings,
    handle_config_validate_subcommand,
    handle_doctor_subcommand,
)
from agentao.cli.entrypoints import _build_parser


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _ns(json_output: bool = False, **extra) -> argparse.Namespace:
    return argparse.Namespace(json_output=json_output, **extra)


@pytest.fixture
def isolated_wd(tmp_path, monkeypatch):
    """Run each test in a clean cwd with no inherited LLM env."""
    monkeypatch.chdir(tmp_path)
    # Reset every env var the provider collector reads, so test ordering
    # never lets credentials from a previous test leak into the snapshot.
    for var in (
        "LLM_PROVIDER", "LLM_TEMPERATURE", "LLM_MAX_TOKENS",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
        "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
        "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    # Re-route ~/.agentao away from the real user home so tests do not
    # observe (or mutate) the developer's memory store.
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return tmp_path


# ----------------------------------------------------------------------
# Argument parser wiring
# ----------------------------------------------------------------------


class TestArgParser:
    def test_doctor_json_flag(self):
        parser = _build_parser()
        ns, extras = parser.parse_known_args(["doctor", "--json"])
        assert ns.subcommand == "doctor"
        assert ns.json_output is True
        assert extras == []

    def test_doctor_default_human(self):
        parser = _build_parser()
        ns, _ = parser.parse_known_args(["doctor"])
        assert ns.subcommand == "doctor"
        assert ns.json_output is False

    def test_config_validate_json(self):
        parser = _build_parser()
        ns, _ = parser.parse_known_args(["config", "validate", "--json"])
        assert ns.subcommand == "config"
        assert ns.config_action == "validate"
        assert ns.json_output is True


# ----------------------------------------------------------------------
# Per-collector unit tests
# ----------------------------------------------------------------------


class TestCollectSettings:
    def test_absent_file_is_not_an_error(self, isolated_wd):
        report = DiagnosticReport()
        data = _collect_settings(isolated_wd, report)
        assert data is None
        assert report.ok is True
        assert report.sections["settings"]["status"] == "absent"

    def test_malformed_json_is_an_error(self, isolated_wd):
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "settings.json").write_text("{not json", encoding="utf-8")

        report = DiagnosticReport()
        _collect_settings(isolated_wd, report)
        assert report.ok is False
        assert report.sections["settings"]["status"] == "malformed"
        # The source path must be in the finding so the user can fix it.
        err = next(f for f in report.findings if f.level == "error")
        assert err.area == "settings"
        assert "settings.json" in (err.source or "")

    def test_non_object_top_level_is_an_error(self, isolated_wd):
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")

        report = DiagnosticReport()
        _collect_settings(isolated_wd, report)
        assert report.ok is False
        assert report.sections["settings"]["status"] == "malformed"

    def test_well_formed_settings_is_ok(self, isolated_wd):
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "settings.json").write_text(
            json.dumps({"replay": {"enabled": True}}), encoding="utf-8",
        )

        report = DiagnosticReport()
        data = _collect_settings(isolated_wd, report)
        assert data == {"replay": {"enabled": True}}
        assert report.ok is True
        assert report.sections["settings"]["status"] == "ok"
        assert report.sections["settings"]["keys"] == ["replay"]


class TestCollectProvider:
    def test_missing_api_key_is_warning_not_error(self, isolated_wd):
        report = DiagnosticReport()
        _collect_provider(report)
        # No API key → warning, but the report stays ok=True (warnings do
        # not flip the gate).
        assert report.ok is True
        assert any(
            f.level == "warning" and f.area == "provider" for f in report.findings
        )

    def test_api_key_value_is_redacted(self, isolated_wd, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-do-not-leak")
        report = DiagnosticReport()
        _collect_provider(report)

        section = report.sections["provider"]
        # The presence flag is reported; the value itself never appears.
        assert section["api_key_present"] is True
        serialized = json.dumps(report.to_dict())
        assert "sk-secret-do-not-leak" not in serialized

    def test_malformed_temperature_is_error(self, isolated_wd, monkeypatch):
        monkeypatch.setenv("LLM_TEMPERATURE", "not-a-float")
        report = DiagnosticReport()
        _collect_provider(report)
        assert report.ok is False
        assert any(
            f.level == "error" and "LLM_TEMPERATURE" in f.message
            for f in report.findings
        )


class TestCollectPermissions:
    def test_missing_files_are_silent(self, isolated_wd):
        report = DiagnosticReport()
        _collect_permissions(isolated_wd, report)
        assert report.ok is True
        section = report.sections["permissions"]
        assert section["user_status"] == "absent"
        assert section["project_status"] == "absent"

    def test_malformed_user_permissions_is_error(self, isolated_wd):
        ur = Path.home() / ".agentao"
        ur.mkdir(parents=True, exist_ok=True)
        (ur / "permissions.json").write_text("{broken", encoding="utf-8")

        report = DiagnosticReport()
        _collect_permissions(isolated_wd, report)
        assert report.ok is False
        assert report.sections["permissions"]["user_status"] == "malformed"

    def test_project_scope_permissions_warns(self, isolated_wd):
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "permissions.json").write_text(
            json.dumps({"rules": []}), encoding="utf-8",
        )

        report = DiagnosticReport()
        _collect_permissions(isolated_wd, report)
        # Project-scope file is intentionally ignored — that is a warning, not
        # an error, but it must be surfaced so the user knows.
        assert report.ok is True
        assert report.sections["permissions"]["project_status"] == "ignored"
        assert any(
            f.level == "warning" and f.area == "permissions" for f in report.findings
        )


class TestCollectMcp:
    def test_malformed_mcp_is_error(self, isolated_wd):
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "mcp.json").write_text("{not json", encoding="utf-8")

        report = DiagnosticReport()
        _collect_mcp(isolated_wd, report)
        assert report.ok is False
        assert report.sections["mcp"]["project_status"] == "malformed"

    def test_servers_must_be_object(self, isolated_wd):
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "mcp.json").write_text(
            json.dumps({"mcpServers": [1, 2, 3]}), encoding="utf-8",
        )

        report = DiagnosticReport()
        _collect_mcp(isolated_wd, report)
        assert report.ok is False
        assert report.sections["mcp"]["project_status"] == "malformed"

    def test_individual_server_entry_must_be_object(self, isolated_wd):
        # Regression: previously this passed as "ok" but would crash the
        # runtime loader's _expand_config_env(dict(config)) on the string.
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "mcp.json").write_text(
            json.dumps({"mcpServers": {"bad": "oops", "good": {"command": "x"}}}),
            encoding="utf-8",
        )

        report = DiagnosticReport()
        _collect_mcp(isolated_wd, report)
        assert report.ok is False
        assert report.sections["mcp"]["project_status"] == "malformed"
        err = next(f for f in report.findings if f.level == "error" and f.area == "mcp")
        assert "'bad'" in err.message

    def test_nested_env_must_be_strings(self, isolated_wd):
        # `expand_env_vars` will TypeError on a non-string; the validator
        # must catch this before the runtime loader does.
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "mcp.json").write_text(
            json.dumps({
                "mcpServers": {
                    "srv": {"command": "x", "env": {"PORT": 3000}},
                }
            }),
            encoding="utf-8",
        )
        report = DiagnosticReport()
        _collect_mcp(isolated_wd, report)
        assert report.ok is False
        assert any(
            "'env'" in f.message and "values must be strings" in f.message
            for f in report.findings
        )

    def test_nested_args_must_be_list_of_strings(self, isolated_wd):
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "mcp.json").write_text(
            json.dumps({"mcpServers": {"srv": {"command": "x", "args": ["ok", 5]}}}),
            encoding="utf-8",
        )
        report = DiagnosticReport()
        _collect_mcp(isolated_wd, report)
        assert report.ok is False
        assert any("'args'" in f.message for f in report.findings)

    def test_warns_on_user_project_collision(self, isolated_wd):
        # Runtime drops the project entry silently — validator must warn so
        # the user knows their project entry is dead.
        ur = Path.home() / ".agentao"
        ur.mkdir(parents=True, exist_ok=True)
        (ur / "mcp.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "user-bin"}}}),
            encoding="utf-8",
        )
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "mcp.json").write_text(
            json.dumps({
                "mcpServers": {
                    "shared": {"command": "project-bin"},
                    "only-project": {"command": "x"},
                }
            }),
            encoding="utf-8",
        )

        report = DiagnosticReport()
        _collect_mcp(isolated_wd, report)
        # Both files are still individually well-formed — only the collision
        # is flagged, and as a warning (does not flip the error gate).
        assert report.ok is True
        assert report.sections["mcp"]["shadowed_project_servers"] == ["shared"]
        warn = next(
            f for f in report.findings
            if f.level == "warning" and f.area == "mcp"
        )
        assert "'shared'" in warn.message
        # `only-project` is unique to the project file; it must NOT appear in
        # the shadow list.
        assert "only-project" not in warn.message


class TestCollectReplay:
    def test_default_replay_off(self, isolated_wd):
        report = DiagnosticReport()
        _collect_replay(isolated_wd, report, settings_data=None)
        section = report.sections["replay"]
        assert section["status"] == "ok"
        assert section["enabled"] is False

    def test_passes_through_settings_data(self, isolated_wd):
        # _collect_replay must read the dict it was handed, not re-open
        # settings.json — that's the whole point of threading it through.
        report = DiagnosticReport()
        _collect_replay(
            isolated_wd,
            report,
            settings_data={"replay": {"enabled": True, "max_instances": 5}},
        )
        section = report.sections["replay"]
        assert section["enabled"] is True
        assert section["max_instances"] == 5

    def test_non_object_replay_is_error(self, isolated_wd):
        # ReplayConfig.from_mapping coerces this to defaults silently; the
        # validator must catch it so users notice their settings are ignored.
        report = DiagnosticReport()
        _collect_replay(isolated_wd, report, settings_data={"replay": "on"})
        assert report.ok is False
        err = next(f for f in report.findings if f.level == "error")
        assert "must be an object" in err.message

    def test_bad_max_instances_is_error(self, isolated_wd):
        report = DiagnosticReport()
        _collect_replay(
            isolated_wd, report,
            settings_data={"replay": {"max_instances": "many"}},
        )
        assert report.ok is False
        assert any("max_instances" in f.message for f in report.findings)

    def test_non_positive_max_instances_is_error(self, isolated_wd):
        # `int(0)` succeeds, but from_mapping silently discards the value
        # and uses the default. Validate must flag rather than let the
        # user think their (no-op) setting applied.
        report = DiagnosticReport()
        _collect_replay(
            isolated_wd, report,
            settings_data={"replay": {"max_instances": 0}},
        )
        assert report.ok is False
        err = next(f for f in report.findings if "max_instances" in f.message)
        assert ">= 1" in err.message

    def test_non_bool_capture_flag_is_error(self, isolated_wd):
        report = DiagnosticReport()
        _collect_replay(
            isolated_wd, report,
            settings_data={
                "replay": {"capture_flags": {"capture_llm_delta": 42}},
            },
        )
        assert report.ok is False
        assert any("capture_llm_delta" in f.message for f in report.findings)

    def test_unknown_capture_flag_is_warning(self, isolated_wd):
        report = DiagnosticReport()
        _collect_replay(
            isolated_wd, report,
            settings_data={
                "replay": {"capture_flags": {"unknown_flag": True}},
            },
        )
        # Unknown keys are silently dropped at runtime, so the runtime is OK
        # — but validate should still warn the user that the key is dead.
        assert report.ok is True
        assert any(
            f.level == "warning" and "unknown_flag" in f.message
            for f in report.findings
        )


# ----------------------------------------------------------------------
# End-to-end command behavior
# ----------------------------------------------------------------------


class TestCollectMemoryStores:
    def test_probe_does_not_create_files(self, isolated_wd):
        """Doctor must be read-only — sqlite3.connect on an absent path
        silently creates an empty file. A previous version did exactly that,
        bootstrapping a memory DB just by running ``agentao doctor``."""
        from agentao.cli.diagnostics_cli import _collect_memory_stores

        report = DiagnosticReport()
        _collect_memory_stores(isolated_wd, report)

        section = report.sections["memory"]
        assert section["project_status"] == "absent"
        assert not (isolated_wd / ".agentao" / "memory.db").exists()
        assert not (isolated_wd / ".agentao").exists()  # parent dir untouched too

    def test_probe_reports_ok_when_db_exists(self, isolated_wd):
        import sqlite3
        from agentao.cli.diagnostics_cli import _collect_memory_stores

        (isolated_wd / ".agentao").mkdir()
        db = isolated_wd / ".agentao" / "memory.db"
        sqlite3.connect(str(db)).close()

        report = DiagnosticReport()
        _collect_memory_stores(isolated_wd, report)
        assert report.sections["memory"]["project_status"] == "ok"


class TestHandleDoctor:
    def test_json_output_is_well_formed(self, isolated_wd, capsys):
        try:
            handle_doctor_subcommand(_ns(json_output=True))
        except SystemExit:
            # Non-zero exit when a real error is detected. We do not assert
            # the exit code here — the *shape* of the output is the contract.
            pass
        out = capsys.readouterr().out
        doc = json.loads(out)
        assert "ok" in doc
        assert "sections" in doc
        assert "findings" in doc
        assert "provider" in doc["sections"]
        assert "permissions" in doc["sections"]
        assert "mcp" in doc["sections"]
        assert "replay" in doc["sections"]
        assert "acp_schema" in doc["sections"]
        assert "memory" in doc["sections"]
        assert "plugins" in doc["sections"]
        assert "optional_deps" in doc["sections"]

    def test_human_output_includes_section_headers(self, isolated_wd, capsys):
        try:
            handle_doctor_subcommand(_ns(json_output=False))
        except SystemExit:
            pass
        out = capsys.readouterr().out
        # Rich strips style tags so plain text remains.
        assert "agentao doctor" in out
        assert "LLM provider" in out
        assert "Permissions" in out
        assert "Replay" in out

    def test_error_exit_when_finding_is_error(self, isolated_wd):
        # Malformed settings.json → error → non-zero exit.
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "settings.json").write_text("{broken", encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            handle_doctor_subcommand(_ns(json_output=True))
        assert exc.value.code == 1

    def test_warnings_do_not_flip_exit(self, isolated_wd):
        # No API key is a warning — must not exit non-zero.
        handle_doctor_subcommand(_ns(json_output=True))


class TestDotenvLoading:
    def test_doctor_reads_api_key_from_dotenv(self, isolated_wd, capsys, monkeypatch):
        """When the user runs `agentao init`, the API key lands in `.env`;
        the diagnostics handlers must honor that the same way the factory
        does, or they will falsely warn about a missing key right after
        setup."""
        # Override LLM_PROVIDER so we know which prefix to write.
        monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
        (isolated_wd / ".env").write_text(
            "OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8"
        )

        handle_doctor_subcommand(_ns(json_output=True))
        doc = json.loads(capsys.readouterr().out)
        # The key should be detected as present, and the missing-key warning
        # must NOT appear.
        assert doc["sections"]["provider"]["api_key_present"] is True
        assert not any(
            "API_KEY is not set" in f.get("message", "") for f in doc["findings"]
        )
        # Value itself never leaks into the output.
        assert "sk-from-dotenv" not in json.dumps(doc)


class TestHandleConfigValidate:
    def test_clean_directory_no_errors(self, isolated_wd, capsys):
        # No config files at all → no errors, but a missing-API-key warning.
        handle_config_validate_subcommand(_ns(json_output=True))
        out = capsys.readouterr().out
        doc = json.loads(out)
        assert doc["ok"] is True
        # Errors gate exit; warnings do not.
        assert not any(f["level"] == "error" for f in doc["findings"])

    def test_malformed_settings_exits_nonzero(self, isolated_wd):
        (isolated_wd / ".agentao").mkdir()
        (isolated_wd / ".agentao" / "settings.json").write_text("{broken", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            handle_config_validate_subcommand(_ns(json_output=True))
        assert exc.value.code == 1

    def test_validate_does_not_include_plugin_section(self, isolated_wd, capsys):
        # config validate is configuration-only; plugin diagnostics live in
        # `agentao doctor` and `agentao plugin list`.
        handle_config_validate_subcommand(_ns(json_output=True))
        out = capsys.readouterr().out
        doc = json.loads(out)
        assert "plugins" not in doc["sections"]


class TestRejectUnknownArgs:
    """``agentao doctor`` / ``agentao config`` are automation-oriented; a typo
    like ``--jsno`` must fail loudly the way ``agentao run`` does, rather than
    parse_known_args silently dropping it and exit-0'ing on the wrong flag."""

    def _run(self, monkeypatch, argv):
        import sys as _sys
        from agentao import cli
        monkeypatch.setattr(_sys, "argv", argv)
        # Stub the actual handlers so this test only asserts dispatch.
        monkeypatch.setattr(
            cli, "handle_doctor_subcommand",
            lambda args: pytest.fail("handler should not run with bad args"),
            raising=False,
        )
        monkeypatch.setattr(
            cli, "handle_config_subcommand",
            lambda args: pytest.fail("handler should not run with bad args"),
            raising=False,
        )
        with pytest.raises(SystemExit) as exc:
            cli.entrypoint()
        return exc.value.code

    def test_doctor_typo_exits_two(self, monkeypatch, capsys):
        code = self._run(monkeypatch, ["agentao", "doctor", "--jsno"])
        assert code == 2
        err = capsys.readouterr().err
        assert "agentao doctor: unrecognized arguments" in err
        assert "--jsno" in err

    def test_config_unknown_flag_exits_two(self, monkeypatch, capsys):
        code = self._run(monkeypatch, ["agentao", "config", "validate", "--bogus"])
        assert code == 2
        err = capsys.readouterr().err
        assert "agentao config: unrecognized arguments" in err
        assert "--bogus" in err

    def test_doctor_with_only_known_flag_dispatches(self, monkeypatch):
        """Sanity: a clean invocation still reaches the handler (i.e. the
        extras gate isn't accidentally rejecting valid input)."""
        import sys as _sys
        from agentao import cli
        called: Dict[str, bool] = {}
        monkeypatch.setattr(
            cli, "handle_doctor_subcommand",
            lambda args: called.setdefault("ran", True),
            raising=False,
        )
        monkeypatch.setattr(_sys, "argv", ["agentao", "doctor", "--json"])
        cli.entrypoint()
        assert called.get("ran") is True
