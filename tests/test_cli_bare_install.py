"""``agentao --acp`` and ``agentao --login`` without the ``[cli]`` extras.

An ACP Registry client launches Agentao as ``uvx agentao@<version> --acp``
and logs in with ``--login``: a bare install, no ``rich`` or
``prompt_toolkit``. Each test runs the real console entry point in a
subprocess where those packages cannot be imported, so a module on that path
that grows a ``rich`` import fails here rather than in a user's IDE. The
built-wheel version of the same check is in ``test_cli_missing_dep_message``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Blocks the [cli] extras for the child: ``import rich`` raises ImportError.
_BLOCK = textwrap.dedent("""\
    import sys
    for name in ("rich", "prompt_toolkit", "readchar", "pygments"):
        sys.modules[name] = None
    sys.argv = ["agentao", *sys.argv[1:]]
    from agentao.cli import entrypoint
    entrypoint()
""")


def _run(args, tmp_path, stdin=""):
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("OPENAI_", "ANTHROPIC_", "LLM_", "DEEPSEEK_", "GEMINI_"))
    }
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONPATH=str(REPO_ROOT))
    return subprocess.run(
        [sys.executable, "-c", _BLOCK, *args], input=stdin, capture_output=True,
        text=True, cwd=tmp_path, env=env, timeout=60,
    )


def test_login_runs_and_saves_without_cli_extras(tmp_path):
    # provider 2 (DEEPSEEK), key, default URL, default model, default wire.
    proc = _run(["--login"], tmp_path, stdin="2\nsk-bare\n\n\n\n")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    path = tmp_path / ".agentao" / "llm.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["provider"] == "DEEPSEEK" and saved["api_key"] == "sk-bare"
    assert saved["base_url"] == "https://api.deepseek.com/v1"
    assert saved["model"] == "deepseek-chat"
    assert saved["api_format"] == "openai-completions"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Rich markup is stripped, not printed literally.
    assert "Saved." in proc.stdout and "[success]" not in proc.stdout


def test_login_end_of_input_fails_without_cli_extras(tmp_path):
    proc = _run(["--login"], tmp_path, stdin="")

    assert proc.returncode == 1
    assert "end of input" in proc.stdout
    assert "Traceback" not in proc.stderr
    assert not (tmp_path / ".agentao" / "llm.json").exists()


def test_acp_is_dispatched_without_cli_extras(tmp_path):
    # A bad flag stops before the server starts, so this proves the routing
    # alone: without the light path the extras check would exit 2 with the
    # "requires extra packages" message instead.
    proc = _run(["--acp", "--bogus"], tmp_path)

    assert proc.returncode == 2
    assert "unrecognized arguments: --bogus" in proc.stderr
    assert "requires extra packages" not in proc.stderr


def test_acp_server_answers_initialize_without_cli_extras(tmp_path):
    proc = _run(
        ["--acp"], tmp_path,
        stdin=json.dumps({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": 1, "clientCapabilities": {"auth": {"terminal": True}}},
        }) + "\n",
    )

    reply = json.loads(proc.stdout.splitlines()[0])
    assert reply["id"] == 0
    assert [m["id"] for m in reply["result"]["authMethods"]] == ["agentao-login"]
    assert "Traceback" not in proc.stderr


def test_other_commands_still_require_the_extras(tmp_path):
    # ``--acp`` present but not as the mode: after a subcommand, or where it
    # does not parse at all. Both reach the extras check as before.
    for args in (["plugin", "list", "--acp"], ["run", "--prompt", "--acp"]):
        proc = _run(args, tmp_path)

        assert proc.returncode == 2, args
        assert "requires extra packages" in proc.stderr, args


def test_a_hidden_answer_is_read_from_stdin_when_it_is_not_a_terminal(monkeypatch):
    # getpass reads /dev/tty (or the Windows console), never stdin: with
    # stdin piped from a client, a hidden prompt must not wait on a terminal.
    import io

    from agentao.cli import _plain_tty

    def no_terminal(*_a, **_k):
        raise AssertionError("getpass would read the terminal, not the piped stdin")

    monkeypatch.setattr(_plain_tty.getpass, "getpass", no_terminal)
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-piped\n"))

    assert _plain_tty.Prompt.ask("KEY", password=True) == "sk-piped"
