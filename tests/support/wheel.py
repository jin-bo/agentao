"""Shared helpers for tests that install the built wheel into a fresh venv.

Three slow-marked test files exercise the post-build install path:
``tests/test_clean_install_smoke.py``, ``tests/test_dependency_split.py``,
and ``tests/test_cli_missing_dep_message.py``. They all need:

- the path to ``dist/agentao-*.whl`` (skip if absent — tests run before
  ``uv build`` should not fail, just skip)
- a fresh venv with stdlib ``venv`` so each test starts from a clean
  dependency closure.

These helpers centralize that pattern so each new install-shape test
adds one assertion, not a boilerplate harness.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from packaging.version import InvalidVersion, Version


REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "dist"


def _wheel_version(wheel: Path) -> Version | None:
    """The version segment of a wheel filename, or None if it does not parse."""
    try:
        return Version(wheel.name.split("-")[1])
    except (IndexError, InvalidVersion):
        return None


def find_wheel() -> Path | None:
    """The newest built wheel in ``dist/`` **by version**, not by filename.

    ``sorted()`` on the name puts ``0.4.10`` *before* ``0.4.9``, so a ``dist/``
    holding both — which is what an unclean local tree looks like, since
    ``uv build`` adds rather than replaces — hands every install test the older
    wheel and then reports a pass for it. CI builds into an empty directory and
    would never have shown this.
    """
    if not DIST_DIR.is_dir():
        return None
    versioned = [(v, w) for w in DIST_DIR.glob("agentao-*.whl") if (v := _wheel_version(w))]
    if not versioned:
        return None
    return max(versioned, key=lambda pair: pair[0])[1]


def require_wheel() -> Path:
    """Return the wheel path or skip the calling test if none is built."""
    wheel = find_wheel()
    if wheel is None:
        pytest.skip("no built wheel at dist/agentao-*.whl — run `uv build` first")
    return wheel


@dataclass(frozen=True)
class FreshVenv:
    """A throwaway venv created by stdlib ``venv``.

    Tests that mutate the venv (layering ``[cli]`` / ``[full]``) get
    their own; tests that only read can share via a module-scoped
    fixture if needed.
    """

    root: Path
    python: Path
    agentao_script: Path

    def pip_install(self, *specs: str) -> None:
        subprocess.run(
            [str(self.python), "-m", "pip", "install", "--quiet", *specs],
            check=True,
            capture_output=True,
            text=True,
        )


def make_venv(tmp_path: Path) -> FreshVenv:
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    py_name = "python.exe" if os.name == "nt" else "python"
    script_name = "agentao.exe" if os.name == "nt" else "agentao"
    return FreshVenv(
        root=venv_dir,
        python=bin_dir / py_name,
        agentao_script=bin_dir / script_name,
    )
