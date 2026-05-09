"""Bash/zsh ``$'...'`` ANSI-C escape decoder.

Pure function — no internal package deps. Lifted into its own module so
``_contexts._try_consume_dollar_quote`` can call it without dragging in
the regex-pattern surface.
"""

from __future__ import annotations

from typing import List


def _decode_ansi_c(body: str) -> str:
    """Decode bash/zsh ANSI-C ``$'...'`` escape sequences.

    Bash interprets ``\\n``, ``\\t``, ``\\r``, ``\\\\``, ``\\'``,
    ``\\"``, hex (``\\xHH``), octal (``\\NNN``), Unicode
    (``\\uHHHH`` / ``\\UHHHHHHHH``), and control (``\\cX``) escapes
    inside ``$'...'``. The shell runs the *decoded* string as the
    script body, so ``$'\\nrm -rf /\\n'`` actually executes
    ``rm -rf /`` as a command line, and ``$'\\x72m\\x20-rf\\x20/'``
    decodes to ``rm -rf /`` before bash hands it to the parser.

    The full set is decoded here so a destructive command encoded as
    numeric/Unicode escapes inside a ``bash -c $'...'`` body still
    surfaces real command-name and separator characters to the
    recursive hardline check. A partial decoder would let
    ``bash -c $'\\x72m\\x20-rf\\x20/'`` slip past — the ``\\x72`` /
    ``\\x20`` sequences would lose their leading backslash but leave
    the literal text ``x72m`` / ``x20``, which no rm pattern matches.
    """
    out: List[str] = []
    i = 0
    n = len(body)
    hex_chars = "0123456789abcdefABCDEF"
    oct_chars = "01234567"
    while i < n:
        if body[i] == "\\" and i + 1 < n:
            c = body[i + 1]
            if c == "n":
                out.append("\n")
                i += 2
            elif c == "t":
                out.append("\t")
                i += 2
            elif c == "r":
                out.append("\r")
                i += 2
            elif c == "\\":
                out.append("\\")
                i += 2
            elif c == "'":
                out.append("'")
                i += 2
            elif c == '"':
                out.append('"')
                i += 2
            elif c == "?":
                out.append("?")
                i += 2
            elif c == "a":
                out.append("\a")
                i += 2
            elif c == "b":
                out.append("\b")
                i += 2
            elif c == "e" or c == "E":
                out.append("\x1b")
                i += 2
            elif c == "f":
                out.append("\f")
                i += 2
            elif c == "v":
                out.append("\v")
                i += 2
            elif c == "x":
                # ``\xHH`` — 1 or 2 hex digits. If no hex digit
                # follows, bash leaves the literal ``\x``; the floor
                # mirrors that by emitting ``x``.
                j = i + 2
                digits = ""
                while j < n and len(digits) < 2 and body[j] in hex_chars:
                    digits += body[j]
                    j += 1
                if digits:
                    out.append(chr(int(digits, 16)))
                    i = j
                else:
                    out.append("x")
                    i += 2
            elif c == "u":
                # ``\uHHHH`` — 1 to 4 hex digits.
                j = i + 2
                digits = ""
                while j < n and len(digits) < 4 and body[j] in hex_chars:
                    digits += body[j]
                    j += 1
                if digits:
                    try:
                        out.append(chr(int(digits, 16)))
                    except (ValueError, OverflowError):
                        out.append("u")
                    i = j
                else:
                    out.append("u")
                    i += 2
            elif c == "U":
                # ``\UHHHHHHHH`` — 1 to 8 hex digits.
                j = i + 2
                digits = ""
                while j < n and len(digits) < 8 and body[j] in hex_chars:
                    digits += body[j]
                    j += 1
                if digits:
                    try:
                        out.append(chr(int(digits, 16)))
                    except (ValueError, OverflowError):
                        out.append("U")
                    i = j
                else:
                    out.append("U")
                    i += 2
            elif c in oct_chars:
                # ``\NNN`` — 1 to 3 octal digits (``\0``, ``\07``,
                # ``\077``, ``\0123``). Modulo 256 mirrors bash's
                # 8-bit behavior for values past ``\377``.
                j = i + 1
                digits = ""
                while j < n and len(digits) < 3 and body[j] in oct_chars:
                    digits += body[j]
                    j += 1
                out.append(chr(int(digits, 8) % 256))
                i = j
            elif c == "c":
                # ``\cX`` — control character. Standard mapping
                # ``code = ord(upper(X)) ^ 0x40`` (so ``\cA`` → ``\x01``,
                # ``\c?`` → ``\x7f``).
                if i + 2 < n:
                    cx = body[i + 2]
                    out.append(chr(ord(cx.upper()) ^ 0x40))
                    i += 3
                else:
                    out.append("c")
                    i += 2
            else:
                out.append(c)
                i += 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)
