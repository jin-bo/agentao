"""Finding a PowerShell, and not finding one that was planted.

Discovery is a security surface, not a convenience. What it returns is the image agentao
starts with the model's own text on the command line, so the question every test here asks
is *where the candidate came from* rather than whether one was found.

The environment is supplied explicitly throughout, so these run identically on every
platform. Nothing here starts a process.
"""

from __future__ import annotations

import os

import pytest

from agentao.capabilities import powershell as ps


def _win(tmp_path, **over):
    """A plausible Windows environment rooted in ``tmp_path``, with no PATH by default."""
    env = {
        "ProgramFiles": str(tmp_path / "Program Files"),
        "LOCALAPPDATA": str(tmp_path / "AppData" / "Local"),
        "SystemRoot": str(tmp_path / "Windows"),
        "PATH": "",
    }
    env.update(over)
    return env


def _plant(directory, name: str) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(b"MZ")
    return path


def test_nothing_installed_is_none_rather_than_a_guess(tmp_path):
    """A refusal, never a fall back to cmd: the user asked for PowerShell syntax, and cmd
    does not fail on it — it means something else."""
    assert ps.discover(_win(tmp_path)) is None


def test_powershell_7_wins_over_windows_powershell(tmp_path):
    """Every candidate directory is tried for ``pwsh.exe`` before ``powershell.exe``.

    Ordering by directory instead would hand 5.1 the win on most machines simply because
    ``System32`` happens to be searched earlier.
    """
    env = _win(tmp_path)
    _plant(os.path.join(env["SystemRoot"], "System32", "WindowsPowerShell", "v1.0"),
           "powershell.exe")
    seven = _plant(os.path.join(env["ProgramFiles"], "PowerShell", "7"), "pwsh.exe")
    assert ps.discover(env) == seven


def test_windows_powershell_is_found_when_it_is_all_there_is(tmp_path):
    env = _win(tmp_path)
    five = _plant(os.path.join(env["SystemRoot"], "System32", "WindowsPowerShell", "v1.0"),
                  "powershell.exe")
    assert ps.discover(env) == five


def test_a_newer_major_wins_without_an_edit_here(tmp_path):
    """The version directory is enumerated, not listed, so PowerShell 8 needs no code change.

    Descending numeric order is what makes that safe: a machine carrying 6 and 7 gets 7.
    """
    env = _win(tmp_path)
    _plant(os.path.join(env["ProgramFiles"], "PowerShell", "6"), "pwsh.exe")
    eight = _plant(os.path.join(env["ProgramFiles"], "PowerShell", "8"), "pwsh.exe")
    _plant(os.path.join(env["ProgramFiles"], "PowerShell", "preview"), "pwsh.exe")
    assert ps.discover(env) == eight


def test_a_user_scope_install_counts(tmp_path):
    """A standard user on a managed machine may have no PowerShell 7 anywhere else."""
    env = _win(tmp_path)
    user = _plant(os.path.join(env["LOCALAPPDATA"], "Microsoft", "PowerShell", "7"), "pwsh.exe")
    assert ps.discover(env) == user


def test_the_working_directory_is_never_searched(tmp_path, monkeypatch):
    """The headline reason this does not use ``shutil.which``.

    On Windows that function routes through ``NeedCurrentDirectoryForExePathW`` and prepends
    the current directory to the search — and it does so even when an explicit ``path=`` is
    passed, so there is no way to ask it for a PATH-only lookup. A repository checkout
    carrying a file called ``pwsh.exe`` would then be the interpreter agentao starts.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _plant(str(checkout), "pwsh.exe")
    _plant(str(checkout), "powershell.exe")
    monkeypatch.chdir(checkout)
    assert ps.discover(_win(tmp_path)) is None


@pytest.mark.parametrize("path_value", ["", ".", "relative/dir", ";;"])
def test_a_relative_path_entry_is_not_a_candidate_directory(tmp_path, monkeypatch, path_value):
    """An empty ``PATH`` element means the current directory on Windows.

    ``A;;B`` produces one, and so does a trailing separator, so this is not a hypothetical
    spelling — it is what a real ``PATH`` looks like after a few installers have edited it.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _plant(str(checkout), "pwsh.exe")
    monkeypatch.chdir(checkout)
    env = _win(tmp_path, PATH=path_value.replace("/", os.sep))
    assert ps.discover(env) is None


def test_an_absolute_path_entry_is_a_candidate_directory(tmp_path):
    """PATH still counts — an install this table has never heard of is still an install."""
    elsewhere = tmp_path / "opt" / "ps"
    planted = _plant(str(elsewhere), "pwsh.exe")
    assert ps.discover(_win(tmp_path, PATH=str(elsewhere))) == planted


def test_a_known_location_wins_over_a_path_entry(tmp_path):
    """PATH is the fallback, not the first answer.

    It is the entry a user, an installer or an injected environment can most easily rewrite,
    so a genuine install directory is preferred when both exist.
    """
    env = _win(tmp_path, PATH=str(tmp_path / "somewhere"))
    _plant(str(tmp_path / "somewhere"), "pwsh.exe")
    known = _plant(os.path.join(env["ProgramFiles"], "PowerShell", "7"), "pwsh.exe")
    assert ps.discover(env) == known


def test_an_unreadable_install_root_is_skipped_rather_than_raised(tmp_path):
    """``ProgramFiles`` pointing at nothing is an ordinary state on a stripped image."""
    env = _win(tmp_path, ProgramFiles=str(tmp_path / "does-not-exist"))
    assert ps.discover(env) is None


def test_a_directory_named_like_the_interpreter_is_not_the_interpreter(tmp_path):
    """``isfile``, not ``exists``: a directory called ``pwsh.exe`` cannot be started."""
    env = _win(tmp_path)
    os.makedirs(os.path.join(env["ProgramFiles"], "PowerShell", "7", "pwsh.exe"))
    assert ps.discover(env) is None
