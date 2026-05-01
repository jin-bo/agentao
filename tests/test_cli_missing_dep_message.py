"""P0.10 regression: friendly missing-dep error from the ``agentao`` CLI.

When P0.9 demoted ``rich`` / ``prompt_toolkit`` / ``readchar`` /
``pygments`` to the ``[cli]`` extra, a 0.3.x → 0.4.0 user who runs
``pip install -U agentao && agentao`` would otherwise hit an opaque
``ModuleNotFoundError: rich``. P0.10 wraps the first heavy import in
``agentao.cli.entrypoint`` so the same path produces a one-line
actionable error pointing at ``pip install 'agentao[cli]'`` and exits 2.

Two invariants:

1. In a core-only venv, running ``agentao`` exits with code 2 and the
   message names the missing package + the install command.
2. In the same venv after ``pip install '<wheel>[cli]'``,
   ``agentao --help`` boots and exits 0.

Slow-marked because both paths spin up subprocess venvs.

Run with::

    uv build && uv run pytest tests/test_cli_missing_dep_message.py -m slow
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"


def _find_wheel() -> Path | None:
    if not DIST_DIR.is_dir():
        return None
    wheels = sorted(DIST_DIR.glob("agentao-*.whl"))
    return wheels[-1] if wheels else None


def _make_venv(tmp_path: Path) -> tuple[Path, Path]:
    """Return (python_path, agentao_script_path) for a fresh venv."""
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    py = bin_dir / ("python.exe" if os.name == "nt" else "python")
    agentao = bin_dir / ("agentao.exe" if os.name == "nt" else "agentao")
    return py, agentao


pytestmark = [pytest.mark.slow]


@pytest.mark.skipif(
    _find_wheel() is None,
    reason="no built wheel at dist/agentao-*.whl — run `uv build` first",
)
def test_core_only_cli_prints_friendly_missing_dep(tmp_path: Path) -> None:
    """Core-only ``agentao`` exits 2 with the §9.10 friendly message."""
    wheel = _find_wheel()
    assert wheel is not None

    py, agentao = _make_venv(tmp_path)
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    proc = subprocess.run(
        [str(agentao)],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 2, (
        f"expected exit code 2 (friendly missing-dep), got {proc.returncode}.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # The message must name the actual missing package (the first one
    # the import system trips over) and show both install paths.
    assert "agentao CLI requires extra packages" in proc.stderr
    assert "pip install 'agentao[cli]'" in proc.stderr
    assert "pip install 'agentao[full]'" in proc.stderr
    # No rich traceback should leak through — the shim catches it.
    assert "Traceback" not in proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr


@pytest.mark.skipif(
    _find_wheel() is None,
    reason="no built wheel at dist/agentao-*.whl — run `uv build` first",
)
def test_cli_extra_makes_agentao_help_work(tmp_path: Path) -> None:
    """After installing the ``[cli]`` extra, ``agentao --help`` boots."""
    wheel = _find_wheel()
    assert wheel is not None

    py, agentao = _make_venv(tmp_path)
    # Layer: bare install first, then [cli] extra — mirrors the
    # documented 0.3.x → 0.4.0 migration step.
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", f"{wheel}[cli]"],
        check=True,
        capture_output=True,
        text=True,
    )

    proc = subprocess.run(
        [str(agentao), "--help"],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, (
        f"agentao --help failed after [cli] install:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "usage: agentao" in proc.stdout


@pytest.mark.skipif(
    _find_wheel() is None,
    reason="no built wheel at dist/agentao-*.whl — run `uv build` first",
)
def test_core_only_can_import_agentao_cli_entrypoint(tmp_path: Path) -> None:
    """``from agentao.cli import entrypoint`` resolves in core-only.

    The shim only fires when ``entrypoint()`` is *called*. Importing
    the symbol must not trip rich / prompt_toolkit / readchar /
    pygments — that is the precondition that makes the friendly
    message reachable in the first place.
    """
    wheel = _find_wheel()
    assert wheel is not None

    py, _ = _make_venv(tmp_path)
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    proc = subprocess.run(
        [
            str(py),
            "-c",
            "from agentao.cli import entrypoint; print('import OK')",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"`from agentao.cli import entrypoint` failed in core-only venv:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "import OK" in proc.stdout
