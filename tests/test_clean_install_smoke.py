"""P0.7 regression: a freshly-installed wheel must construct ``Agentao``.

Local mirror of the CI ``smoke`` job. The CI side runs after ``build``
in a separate venv on every PR; this in-tree test exists so a
maintainer can rehearse the same check locally without pushing to CI.

It is gated:

- skipped if no built wheel exists at ``dist/agentao-*.whl`` (CI builds
  the wheel as a separate job, so the artifact is only present when
  invoked alongside ``uv build``)
- skipped on platforms where ``venv`` cannot be spawned in a sandbox
- marked ``slow`` so the default test run does not pay the install cost

Run with::

    uv build && uv run pytest tests/test_clean_install_smoke.py -m slow
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"


def _find_wheel() -> Path | None:
    if not DIST_DIR.is_dir():
        return None
    wheels = sorted(DIST_DIR.glob("agentao-*.whl"))
    return wheels[-1] if wheels else None


pytestmark = [pytest.mark.slow]


@pytest.mark.skipif(
    _find_wheel() is None,
    reason="no built wheel at dist/agentao-*.whl — run `uv build` first",
)
def test_wheel_install_and_embed_construct(tmp_path: Path) -> None:
    """``pip install dist/*.whl`` followed by an embed-only construction works.

    Mirror of the §9.3 / CI ``smoke`` job. The CLI dependency closure is
    intentionally pulled in here (the wheel still bundles those deps in
    0.3.x); P0.9 will split them out into ``[cli]`` and the test
    invocation will switch to ``pip install dist/*.whl`` (no extras).
    """
    wheel = _find_wheel()
    assert wheel is not None  # narrowed by the skipif

    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )

    if os.name == "nt":
        py = venv_dir / "Scripts" / "python.exe"
    else:
        py = venv_dir / "bin" / "python"

    # Install the wheel into the new venv. ``--upgrade-pip`` is omitted
    # deliberately — the canonical embedded-host install is the same
    # ``pip install agentao`` command they would type, no warmups.
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Run the README "Embed in 30 lines" snippet against the installed
    # wheel; deliberately unroutable creds keep the test offline.
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
        ")\n"
        "try:\n"
        "    print('Embedded construct OK')\n"
        "finally:\n"
        "    agent.close()\n"
    )
    proc = subprocess.run(
        [str(py), "-c", snippet],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "Embedded construct failed against the installed wheel:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "Embedded construct OK" in proc.stdout


@pytest.mark.skipif(
    _find_wheel() is None,
    reason="no built wheel at dist/agentao-*.whl — run `uv build` first",
)
def test_wheel_ships_py_typed_marker(tmp_path: Path) -> None:
    """The installed wheel must carry ``py.typed`` (PEP 561, P0.1)."""
    wheel = _find_wheel()
    assert wheel is not None

    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    py = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
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
            (
                "from importlib.resources import files; "
                "assert files('agentao').joinpath('py.typed').is_file(); "
                "print('py.typed OK')"
            ),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"py.typed missing from installed wheel:\nstderr:\n{proc.stderr}"
    )
    assert "py.typed OK" in proc.stdout
