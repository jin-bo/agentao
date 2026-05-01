"""P0.9 regression: dependency split into core + extras must hold.

Two invariants are checked:

1. ``pip install agentao[full]`` resolves to the same package closure
   as the 0.3.x bundled-deps default install. ``tests/data/full_extras_baseline.txt``
   captures that closure (frozen on 2026-05-01); a non-empty diff is
   the P0.9 break leaking through.
2. ``pip install agentao`` (core only, no extras) constructs an
   ``Agentao()`` instance offline. This is the §9.9 acceptance
   criterion that proves the "minimum needed to construct an agent"
   contract holds.

Both tests are gated on a built wheel (``dist/agentao-*.whl``) and
marked ``slow`` because they spin up subprocess venvs.

Run with::

    uv build && uv run pytest tests/test_dependency_split.py -m slow
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


def _pep503_normalize(name: str) -> str:
    """PEP 503 — collapse [-_.]+ to '-' and lowercase."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalize_freeze_line(line: str) -> str:
    """Normalize a single ``pip freeze`` line for cross-tool comparison.

    `pip freeze` preserves the package's canonical casing/separators
    while `uv pip freeze` normalizes them. We normalize both sides to
    PEP 503 form so the closure-equivalence check is tool-agnostic.
    """
    if "==" not in line:
        return line
    name, _, ver = line.partition("==")
    return f"{_pep503_normalize(name)}=={ver}"


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
BASELINE = REPO_ROOT / "tests" / "data" / "full_extras_baseline.txt"


def _find_wheel() -> Path | None:
    if not DIST_DIR.is_dir():
        return None
    wheels = sorted(DIST_DIR.glob("agentao-*.whl"))
    return wheels[-1] if wheels else None


def _make_venv(tmp_path: Path) -> Path:
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    return venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"


pytestmark = [pytest.mark.slow]


@pytest.mark.skipif(
    _find_wheel() is None,
    reason="no built wheel at dist/agentao-*.whl — run `uv build` first",
)
def test_full_extras_freeze_matches_baseline(tmp_path: Path) -> None:
    """``[full]`` after P0.9 reproduces the 0.3.x bundled closure exactly."""
    wheel = _find_wheel()
    assert wheel is not None
    assert BASELINE.is_file(), f"baseline missing: {BASELINE}"

    py = _make_venv(tmp_path)
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", f"{wheel}[full]"],
        check=True,
        capture_output=True,
        text=True,
    )
    proc = subprocess.run(
        [str(py), "-m", "pip", "freeze"],
        check=True,
        capture_output=True,
        text=True,
    )
    # Drop the agentao line itself — it varies by install context
    # (PyPI version pin vs file:// path) and is not part of the
    # closure-equivalence check.
    actual = sorted(
        _normalize_freeze_line(line)
        for line in proc.stdout.splitlines()
        if line and not line.startswith("agentao @ file") and not line.startswith("agentao==")
    )
    expected = sorted(_normalize_freeze_line(line) for line in BASELINE.read_text().splitlines())

    if actual != expected:
        # Build a focused diff message for the failure path.
        only_actual = sorted(set(actual) - set(expected))
        only_expected = sorted(set(expected) - set(actual))
        raise AssertionError(
            "[full] closure drifted from baseline.\n"
            f"  packages only in [full]: {only_actual}\n"
            f"  packages only in baseline: {only_expected}\n"
            "  Update tests/data/full_extras_baseline.txt only if the "
            "drift is intentional (e.g. a new extra added)."
        )


@pytest.mark.skipif(
    _find_wheel() is None,
    reason="no built wheel at dist/agentao-*.whl — run `uv build` first",
)
def test_core_install_constructs_agentao(tmp_path: Path) -> None:
    """Bare ``pip install agentao`` (no extras) constructs an Agentao()."""
    wheel = _find_wheel()
    assert wheel is not None

    py = _make_venv(tmp_path)
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    snippet = (
        "import tempfile\n"
        "from pathlib import Path\n"
        "from agentao import Agentao\n"
        "from agentao.llm import LLMClient\n"
        "from agentao.transport import NullTransport\n"
        "agent = Agentao(\n"
        "    working_directory=Path(tempfile.mkdtemp()),\n"
        "    llm_client=LLMClient(api_key='dummy', base_url='http://localhost:1', model='dummy'),\n"
        "    transport=NullTransport(),\n"
        "    project_instructions='hi',\n"
        ")\n"
        "try:\n"
        "    print('Core-only construct OK')\n"
        "finally:\n"
        "    agent.close()\n"
    )
    proc = subprocess.run(
        [str(py), "-c", snippet],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "Core-only construct failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "Core-only construct OK" in proc.stdout


@pytest.mark.skipif(
    _find_wheel() is None,
    reason="no built wheel at dist/agentao-*.whl — run `uv build` first",
)
def test_core_install_omits_cli_web_i18n(tmp_path: Path) -> None:
    """Bare ``pip install agentao`` must NOT pull rich/bs4/jieba.

    These were demoted to ``[cli]`` / ``[web]`` / ``[i18n]`` extras in
    P0.9. If they reappear in a core-only freeze, the dependency split
    has regressed.
    """
    wheel = _find_wheel()
    assert wheel is not None

    py = _make_venv(tmp_path)
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    proc = subprocess.run(
        [str(py), "-m", "pip", "freeze"],
        check=True,
        capture_output=True,
        text=True,
    )
    installed = {line.split("==")[0].lower() for line in proc.stdout.splitlines() if "==" in line}
    forbidden = {"rich", "beautifulsoup4", "jieba", "prompt-toolkit", "readchar", "pygments"}
    leaked = forbidden & installed
    assert not leaked, (
        f"core install pulled in extras-only packages: {sorted(leaked)}"
    )
