"""Fast tests for the two helpers the clean-install suite leans on.

Both are pure functions, and both had a defect that the slow tests they serve
could not have surfaced: a wrong answer here reads as a *pass* over there,
because the suite goes on to measure whatever it was handed.
"""

from __future__ import annotations

from pathlib import Path

from tests.support import wheel as wheel_support
from tests.test_dependency_split import _distribution_names


def test_the_newest_wheel_is_the_highest_version_not_the_last_name(tmp_path, monkeypatch):
    """``0.4.9`` sorts after ``0.4.10`` as text, and dist/ accumulates builds.

    ``uv build`` adds to ``dist/`` rather than replacing it, so a local tree that
    has been built twice holds both — and the string sort quietly picks the older
    one. Every install test then measures a wheel that is not the one under
    change and passes.
    """
    monkeypatch.setattr(wheel_support, "DIST_DIR", tmp_path)
    for name in ("agentao-0.4.9-py3-none-any.whl", "agentao-0.4.10-py3-none-any.whl"):
        (tmp_path / name).touch()

    assert wheel_support.find_wheel() == tmp_path / "agentao-0.4.10-py3-none-any.whl"


def test_a_dev_build_beats_the_release_it_follows(tmp_path, monkeypatch):
    monkeypatch.setattr(wheel_support, "DIST_DIR", tmp_path)
    (tmp_path / "agentao-0.4.22-py3-none-any.whl").touch()
    (tmp_path / "agentao-0.4.23.dev0-py3-none-any.whl").touch()

    assert wheel_support.find_wheel() == tmp_path / "agentao-0.4.23.dev0-py3-none-any.whl"


def test_a_filename_that_is_not_a_version_is_skipped_not_raised(tmp_path, monkeypatch):
    """A stray file matching the glob must not take the suite down with it."""
    monkeypatch.setattr(wheel_support, "DIST_DIR", tmp_path)
    (tmp_path / "agentao-notaversion-py3-none-any.whl").touch()
    (tmp_path / "agentao-0.4.22-py3-none-any.whl").touch()

    assert wheel_support.find_wheel() == tmp_path / "agentao-0.4.22-py3-none-any.whl"


def test_no_wheel_at_all_is_none_rather_than_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(wheel_support, "DIST_DIR", tmp_path)
    assert wheel_support.find_wheel() is None

    monkeypatch.setattr(wheel_support, "DIST_DIR", tmp_path / "absent")
    assert wheel_support.find_wheel() is None


def test_one_reader_serves_freeze_output_and_the_baseline_file():
    """The baseline holds bare names; a freeze holds pinned ones and a local URL.

    Reading them with two functions is how the two sides start to disagree about
    what a name is — ``prompt_toolkit`` and ``prompt-toolkit`` are one package.
    """
    freeze = [
        "annotated-types==0.8.0",
        "prompt_toolkit==3.0.53",
        "agentao @ file:///tmp/agentao-0.4.22-py3-none-any.whl",
    ]
    baseline = [
        "# a comment the baseline carries",
        "",
        "annotated-types",
        "prompt-toolkit",
    ]

    assert _distribution_names(freeze) == _distribution_names(baseline)
    assert _distribution_names(freeze) == {"annotated-types", "prompt-toolkit"}


def test_the_shipped_baseline_parses_to_names_only():
    """A version left in the file would silently become part of the name."""
    from tests.test_dependency_split import BASELINE

    names = _distribution_names(BASELINE.read_text().splitlines())
    assert names, "baseline holds no names"
    assert not any(any(c in n for c in "=<>!~ ") for n in names), sorted(names)
    assert "openai" in names and "agentao" not in names


def test_the_repo_root_helper_points_at_this_repo():
    assert (Path(wheel_support.REPO_ROOT) / "pyproject.toml").is_file()
