r"""The Windows half of the dangerous table — one table, two dialects.

PR-2 defined it (q2, decided 2026-09-05); PR-4 moved it here. It lived in ``_cmd`` and was
read only by the cmd floor, which made every class in it unreachable from a PowerShell rung —
and two of its entries are already spelled as PowerShell (``Remove-BitlockerKeyProtector``,
``Remove-Item … Win32_ShadowCopy``), so the table was never about cmd. **What a class refuses
is a property of the platform, not of the syntax that reached it**: formatting a volume
destroys the same bytes whichever interpreter typed the command.

The membership test is the one the POSIX table already used: **irrecoverable loss**. Each
entry is the counterpart of a class the POSIX side refuses, which is why launching a program
with a URL and clearing a credential store are *not* here — they are bad, and they are
recoverable (q2 / q3, decided 2026-09-05).

The patterns are unanchored. Each dialect anchors them its own way: cmd searches the body
text from a command position, PowerShell matches them against each lowered command's own
reconstructed line, where there is nothing to anchor against because the command is already
by itself.
"""

from __future__ import annotations

from typing import List, Tuple

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
    # Recursive force-delete of a drive root or a system directory.
    (r"(?:del|erase)\s+(?:/[a-zA-Z]+\s+)*[A-Za-z]:\\?\s*\*",
     "hardline:delete-drive-root"),
    (r"rd\s+(?:/[a-zA-Z]+\s+)*[A-Za-z]:\\?\s*$", "hardline:remove-drive-root"),
    (r"rd\s+(?:/[a-zA-Z]+\s+)*%SystemRoot%", "hardline:remove-system-root"),
    # `Remove-Item -Recurse -Force C:\` is `del /s /q C:\*` under another name. The switches
    # may appear in any order and abbreviate, and the path may be quoted, so the class is
    # "a recursive Remove-Item whose target is a drive root".
    (r"Remove-Item\b(?=[^\n]*\s-[Rr]ec)[^\n]*\s\"?[A-Za-z]:\\?\"?(?:\s|$)",
     "hardline:delete-drive-root"),
]
