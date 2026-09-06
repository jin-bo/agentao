"""Closure-equivalence and core-only invariants for the 0.4.0 dep split.

Three slow-marked tests against a built wheel:

- ``[full]`` pulls exactly the distributions in
  ``tests/data/full_extras_baseline.txt``.
- bare ``pip install agentao`` constructs an ``Agentao()`` offline.
- bare install does NOT pull rich/bs4/jieba/prompt-toolkit/readchar/pygments
  — they live in ``[cli]`` / ``[web]`` / ``[i18n]``.

**The closure is compared by name, not by version.** ``pyproject.toml`` declares
floors, so every version in the closure floats by design: pinning them made the
test fail on any upstream release with nothing wrong in-tree, and the churn was
not harmless noise — it hid the signal. The refresh that produced today's
baseline found 26 version bumps and, buried among them, the only thing the test
exists to catch: ``distro`` and ``tqdm`` left the closure when ``openai`` went
2.x → 3.x and dropped them. A supported-version *range* is asserted where it
belongs, in the metadata (``mcp>=1.26.0,<3``) and in the mcp-compat CI job that
installs both majors and runs against each.

The baseline is a closure for one interpreter — Python 3.12, which the CI job
pins — because a version-gated backport makes another interpreter's answer
legitimately different.

Run with::

    uv build && uv run pytest tests/test_dependency_split.py -m slow
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable

import pytest
from packaging.utils import canonicalize_name

from tests.support.wheel import REPO_ROOT, make_venv, require_wheel


BASELINE = REPO_ROOT / "tests" / "data" / "full_extras_baseline.txt"

#: Leading distribution name of a requirement-ish line, whatever follows it —
#: ``foo==1.2`` from a freeze, ``agentao @ file:///…`` for the local install,
#: or the bare ``foo`` the baseline file holds. One reader for both sides.
_NAME = re.compile(r"[\s=@<>!~]")


def _distribution_names(lines: Iterable[str]) -> set[str]:
    """PEP 503 names in freeze output or a baseline file, minus agentao itself.

    Comments and blank lines are skipped so the baseline can carry its own
    refresh instructions instead of leaving them somewhere that goes stale.
    """
    names = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = _NAME.split(line, maxsplit=1)[0].strip()
        if name:
            names.add(canonicalize_name(name))
    names.discard("agentao")
    return names


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
    actual = _distribution_names(proc.stdout.splitlines())
    expected = _distribution_names(BASELINE.read_text().splitlines())
    assert expected, f"baseline holds no names: {BASELINE}"

    added = sorted(actual - expected)
    gone = sorted(expected - actual)
    if added or gone:
        raise AssertionError(
            "[full] closure membership drifted from baseline.\n"
            f"  new in [full]:    {added}\n"
            f"  gone from [full]: {gone}\n"
            "  Versions are not compared, so this is a real change in what a\n"
            "  user gets. Update tests/data/full_extras_baseline.txt only once\n"
            "  the change is understood and intended."
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
    installed = _distribution_names(proc.stdout.splitlines())
    forbidden = {canonicalize_name(p) for p in (
        "rich", "beautifulsoup4", "jieba",
        "prompt-toolkit", "readchar", "pygments",
    )}
    leaked = forbidden & installed
    assert not leaked, f"core install pulled in extras-only packages: {sorted(leaked)}"
