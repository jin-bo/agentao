"""Closure-equivalence and core-only invariants for the 0.4.0 dep split.

Three slow-marked tests against a built wheel:

- ``[full]`` reproduces the 0.3.x bundled closure exactly (122 packages,
  baseline frozen 2026-05-01 in ``tests/data/full_extras_baseline.txt``).
- bare ``pip install agentao`` constructs an ``Agentao()`` offline.
- bare install does NOT pull rich/bs4/jieba/prompt-toolkit/readchar/pygments
  — they live in ``[cli]`` / ``[web]`` / ``[i18n]``.

Run with::

    uv build && uv run pytest tests/test_dependency_split.py -m slow
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from packaging.utils import canonicalize_name

from tests.support.wheel import REPO_ROOT, make_venv, require_wheel


BASELINE = REPO_ROOT / "tests" / "data" / "full_extras_baseline.txt"


def _normalize_freeze_line(line: str) -> str:
    """Apply PEP 503 to the package name so pip vs uv freeze output compares."""
    if "==" not in line:
        return line
    name, _, ver = line.partition("==")
    return f"{canonicalize_name(name)}=={ver}"


pytestmark = [pytest.mark.slow]


def test_full_extras_freeze_matches_baseline(tmp_path: Path) -> None:
    wheel = require_wheel()
    assert BASELINE.is_file(), f"baseline missing: {BASELINE}"

    venv = make_venv(tmp_path)
    venv.pip_install(f"{wheel}[full]")

    proc = subprocess.run(
        [str(venv.python), "-m", "pip", "freeze"],
        check=True, capture_output=True, text=True,
    )
    actual = sorted(
        _normalize_freeze_line(line)
        for line in proc.stdout.splitlines()
        if line and not line.startswith(("agentao @ file", "agentao=="))
    )
    expected = sorted(_normalize_freeze_line(line) for line in BASELINE.read_text().splitlines())

    if actual != expected:
        only_actual = sorted(set(actual) - set(expected))
        only_expected = sorted(set(expected) - set(actual))
        raise AssertionError(
            "[full] closure drifted from baseline.\n"
            f"  packages only in [full]: {only_actual}\n"
            f"  packages only in baseline: {only_expected}\n"
            "  Update tests/data/full_extras_baseline.txt only if the "
            "drift is intentional (e.g. a new extra added)."
        )


def test_core_install_constructs_agentao(tmp_path: Path) -> None:
    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(str(wheel))

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
        [str(venv.python), "-c", snippet],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"Core-only construct failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "Core-only construct OK" in proc.stdout


def test_core_install_omits_cli_web_i18n(tmp_path: Path) -> None:
    """Bare install must not pull rich / bs4 / jieba / prompt-toolkit / readchar / pygments."""
    wheel = require_wheel()
    venv = make_venv(tmp_path)
    venv.pip_install(str(wheel))

    proc = subprocess.run(
        [str(venv.python), "-m", "pip", "freeze"],
        check=True, capture_output=True, text=True,
    )
    installed = {
        canonicalize_name(line.split("==")[0])
        for line in proc.stdout.splitlines() if "==" in line
    }
    forbidden = {canonicalize_name(p) for p in (
        "rich", "beautifulsoup4", "jieba",
        "prompt-toolkit", "readchar", "pygments",
    )}
    leaked = forbidden & installed
    assert not leaked, f"core install pulled in extras-only packages: {sorted(leaked)}"
