r"""The native oracle's two access-mask questions, against ACLs this test writes itself.

Most of this file only runs on Windows, and that is the point: the questions are about a real
token and a real security descriptor, and a fake of either would only restate what the code
already believes (there is no way to falsify a mask check with a stub that returns the mask).

**The runner is an administrator.** Measured, not assumed — see `docs/reference/
powershell-support-evidence.zh.md` §3.23. So the privilege short-circuit answers "can
replace" for everything there, which is correct and makes every DACL test vacuous. The tests
below therefore assert the short-circuit *once*, on its own, and then disable it so the mask
logic underneath can be exercised. Disabling it is honest here precisely because the thing
being tested is the DACL arithmetic, not the privilege rule.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agentao.capabilities.shell_spec import Subject
from agentao.permissions_hardline._windows_identity import (
    ANCESTOR_MASK,
    REPLACE_PRIVILEGES,
    TARGET_DIRECTORY_MASK,
    TARGET_FILE_MASK,
    WindowsAccessOracle,
    token_privileges,
    token_sid,
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="a Windows token and a real DACL")

_ADD_BITS = 0x0002 | 0x0004  # FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY


# --------------------------------------------------------------- everywhere


def test_the_ancestor_mask_drops_exactly_the_two_add_rights():
    r"""IMG-06a's split, stated as arithmetic so it cannot drift quietly.

    A stock ``C:\`` grants standard users FILE_ADD_SUBDIRECTORY and nothing else on this
    list, so an ancestor mask containing either ADD bit makes IMG-01 false for every path on
    every machine (evidence §3.23).
    """
    assert ANCESTOR_MASK & _ADD_BITS == 0
    assert TARGET_DIRECTORY_MASK & _ADD_BITS == _ADD_BITS
    assert ANCESTOR_MASK & TARGET_DIRECTORY_MASK == ANCESTOR_MASK  # strictly narrower


def test_the_file_target_mask_covers_writing_the_image_itself():
    assert TARGET_FILE_MASK & 0x0002  # FILE_WRITE_DATA — the same bit as FILE_ADD_FILE
    assert TARGET_FILE_MASK & 0x00010000  # DELETE


def test_the_privilege_list_names_the_ones_access_check_does_not_apply():
    """``AccessCheck`` consults SeTakeOwnershipPrivilege and no other on this list; the file
    system consults SeRestore and SeBackup when a handle opens, long after that call."""
    assert {"SeRestorePrivilege", "SeBackupPrivilege"} <= REPLACE_PRIVILEGES


# --------------------------------------------------------------- Windows only


def _icacls(path, *args: str) -> None:
    result = subprocess.run(
        ["icacls", str(path), *args], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"icacls {args}: {result.stdout}{result.stderr}"


def _disowned(path, subject: Subject, rights: str) -> None:
    r"""Give ``subject`` exactly ``rights`` on ``path``, and give ownership away.

    **Ownership is the trap here, and it is the code being right rather than the test.** An
    owner implicitly holds READ_CONTROL and WRITE_DAC, and WRITE_DAC is in both of IMG-06a's
    masks — rewrite the DACL and you can grant yourself anything. So a directory the test
    creates is one the test's own subject owns, and every mask question about it correctly
    answers "can replace"; a refusal is unobservable until ownership moves. IMG-01 says as
    much ("or ownership") and the first version of these tests read past it.

    Ownership goes last: handing it away costs WRITE_DAC, so the ACL is written first.
    ``icacls`` can still do it because the runner holds SeTakeOwnershipPrivilege — which is
    also why the oracle short-circuits on that privilege and why these tests turn that off.
    """
    _icacls(path, "/inheritance:r", "/grant", f"*{subject}:({rights})")
    _icacls(path, "/setowner", "NT AUTHORITY\\SYSTEM")


@pytest.fixture
def subject() -> Subject:
    sid = token_sid()
    assert sid, "the oracle cannot bind to a subject it cannot name"
    return Subject(sid)


@pytest.fixture
def oracle(subject, monkeypatch):
    """An oracle with the privilege short-circuit off, so the DACL arithmetic is reachable.

    Asserted separately by ``test_a_privileged_token_can_replace_everything``; without that
    pairing this fixture would be quietly weakening the thing it tests.
    """
    built = WindowsAccessOracle(subject)
    monkeypatch.setattr(built, "_privileged", False)
    return built


@windows_only
def test_a_privileged_token_can_replace_everything(subject, tmp_path):
    """The rule that makes an elevated agentao its own attacker, and the reason the trusted
    set is empty on this runner."""
    built = WindowsAccessOracle(subject)
    locked = tmp_path / "locked"
    locked.mkdir()
    _disowned(locked, subject, "RX")

    if token_privileges() & REPLACE_PRIVILEGES:
        assert built.subject_can_replace(str(locked), subject) is True
        assert built.subject_can_replace_entries(str(locked), subject) is True
    else:
        pytest.skip("this token holds none of the replace privileges; nothing to assert")


@windows_only
def test_a_read_execute_directory_is_not_replaceable(oracle, subject, tmp_path):
    target = tmp_path / "readonly"
    target.mkdir()
    _disowned(target, subject, "RX")

    assert oracle.subject_can_replace(str(target), subject) is False
    assert oracle.subject_can_replace_entries(str(target), subject) is False


@windows_only
def test_a_modifiable_directory_is_replaceable_under_both_masks(oracle, subject, tmp_path):
    target = tmp_path / "writable"
    target.mkdir()
    _disowned(target, subject, "M")

    assert oracle.subject_can_replace(str(target), subject) is True
    assert oracle.subject_can_replace_entries(str(target), subject) is True


@windows_only
def test_add_only_is_the_case_the_split_exists_for(oracle, subject, tmp_path):
    r"""The stock volume root's shape, reproduced in a directory this test owns.

    ``C:\`` grants standard users add-subdirectory and none of delete, delete-child,
    write-DAC or write-owner. Under one mask that made every ancestor chain fail; under two,
    it is replaceable *as a target* and harmless *as an ancestor*, which is exactly the
    distinction between planting something new and replacing what resolved.
    """
    target = tmp_path / "addonly"
    target.mkdir()
    _disowned(target, subject, "RX,AD,WD")

    assert oracle.subject_can_replace(str(target), subject) is True
    assert oracle.subject_can_replace_entries(str(target), subject) is False


@windows_only
def test_delete_child_is_dangerous_under_both_masks(oracle, subject, tmp_path):
    """Deleting or renaming the next link *is* replacing it, so the narrow mask keeps this."""
    target = tmp_path / "deletechild"
    target.mkdir()
    _disowned(target, subject, "RX,DC")

    assert oracle.subject_can_replace_entries(str(target), subject) is True


@windows_only
def test_ownership_alone_answers_can_replace(oracle, subject, tmp_path):
    r"""IMG-01's "or ownership", and the reason every other case here gives ownership away.

    An owner implicitly holds READ_CONTROL and WRITE_DAC whatever the DACL grants, so it can
    rewrite that DACL and then hold anything. This directory is granted read-execute only and
    keeps its owner, and both masks still answer "can replace" — which is the code being
    right. Without this case the ``_disowned`` helper looks like ceremony.
    """
    owned = tmp_path / "owned"
    owned.mkdir()
    _icacls(owned, "/inheritance:r", "/grant", f"*{subject}:(RX)")

    assert oracle.subject_can_replace(str(owned), subject) is True
    assert oracle.subject_can_replace_entries(str(owned), subject) is True


@windows_only
def test_a_different_subject_is_refused_rather_than_answered(oracle, tmp_path):
    """SPEC-05: an oracle bound to one subject may not answer about another one."""
    target = tmp_path / "readonly2"
    target.mkdir()
    assert oracle.subject_can_replace(str(target), Subject("S-1-5-21-0-0-0-1234")) is True


@windows_only
def test_a_path_that_does_not_exist_answers_can_replace(oracle, subject, tmp_path):
    """Not knowing is not "no" — an unexamined path must not be walked as examined."""
    assert oracle.subject_can_replace(str(tmp_path / "absent"), subject) is True


@windows_only
def test_a_file_uses_the_file_mask_and_a_directory_the_directory_mask(oracle, subject, tmp_path):
    """Add-file on a *file* is write-data, which is replacing it; on a directory it is not."""
    image = tmp_path / "img.exe"
    image.write_bytes(b"MZ")
    _disowned(image, subject, "RX,WD")

    assert oracle.subject_can_replace(str(image), subject) is True


@windows_only
def test_content_hash_matches_hashlib(oracle, tmp_path):
    import hashlib

    blob = tmp_path / "blob.bin"
    payload = os.urandom(3 * 1024 * 1024)   # larger than the read chunk
    blob.write_bytes(payload)
    assert oracle.content_hash(str(blob)) == hashlib.sha256(payload).hexdigest()


@windows_only
def test_an_alternate_data_stream_is_refused_not_normalised(oracle, tmp_path):
    """`a.exe:x` is a different byte stream from `a.exe`, and nothing downstream tells them
    apart, so canonicalisation refuses rather than dropping the suffix."""
    assert oracle.canonicalize(str(tmp_path / "a.exe") + ":stream") is None


@windows_only
def test_a_junction_resolves_and_a_plain_directory_does_not(oracle, tmp_path):
    from agentao.permissions_hardline._trust import ReparseState

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)],
                   capture_output=True, timeout=60, check=True)

    assert oracle.resolve_reparse(str(real)).state is ReparseState.not_reparse
    resolved = oracle.resolve_reparse(str(link))
    assert resolved.state is ReparseState.resolved
    assert os.path.normcase(resolved.target or "") == os.path.normcase(str(real))


@windows_only
def test_an_absent_path_is_an_error_state_not_not_a_reparse_point(oracle, tmp_path):
    from agentao.permissions_hardline._trust import ReparseState

    assert oracle.resolve_reparse(str(tmp_path / "gone")).state is ReparseState.error


def test_the_module_imports_on_every_platform():
    """It is reached from `permissions_hardline`, which POSIX hosts import; binding Win32 at
    call time rather than import time is what keeps that true."""
    assert sys.modules["agentao.permissions_hardline._windows_identity"]
