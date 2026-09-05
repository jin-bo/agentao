"""Measure what a native Windows identity oracle would have to answer.

The PowerShell ladder's `IdentityOracle` has nineteen methods, and one of them is the
project: `subject_can_replace`, which IMG-01 asks about *the token the child will run as*
and IMG-06a spells out as an access mask rather than the word "writable". This script is
the measurement that has to come before the implementation, for two questions the design
records as open and explicitly refuses to guess (method rule 9):

  q14  Does `C:\\ProgramData` pass IMG-01 on a stock Windows? ENV-06g keeps it in the class
       that must, and IMG-06a's directory mask includes FILE_ADD_FILE. If ordinary users
       can create entries there, the rule as written refuses every policy-on rung.

  new  Is the CI runner's token an administrator? If it is, the trusted set is empty there
       *by design* (an elevated agentao is its own attacker), so the Windows job can
       exercise the oracle's refusal path and not its acceptance path. That decides how
       the oracle gets tested, so it is measured rather than read off documentation.

Stdlib only, on purpose. The oracle itself cannot depend on an optional package: an
incomplete oracle does not degrade to "unattested", it empties the ladder, and LADDER-03
turns an empty ladder into a denial on every shell call. Whatever this script proves it can
do with `ctypes` is what the oracle is allowed to use.

Output is one JSON document on stdout, or to `--out`. Nothing is interpreted here: the
script reports granted masks and group membership, and the reading happens in review.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from ctypes import wintypes
from typing import Any, Dict, List, Optional

if sys.platform != "win32":  # pragma: no cover - the whole point is the other platform
    raise SystemExit("windows_oracle_probe is a Windows measurement; run it on the runner")

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------- constants

TOKEN_QUERY = 0x0008
TOKEN_DUPLICATE = 0x0002
TOKEN_IMPERSONATE = 0x0004

TokenUser = 1
TokenGroups = 2
TokenPrivileges = 3
TokenElevation = 20
TokenIntegrityLevel = 25

SecurityImpersonation = 2

SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
GROUP_SECURITY_INFORMATION = 0x00000002
DACL_SECURITY_INFORMATION = 0x00000004

MAXIMUM_ALLOWED = 0x02000000

# IMG-06a's mask, by name. FILE_WRITE_DATA and FILE_ADD_FILE are the same bit, as are
# FILE_APPEND_DATA and FILE_ADD_SUBDIRECTORY — the pair of names is about what the bit
# means on a file versus a directory, not two different rights.
ACCESS_BITS = {
    "FILE_WRITE_DATA / FILE_ADD_FILE": 0x0002,
    "FILE_APPEND_DATA / FILE_ADD_SUBDIRECTORY": 0x0004,
    "FILE_DELETE_CHILD": 0x0040,
    "DELETE": 0x00010000,
    "WRITE_DAC": 0x00040000,
    "WRITE_OWNER": 0x00080000,
}

FILE_ATTRIBUTE_DIRECTORY = 0x0010
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400

SE_GROUP_ENABLED = 0x00000004
SE_PRIVILEGE_ENABLED = 0x00000002

# Privileges that amount to "can replace", whatever the DACL says. An oracle that reads
# only the DACL and ignores these would call a path trusted that its subject owns outright.
REPLACE_PRIVILEGES = {
    "SeRestorePrivilege",
    "SeTakeOwnershipPrivilege",
    "SeBackupPrivilege",
    "SeDebugPrivilege",
    "SeImpersonatePrivilege",
    "SeLoadDriverPrivilege",
}

WELL_KNOWN = {
    "S-1-5-32-544": "BUILTIN\\Administrators",
    "S-1-5-32-545": "BUILTIN\\Users",
    "S-1-5-18": "NT AUTHORITY\\SYSTEM",
    "S-1-16-12288": "Mandatory Label\\High",
    "S-1-16-8192": "Mandatory Label\\Medium",
    "S-1-16-16384": "Mandatory Label\\System",
}


# ---------------------------------------------------------------- structures

class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", SID_AND_ATTRIBUTES)]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class GENERIC_MAPPING(ctypes.Structure):
    _fields_ = [
        ("GenericRead", wintypes.DWORD), ("GenericWrite", wintypes.DWORD),
        ("GenericExecute", wintypes.DWORD), ("GenericAll", wintypes.DWORD),
    ]


class PRIVILEGE_SET(ctypes.Structure):
    """Sized for several entries, not one.

    ``AccessCheck`` writes the privileges it used here and fails outright with
    ``ERROR_INSUFFICIENT_BUFFER`` if the buffer is too small. A one-entry set is the shape
    the documentation shows and not a safe size to pass.
    """

    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD), ("Control", wintypes.DWORD),
        ("Privilege", LUID_AND_ATTRIBUTES * 16),
    ]


# ------------------------------------------------- prototypes, declared on purpose
#
# Without these ``ctypes`` assumes every return value is a C ``int``. On 64-bit that
# truncates handles, and ``GetFileAttributesW``'s INVALID_FILE_ATTRIBUTES (0xFFFFFFFF)
# arrives as -1 so the failure test never fires. A probe that silently measures the wrong
# thing is worse than one that does not run, so the signatures are spelled out.

PSID = ctypes.c_void_p
PSECURITY_DESCRIPTOR = ctypes.c_void_p

kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetFileAttributesW.restype = wintypes.DWORD
kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
kernel32.LocalFree.restype = ctypes.c_void_p
kernel32.LocalFree.argtypes = [ctypes.c_void_p]

advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
]
advapi32.DuplicateToken.restype = wintypes.BOOL
advapi32.DuplicateToken.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.HANDLE),
]
advapi32.GetTokenInformation.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
advapi32.ConvertSidToStringSidW.argtypes = [PSID, ctypes.POINTER(wintypes.LPWSTR)]
advapi32.LookupAccountSidW.restype = wintypes.BOOL
advapi32.LookupAccountSidW.argtypes = [
    wintypes.LPCWSTR, PSID, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
]
advapi32.LookupPrivilegeNameW.restype = wintypes.BOOL
advapi32.LookupPrivilegeNameW.argtypes = [
    wintypes.LPCWSTR, ctypes.POINTER(LUID), wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
advapi32.GetNamedSecurityInfoW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
    ctypes.POINTER(PSID), ctypes.POINTER(PSID),
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(PSECURITY_DESCRIPTOR),
]
advapi32.AccessCheck.restype = wintypes.BOOL
advapi32.AccessCheck.argtypes = [
    PSECURITY_DESCRIPTOR, wintypes.HANDLE, wintypes.DWORD,
    ctypes.POINTER(GENERIC_MAPPING), ctypes.POINTER(PRIVILEGE_SET),
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.BOOL),
]


# ---------------------------------------------------------------- helpers

def _last_error() -> str:
    code = ctypes.get_last_error()
    return f"WinError {code}: {ctypes.FormatError(code)}"


def _sid_to_string(sid: ctypes.c_void_p) -> Optional[str]:
    out = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(out)):
        return None
    try:
        return out.value
    finally:
        kernel32.LocalFree(out)


def _sid_name(sid: ctypes.c_void_p) -> Optional[str]:
    name = ctypes.create_unicode_buffer(256)
    domain = ctypes.create_unicode_buffer(256)
    n = wintypes.DWORD(256)
    d = wintypes.DWORD(256)
    use = wintypes.DWORD()
    if not advapi32.LookupAccountSidW(None, sid, name, ctypes.byref(n),
                                      domain, ctypes.byref(d), ctypes.byref(use)):
        return None
    return f"{domain.value}\\{name.value}" if domain.value else name.value


def _token_info(token: wintypes.HANDLE, cls: int) -> Optional[ctypes.Array]:
    size = wintypes.DWORD()
    advapi32.GetTokenInformation(token, cls, None, 0, ctypes.byref(size))
    if size.value == 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if not advapi32.GetTokenInformation(token, cls, buf, size, ctypes.byref(size)):
        return None
    return buf


def _open_process_token() -> wintypes.HANDLE:
    token = wintypes.HANDLE()
    ok = advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE, ctypes.byref(token),
    )
    if not ok:
        raise OSError(_last_error())
    return token


def _impersonation_token(token: wintypes.HANDLE) -> wintypes.HANDLE:
    """``AccessCheck`` takes a *client* token, so the process token has to be duplicated."""
    dup = wintypes.HANDLE()
    ok = advapi32.DuplicateToken(token, SecurityImpersonation, ctypes.byref(dup))
    if not ok:
        raise OSError(_last_error())
    return dup


# ---------------------------------------------------------------- the report

def describe_token(token: wintypes.HANDLE) -> Dict[str, Any]:
    report: Dict[str, Any] = {}

    buf = _token_info(token, TokenUser)
    if buf is not None:
        user = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER)).contents
        report["user_sid"] = _sid_to_string(user.User.Sid)
        report["user_name"] = _sid_name(user.User.Sid)

    groups: List[Dict[str, Any]] = []
    buf = _token_info(token, TokenGroups)
    if buf is not None:
        count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
        offset = ctypes.sizeof(ctypes.c_void_p)  # DWORD + padding to pointer alignment
        array = ctypes.cast(
            ctypes.byref(buf, offset), ctypes.POINTER(SID_AND_ATTRIBUTES * count),
        ).contents
        for entry in array:
            sid = _sid_to_string(entry.Sid)
            groups.append({
                "sid": sid,
                "name": _sid_name(entry.Sid) or WELL_KNOWN.get(sid or ""),
                "enabled": bool(entry.Attributes & SE_GROUP_ENABLED),
            })
    report["groups"] = groups
    report["is_administrators_member"] = any(
        g["sid"] == "S-1-5-32-544" for g in groups
    )
    report["administrators_enabled"] = any(
        g["sid"] == "S-1-5-32-544" and g["enabled"] for g in groups
    )

    privileges: List[Dict[str, Any]] = []
    buf = _token_info(token, TokenPrivileges)
    if buf is not None:
        count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
        offset = ctypes.sizeof(wintypes.DWORD)
        array = ctypes.cast(
            ctypes.byref(buf, offset), ctypes.POINTER(LUID_AND_ATTRIBUTES * count),
        ).contents
        for entry in array:
            name = ctypes.create_unicode_buffer(256)
            n = wintypes.DWORD(256)
            if advapi32.LookupPrivilegeNameW(None, ctypes.byref(entry.Luid), name,
                                             ctypes.byref(n)):
                privileges.append({
                    "name": name.value,
                    "enabled": bool(entry.Attributes & SE_PRIVILEGE_ENABLED),
                })
    report["privileges"] = privileges
    report["replace_privileges_held"] = sorted(
        p["name"] for p in privileges if p["name"] in REPLACE_PRIVILEGES
    )

    buf = _token_info(token, TokenElevation)
    if buf is not None:
        report["is_elevated"] = bool(
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
        )

    return report


def access_of(path: str, client: wintypes.HANDLE) -> Dict[str, Any]:
    """The IMG-06a question for one path: what may this token actually do to it."""
    result: Dict[str, Any] = {"path": path}

    attrs = kernel32.GetFileAttributesW(path)
    if attrs == 0xFFFFFFFF:
        result["error"] = f"GetFileAttributes: {_last_error()}"
        return result
    result["is_directory"] = bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
    result["is_reparse_point"] = bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)

    owner = PSID()
    sd = PSECURITY_DESCRIPTOR()
    err = advapi32.GetNamedSecurityInfoW(
        path, SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        ctypes.byref(owner), None, None, None, ctypes.byref(sd),
    )
    if err != 0:
        result["error"] = f"GetNamedSecurityInfo failed with {err}"
        return result

    try:
        result["owner_sid"] = _sid_to_string(owner)
        result["owner_name"] = _sid_name(owner)

        mapping = GENERIC_MAPPING(0x120089, 0x120116, 0x1200A0, 0x1F01FF)
        priv = PRIVILEGE_SET()
        priv_len = wintypes.DWORD(ctypes.sizeof(PRIVILEGE_SET))
        granted = wintypes.DWORD()
        status = wintypes.BOOL()

        ok = advapi32.AccessCheck(
            sd, client, MAXIMUM_ALLOWED, ctypes.byref(mapping),
            ctypes.byref(priv), ctypes.byref(priv_len),
            ctypes.byref(granted), ctypes.byref(status),
        )
        if not ok:
            result["error"] = f"AccessCheck: {_last_error()}"
            return result

        mask = granted.value
        result["granted_mask"] = f"0x{mask:08X}"
        result["granted"] = {
            name: bool(mask & bit) for name, bit in ACCESS_BITS.items()
        }
        # IMG-06a: any one of these, or ownership, means the subject can replace it.
        replaceable = any(result["granted"].values())
        result["can_replace_this_path"] = replaceable
        return result
    finally:
        kernel32.LocalFree(sd)


def ancestors_to_volume_root(path: str) -> List[str]:
    """IMG-01 evaluates the whole chain, so the probe reports the whole chain."""
    chain: List[str] = []
    current = os.path.abspath(path)
    while True:
        chain.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            return chain
        current = parent


def img01_verdict(path: str, client: wintypes.HANDLE) -> Dict[str, Any]:
    """IMG-01 holds for a path only if the subject can replace *nothing* on its chain."""
    steps = [access_of(p, client) for p in ancestors_to_volume_root(path)]
    unreadable = [s["path"] for s in steps if "error" in s]
    replaceable = [s["path"] for s in steps if s.get("can_replace_this_path")]
    return {
        "path": path,
        # Unreadable is not "fine": IMG-06c's whole point is that an unanswerable link must
        # not be walked as though it had been read and found ordinary.
        "img01_holds": not replaceable and not unreadable,
        "replaceable_ancestors": replaceable,
        "unreadable_ancestors": unreadable,
        "chain": steps,
    }


DEFAULT_PATHS = [
    r"C:\Windows",
    r"C:\Windows\System32",
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    r"C:\Program Files",
    r"C:\Program Files\PowerShell\7\pwsh.exe",
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\ProgramData",          # q14 — the question this probe exists for
    r"C:\Users\Public",
    r"C:\Windows\System32\cmd.exe",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write the JSON here instead of stdout")
    parser.add_argument("--path", action="append", default=[],
                        help="extra path to measure; repeatable")
    args = parser.parse_args()

    token = _open_process_token()
    client = _impersonation_token(token)

    paths = DEFAULT_PATHS + args.path
    report = {
        "probe_version": 1,
        "python": sys.version,
        "cwd": os.getcwd(),
        "token": describe_token(token),
        "paths": [img01_verdict(p, client) for p in paths if os.path.exists(p)],
        "missing_paths": [p for p in paths if not os.path.exists(p)],
    }
    report["summary"] = {
        "img01_holds_for": [p["path"] for p in report["paths"] if p["img01_holds"]],
        "img01_fails_for": [p["path"] for p in report["paths"] if not p["img01_holds"]],
    }

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as handle:
            handle.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
