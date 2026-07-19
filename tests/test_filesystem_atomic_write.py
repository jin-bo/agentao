"""``LocalFileSystem.write_text`` must never destroy existing content.

A plain ``open(path, "w")`` truncates *before* the write, so an
interruption in between — Ctrl+C, host OOM, ``kill`` — leaves the user's
source file empty or half-written. Agentao runs inside a host process it
does not control, so replacing an existing file goes through a sibling
temp file and ``os.replace``.

Each interrupt test fires ``KeyboardInterrupt`` at one specific window
inside ``write_text`` and asserts the target is byte-identical afterwards
and that no temp debris is left behind.
"""

from __future__ import annotations

import os

import pytest

from agentao.capabilities import filesystem as fsmod
from agentao.capabilities.filesystem import LocalFileSystem


ORIGINAL = "ORIGINAL CONTENT\n" * 100


@pytest.fixture
def fs():
    return LocalFileSystem()


@pytest.fixture
def existing(tmp_path):
    p = tmp_path / "precious.py"
    p.write_text(ORIGINAL)
    return p


def _temp_debris(directory):
    return [f for f in os.listdir(directory) if f.endswith(".tmp")]


class TestInterruptedOverwrite:
    def test_interrupt_mid_write_preserves_original(self, fs, existing, monkeypatch):
        """Half the new content lands in the temp file, then we die."""
        real_fdopen = fsmod.os.fdopen

        def patched(fd, *args, **kwargs):
            handle = real_fdopen(fd, *args, **kwargs)
            original_write = handle.write

            def half_then_boom(s):
                original_write(s[: len(s) // 2])
                raise KeyboardInterrupt()

            handle.write = half_then_boom
            return handle

        monkeypatch.setattr(fsmod.os, "fdopen", patched)

        with pytest.raises(KeyboardInterrupt):
            fs.write_text(existing, "SHORT\n")

        assert existing.read_text() == ORIGINAL
        assert _temp_debris(existing.parent) == []

    def test_interrupt_before_chmod_preserves_original(self, fs, existing, monkeypatch):
        def boom(*args, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(fsmod.os, "chmod", boom)

        with pytest.raises(KeyboardInterrupt):
            fs.write_text(existing, "SHORT\n")

        assert existing.read_text() == ORIGINAL
        assert _temp_debris(existing.parent) == []

    def test_interrupt_before_replace_preserves_original(self, fs, existing, monkeypatch):
        def boom(*args, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(fsmod.os, "replace", boom)

        with pytest.raises(KeyboardInterrupt):
            fs.write_text(existing, "SHORT\n")

        assert existing.read_text() == ORIGINAL
        assert _temp_debris(existing.parent) == []

    def test_disk_error_also_leaves_target_intact(self, fs, existing, monkeypatch):
        """Not just interrupts — an ordinary OSError must not corrupt either."""

        def boom(*args, **kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr(fsmod.os, "replace", boom)

        with pytest.raises(OSError):
            fs.write_text(existing, "SHORT\n")

        assert existing.read_text() == ORIGINAL
        assert _temp_debris(existing.parent) == []


class TestNormalBehaviorPreserved:
    def test_overwrite_replaces_content(self, fs, existing):
        fs.write_text(existing, "REPLACED\n")
        assert existing.read_text() == "REPLACED\n"

    def test_permission_bits_survive_replacement(self, fs, tmp_path):
        """``os.replace`` would otherwise install the temp file's 0o600."""
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/sh\necho old\n")
        os.chmod(script, 0o755)

        fs.write_text(script, "#!/bin/sh\necho new\n")

        assert script.stat().st_mode & 0o7777 == 0o755
        assert script.read_text().endswith("echo new\n")

    def test_append_still_appends(self, fs, tmp_path):
        log = tmp_path / "a.log"
        fs.write_text(log, "line1\n")
        fs.write_text(log, "line2\n", append=True)
        assert log.read_text() == "line1\nline2\n"

    def test_append_to_missing_file_creates_it(self, fs, tmp_path):
        log = tmp_path / "new.log"
        fs.write_text(log, "first\n", append=True)
        assert log.read_text() == "first\n"

    def test_new_file_is_created_with_parents(self, fs, tmp_path):
        fresh = tmp_path / "sub" / "dir" / "new.txt"
        fs.write_text(fresh, "hello\n")
        assert fresh.read_text() == "hello\n"

    def test_unicode_round_trips(self, fs, tmp_path):
        p = tmp_path / "u.txt"
        fs.write_text(p, "你好 🌍\n")
        fs.write_text(p, "再见 🌏\n")
        assert p.read_text(encoding="utf-8") == "再见 🌏\n"

    def test_symlink_is_written_through_not_replaced(self, fs, tmp_path):
        """Matches the previous ``open()`` behavior: follow the link."""
        real = tmp_path / "real.txt"
        real.write_text("old\n")
        link = tmp_path / "link.txt"
        link.symlink_to(real)

        fs.write_text(link, "via link\n")

        assert link.is_symlink()
        assert real.read_text() == "via link\n"

    def test_no_temp_files_left_after_success(self, fs, existing):
        fs.write_text(existing, "REPLACED\n")
        assert _temp_debris(existing.parent) == []


class TestConcurrentReadersNeverSeeTornFiles:
    def test_reader_observes_only_whole_states(self, fs, tmp_path):
        import threading

        target = tmp_path / "big.txt"
        target.write_text("A" * 20000)

        observed = set()
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    text = target.read_text()
                except OSError:
                    continue
                observed.add((len(text), text[:1] if text else ""))

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                fs.write_text(target, "B" * 20000)
                fs.write_text(target, "A" * 20000)
        finally:
            stop.set()
            thread.join(timeout=5)

        assert observed <= {(20000, "A"), (20000, "B")}, f"torn read: {observed}"


class TestDegradedEnvironments:
    def test_readonly_directory_still_allows_writing_an_existing_file(
        self, fs, tmp_path
    ):
        """Staging needs a writable dir; the old direct path did not.

        Falling back keeps a previously-working case working — atomicity
        is best-effort, and refusing the write would be the bigger harm.
        """
        target = tmp_path / "locked" / "file.txt"
        target.parent.mkdir()
        target.write_text("old\n")
        os.chmod(target.parent, 0o500)
        try:
            fs.write_text(target, "new\n")
            assert target.read_text() == "new\n"
        finally:
            os.chmod(target.parent, 0o700)


class TestAtomicWriteDoesNotWidenPermissions:
    def test_read_only_file_is_still_refused(self, fs, tmp_path):
        """``os.replace`` only needs a writable *directory*.

        Without an explicit check, making a file read-only would stop
        protecting it the moment writes became atomic — the opposite of
        what the user asked for with ``chmod 444``.
        """
        target = tmp_path / "generated.py"
        target.write_text("# do not edit\n")
        os.chmod(target, 0o444)
        try:
            with pytest.raises(PermissionError):
                fs.write_text(target, "clobbered\n")
            assert target.read_text() == "# do not edit\n"
        finally:
            os.chmod(target, 0o644)

    def test_staging_failure_that_is_not_permissions_does_not_truncate(
        self, fs, existing, monkeypatch
    ):
        """ENOSPC must propagate, never fall through to a truncating write."""
        import errno as _errno

        def no_space(*args, **kwargs):
            raise OSError(_errno.ENOSPC, "No space left on device")

        monkeypatch.setattr("tempfile.mkstemp", no_space)

        with pytest.raises(OSError):
            fs.write_text(existing, "new\n")

        assert existing.read_text() == ORIGINAL

    def test_target_deleted_mid_write_still_lands(self, fs, existing, monkeypatch):
        """A concurrent unlink must not turn into a lost write.

        The vanish is staged only *after* mkstemp, so the earlier
        ``exists()`` probe behaves normally — this reproduces the real
        race (target removed by a build step while we were staging)
        rather than a file that was never there.
        """
        import tempfile as _tempfile

        staged = {"yet": False}
        real_mkstemp = _tempfile.mkstemp
        real_stat = os.stat

        def marking_mkstemp(*args, **kwargs):
            out = real_mkstemp(*args, **kwargs)
            staged["yet"] = True
            return out

        def vanishing_stat(path, *args, **kwargs):
            if staged["yet"] and str(path).endswith("precious.py"):
                raise FileNotFoundError("target vanished")
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(_tempfile, "mkstemp", marking_mkstemp)
        monkeypatch.setattr(os, "stat", vanishing_stat)

        fs.write_text(existing, "written anyway\n")

        assert existing.read_text() == "written anyway\n"
