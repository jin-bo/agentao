"""A Windows Job Object holding a login process and everything it starts.

Windows has no process groups to signal, and a process tree is only walkable
while its root is alive: once a runner such as ``npx.cmd`` exits on Ctrl+C,
``taskkill /T`` can no longer find the login child it started. A job contains
every descendant regardless (child processes join their parent's job), so
``terminate`` ends whatever is left.

The process is created suspended and resumed only after it is in the job, so
it cannot start a child outside it first. Every failure here degrades to "no
job" — the caller then falls back to a tree kill — never to a failed login.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

#: ``CREATE_SUSPENDED`` (``subprocess`` only defines it on Windows).
CREATE_SUSPENDED = 0x00000004


class LoginJob:
    """One job object; Windows only."""

    def __init__(self, handle: int, kernel32: Any) -> None:
        self._handle = handle
        self._k32 = kernel32

    @classmethod
    def create(cls) -> Optional["LoginJob"]:
        if sys.platform != "win32":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
            k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
            k32.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = k32.CreateJobObjectW(None, None)
        except (OSError, AttributeError):
            return None
        return cls(handle, k32) if handle else None

    def adopt(self, process_handle: int) -> bool:
        """Put a (suspended) process in the job, then resume it.

        Returns ``False`` when it could not be assigned; the process is
        resumed either way, so the caller never holds a frozen login.
        """
        assigned = bool(self._k32.AssignProcessToJobObject(self._handle, process_handle))
        _resume(process_handle)
        return assigned

    def terminate(self) -> None:
        """End every process still in the job."""
        self._k32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        """Release the job. Processes still in it keep running."""
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = 0


def _resume(process_handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    ntdll.NtResumeProcess(process_handle)
