r"""Finding a PowerShell, handing it a script, and reading what comes back.

Three jobs, and they are here together because each one is a fact about *this
interpreter* rather than about shells in general:

* **Discovery** — which ``pwsh.exe`` or ``powershell.exe`` this machine has.
* **Encoding** — how a command body becomes a command line PowerShell will
  parse as the body and nothing else, and how the child's exit code comes back.
* **CLIXML** — Windows PowerShell 5.1 wraps a redirected error stream in an XML
  envelope, so the bytes on stderr are not the message the user wrote.

Nothing here judges a command. The floor lives in
``agentao.permissions_hardline``; this module only builds the launch and reads
the result.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
from typing import Dict, Iterator, List, Mapping, Optional

# --------------------------------------------------------------------- discovery

PWSH = "pwsh.exe"
POWERSHELL = "powershell.exe"

#: Tried in this order. ``pwsh`` first because PowerShell 7 is the maintained
#: edition; ``powershell.exe`` is Windows PowerShell 5.1, which every Windows
#: install still ships. A machine with neither is an error rather than a silent
#: fall back to cmd: the user asked for PowerShell syntax, and cmd would read
#: the same text as something else entirely.
CANDIDATES = (PWSH, POWERSHELL)


def _install_dirs(env: Mapping[str, str]) -> Iterator[str]:
    r"""Known install locations, most-preferred first.

    Deliberately **not** ``shutil.which``. On Windows that function routes
    through ``os._win_path_needs_curdir`` → ``NeedCurrentDirectoryForExePathW``,
    which prepends the *current directory* to the search — and it does so even
    when an explicit ``path=`` is passed, so there is no way to ask it for a
    PATH-only lookup. A repository checkout containing a file called
    ``pwsh.exe`` would then be the interpreter agentao starts.

    The PowerShell 7 directory is enumerated rather than version-listed, so a
    later major is found without an edit here; entries sort descending so 7
    wins over 6 on a machine carrying both.
    """
    program_dirs = [
        env.get("ProgramFiles"),
        env.get("ProgramW6432"),
        env.get("ProgramFiles(x86)"),
        # A user-scope install (``winget`` / ``dotnet tool``) lands here, and a
        # standard user on a managed machine may have no other PowerShell 7.
        os.path.join(env["LOCALAPPDATA"], "Microsoft") if env.get("LOCALAPPDATA") else None,
    ]
    for base in program_dirs:
        if not base:
            continue
        root = os.path.join(base, "PowerShell")
        try:
            majors = sorted(os.listdir(root), key=_major_key, reverse=True)
        except OSError:
            continue
        for major in majors:
            yield os.path.join(root, major)
    if env.get("LOCALAPPDATA"):
        # The Store/App-Execution-Alias directory. A reparse point, but one that
        # resolves to a real installed image.
        yield os.path.join(env["LOCALAPPDATA"], "Microsoft", "WindowsApps")
    system_root = env.get("SystemRoot") or env.get("windir")
    if system_root:
        for arch in ("System32", "SysWOW64"):
            yield os.path.join(system_root, arch, "WindowsPowerShell", "v1.0")


def _major_key(name: str) -> tuple:
    """Sort ``7`` above ``6`` above ``preview``, without failing on a stray name.

    ``isascii()`` as well as ``isdigit()``: the latter is true of characters ``int()``
    rejects — ``"²"`` is one — and the ``ValueError`` that raises is not the ``OSError``
    the caller guards, so it would leave the whole spec unresolvable.
    """
    return (1, int(name)) if name.isascii() and name.isdigit() else (0, 0)


def _path_dirs(env: Mapping[str, str]) -> Iterator[str]:
    """The **absolute** directories of the parent process's ``PATH``.

    Relative entries are dropped, and the empty entry that ``A;;B`` produces is
    one of them: on Windows an empty PATH element means the current directory.
    Searching it would make the interpreter agentao launches depend on where
    the model happened to leave the working directory.
    """
    for entry in (env.get("PATH") or "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry and os.path.isabs(entry):
            yield entry


def discover(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The interpreter to launch, or ``None`` when this machine has neither.

    Each candidate name is tried against every directory before the next name,
    so a machine with PowerShell 7 anywhere gets PowerShell 7 — rather than
    5.1 simply because ``System32`` sorts earlier.
    """
    env = os.environ if env is None else env
    directories = [*_install_dirs(env), *_path_dirs(env)]
    for name in CANDIDATES:
        for directory in directories:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                return candidate
    return None


# --------------------------------------------------------------------- encoding

PRELUDE = (
    # ``$OutputEncoding`` first and unguarded: it governs what PowerShell writes
    # to a *native* command's stdin, and Windows PowerShell 5.1 defaults it to
    # ASCII, so every non-ASCII byte piped into a native program becomes `?`.
    # It is a plain variable assignment and cannot fail.
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    # ``[Console]::OutputEncoding`` needs a console, and a background launch has
    # none (``DETACHED_PROCESS``, all three streams at DEVNULL), where the
    # assignment throws. Catching it here is what keeps one launch shape usable
    # for both delivery faces — and the catch is around this clause only, never
    # around the body, so a failure here cannot swallow the user's own errors.
    "try { [Console]::OutputEncoding = $OutputEncoding } catch {}\n"
    # Read by the trailer below. Without the initialisation the trailer would
    # consult whatever an earlier session left, or nothing at all.
    "$LASTEXITCODE = 0"
)

EPILOGUE = (
    # ``$?`` must be captured before anything else runs, because every statement
    # after it — including the ``if`` — replaces it.
    "$__agentao_ok = $?; if ($__agentao_ok) { exit 0 }; "
    # The last statement failed. A *native* command's own code is the useful
    # one; a cmdlet that wrote an error leaves ``$LASTEXITCODE`` untouched, so
    # the generic failure code stands in. Appending a bare
    # ``exit $LASTEXITCODE`` instead would report a stale 0 for exactly that
    # case, which is a failing command reported as success.
    "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; exit 1"
)

ARGUMENTS = ("-NoLogo", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text")

#: ``CreateProcessW``'s ``lpCommandLine`` limit, including the terminating NUL.
CREATEPROCESS_MAX_UNITS = 32767


def wrap(body: str) -> str:
    """Prelude, the body verbatim, trailer — one newline between each.

    The body is not inspected, quoted or rewritten. Both fixed halves are
    agentao's own text and contain no byte of the body or of any configuration,
    which is what makes the wrapping safe to state as a property rather than to
    argue about case by case.

    They are, however, parsed *with* the body, and PowerShell parses the whole
    script at once. A body ending in a continuation backtick, an unterminated
    string or a line comment absorbs the trailer — sometimes without a syntax
    error — so the exit-code guarantee below holds for a body that ends on its
    own and not for one that does not.
    """
    return f"{PRELUDE}\n{body}\n{EPILOGUE}"


def encode(body: str) -> str:
    """``-EncodedCommand``'s argument: the wrapped script as base64 of UTF-16LE.

    Encoding is what removes the quoting problem — no layer between here and
    the interpreter has to agree about backslashes, percent signs, embedded
    quotes or newlines. It removes nothing else: PowerShell still parses the
    decoded text as PowerShell, so this is a transport decision, not a
    sandbox.
    """
    return base64.b64encode(wrap(body).encode("utf-16-le")).decode("ascii")


def command_line(interpreter: str, body: str) -> str:
    """The full ``lpCommandLine`` for this body."""
    return subprocess.list2cmdline([interpreter, *ARGUMENTS, "-EncodedCommand", encode(body)])


def command_line_units(line: str) -> int:
    """UTF-16 code units of a command line, counting the terminating NUL."""
    return len(line.encode("utf-16-le", errors="surrogatepass")) // 2 + 1


def oversize(line: str) -> Optional[str]:
    """A readable refusal for a command line Windows will not accept, or ``None``.

    Base64 of UTF-16LE is 8 units of command line per 3 characters of body, so
    a body of roughly 12 KB is the practical ceiling and a large here-doc-style
    paste reaches it. Reporting the measurement is the whole point: the
    alternatives are a truncation that changes what runs, or a bare
    ``CreateProcess`` failure the model cannot act on.
    """
    units = command_line_units(line)
    if units <= CREATEPROCESS_MAX_UNITS:
        return None
    return (
        f"the encoded PowerShell command line is {units:,} UTF-16 units, over the "
        f"{CREATEPROCESS_MAX_UNITS:,} Windows allows. Base64 of UTF-16LE costs about 8 "
        "units per 3 characters of the command, so split the work or write the script to "
        "a file and run that."
    )


# ----------------------------------------------------------------------- CLIXML

CLIXML_MARKER = "#< CLIXML"

_S_OPEN = re.compile(r"<S(?:\s[^<>]*)?>")
_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_ESCAPE = re.compile(r"&(?:(lt|gt|amp|quot|apos)|#(?:x([0-9A-Fa-f]{1,6})|([0-9]{1,7})));")
_ENCODED_CHAR = re.compile(r"_x([0-9A-Fa-f]{4})_")

_NAMED: Dict[str, str] = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def _unescape_once(text: str) -> str:
    """The five predefined entities and legal numeric character references, one pass.

    One pass, and one regex covering both forms, so ``&amp;lt;`` comes back as
    the literal ``&lt;`` rather than as ``<``. Recursion here would let a
    crafted stderr stream expand into something the length cap never sees.

    A reference naming a surrogate, ``NUL``, or a code point above the Unicode
    range is not legal XML and is left exactly as written — decoding it would
    invent a character the producer could not have meant.
    """

    def replace(match: "re.Match[str]") -> str:
        name, hexadecimal, decimal = match.groups()
        if name is not None:
            return _NAMED[name]
        code = int(hexadecimal, 16) if hexadecimal is not None else int(decimal)
        if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
            return match.group(0)
        return chr(code)

    return _ESCAPE.sub(replace, text)


def _decode_encoded_chars(text: str) -> str:
    """PowerShell's ``_xHHHH_`` escape, one pass.

    This is how a newline survives an XML text node: CLIXML writes a line break
    as ``_x000D__x000A_``. Surrogates are left alone for the same reason as
    above.
    """

    def replace(match: "re.Match[str]") -> str:
        code = int(match.group(1), 16)
        return match.group(0) if 0xD800 <= code <= 0xDFFF else chr(code)

    return _ENCODED_CHAR.sub(replace, text)


def _bounded(text: str, limit: int, note: Optional[str] = None) -> str:
    """The text itself never longer than ``limit``, and honest about it when it had to cut.

    The notes are appended after the cut, so the returned string can be a little longer than
    ``limit`` — a cap that swallowed its own explanation would be the wrong trade.

    The head is kept rather than the tail, which is the opposite of the tool's
    own output truncation: what is being cut here is a wrapper whose shape is
    the diagnostic, and the shape is at the front.
    """
    parts: List[str] = []
    if len(text) > limit:
        parts.append(text[:limit])
        parts.append(f"\n[... {len(text) - limit:,} chars omitted ...]")
    else:
        parts.append(text)
    if note:
        parts.append(f"\n{note}")
    return "".join(parts)


def extract(text: str, limit: int) -> str:
    """Readable text out of a CLIXML-wrapped stream; anything else passes through.

    Windows PowerShell 5.1 serialises a *redirected* error stream as CLIXML, so
    the bytes agentao captures on stderr are an XML envelope rather than the
    message. ``-OutputFormat Text`` does not prevent it, which is why this runs
    on the result rather than being argued away at the launch.

    Deliberately not an XML parser. The text comes from a subprocess running
    model-written code, and the standard-library parser expands internal
    entities: four nested levels of a two-hundred-byte document reach ten
    thousand characters, and this repository has no XML parser on any other
    path. So: scan for complete ``<S>`` elements, unescape once, decode once,
    and cap the result.

    Anything this cannot read cleanly — an entity declaration, a missing
    closing tag, no text elements at all — comes back as the original stream,
    length-capped, with a line saying why. Losing the text silently would be
    worse than showing the envelope.
    """
    if CLIXML_MARKER not in text:
        return text
    head, _, tail = text.partition(CLIXML_MARKER)
    if _DECLARATION.search(tail):
        return _bounded(text, limit, "[clixml: entity declaration present; shown unexpanded]")
    if "</Objs>" not in tail:
        return _bounded(text, limit, "[clixml: the wrapper is truncated; shown raw]")
    pieces: List[str] = []
    found = False
    total = 0
    # Split on the closing tag first, then find each element's opening inside its own
    # segment. A single ``<S…>(.*?)</S>`` regex is quadratic on a stream carrying many
    # unclosed ``<S`` — every one of them scans to the end of the input looking for a close
    # that is not there — and this text comes from a subprocess running model-written code.
    # Splitting partitions the string, so the whole walk is linear, and it stops as soon as
    # there is more text than the cap will keep.
    for segment in tail.split("</S>")[:-1]:
        opening = None
        for opening in _S_OPEN.finditer(segment):
            pass  # the last opening in this segment is the one this </S> closes
        if opening is None:
            continue
        found = True
        piece = _decode_encoded_chars(_unescape_once(segment[opening.end():]))
        pieces.append(piece)
        total += len(piece)
        if total > limit:
            break
    if not found:
        return _bounded(text, limit, "[clixml: no text elements found; shown raw]")
    return _bounded(head + "".join(pieces), limit)
