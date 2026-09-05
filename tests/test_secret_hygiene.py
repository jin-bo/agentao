"""Secrets must not be written to disk, and must not ride into children.

Two independent leaks, same root cause — agentao was more generous with
its own credentials than it needed to be:

1. The 10-pattern secret scanner existed but was reachable only through
   the replay recorder, which is **off by default**. Everything agentao
   writes to disk unconditionally — ``agentao.log`` and
   ``.agentao/tool-outputs/`` — went out unscanned.
2. Every shell and MCP child inherited the full parent environment,
   including the provider key that pays for the LLM. A prompt-injected
   ``run_shell_command("env")`` needed no exotic technique.

Deliberate non-goal: the tool result handed to the *model* is left
verbatim. Pattern matching cannot tell a live credential from a test
fixture, and mangling ``sk-test-…`` in a file the agent is editing breaks
work it can neither see nor fix.
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

from agentao.capabilities.process import HARNESS_ENV_KEYS, build_child_env
from agentao.security.secret_scan import redact, scan_and_redact


# A syntactically valid, obviously fake OpenAI-shaped key.
FAKE_KEY = "sk-proj-" + "A1b2C3d4E5f6G7h8J9k0" * 2


def _reads_key() -> list:
    """A child that prints the key it inherited, without asking a shell to expand anything.

    The shell form (``echo "key=[${OPENAI_API_KEY}]"``) reads the variable through the
    platform shell, and ``cmd.exe`` does not expand ``${...}`` — it prints the text back and
    the test then measures its own quoting rather than the scrub. The exec form asks the
    child directly, which is the thing under test on every platform.
    """
    return [
        sys.executable, "-c",
        "import os; print('key=[' + os.environ.get('OPENAI_API_KEY', '') + ']')",
    ]


class TestScanner:
    def test_finds_a_provider_key(self):
        cleaned, hits = scan_and_redact(f"OPENAI_API_KEY={FAKE_KEY}")
        assert FAKE_KEY not in cleaned
        assert hits

    def test_clean_text_is_untouched(self):
        text = "the quick brown fox jumps over the lazy dog"
        cleaned, hits = scan_and_redact(text)
        assert cleaned == text
        assert hits == {}

    def test_short_strings_skip_the_scan(self):
        assert scan_and_redact("ok") == ("ok", {})

    def test_redact_drops_the_counters(self):
        assert FAKE_KEY not in redact(f"key={FAKE_KEY}")

    def test_non_string_passes_through(self):
        value, hits = scan_and_redact(None)  # type: ignore[arg-type]
        assert value is None and hits == {}

    def test_replay_re_export_is_the_same_function(self):
        """Existing replay callers must keep working after the move."""
        from agentao.replay.redact import scan_and_redact as replay_scan

        assert replay_scan is scan_and_redact


class TestLogRedaction:
    def test_credentials_do_not_reach_the_log_file(self, tmp_path):
        from agentao.llm.client import _RedactingFormatter

        log_file = tmp_path / "agentao.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(_RedactingFormatter("%(message)s"))
        logger = logging.getLogger("agentao.test.redaction")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False
        try:
            logger.info("calling provider with OPENAI_API_KEY=%s", FAKE_KEY)
        finally:
            logger.removeHandler(handler)
            handler.close()

        written = log_file.read_text(encoding="utf-8")
        assert FAKE_KEY not in written
        assert "REDACTED" in written

    def test_ordinary_lines_are_unharmed(self, tmp_path):
        from agentao.llm.client import _RedactingFormatter

        log_file = tmp_path / "agentao.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(_RedactingFormatter("%(message)s"))
        logger = logging.getLogger("agentao.test.redaction.clean")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False
        try:
            logger.info("LLM iteration 3/15")
        finally:
            logger.removeHandler(handler)
            handler.close()

        assert log_file.read_text(encoding="utf-8").strip() == "LLM iteration 3/15"

    def test_a_scanner_failure_cannot_break_logging(self, tmp_path, monkeypatch):
        """Logging must degrade to unredacted, never raise into the caller."""
        from agentao.llm import client as client_mod

        def boom(_text):
            raise RuntimeError("scanner exploded")

        monkeypatch.setattr(client_mod, "redact", boom)

        log_file = tmp_path / "agentao.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(client_mod._RedactingFormatter("%(message)s"))
        logger = logging.getLogger("agentao.test.redaction.boom")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False
        try:
            logger.info("still logged")
        finally:
            logger.removeHandler(handler)
            handler.close()

        assert "still logged" in log_file.read_text(encoding="utf-8")


class TestToolOutputSpill:
    def test_saved_output_is_redacted_but_the_excerpt_is_not(self, tmp_path, monkeypatch):
        """Disk gets scrubbed; the model's copy stays verbatim on purpose."""
        from agentao.runtime import tool_result_formatter as trf

        monkeypatch.setattr(trf, "_TOOL_OUTPUT_DIR", tmp_path / "tool-outputs")

        content = f"line\nOPENAI_API_KEY={FAKE_KEY}\n" + ("filler\n" * 20000)
        excerpt, disk_path = trf._save_and_truncate(content, "run_shell_command", None)

        assert disk_path is not None
        on_disk = (tmp_path / "tool-outputs").glob("*.txt")
        written = next(on_disk).read_text(encoding="utf-8")
        assert FAKE_KEY not in written
        assert "REDACTED" in written

        # The in-context excerpt is intentionally NOT redacted — see module
        # docstring. Head ratio keeps the front of the output, where the key is.
        assert FAKE_KEY in excerpt


class TestChildEnvironment:
    def test_harness_provider_keys_are_dropped(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        env = build_child_env()
        assert "OPENAI_API_KEY" not in env

    def test_unrelated_variables_survive(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("MY_APP_SETTING", "keep-me")
        env = build_child_env()
        assert env["PATH"] == "/usr/bin"
        assert env["MY_APP_SETTING"] == "keep-me"

    def test_user_secrets_are_not_touched(self):
        """Scrubbing the user's own secrets is the host's call, not ours."""
        env = build_child_env(base={"AWS_SECRET_ACCESS_KEY": "x", "DATABASE_URL": "y"})
        assert env["AWS_SECRET_ACCESS_KEY"] == "x"
        assert env["DATABASE_URL"] == "y"

    def test_explicit_override_beats_the_scrub(self, monkeypatch):
        """An MCP server that genuinely needs a key can still be given one."""
        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        env = build_child_env({"OPENAI_API_KEY": "explicitly-provided"})
        assert env["OPENAI_API_KEY"] == "explicitly-provided"

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE"])
    def test_opt_out_restores_full_inheritance(self, monkeypatch, value):
        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        monkeypatch.setenv("AGENTAO_SCRUB_CHILD_ENV", value)
        assert build_child_env()["OPENAI_API_KEY"] == FAKE_KEY

    def test_unset_opt_out_means_scrubbing_is_on(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        monkeypatch.delenv("AGENTAO_SCRUB_CHILD_ENV", raising=False)
        assert "OPENAI_API_KEY" not in build_child_env()

    def test_every_declared_key_is_actually_dropped(self):
        base = {k: "secret" for k in HARNESS_ENV_KEYS}
        base["KEEP"] = "yes"
        env = build_child_env(base=base)
        assert set(env) == {"KEEP"}


class TestShellChildEnvironment:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell expansion in the probe")
    def test_shell_child_does_not_see_the_provider_key(self, monkeypatch):
        """End-to-end through the tool, which is where the environment is now decided.

        This used to hand the executor a request with no environment and rely on it calling
        ``build_child_env()`` itself. The launch request now carries a complete environment
        that the executor sets verbatim (LAUNCH-01a), so the scrub happens one layer up —
        and the only way to keep proving the guarantee is to drive the layer that does it.
        """
        from agentao.tools.shell import ShellTool

        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        # The project root, not tmp_path: PathPolicy refuses a working directory outside it,
        # and this test is about the environment, not about where the child starts.
        out = ShellTool().execute(
            command='echo "key=[${OPENAI_API_KEY}]"', working_directory=".", timeout=30
        )
        assert "key=[]" in out

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell expansion in the probe")
    def test_the_executor_sets_the_launch_environment_verbatim(self, monkeypatch, tmp_path):
        """LAUNCH-01a: the executor sets the environment it was given, and computes none."""
        from types import MappingProxyType

        from agentao.capabilities.shell import LocalShellExecutor, ShellRequest
        from agentao.capabilities.shell_spec import AbsPath, LegacyLaunch, Sha256

        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        result = LocalShellExecutor().run(
            ShellRequest(
                launch=LegacyLaunch(
                    command='echo "key=[${OPENAI_API_KEY}]"',
                    cwd=AbsPath(str(tmp_path)),
                    env=MappingProxyType(
                        {"OPENAI_API_KEY": "host-chose-this", "PATH": os.environ["PATH"]}
                    ),
                    spec_fingerprint=Sha256(""),
                ),
                timeout=30,
            )
        )
        assert "key=[host-chose-this]" in result.stdout.decode("utf-8", errors="replace")


class TestScrubCoversEveryChildSpawner:
    """A guarantee that holds on one spawn path and not the others is not
    a guarantee — plugin hooks and search both go through run_captured."""

    def test_run_captured_scrubs_by_default(self, monkeypatch, tmp_path):
        from agentao.capabilities.process import run_captured

        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        out = run_captured(_reads_key(), cwd=str(tmp_path))
        assert "key=[]" in out.stdout

    def test_run_captured_honours_an_explicit_env(self, monkeypatch, tmp_path):
        from agentao.capabilities.process import run_captured

        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
        env = {"OPENAI_API_KEY": "caller-chose", "PATH": os.environ["PATH"]}
        # Windows cannot start a Python child without SystemRoot — it fails before
        # `main`, so the assertion below would read an empty stdout and blame the
        # scrub. These are the interpreter's launch prerequisites, not the subject.
        for name in ("SystemRoot", "SYSTEMROOT", "COMSPEC", "PATHEXT"):
            if name in os.environ:
                env[name] = os.environ[name]

        out = run_captured(_reads_key(), cwd=str(tmp_path), env=env)
        assert "key=[caller-chose]" in out.stdout


    def test_acp_server_children_are_scrubbed(self, monkeypatch):
        """An ACP server binary is the same trust position as an MCP one.

        Both are third-party executables spawned from a config file. If
        MCP children lose the provider key and ACP children keep it, the
        scrub is decorative — an attacker just picks the other door.
        """
        import inspect

        from agentao.acp_client import process as acp_process

        src = inspect.getsource(acp_process)
        assert "build_child_env" in src, (
            "acp_client spawns a child without the shared scrubbed base env"
        )
        assert "dict(os.environ)" not in src

    def test_acp_server_env_block_still_wins(self):
        """Explicit config env is applied after the drop, as everywhere else."""
        env = build_child_env(
            {"OPENAI_API_KEY": "declared-in-acp-json"},
            base={"OPENAI_API_KEY": FAKE_KEY},
        )
        assert env["OPENAI_API_KEY"] == "declared-in-acp-json"


class TestProviderPrefixedKeys:
    """`embedding/factory.py` resolves `f"{provider}_API_KEY"`, so a
    hard-coded list gives users on unlisted providers *no* scrubbing —
    while the docs tell them they are covered. Silent non-coverage is
    worse than none."""

    def test_unlisted_provider_key_is_derived_and_stripped(self):
        env = build_child_env(
            base={"LLM_PROVIDER": "qwen", "QWEN_API_KEY": FAKE_KEY, "PATH": "/bin"},
        )
        assert "QWEN_API_KEY" not in env
        assert env["PATH"] == "/bin"
        assert "QWEN_API_KEY" not in HARNESS_ENV_KEYS, (
            "test is vacuous if the key is in the static list"
        )

    def test_derivation_is_case_insensitive(self):
        env = build_child_env(base={"LLM_PROVIDER": "MiStRaL", "MISTRAL_API_KEY": FAKE_KEY})
        assert "MISTRAL_API_KEY" not in env

    def test_unrelated_provider_keys_survive(self):
        """Only the *configured* provider's key is derived — not every
        `*_API_KEY` in the environment, which would scrub the user's own."""
        env = build_child_env(
            base={"LLM_PROVIDER": "qwen", "QWEN_API_KEY": FAKE_KEY,
                  "STRIPE_API_KEY": "user-owned"},
        )
        assert env["STRIPE_API_KEY"] == "user-owned"

    @pytest.mark.parametrize("provider", ["", "   ", "has space", "semi;colon", "$(inject)"])
    def test_malformed_provider_is_ignored(self, provider):
        base = {"LLM_PROVIDER": provider, "PATH": "/bin"}
        env = build_child_env(base=base)
        assert env["PATH"] == "/bin"

    def test_opt_out_also_restores_the_derived_key(self):
        env = build_child_env(
            base={"AGENTAO_SCRUB_CHILD_ENV": "0",
                  "LLM_PROVIDER": "qwen", "QWEN_API_KEY": FAKE_KEY},
        )
        assert env["QWEN_API_KEY"] == FAKE_KEY


class TestScrubDoesNotOverreach:
    def test_google_api_key_is_preserved(self, monkeypatch):
        """GOOGLE_API_KEY is the standard name for Maps/Drive/YouTube too.

        Stripping it breaks user scripts with a 401 that points nowhere
        near agentao. Gemini's own GEMINI_API_KEY is unambiguous.
        """
        monkeypatch.setenv("GOOGLE_API_KEY", "maps-key")
        assert build_child_env()["GOOGLE_API_KEY"] == "maps-key"
        assert "GOOGLE_API_KEY" not in HARNESS_ENV_KEYS

    def test_gemini_key_is_still_stripped(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
        assert "GEMINI_API_KEY" not in build_child_env()


class TestMemoryGuardSharesTheScanner:
    """A weaker private copy of the patterns silently let secrets into
    memory.db, which is then re-injected into every later prompt."""

    @pytest.mark.parametrize("secret", [
        "sk-ant-" + "a" * 45,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27u",
        "AIza" + "B" * 35,
        "xoxb-123456789012-abcdefghijkl",
        "gho_" + "c" * 36,
    ])
    def test_patterns_the_local_copy_missed_are_now_caught(self, secret):
        from agentao.memory.guards import MemoryGuard, SensitiveMemoryError

        with pytest.raises(SensitiveMemoryError):
            MemoryGuard().detect_sensitive(f"the value is {secret}")

    def test_partial_private_key_header_still_detected(self):
        """Detector-only addition: the shared pattern needs a full
        BEGIN..END block because it substitutes; a truncated key pasted
        into a memory should still be refused."""
        from agentao.memory.guards import MemoryGuard, SensitiveMemoryError

        with pytest.raises(SensitiveMemoryError):
            MemoryGuard().detect_sensitive("-----BEGIN RSA PRIVATE KEY-----\nMIIE")

    def test_ordinary_memory_still_saves(self):
        from agentao.memory.guards import MemoryGuard

        MemoryGuard().detect_sensitive("user prefers tabs over spaces")
