r"""The Windows dangerous table, and the PowerShell spellings that reach it.

The membership test is the one the POSIX table already uses: **irrecoverable
loss**. Each entry is the counterpart of a class the POSIX side refuses, which
is why launching a program with a URL or clearing a credential store are *not*
here — they are bad, and they are recoverable.

**Only the PowerShell dialect reads this table.** Windows' default stays
``%COMSPEC% /c`` running the general floor exactly as it did, so a cmd body
saying ``format C:`` is not stopped here. That is the status quo being kept
rather than a claim that the command is safe; the permission rules still run.

**A cmdlet has more than one spelling, and the floor has to know all of them.**
``Remove-Item`` answers to six built-in aliases, and every PowerShell parameter
can be abbreviated to any unambiguous prefix, so ``ri -r -fo C:\`` and
``Remove-Item -Recurse -Force C:\`` are the same call. A table written in
canonical names alone would refuse one and allow the other, which is worse than
refusing neither: it reads as coverage. So the command word is resolved through
the alias table first, and the one class that depends on its arguments is
checked structurally rather than by regex.
"""

from __future__ import annotations

import re
from typing import List, Mapping, Sequence, Tuple

#: PowerShell's built-in aliases, for the cmdlets this table names. Not a
#: general alias table — the user's own ``Set-Alias`` rebindings are not
#: knowable from here, and pretending otherwise would be a promise the floor
#: cannot keep. ``del`` / ``erase`` / ``rd`` / ``rmdir`` are cmd's words, and in
#: PowerShell they are aliases of ``Remove-Item``, not of cmd's builtins.
POWERSHELL_ALIASES: Mapping[str, str] = {
    "rm": "Remove-Item",
    "ri": "Remove-Item",
    "del": "Remove-Item",
    "erase": "Remove-Item",
    "rd": "Remove-Item",
    "rmdir": "Remove-Item",
    "rwmi": "Remove-WmiObject",
    "rcim": "Remove-CimInstance",
}

_IMAGE_SUFFIXES = (".exe", ".com", ".cmd", ".bat")

WINDOWS_DANGEROUS: List[Tuple[str, str]] = [
    # `format` — the counterpart of mkfs. `/fs:` or a bare drive both reach it.
    (r"format\s+[A-Za-z]:", "hardline:format-volume"),
    # PowerShell's own spelling of the same act. `-DriveLetter`, `-Partition` and a piped
    # volume all reach it, so the cmdlet name alone is the class.
    (r"Format-Volume\b", "hardline:format-volume"),
    # `diskpart` scripted with `clean`, which zeroes the partition table. `clean` is a
    # diskpart *script* word and means nothing on its own, so it is anchored to a body that
    # reaches diskpart — unanchored, `npm run build && clean` read as a disk wipe.
    (r"diskpart\b[^\n]*\/s\b", "hardline:diskpart-script"),
    (r"diskpart\b[\s\S]*?\bclean\b(?:\s+all)?", "hardline:diskpart-clean"),
    # `Clear-Disk` is diskpart's `clean` as a cmdlet: it removes every partition on the disk.
    (r"Clear-Disk\b", "hardline:diskpart-clean"),
    # `cipher /w` overwrites free space; the data it removes is not recoverable.
    (r"cipher\s+(?:[^\n]*\s)?/w[:\s]", "hardline:cipher-wipe"),
    # BitLocker: forcing a wipe, or destroying the key protectors that make the volume
    # readable at all. Losing the last protector is losing the volume.
    (r"manage-bde\b[^\n]*-(?:forcerecovery|off)\b", "hardline:bitlocker-disable"),
    (r"Disable-BitLocker\b", "hardline:bitlocker-disable"),
    (r"manage-bde\b[^\n]*-(?:delete|remove)\s*-?(?:pr|protectors)\b",
     "hardline:bitlocker-protector-delete"),
    (r"Remove-BitlockerKeyProtector\b", "hardline:bitlocker-protector-delete"),
    # Shadow copies are the restore path for everything else on the volume; deleting them
    # is what turns a recoverable mistake into an unrecoverable one. Three removal verbs
    # reach the same WMI class, and naming only one of them is naming none of them.
    (r"vssadmin\s+delete\s+shadows\b", "hardline:shadow-copy-delete"),
    (r"wmic\s+shadowcopy\s+delete\b", "hardline:shadow-copy-delete"),
    (r"Remove-(?:Item|WmiObject|CimInstance)\b[^\n]*\bWin32_ShadowCopy\b",
     "hardline:shadow-copy-delete"),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), reason) for pattern, reason in WINDOWS_DANGEROUS]

# `Remove-Item -Recurse` against a drive root is `del /s /q C:\*` under another name, and it
# is checked structurally rather than by regex because both halves vary: the switch may be
# abbreviated to any prefix and may sit anywhere in the line, and the path may be written
# four ways. A regex covering that cross product is a regex nobody can read back.
_DRIVE_ROOT = re.compile(r"^[A-Za-z]:[\\/]?\*?$")
_RECURSE = "recurse"

DELETE_DRIVE_ROOT = "hardline:delete-drive-root"


def canonical_command(word: str) -> str:
    r"""The cmdlet a command word names: alias resolved, image suffix and path dropped.

    ``C:\Windows\System32\format.com`` and ``format`` are the same program, and
    ``ri`` and ``Remove-Item`` are the same cmdlet. Everything else is returned
    unchanged, so a word this table has never heard of stays exactly as the
    model wrote it.
    """
    base = word.replace("\\", "/").rsplit("/", 1)[-1]
    lowered = base.lower()
    for suffix in _IMAGE_SUFFIXES:
        if lowered.endswith(suffix):
            base, lowered = base[: -len(suffix)], lowered[: -len(suffix)]
            break
    return POWERSHELL_ALIASES.get(lowered, base)


def _is_recurse_switch(word: str) -> bool:
    """``-Recurse`` and every prefix of it PowerShell accepts, case-insensitively.

    PowerShell resolves a parameter name from any prefix that is unambiguous
    among the cmdlet's own parameters, and ``Recurse`` is the only one of
    ``Remove-Item``'s (common parameters included) that starts with ``r`` — so
    ``-r`` is a real spelling of it and not a guess.
    """
    if not word.startswith("-") or len(word) < 2:
        return False
    name = word[1:].lower()
    return _RECURSE.startswith(name)


def _recursive_drive_root(argv: Sequence[str]) -> bool:
    r"""A ``Remove-Item`` that recurses, whose target is a drive root.

    The target is looked for among the *arguments*, not at a fixed position:
    ``-Path`` may be named or positional and the switches may come first, last
    or either side of it. Quoting is already gone — the caller hands over a
    lowered argv, where ``"C:\"`` and ``C:\`` are the same word.
    """
    # Case-folded, because PowerShell command names are case-insensitive and the alias half
    # of ``canonical_command`` already is: ``rm`` resolved but ``remove-item`` did not, so the
    # canonical spelling was the one spelling of the seven this class did not cover.
    if canonical_command(argv[0]).lower() != "remove-item":
        return False
    if not any(_is_recurse_switch(word) for word in argv[1:]):
        return False
    return any(
        not word.startswith("-") and _DRIVE_ROOT.match(word) for word in argv[1:]
    )


def dangerous_reason(argv: Sequence[str]) -> str | None:
    """The class this lowered command falls into, or ``None``.

    ``argv`` is one command PowerShell will run, already lowered to literal
    words — so there is nothing to anchor against: the command is by itself,
    and a class matched here is matched in command position by construction.
    That is why ``match`` is used rather than ``search``. Searching the joined
    line would read ``Write-Output Format-Volume`` as a format.
    """
    if not argv:
        return None
    if _recursive_drive_root(argv):
        return DELETE_DRIVE_ROOT
    line = " ".join([canonical_command(argv[0]), *argv[1:]])
    for pattern, reason in _COMPILED:
        if pattern.match(line) is not None:
            return reason
    return None

