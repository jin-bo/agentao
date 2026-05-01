"""Friendly missing-dep message from the ``agentao`` CLI in core-only installs.

Three slow-marked tests against a built wheel:

- core-only: ``agentao`` exits 2 with the named-package + install-line message
- core-only + ``[cli]``: ``agentao --help`` boots and exits 0
- core-only: ``from agentao.cli import entrypoint`` resolves without
  tripping rich/prompt_toolkit (precondition for the friendly path)

Run with::

    uv build && uv run pytest tests/test_cli_missing_dep_message.py -m slow
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.support.wheel import make_venv, require_wheel


pytestmark = [pytest.mark.slow]


def test_core_only_cli_prints_friendly_missing_dep(tmp_path: Path) -> None:
    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(str(wheel))

    proc = subprocess.run([str(venv.agentao_script)], capture_output=True, text=True)

    assert proc.returncode == 2, (
        f"expected exit code 2, got {proc.returncode}.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "agentao CLI requires extra packages" in proc.stderr
    assert "pip install 'agentao[cli]'" in proc.stderr
    assert "pip install 'agentao[full]'" in proc.stderr
    # The shim must catch the ImportError before the traceback escapes —
    # otherwise the user sees the opaque ModuleNotFoundError we are here to hide.
    assert "Traceback" not in proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr


def test_cli_extra_makes_agentao_help_work(tmp_path: Path) -> None:
    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(f"{wheel}[cli]")

    proc = subprocess.run(
        [str(venv.agentao_script), "--help"],
        capture_output=True, text=True,
    )

    assert proc.returncode == 0, (
        f"agentao --help failed after [cli] install:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "usage: agentao" in proc.stdout


def test_core_only_can_import_agentao_cli_entrypoint(tmp_path: Path) -> None:
    """The shim only fires on call; the import itself must stay light."""
    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(str(wheel))

    proc = subprocess.run(
        [str(venv.python), "-c", "from agentao.cli import entrypoint; print('import OK')"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"`from agentao.cli import entrypoint` failed in core-only venv:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "import OK" in proc.stdout
