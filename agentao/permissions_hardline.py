"""Hardline shell-safety scanner: pre-permission floor for unrecoverable ops.

This module is the opt-out *floor* that :class:`agentao.permissions.PermissionEngine`
consults before any rule is evaluated. It detects shell commands whose
damage is *unrecoverable* (disk wipe, host poweroff, fork bomb) and
returns a stable, source-tagged reason string the engine surfaces as
the matched-rule reason of a DENY decision.

The floor exists so a CLI user — or an embedded host that hasn't
thought through threat modeling — is protected from prompt-injected
disk wipes by default. A host that takes the policy responsibility
itself (typically because Agentao is sandboxed in a container) can
disable the floor with ``enable_hardline=False`` on the
:class:`PermissionEngine`.

Recoverable-but-costly operations (``git reset --hard``, ``pip
install``, ``chmod -R 777``, ``curl | sh``) deliberately stay outside
the floor so they remain host-policy decisions, not library-baked
invariants.

Public API:

- :func:`hardline_check` — top-level entry: ``(tool_name, tool_args)``
  → ``Optional[str]`` (a ``"hardline:<description>"`` reason string,
  or ``None`` when the call is not on the floor).
- :data:`REASON_HARDLINE` — the stable ``"hardline"`` source tag.
  Hosts and audit displays may pattern-match this prefix in
  ``PermissionDecisionEvent.reason``; it is part of the public event
  contract.
"""

import re
from collections import deque
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Public source tag for ``PermissionDecisionEvent.reason``.
# ---------------------------------------------------------------------------
#
# Hosts and audit displays may rely on this prefix; it is part of the
# public event contract once a ``PermissionDecisionEvent`` is emitted
# with the ``reason`` field.
REASON_HARDLINE = "hardline"


# ---------------------------------------------------------------------------
# Hardline patterns (opt-out, default ON)
# ---------------------------------------------------------------------------
#
# Each pattern is anchored at a *command position* — start of line, after
# a shell separator (``;``, ``&&``, ``||``, ``|``), after backtick / ``$(``,
# or after a ``sudo`` / ``env`` wrapper — so benign text like
# ``echo "reboot logs"`` does not false-positive.

# Optional system bin path prefix shared by every hardline pattern, so
# path-qualified invocations like ``/bin/rm -rf /`` or
# ``/usr/sbin/shutdown`` are caught alongside bare command names. Covers
# the conventional UNIX system locations (``/bin``, ``/sbin``,
# ``/usr/bin``, ``/usr/sbin``, ``/usr/local/bin``, ``/usr/local/sbin``);
# arbitrary user paths are intentionally not matched to keep
# false-positives minimal.
# Two arms:
#   1. Literal system locations (``/bin``, ``/sbin``, ``/usr/bin``,
#      ``/usr/sbin``, ``/usr/local/bin``, ``/usr/local/sbin``).
#   2. Path-with-glob: any path whose remaining word contains a glob
#      metachar (``?`` ``*`` ``[``). Bash glob-expands such paths at
#      exec time, so ``/[b]in/rm`` and ``/u?r/bin/rm`` resolve to
#      ``/bin/rm`` / ``/usr/bin/rm`` and execute the system rm. The
#      lookahead requires at least one glob metachar so arbitrary user
#      paths like ``./rm`` or ``/home/user/rm`` are not matched —
#      otherwise a benign local ``rm`` script under the user's home
#      would also trigger the floor.
_HARDLINE_BIN_PATH = (
    r"(?:"
    r"/(?:usr/(?:local/)?)?s?bin/"
    r"|"
    r"(?=[^\s]*[?*\[])/[^\s]+/"
    r")?"
)

# Match a "command position": start of string, or after a shell separator
# (``;`` ``&&`` ``||`` ``|`` ``` ` ``` ``$(`` *or a literal newline*),
# then optionally consume:
#   1. Inline ``NAME=VALUE`` shell-environment assignments at the head
#      of a command (``PATH=/bin rm -rf /``, ``FOO=bar /bin/rm -rf /``).
#      Bash applies these assignments to the immediately-following
#      command, so the destructive ``rm`` runs as expected even when
#      the floor's command-position anchor is "wrong".
#   2. One or more ``sudo`` / ``env`` wrappers, each of which may carry
#      its own flags — including flags that take a separate argument
#      value (``sudo -u root``, ``sudo --user=root``, ``env -u VAR``),
#      bundled flags (``sudo -n``), end-of-options markers
#      (``sudo --``), and inline ``NAME=VALUE`` assignments
#      (``env FOO=bar``).
#   3. A shell-interpreter wrapper that runs a quoted script: ``sh -c
#      'rm -rf /'``, ``bash -c "rm -rf /etc"``, ``sudo bash -c '...'``.
#      ``run_shell_command`` already executes through a shell, so a
#      nested ``sh -c 'cmd'`` is *the* idiomatic way to smuggle a
#      destructive command past a hardline that only inspects the
#      outer-level invocation. The wrapper consumes the interpreter
#      name (optionally path-qualified), any preceding flags, the
#      ``-c`` flag, and the opening quote of the script — so the rm /
#      mkfs / shutdown patterns can match the *script's* first
#      command exactly as if it were top-level text.
# The wrapper is *consumed* rather than anchored via lookbehind:
# Python's ``re`` only supports fixed-width lookbehinds, and a wrapper
# plus its variable-length arguments is not fixed-width. Newlines are
# explicit separators because the shell executor runs commands with
# ``shell=True`` — a multi-line input like ``echo ok\nrm -rf /`` runs
# both lines.
# Command-position prefix without the shell-interpreter tail. Shared
# between ``_CMDPOS`` (which appends the interpreter wrapper) and the
# ``_SHELL_SCRIPT_WRAPPER`` extractor (which IS the interpreter wrapper
# match — anchoring it with the same head means ``echo sh -c '...'``
# does NOT cause body recursion, since ``sh`` there is an arg, not a
# command).
_CMDPOS_HEAD = (
    # Separator class accepts:
    #   ``;`` ``&`` ``|`` backtick newline carriage-return — ordinary
    #     control-flow separators
    #   ``(`` ``{`` — subshell / brace-group openers (``(rm -rf /)``,
    #     ``{ rm -rf /; }`` both execute the wrapped command in the
    #     current shell)
    #   ``)`` — closes a ``case`` pattern and starts an executable
    #     command list (``case x in x) rm -rf /;; esac``); also closes
    #     ``$(...)`` command substitution and subshells. Treating it as
    #     a separator can cause an unrelated argv after a closing
    #     ``$(...)`` to be flagged (``echo $(date) rm -rf /`` is just
    #     args to ``echo`` but would match), but the conservative
    #     trade-off is intentional — the alternative leaves a real bypass
    #     for case arms in full-access mode.
    #   ``!`` — negation (``! rm -rf /`` runs rm and inverts exit code)
    # Plus ``$(`` (command substitution) as a longer alternative.
    # Bash control-flow keywords ``then`` / ``do`` / ``else`` / ``elif``
    # are NOT included in the separator alternation — bare ``echo then
    # rm -rf /`` would false-positive there because the keyword is just
    # an argv to ``echo``, not a real shell control-flow boundary. They
    # are instead handled as wrapper words below: a wrapper is only
    # consumed AFTER a real separator (or ``^``), so
    # ``if true; then rm -rf /; fi`` and ``while true; do rm -rf /;
    # done`` still hit the floor while ``echo then rm -rf /`` does not.
    r"(?:^|[;&|`\n\r(){!]|\$\()\s*"
    # Shell environment assignments at command head: ``NAME=VALUE``
    # tokens whose value contains no whitespace and no separator. We
    # exclude shell separators from the value so a stray ``FOO=bar;``
    # doesn't greedily eat into the next command.
    r"(?:[A-Za-z_][A-Za-z0-9_]*=[^\s;&|`]*\s+)*"
    r"(?:"
    # Wrapper-style prefixes that execute another command:
    #   sudo / env (privilege / env adjusters with their own flag rules);
    #   command / builtin / exec (shell builtins that bypass aliases or
    #     replace the shell process with the next command);
    #   nohup / setsid (detach from controlling tty / session);
    #   nice / ionice / taskset / chrt (priority/affinity wrappers);
    #   time (reserved word that runs the wrapped command and times it);
    #   busybox (multi-call binary — ``busybox rm -rf /`` is the same
    #     destructive operation as bare ``rm``);
    #   then / do / else / elif (bash control-flow keywords — they
    #     introduce a fresh command context after the preceding ``;``
    #     / newline, so ``if cond; then rm -rf /; fi`` and ``while
    #     cond; do rm -rf /; done`` still hit the floor; these only
    #     act as wrappers when sitting at a real command position via
    #     the separator alternation above).
    # ``eval`` is a shell builtin that re-parses its arguments as
    # shell text and runs them. Treating it as a wrapper means
    # ``eval rm -rf /`` consumes ``eval`` and falls through to the
    # ``rm`` command-name pattern. Indirect forms like ``eval $cmd`` /
    # ``eval "$(cat file)"`` still slip through — those require data-
    # flow analysis the floor explicitly does not attempt — but the
    # literal form is the one prompt-injected attacks actually use.
    r"(?:sudo|env|command|builtin|exec|eval|nohup|setsid|nice|ionice|taskset|chrt|time|coproc|busybox|then|do|else|elif)"
    r"(?:\s+(?:"
    # Short flag known to accept a separate argument value
    # (sudo: -u/-U/-g/-G/-h/-H/-p/-P/-D/-C/-r/-R/-t/-T;
    # env: -u/-U/-S/-C). The lookahead guards against partial matches
    # like ``-uroot`` slipping through as ``-u`` + leftover text — those
    # fall to the general flag arm below.
    r"-[uUgGhHpPDCrRtTS](?=\s|=|$)(?:=[^\s]*|\s+[^\s\-=][^\s]*)?"
    r"|"
    # Long flag known to accept a separate argument value
    # (``--user=root``, ``--user root``, ``--chdir /tmp``, ...).
    r"--(?:user|group|host|prompt|role|type|chdir|close-from"
    r"|unset|split-string)(?:=[^\s]*|\s+[^\s\-=][^\s]*)?"
    r"|"
    # General flag — any other -X / --xxx token, including bundled
    # combinations (``-nE``, ``-uroot``) and the end-of-options
    # separator ``--``.
    r"-[^\s]+"
    r"|"
    # NAME=VALUE assignment (sudo and env both accept these inline).
    r"[A-Za-z_][A-Za-z0-9_]*=[^\s]*"
    r"))*"
    r"\s+"
    r")*"
)

# Optional shell-interpreter wrapper appended to ``_CMDPOS_HEAD`` to
# form ``_CMDPOS``: ``sh -c '...'``, ``bash --noprofile -c "..."``,
# ``zsh -xc 'rm -rf /'``, etc. The interpreter may be path-qualified
# (``/bin/sh``). Other flags can appear before ``-c``. Combined-letter
# flags ending in ``c`` (e.g. ``-xc``) are handled by the trailing
# ``c`` arm.
_CMDPOS_INTERP = (
    r"(?:"
    r"(?:[\w/]*/)?(?:sh|bash|zsh|dash|ash|ksh|csh|tcsh|busybox)"
    r"(?:\s+-[^\s]*)*"  # any preceding flags
    r"\s+(?:-c|-[a-zA-Z]*c)\s+"  # -c, possibly bundled like -xc
    r"['\"]?"  # opening quote of the script (optional — bare unquoted
               # ``-c rm`` invocations don't pass extra args, so they
               # aren't a destructive bypass)
    r")?"
)

_CMDPOS = _CMDPOS_HEAD + _CMDPOS_INTERP

# Path alternation shared between the ``rm -rf`` / ``rm -fr`` patterns.
# Each arm permits an optional opening quote (``'`` / ``"``) and accepts a
# closing quote at its trailing boundary, so quoted forms like
# ``rm -rf "$HOME"``, ``rm -rf '~'``, ``rm -rf "/etc"``, ``rm -rf '/'``
# match alongside the unquoted forms. We don't enforce balanced quoting —
# the intent we're catching is destructive, not well-formed shell.
# The trailing boundary class also accepts shell separators
# (``;`` ``&`` ``|``), command-substitution closers (``)`` ``` ` ```), so
# ``echo $(rm -rf /)``, ``` `rm -rf /etc` ```, and ``rm -rf /;reboot``
# all qualify.
# The bare ``/`` arm requires whitespace, a quote, EOL, or a separator
# right after so that ``/tmp/foo`` (a non-system path) does NOT trigger
# the floor — only the literal root filesystem path does.
_HARDLINE_RM_PATHS = (
    r"""["']?"""
    # The trailing-boundary class includes glob metacharacters (``*``
    # ``?``) so common root/system glob deletes like ``rm -rf /*``,
    # ``rm -rf /etc/*``, ``rm -rf ~/*`` are also caught — those expand
    # to the same destructive set the bare-literal forms hit.
    # The bare-``/`` arm also accepts any sequence of ``.`` / ``..``
    # path-component aliases (``/.``, ``/./``, ``/..``, ``/../``,
    # ``/.././``) — they all resolve to ``/`` at execution time, so the
    # floor must catch them. ``/+`` (one or more leading slashes) and
    # ``/*`` after each dot alias also catch repeated-slash spellings
    # (``//``, ``///``, ``/.//``, ``///etc``); the kernel collapses
    # consecutive ``/`` to one, so they all resolve to ``/`` (or to the
    # named system dir for the second arm).
    # The boundary class also accepts ``{`` and ``[``: bash brace
    # expansion ``rm -rf /{etc,usr,bin}`` and glob classes ``rm -rf
    # /[bes]*`` both produce destructive operands at execution time
    # (``/etc /usr /bin`` and the matching root entries respectively),
    # so the floor needs to recognize the ``/`` followed by a brace or
    # bracket as the same destructive intent the bare-literal forms hit.
    r"""(?:/+(?:\.{1,2}/*)*(?:[\s"'`);&|*?{[]|$)"""
    r"""|/+(?:etc|usr|var|boot|bin|sbin|lib|home|root)(?:[/\s"'`);&|*?{[]|$)"""
    # Tilde expansions. Bash recognizes:
    #   ``~``      → ``$HOME``
    #   ``~user``  → that user's home directory (``getpwnam``)
    #   ``~+``     → ``$PWD``
    #   ``~-``     → ``$OLDPWD``
    # Any of these can resolve to a destructive path, so all four
    # forms must hit the floor. The optional name/sigil arm permits
    # ``[a-zA-Z_][\w-]*`` (POSIX-ish login name) or a single ``+``/``-``;
    # the trailing boundary keeps benign suffixes like ``~mate`` from
    # being eaten by the regex when the destructive intent isn't
    # actually expressed (the boundary still requires the same
    # post-tilde delimiter set the bare-tilde arm did).
    r"""|~(?:[a-zA-Z_][\w-]*|[+\-])?(?:[/\s"'`);&|*?{[]|$)"""
    # ``$HOME`` and the full family of ``${HOME...}`` parameter
    # expansions: ``${HOME}``, ``${HOME:?}``, ``${HOME:?msg}``,
    # ``${HOME:-default}``, ``${HOME%/*}``, ``${HOME:0:5}``, ``${HOME^^}``,
    # etc. The expansion arm requires a non-identifier character
    # immediately after ``HOME`` so that ``${HOMEBREW_CACHE}`` /
    # ``${HOMELESS_DIR}`` (different variables) do NOT match — the bash
    # variable name must end at ``HOME``. A trailing boundary keeps the
    # bare ``$HOME`` arm from false-matching ``$HOMEBREW``.
    r"""|\$(?:HOME|\{HOME(?:\}|[^a-zA-Z0-9_}][^}]*\}))"""
    r"""(?:[/\s"'`);&|*?]|$)"""
    r""")"""
)

# Recursive marker: any short flag token containing ``r`` or ``R``
# (``-r``, ``-R``, ``-rf``, ``-vR``, ``-rfv``, ...) or the long form
# ``--recursive``.
_RM_RECURSIVE = r"(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)"
# Force marker: any short flag token containing ``f`` or ``F`` or the
# long form ``--force``.
_RM_FORCE = r"(?:-[a-zA-Z]*[fF][a-zA-Z]*|--force)"

# Argument-region assertion for the rm pattern. Three zero-width
# lookaheads scan ALL tokens after ``rm`` and require:
#   1. a recursive marker (``-r``, ``-R``, ``-rf``, ``--recursive``, ...)
#   2. a force marker     (``-f``, ``--force``, ``-rf``, ...)
#   3. a destructive path (``/``, ``/etc``, ``~``, ``$HOME``, ...)
# anywhere in the argv. Pure-lookahead (no consumption) so the three
# can appear in *any* order — GNU rm parses options after operands too,
# so ``rm /home/user -rf`` and ``rm / --no-preserve-root -rf`` are the
# same destructive call as the canonical ``rm -rf /home/user``. The
# token-skip uses ``*`` (unbounded) so an attacker can't bypass the
# floor by padding with extra operands like
# ``rm -rf /tmp/a0 ... /tmp/a99 /``. Each iteration is non-overlapping
# whitespace-separated tokens, so the regex engine still runs in
# O(n) over the command length.
_HARDLINE_RM_ARGS_LOOKAHEAD = (
    r"(?=(?:[^\s]+\s+)*" + _RM_RECURSIVE + r"\b)"
    r"(?=(?:[^\s]+\s+)*" + _RM_FORCE + r"\b)"
    r"(?=(?:[^\s]+\s+)*" + _HARDLINE_RM_PATHS + r")"
)

# Raw block-device prefix shared by the ``dd`` and redirect patterns.
# Covers Linux (``sd``, ``nvme``, ``hd``, ``mmcblk``, ``vd``, ``xvd``)
# AND macOS/BSD (``disk``, ``rdisk``) so ``dd of=/dev/disk0 ...`` and
# ``cat foo > /dev/rdisk0`` are denied across platforms.
# An optional opening quote (``'`` / ``"``) is allowed so quoted shell
# forms — ``dd of="/dev/sda"``, ``cat image > '/dev/disk0'`` — match
# alongside the bare-literal forms.
_HARDLINE_RAW_DEVICE = (
    r"""["']?/dev/(?:sd|nvme|hd|mmcblk|vd|xvd|r?disk)"""
)

# (regex_source, human-readable description)
_HARDLINE_PATTERNS: List[Tuple[str, str]] = [
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"rm\s+"
        + _HARDLINE_RM_ARGS_LOOKAHEAD,
        "recursive delete of root / system directory / home",
    ),
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"mkfs(?:\.[a-z0-9]+)?\b",
        "filesystem creation (mkfs)",
    ),
    (
        _CMDPOS + _HARDLINE_BIN_PATH
        + r"dd\b[^\n]*\bof=" + _HARDLINE_RAW_DEVICE,
        "dd write to raw block device",
    ),
    (
        r">\s*" + _HARDLINE_RAW_DEVICE,
        "redirect output to raw block device",
    ),
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"kill\s+-(?:1|HUP)\s+-1\b",
        "kill -HUP broadcast",
    ),
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"kill\s+-(?:9|KILL)\s+-1\b",
        "kill -KILL broadcast",
    ),
    (_CMDPOS + _HARDLINE_BIN_PATH + r"shutdown\b", "shutdown"),
    (_CMDPOS + _HARDLINE_BIN_PATH + r"reboot\b", "reboot"),
    (_CMDPOS + _HARDLINE_BIN_PATH + r"halt\b", "halt"),
    (_CMDPOS + _HARDLINE_BIN_PATH + r"poweroff\b", "poweroff"),
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"(?:tel)?init\s+[06]\b",
        "init runlevel 0/6",
    ),
    (
        _CMDPOS + _HARDLINE_BIN_PATH
        + r"systemctl\s+(?:poweroff|reboot|halt|kexec)\b",
        "systemctl power transition",
    ),
    # Disk-destruction tools that end with a raw block device argument.
    # Each lookahead requires the device to appear *somewhere* on the
    # same logical command (no shell separators in between), so flags
    # before the device — ``shred -n 1 -z /dev/sda``, ``parted -s
    # /dev/sda mklabel gpt`` — are matched alongside the
    # ``tool /dev/sda`` form. The lookahead does not consume input, so
    # one tool name + one raw-device target pairs unambiguously.
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"shred\b"
        r"(?=[^\n;&|]*\s" + _HARDLINE_RAW_DEVICE + r")",
        "shred raw block device",
    ),
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"wipefs\b"
        r"(?=[^\n;&|]*\s" + _HARDLINE_RAW_DEVICE + r")",
        "wipefs raw block device",
    ),
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"blkdiscard\b"
        r"(?=[^\n;&|]*\s" + _HARDLINE_RAW_DEVICE + r")",
        "blkdiscard raw block device",
    ),
    (
        _CMDPOS + _HARDLINE_BIN_PATH
        + r"(?:parted|sgdisk|fdisk|cfdisk|gdisk)\b"
        # Read-only list/print modes (``fdisk -l /dev/sda``,
        # ``parted -l``, ``sgdisk --print``) are routine inspections
        # and should not hit the floor. The negative lookahead skips
        # the match when ``-l`` / ``--list`` / ``-p`` / ``--print``
        # appears anywhere in the same logical command.
        r"(?![^\n;&|]*(?:[ \t]-l\b|[ \t]--list\b|[ \t]-p\b|[ \t]--print\b))"
        r"(?=[^\n;&|]*\s" + _HARDLINE_RAW_DEVICE + r")",
        "partition tool on raw block device",
    ),
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"tee\b"
        r"(?=[^\n;&|]*\s" + _HARDLINE_RAW_DEVICE + r")",
        "tee write to raw block device",
    ),
    # cryptsetup luksFormat irreversibly encrypts (i.e. wipes) a
    # device. The subcommand can appear after flags, so a permissive
    # in-between region is allowed.
    (
        _CMDPOS + _HARDLINE_BIN_PATH
        + r"cryptsetup\b(?:[ \t]+\S+)*\s+luksFormat\b",
        "cryptsetup luksFormat",
    ),
    # macOS / BSD diskutil: eraseDisk / secureErase / zeroDisk /
    # eraseVolume / reformat all destroy the target. The disk
    # identifier (``disk0``, ``/dev/disk0``) is a separate operand;
    # detection on the subcommand alone is sufficient — there is no
    # benign use of these subcommands.
    (
        _CMDPOS + _HARDLINE_BIN_PATH
        + r"diskutil\s+(?:eraseDisk|secureErase|zeroDisk|eraseVolume|reformat)\b",
        "diskutil destructive op",
    ),
    # ``xargs ... rm -rf`` builds the destructive operand from stdin
    # — the floor cannot see the operand, but the combination of
    # ``xargs``, ``rm``, and both recursive+force flags is essentially
    # never benign. ``xargs rm`` alone (no ``-rf``) stays outside the
    # floor since plain rm of an explicit list is recoverable via
    # backups / undelete tools and is a normal admin operation.
    # The intermediate-token skip is lazy + permissive: xargs takes
    # flags with their own value arguments (``-I {}``, ``-n 10``,
    # ``-P 4``, ``-d '\n'``) where the value is not a flag, so a
    # restrictive flag-only class would miss those forms. We instead
    # accept any non-whitespace tokens between ``xargs`` and the
    # destructive command name; the lazy quantifier finds the first
    # ``rm`` to anchor on, and the ``-r``/``-f`` lookaheads still
    # gate the match.
    (
        _CMDPOS + r"xargs\b(?:[ \t]+\S+)*?[ \t]+"
        + _HARDLINE_BIN_PATH + r"rm\b"
        r"(?=[^\n;&|]*" + _RM_RECURSIVE + r"\b)"
        r"(?=[^\n;&|]*" + _RM_FORCE + r"\b)",
        "xargs rm with recursive+force flags",
    ),
    # ``find <root-or-system-path> ... -delete`` walks and deletes the
    # tree rooted at the given path. The lookahead requires both a
    # destructive root path and a ``-delete`` action on the same
    # logical command — ``find /tmp -delete`` (a benign cleanup) does
    # NOT match because ``/tmp`` is not in the destructive-path set.
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"find\b"
        r"(?=[^\n;&|]*\s" + _HARDLINE_RM_PATHS + r")"
        r"(?=[^\n;&|]*-delete\b)",
        "find -delete on root/system path",
    ),
    # ``find <root-or-system-path> ... -exec rm -rf {}`` is the
    # idiomatic find-driven recursive delete. Same destructive-path
    # gate as ``-delete``; the rm wrapper inside ``-exec`` is what
    # actually runs against each visited path.
    (
        _CMDPOS + _HARDLINE_BIN_PATH + r"find\b"
        r"(?=[^\n;&|]*\s" + _HARDLINE_RM_PATHS + r")"
        r"(?=[^\n;&|]*-exec(?:dir)?\s+(?:[\w/]*/)?rm\b)"
        r"(?=[^\n;&|]*" + _RM_FORCE + r"\b)",
        "find -exec rm on root/system path",
    ),
]

_HARDLINE_PATTERNS_COMPILED: List[Tuple["re.Pattern[str]", str]] = [
    (re.compile(src, re.IGNORECASE), desc) for src, desc in _HARDLINE_PATTERNS
]

# First-char set for the ``_CMDPOS_HEAD`` separator alternation
# ``(?:^|[;&|`\n\r({!]|\$\(|\b(?:then|do|else|elif)\b)``. When a hardline
# match starts at offset 0 in the normalized view AND that offset
# doesn't begin with one of these characters, the regex anchored at
# ``^`` and consumed straight into the command-name portion — i.e. the
# command begins at the very start of the input. The start of the
# command is always at top-level shell (no enclosing quote/cmdsub
# context can wrap the entire input), so the literal-quote and escape
# filters don't apply: matches like ``'rm' -rf /``, ``\rm -rf /``,
# ``'r''m' -rf /``, where the shell-word view collapsed quote/escape
# splits in the command name itself, are real bypasses we need to
# block. Any other start offset (including 0 when ``norm[0]`` IS one
# of these chars, e.g. ``$(...)`` at the very beginning) means the
# regex consumed a real separator character whose context we still
# need to validate.
_CMDPOS_SEP_FIRST_CHARS = set("$;&|()`{!\n\r")


def _position_contexts(
    cmd: str,
) -> Tuple[List[Optional[str]], set]:
    """For each position in ``cmd``, return ``(contexts, escaped)``.

    ``contexts[i]`` is the innermost shell context at offset ``i`` and
    takes one of these values:

    - ``None``     — top-level shell text
    - ``"'"``      — inside a single-quoted string (fully literal)
    - ``'"'``      — inside a double-quoted string (literal text, but
                     ``$(...)`` and `` `...` `` substitutions are still
                     evaluated)
    - ``"$("``     — inside a ``$(...)`` command substitution
    - ``"`"``      — inside a backtick ``` `...` ``` command substitution

    ``escaped`` is the set of positions whose preceding ``\\`` made the
    character at that position a literal — ``\\$`` and ``\\``` inside
    a double-quoted string don't actually open a command substitution,
    so the hardline filter must reject matches whose start position is
    in this set (otherwise ``echo "\\$(rm -rf /)"`` is a false positive
    even though it only prints text).

    The hardline post-filter uses both maps to decide whether a matched
    danger position is real shell syntax or literal text:

    - top-level / ``$(`` / ``` ` ``` → real shell, accept the match
    - single quote → fully literal, reject
    - double quote → literal text, but accept the match if its first
      character is ``$`` or ``` ` ``` *and* it isn't in ``escaped`` (it
      opens a *new* substitution)

    The state machine handles arbitrary nesting (``echo "$(echo ok;
    rm -rf /)"`` — the ``;`` inside ``$(...)`` is shell context even
    though the surrounding ``"..."`` is a double-quoted literal). Any
    unclosed construct extends to end-of-string so a malformed input
    never falls back to "treat as top-level shell" by accident.
    """
    n = len(cmd)
    contexts: List[Optional[str]] = [None] * n
    escaped: set = set()
    stack: List[str] = []
    i = 0
    while i < n:
        c = cmd[i]
        cur = stack[-1] if stack else None
        contexts[i] = cur

        # Inside a single quote: only the closing ``'`` matters; nothing
        # else is processed (no escapes, no expansions).
        if cur == "'":
            if c == "'":
                stack.pop()
            i += 1
            continue

        # Backslash escape — consume next char with current context and
        # record it as ``escaped`` so the filter can reject false
        # positives like ``echo "\$(rm -rf /)"``. Single-quote context
        # is already handled above, so the escape rule only fires in
        # top-level / double-quote / cmdsub regions.
        if c == "\\" and i + 1 < n:
            escaped.add(i + 1)
            contexts[i + 1] = cur
            i += 2
            continue

        # Inside a double quote: closing ``"`` ends it; ``$(...)`` and
        # `` `...` `` are still active.
        if cur == '"':
            if c == '"':
                stack.pop()
                i += 1
                continue
            if c == "$" and i + 1 < n and cmd[i + 1] == "(":
                stack.append("$(")
                if i + 1 < n:
                    contexts[i + 1] = "$("
                i += 2
                continue
            if c == "`":
                stack.append("`")
                i += 1
                continue
            i += 1
            continue

        # Inside ``$(...)`` cmdsub — like top-level shell, but ``)``
        # closes it.
        if cur == "$(":
            if c == ")":
                stack.pop()
                i += 1
                continue
            if c == "'":
                stack.append("'")
                i += 1
                continue
            if c == '"':
                stack.append('"')
                i += 1
                continue
            if c == "$" and i + 1 < n and cmd[i + 1] == "(":
                stack.append("$(")
                if i + 1 < n:
                    contexts[i + 1] = "$("
                i += 2
                continue
            if c == "`":
                stack.append("`")
                i += 1
                continue
            i += 1
            continue

        # Inside backtick cmdsub — closing backtick ends it.
        if cur == "`":
            if c == "`":
                stack.pop()
                i += 1
                continue
            if c == "'":
                stack.append("'")
                i += 1
                continue
            if c == '"':
                stack.append('"')
                i += 1
                continue
            if c == "$" and i + 1 < n and cmd[i + 1] == "(":
                stack.append("$(")
                if i + 1 < n:
                    contexts[i + 1] = "$("
                i += 2
                continue
            i += 1
            continue

        # Top-level: open new context as needed.
        if c == "'":
            stack.append("'")
            i += 1
            continue
        if c == '"':
            stack.append('"')
            i += 1
            continue
        if c == "$" and i + 1 < n and cmd[i + 1] == "(":
            stack.append("$(")
            if i + 1 < n:
                contexts[i + 1] = "$("
            i += 2
            continue
        if c == "`":
            stack.append("`")
            i += 1
            continue
        i += 1
    return contexts, escaped


def _try_consume_dollar_quote(
    cmd: str, i: int, n: int,
) -> Optional[Tuple[int, str]]:
    """Try to consume a ``$'...'`` or ``$"..."`` opener at ``cmd[i]``.

    ``cmd[i]`` must be ``$``. Returns ``(new_i, decoded_body)`` when
    ``cmd[i+1]`` is ``'`` (ANSI-C quoting — body is decoded by
    :func:`_decode_ansi_c`) or ``"`` (locale string — body is emitted
    literally; bash performs gettext lookup at runtime, but the source
    string is what determines the destructive intent we scan for).
    Returns ``None`` for any other follower (the caller falls through
    to the existing ``$(`` / bare-``$`` handling).

    Bash treats ``$'...'`` and ``$"..."`` as a single shell word whose
    decoded value is what gets parsed as argv. From a destructiveness
    standpoint the decoded chars are *executed text* — the same as if
    they appeared at top-level — even though they live inside a
    syntactic quote span. The closing quote is found while skipping
    backslash-escaped quote chars (``\\'`` inside ``$'...'``,
    ``\\"`` inside ``$"..."``) so attacker forms like
    ``$'rm \\'-rf\\' /'`` are walked correctly.
    """
    if i + 1 >= n:
        return None
    nxt = cmd[i + 1]
    if nxt == "'":
        j = i + 2
        while j < n:
            if cmd[j] == "\\" and j + 1 < n:
                j += 2
            elif cmd[j] == "'":
                break
            else:
                j += 1
        body = cmd[i + 2:j]
        return (j + 1 if j < n else j), _decode_ansi_c(body)
    if nxt == '"':
        j = i + 2
        while j < n:
            if cmd[j] == "\\" and j + 1 < n:
                j += 2
            elif cmd[j] == '"':
                break
            else:
                j += 1
        body = cmd[i + 2:j]
        return (j + 1 if j < n else j), body
    return None


def _shell_word_normalize(cmd: str) -> Tuple[str, List[int]]:
    """Return a shell-word-unquoted view of ``cmd`` plus an index map.

    Walks ``cmd`` and removes quote boundary characters (``"`` and
    ``'``) and the leading backslash of any ``\\X`` escape, while
    preserving the *content* of those quoted/escaped regions. The
    resulting string is what the shell would see *as far as command-
    word concatenation goes*: forms like ``r"m"``, ``r\\m``,
    ``'r''m'``, ``m"k"fs.ext4``, and ``\\rm`` all collapse to the
    single shell word ``rm`` / ``mkfs.ext4`` so the hardline command-
    name patterns can detect quote-split bypasses.

    Returns ``(normalized, idx_map)`` where ``idx_map[i]`` is the
    *original* offset in ``cmd`` of ``normalized[i]``. The caller uses
    this map to query the per-position quote/escape contexts of the
    original string, so literal-quoted data (``echo "rm -rf /"``) is
    still rejected by :func:`_hardline_match` — its normalized text
    looks dangerous, but the mapped start position lives inside a
    literal double-quoted region where bash never executes it.

    The walker mirrors :func:`_position_contexts`: same state machine,
    same handling of nested ``$(...)`` and `` `...` ``, same
    backslash-escape semantics (no escapes inside single quotes). The
    only divergence is the emit policy — ``_position_contexts``
    records *where* every char lives, while this function decides
    *whether* to keep each char in the unquoted view.

    Quote boundary chars (``"`` ``'``) are dropped because they are
    syntactic, not data. The opening ``$`` and ``(`` of a command
    substitution ARE kept (they're shell syntax executed by bash) so
    a separator like ``$(`` survives in normalized form. The same
    applies to backticks. Inside ``$(...)`` and `` `...` `` the
    handling is identical to top-level — those are real shell
    contexts, so quotes there get the same treatment.
    """
    n = len(cmd)
    norm: List[str] = []
    idx_map: List[int] = []
    stack: List[str] = []
    i = 0
    while i < n:
        c = cmd[i]
        cur = stack[-1] if stack else None

        if cur == "'":
            # Single-quoted: only the closing ``'`` has meaning. The
            # quote char itself is dropped from the view; everything
            # else is emitted as literal content.
            if c == "'":
                stack.pop()
                i += 1
                continue
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue

        # Backslash escape (in any context except single-quote, which
        # is handled above). The backslash is dropped; the escaped
        # character is emitted at its original offset so the index map
        # points the filter at the actual literal char.
        if c == "\\" and i + 1 < n:
            norm.append(cmd[i + 1])
            idx_map.append(i + 1)
            i += 2
            continue

        if cur == '"':
            if c == '"':
                # Closing of double-quote: drop from view.
                stack.pop()
                i += 1
                continue
            if c == "$" and i + 1 < n and cmd[i + 1] == "(":
                # ``$(...)`` opens a command substitution even inside
                # double quotes. Keep the opener so separator regexes
                # still see ``$(``.
                stack.append("$(")
                norm.append("$")
                idx_map.append(i)
                norm.append("(")
                idx_map.append(i + 1)
                i += 2
                continue
            if c == "`":
                stack.append("`")
                norm.append("`")
                idx_map.append(i)
                i += 1
                continue
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue

        # Inside ``$(...)`` cmdsub — like top-level, but ``)`` closes.
        # Keep the closer so separator regexes still see ``)``.
        if cur == "$(" and c == ")":
            stack.pop()
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue
        # Inside backtick cmdsub — closing backtick ends it. Keep it.
        if cur == "`" and c == "`":
            stack.pop()
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue

        # Top-level / inside cmdsub: open new contexts, drop quote
        # boundary chars, otherwise emit literally.
        if c == "'":
            stack.append("'")
            i += 1
            continue
        if c == '"':
            stack.append('"')
            i += 1
            continue
        if c == "$":
            # ``$'...'`` (ANSI-C) and ``$"..."`` (locale string) are
            # bash-specific quote forms whose decoded body is the
            # *executed* shell word. Decode the body and emit the
            # decoded chars mapped to the offset of the leading ``$``
            # — that offset lives at top-level shell context in
            # :func:`_position_contexts`, so the hardline filter sees
            # the decoded text as real shell, not as quoted literal.
            # Without this, attacker forms like ``$'rm' -rf /`` and
            # ``rm -rf $'/etc'`` slip through because the body chars
            # carry the inner ``'``/``"`` context. Bash treats
            # ``$'...'`` / ``$"..."`` inside another quote as literal,
            # so this branch only fires in real shell contexts
            # (top-level / cmdsub / backtick — same set the rest of
            # this code path covers).
            dq = _try_consume_dollar_quote(cmd, i, n)
            if dq is not None:
                new_i, decoded = dq
                for ch in decoded:
                    norm.append(ch)
                    idx_map.append(i)
                i = new_i
                continue
            if i + 1 < n and cmd[i + 1] == "(":
                stack.append("$(")
                norm.append("$")
                idx_map.append(i)
                norm.append("(")
                idx_map.append(i + 1)
                i += 2
                continue
            # Bare ``$`` (e.g., ``$VAR``) — emit literally.
            norm.append(c)
            idx_map.append(i)
            i += 1
            continue
        if c == "`":
            stack.append("`")
            norm.append("`")
            idx_map.append(i)
            i += 1
            continue

        norm.append(c)
        idx_map.append(i)
        i += 1

    return "".join(norm), idx_map


# Pre-extract pattern for ``sh -c 'script'`` / ``bash -c "script"`` /
# similar shell-interpreter wrappers. The captured group is the script
# body, which the floor re-checks recursively as if it were a top-level
# command — that catches ``sh -c 'echo ok; rm -rf /'`` where the
# destructive operation isn't the first token of the wrapped script.
# The wrapper is anchored at a real command position by ``_CMDPOS_HEAD``
# so plain-text mentions of ``sh -c '...'`` (for example
# ``echo sh -c 'rm -rf /'`` where ``sh`` is just an argument to echo)
# don't trigger body recursion.
_SHELL_SCRIPT_WRAPPER = re.compile(
    _CMDPOS_HEAD
    # The interpreter token can be either:
    #   1. A literal shell name (``sh``, ``bash``, ``/bin/zsh``, ...).
    #   2. A variable expansion (``$VAR``, ``${VAR}``) — common
    #      indirection idiom (``s=bash; $s -c '...'``). The body
    #      recursion catches the destructive command iff the body
    #      itself is destructive; benign ``$EDITOR -c ":wq"`` does
    #      not surface a deny because the recursion runs the same
    #      hardline patterns and ``:wq`` never matches them.
    + r"(?:(?:[\w/]*/)?(?:sh|bash|zsh|dash|ash|ksh|csh|tcsh|busybox)"
    + r"|\$\w+|\$\{\w+\})"
    + r"(?:\s+-[^\s]*)*"
    + r"\s+(?:-c|-[a-zA-Z]*c)\s+"
    # Body opener forms: ``'body'`` (single-quoted), ``"body"``
    # (double-quoted), ``$'body'`` (bash/zsh ANSI-C quoting — escapes
    # like ``\\n`` and ``\\t`` are interpreted), ``$"body"`` (bash/zsh
    # locale string — runtime gettext lookup). The leading ``$`` is
    # captured so the body extractor can decide whether to ANSI-C
    # decode the body before recursing.
    + r"(?:"
    +     r"(\$)?'([^']*)'"
    +     r"|(\$)?\"((?:[^\"\\]|\\.)*)\""
    + r")",
    re.IGNORECASE,
)


# Here-string ``shell-interp <<< 'body'`` — the body is fed to the
# interpreter on stdin, where the shell parses it as code. This is the
# inline counterpart of the here-doc owner check in
# ``_heredoc_owner_is_shell``; ``<<<`` carries its body on the same
# logical command line, so the body extraction is symmetric to the
# ``-c`` wrapper above.
_HERESTRING_TO_SHELL = re.compile(
    _CMDPOS_HEAD
    + r"(?:[\w/]*/)?(?:sh|bash|zsh|dash|ash|ksh|csh|tcsh|busybox)\b"
    + r"(?:[ \t]+(?:-[^\s]+|[A-Za-z_]\w*=\S+))*"
    + r"[ \t]+<<<[ \t]*"
    + r"(?:"
    +     r"(\$)?'([^']*)'"
    +     r"|(\$)?\"((?:[^\"\\]|\\.)*)\""
    + r")",
    re.IGNORECASE,
)


# Pipe-to-shell ``echo ARGS | sh`` / ``printf ARGS | bash`` — the
# left-hand side prints text to stdout, and the right-hand shell reads
# it as a script. Catches the prompt-injection idiom where the
# destructive command lives inside an ``echo``/``printf`` arg quoted
# at the outer layer (``echo "rm -rf /" | sh``). The captured ARGS
# region is shell-word-normalized before recursion so quoted forms
# (``"rm -rf /"``, ``'rm -rf /'``) collapse to executable text just
# like the cmd would see at runtime.
_ECHO_PIPE_TO_SHELL = re.compile(
    _CMDPOS_HEAD
    + r"(?:[\w/]*/)?(?:echo|printf)\b"
    + r"(?:[ \t]+-[^\s|]+)*"
    + r"[ \t]+([^|\n]+?)"
    + r"[ \t]*\|[ \t]*"
    + r"(?:[\w/]*/)?(?:sh|bash|zsh|dash|ash|ksh|csh|tcsh|busybox)\b",
    re.IGNORECASE,
)


# Process substitution feeding a shell loader:
# ``source <(echo SCRIPT)``, ``. <(echo SCRIPT)``,
# ``bash <(echo SCRIPT)``. Bash spawns ``echo SCRIPT`` against a fifo
# and then loads/executes from that fifo, so SCRIPT IS the executed
# shell text. The capture is the args region of the inner echo /
# printf; the floor recurses into a normalized version of it, the
# same way ``echo X | sh`` is handled.
_PROCSUBST_TO_SHELL = re.compile(
    _CMDPOS_HEAD
    # ``.`` (the POSIX dot-source builtin) is non-word, so a trailing
    # ``\b`` would never match — bash boundary semantics treat
    # ``. <(...)`` as a real command boundary because the next char is
    # whitespace. Match each alternative with its appropriate
    # right-edge guard: ``source``/shell-interp use ``\b``, the dot
    # case uses an explicit whitespace lookahead.
    + r"(?:source\b|\.(?=[ \t])|(?:[\w/]*/)?(?:sh|bash|zsh|dash|ash|ksh|csh|tcsh|busybox)\b)"
    + r"(?:[ \t]+(?:-[^\s]+|[A-Za-z_]\w*=\S+))*"
    + r"[ \t]+<\([ \t]*"
    + r"(?:[\w/]*/)?(?:echo|printf)\b"
    + r"(?:[ \t]+-[^\s|]+)*"
    + r"[ \t]+([^)\n]+?)"
    + r"[ \t]*\)",
    re.IGNORECASE,
)


# When the body of ``shell-interp -c <body>`` is itself a command
# substitution whose payload comes from a literal ``echo``/``printf``
# (``bash -c "$(echo rm -rf /)"``), the *runtime* script is the
# echo/printf args, not the textual body. We re-extract that inner
# args region so the recursive scan sees what bash actually executes.
# Anchored to the start of the body (after stripping leading
# whitespace) so we don't false-trigger on bodies that merely contain
# a ``$(echo ...)`` somewhere — only the form where the entire body
# IS the cmdsub matters for the substitution-as-script bypass.
_CMDSUB_ECHO_AS_BODY = re.compile(
    r"^[ \t]*\$\([ \t]*"
    + r"(?:[\w/]*/)?(?:echo|printf)\b"
    + r"(?:[ \t]+-[^\s]+)*"
    + r"[ \t]+(.+?)"
    + r"[ \t]*\)[ \t]*$",
    re.IGNORECASE | re.DOTALL,
)


# Cmdsub at command position whose payload comes from a literal
# echo / printf: ``$(echo rm -rf /)`` or ``` `echo rm -rf /` ``` typed
# directly at the prompt, not inside double quotes that an outer
# echo/cat would just print. Bash captures the inner echo's output
# and re-parses it as a command, so the printed args become the
# executed command line. The match is anchored at a real
# command-position separator via ``_CMDPOS_HEAD``; the post-filter
# ``_is_real_shell_pos`` then rejects matches that turned out to live
# inside a literal-quoted region.
_CMDSUB_ECHO_AT_CMDPOS = re.compile(
    _CMDPOS_HEAD
    + r"(?:\$\(|`)[ \t]*"
    + r"(?:[\w/]*/)?(?:echo|printf)\b"
    + r"(?:[ \t]+-[^\s]+)*"
    + r"[ \t]+([^)`\n]+?)"
    + r"[ \t]*(?:\)|`)",
    re.IGNORECASE,
)


def _is_real_shell_pos(
    text: str,
    contexts: List[Optional[str]],
    escaped: set,
    start: int,
) -> bool:
    """True when ``text[start]`` is at executable shell context.

    Mirrors the in-line filter the ``_SHELL_SCRIPT_WRAPPER`` loop has
    used since this module was written: skip matches whose start
    position was backslash-escaped (``\\$(...)``), or that sit in a
    fully-literal quoted region with no substitution opener as the
    first character. Centralized so the new ``<<<`` / pipe / process-
    substitution extractors apply the same context rules.
    """
    if start in escaped:
        return False
    ctx = contexts[start] if 0 <= start < len(contexts) else None
    head = text[start:start + 1]
    if ctx in ("'", '"') and head not in ("$", "`"):
        return False
    return True


def _normalize_indirect_body(text: str) -> str:
    """Return the shell-word-normalized view of ``text``.

    Used by the indirect-execution extractors — ``echo ARG | sh``,
    ``bash <<< 'ARG'``, ``source <(echo ARG)`` — to turn the captured
    arg region (which still contains the shell quoting that surrounded
    it) into the literal byte sequence the downstream interpreter sees
    at runtime. Without this step a body of ``"rm -rf /"`` would
    recurse with its outer double quotes still attached, and the
    inner positions would still be marked as quote-context by
    ``_position_contexts`` — leaving the bypass open.
    """
    norm, _ = _shell_word_normalize(text)
    return norm


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


# Here-doc opener: ``<<TAG``, ``<<-TAG``, ``<<'TAG'``, ``<<"TAG"``.
# Group 1 captures the optional ``-`` (strip-leading-tabs form).
# Group 3 is the bare TAG word; group 2 is the optional surrounding
# quote that ``\\2`` requires the closer to repeat.
_HEREDOC_OPENER_RE = re.compile(r"<<(-?)(['\"]?)(\w+)\2")

# Shell interpreter token, anchored at a real word boundary (preceded
# by start-of-line or whitespace, followed by whitespace or
# end-of-line). Used to detect when a here-doc's launching line feeds
# its body to ``sh`` / ``bash`` / etc. — in that case the body is
# *executed as shell code*, not consumed as data, so the hardline mask
# would hide a destructive bypass like ``bash <<EOF\\nrm -rf /\\nEOF``.
# The match is intentionally generous (anywhere on the launching line,
# including past the ``<<TAG`` opener — covers pipelines like ``cat
# <<EOF | bash``); the cost of a false positive is a benign here-doc
# body being scanned for destructive intent, which is acceptable for
# a safety floor.
_SHELL_INTERP_LINE_RE = re.compile(
    r"(?:^|\s)(?:[\w/]*/)?(?:sh|bash|zsh|dash|ash|ksh|csh|tcsh|busybox)(?=\s|$)"
)


def _heredoc_owner_is_shell(cmd: str, heredoc_pos: int) -> bool:
    """Return True when the launching line of ``cmd[heredoc_pos:]`` is
    likely feeding its body to a shell interpreter.

    Looks at the entire line containing the ``<<TAG`` opener (the body
    starts at the next newline). If a shell-interpreter token appears
    anywhere on that line — at command position, on either side of a
    pipe, or as the target of a redirection — we conservatively treat
    the body as executable shell code and skip masking. That way
    ``bash <<EOF\\nrm -rf /\\nEOF``, ``cat <<EOF | bash\\n...\\nEOF``,
    and ``sudo bash <<EOF`` all expose their body to the regular
    hardline scan instead of being neutralized into spaces.
    """
    line_start = cmd.rfind("\n", 0, heredoc_pos) + 1
    line_end = cmd.find("\n", heredoc_pos)
    if line_end == -1:
        line_end = len(cmd)
    return _SHELL_INTERP_LINE_RE.search(cmd[line_start:line_end]) is not None


@lru_cache(maxsize=256)
def _heredoc_closer_re(is_dash: bool, tag: str) -> "re.Pattern[str]":
    """Compiled closer regex for ``\\nTAG`` (or ``\\n[\\t]*TAG`` with ``<<-``).

    Cached because the same shell command can repeat the same TAG and
    ``_mask_heredoc_bodies`` runs on every ``run_shell_command``
    permission check.
    """
    leading = r"[\t]*" if is_dash else r""
    return re.compile(rf"\n{leading}{re.escape(tag)}[ \t]*(?=\n|$)")


def _mask_heredoc_bodies(cmd: str) -> str:
    """Replace each here-doc body in ``cmd`` with spaces.

    Bash here-doc syntax (``cmd <<TAG\\nbody\\nTAG\\n``,
    ``cmd <<-TAG\\n\\tbody\\n\\tTAG\\n``, ``cmd <<'TAG'\\n...\\nTAG``)
    feeds the BODY as DATA to the host command — cat / dd / grep /
    a generator script reads it through stdin, but the shell never
    executes it. Embedded ``\\n`` chars are line breaks in the data
    stream, not command separators, so the hardline scanner must not
    treat ``cat <<EOF\\nrm -rf /\\nEOF`` as if ``rm -rf /`` sat at a
    fresh command position after the inline newline.

    The mask is length-preserving: every body character (including
    body newlines) becomes a space so downstream
    :func:`_position_contexts` and :func:`_shell_word_normalize`
    index maps still align with the original ``cmd``. The launching
    ``cat <<EOF`` line and the closing ``EOF`` line are left intact
    so the regex can still see the surrounding shell command.

    Quoted or escaped ``<<`` text (``echo "<<EOF"``,
    ``echo \\<\\<EOF``) is *not* a here-doc opener — bash sees those
    as literal text. The detector consults
    :func:`_position_contexts` so only top-level / cmdsub openers
    are processed.
    """
    if "<<" not in cmd:
        return cmd
    contexts, escaped = _position_contexts(cmd)
    chars: Optional[List[str]] = None
    n = len(cmd)
    i = 0
    while i < n:
        if (
            i + 1 < n
            and cmd[i] == "<" and cmd[i + 1] == "<"
            and i not in escaped
            and contexts[i] in (None, "$(", "`")
        ):
            m = _HEREDOC_OPENER_RE.match(cmd, i)
            if m is None:
                i += 1
                continue
            is_dash = bool(m.group(1))
            tag = m.group(3)
            if not tag:
                i += 1
                continue
            opener_end = m.end()
            # Find end of the opener line. Bash allows other
            # redirections / commands on the same line as ``<<TAG``
            # (``cat <<EOF | wc -l``), and the body starts at the
            # next newline regardless.
            nl = cmd.find("\n", opener_end)
            if nl == -1:
                # Declared but no body — leave cmd alone, advance past
                # the opener.
                i = opener_end
                continue
            body_start = nl + 1
            # Closer line: ``\nTAG`` (plain ``<<``) or
            # ``\n[\t]*TAG`` (``<<-``), optionally followed by
            # trailing whitespace, ending at ``\n`` or end-of-string.
            closer_m = _heredoc_closer_re(is_dash, tag).search(cmd, body_start - 1)
            if closer_m is None:
                # Unterminated here-doc — body runs to end of cmd.
                body_end = n
            else:
                # ``closer_m.start()`` is the ``\n`` *before* TAG.
                # Mask the body up to (but not including) that
                # newline, leaving the closer line visible.
                body_end = closer_m.start()
            if _heredoc_owner_is_shell(cmd, i):
                # Launching line feeds the body to a shell interpreter
                # (``bash <<EOF``, ``cat <<EOF | bash``, ...). The body
                # is executable shell code, not data — leave it visible
                # so the regular hardline scan picks up a destructive
                # ``rm -rf /`` after the body's ``\\n`` separators.
                # Otherwise masking would create a real bypass: the
                # destructive command would be replaced with spaces and
                # the floor would never see it.
                i = body_end
                continue
            if chars is None:
                # Allocate the mutable buffer lazily — when ``<<`` only
                # appears as text (escaped, quoted, or no opener
                # follows) we never enter this branch and the original
                # ``cmd`` is returned unchanged.
                chars = list(cmd)
            for k in range(body_start, body_end):
                chars[k] = " "
            i = body_end
            continue
        i += 1
    return cmd if chars is None else "".join(chars)


def _hardline_match(
    cmd: str,
    contexts: Optional[List[Optional[str]]] = None,
    escaped: Optional[set] = None,
) -> Optional[str]:
    """Run hardline patterns against ``cmd`` and return a deny reason
    for the first valid match (filtered by shell context).

    A "valid" match is one whose start position is in real shell
    context — top-level, inside a command substitution, or starting a
    new substitution inside a double-quoted string — *and* its first
    character isn't backslash-escaped. Matches whose start sits in a
    fully-literal region (single quotes, or arbitrary text inside
    double quotes), or whose opening character was preceded by ``\\``,
    are skipped. The caller can pass in pre-computed ``contexts`` and
    ``escaped`` so the BFS in :func:`hardline_check` doesn't pay to
    recompute them twice per iteration.

    Patterns are matched against a *shell-word-unquoted* view of
    ``cmd`` (see :func:`_shell_word_normalize`), not the raw text.
    That way command-name forms split by per-character shell quoting
    (``r"m" -rf /``, ``r\\m -rf /``, ``m"k"fs.ext4 /dev/sda1``,
    ``'r''m' -rf /``) — which bash unquotes to ``rm`` / ``mkfs.ext4``
    before execution — still hit the floor. Each match's start in the
    normalized view is mapped back to its origin offset in ``cmd``,
    and the original ``contexts`` / ``escaped`` of that origin govern
    the literal-vs-executed decision. Quoted-data cases like
    ``echo "rm -rf /"`` therefore stay benign: their normalized
    string looks dangerous, but the mapped start lives inside a
    literal double-quoted region with no substitution opener, so the
    filter rejects them.

    Returns the ``"hardline:<description>"`` reason string for the
    first surviving match, or ``None`` when no match remains.
    """
    if contexts is None or escaped is None:
        contexts, escaped = _position_contexts(cmd)
    norm, idx_map = _shell_word_normalize(cmd)
    norm_n = len(norm)
    for compiled, desc in _HARDLINE_PATTERNS_COMPILED:
        for m in compiled.finditer(norm):
            start = m.start()
            if start >= norm_n:
                # Defensive — ``finditer`` should never return out-of-
                # bounds offsets, but guard against future refactors.
                continue
            if start == 0 and norm[0] not in _CMDPOS_SEP_FIRST_CHARS:
                # ``^``-anchored match where the first emitted char of
                # the normalized view is part of the command name
                # itself (or leading whitespace before it), not a
                # ``$(`` / ``;`` / ``` ` ``` / keyword separator. The
                # command begins at the very start of the input, which
                # is always at top-level shell, so the literal-quote
                # and escape filters don't apply — the shell-word view
                # has already resolved any quote/escape splits in the
                # command name (``'rm' -rf /``, ``\rm -rf /``,
                # ``'r''m' -rf /``). Block.
                return f"{REASON_HARDLINE}:{desc}"
            orig_start = idx_map[start]
            if orig_start in escaped:
                # The mapped origin char was backslash-escaped at the
                # outer layer (``\$``, ``\`` ``, ``\(``, ``\;``, ...).
                # Bash treats the next char literally there, so this
                # isn't actual shell syntax. Note: backslashes that
                # _shell_word_normalize already consumed (escapes of
                # plain word chars, ``\rm``) don't show up here —
                # ``escaped`` records the *original* offset of the
                # escaped char, and idx_map points the regex at that
                # same offset, so those positions are top-level and
                # accepted.
                continue
            ctx = contexts[orig_start] if 0 <= orig_start < len(contexts) else None
            if ctx is None or ctx in ("$(", "`"):
                # Top-level shell text or already inside a command
                # substitution — bash will execute the matched syntax.
                return f"{REASON_HARDLINE}:{desc}"
            if ctx == "'":
                # Single-quoted: fully literal, never executed.
                continue
            # ctx == '"': inside a double-quoted string. Bash still
            # evaluates ``$(...)`` and `` `...` `` here, so the match is
            # real iff its first character opens a new substitution.
            head = cmd[orig_start:orig_start + 1]
            if head in ("$", "`"):
                return f"{REASON_HARDLINE}:{desc}"
    return None


def hardline_check(
    tool_name: str, tool_args: Dict[str, Any],
) -> Optional[str]:
    """Return a ``"hardline:<desc>"`` reason when ``tool_args`` is unrecoverable.

    Only inspects shell commands today: the floor is about preventing
    unrecoverable operations, and ``run_shell_command`` is the single
    surface that can express them. File-write tools have their own
    PathPolicy; other tools have narrow, named effects.

    Each pattern is searched with ``finditer``; matches whose start
    position is in a *literal* shell context are suppressed — that
    protects benign commands like ``echo "(reboot required)"`` or
    ``printf "backup > /dev/disk0"`` from being denied. The shell
    context is computed by :func:`_position_contexts`, which handles
    nested ``$(...)`` and `` `...` `` correctly: a destructive command
    inside command substitution is real shell, even when the outer
    layer is a double-quoted string (``echo "$(echo ok; rm -rf /)"``).

    After the direct check, the function recursively descends into
    ``sh -c '...'`` / ``bash -c "..."`` / similar shell-interpreter
    bodies and reruns the floor against each. The body is *literal* to
    the outer shell but *executed as shell* by the nested interpreter,
    so a destructive command anywhere inside it counts —
    ``sh -c 'echo ok; rm -rf /'`` is denied even though the ``;`` and
    ``rm`` sit inside an outer single-quoted region.

    Returns ``None`` when no surviving match remains. The caller
    (typically :class:`PermissionEngine`) wraps a non-``None`` return
    into a deny decision.
    """
    if tool_name != "run_shell_command":
        return None
    cmd = str(tool_args.get("command", ""))
    if not cmd:
        return None

    # BFS through the original command and all reachable sh -c bodies.
    # Each iteration runs the same matcher on a separate piece of
    # text. Bodies are bounded by the outer command's length, so the
    # queue can't grow unboundedly; the explicit cap is a defense in
    # depth against pathological inputs.
    queue: deque[str] = deque([cmd])
    inspected = 0
    while queue and inspected < 16:
        text = queue.popleft()
        inspected += 1
        # ``$(echo SCRIPT)`` as a whole-body — when a queued text is
        # exactly the cmdsub of an echo / printf, the runtime script is
        # the inner args, not the textual cmdsub itself. Surface it
        # before pattern matching so the recursive scan sees what bash
        # actually executes. This covers ``bash -c "$(echo rm -rf /)"``
        # (body queued by the ``-c`` extractor), ``echo "$(echo rm -rf
        # /)" | bash`` (body queued by the pipe extractor), and any
        # other indirect path that funnels a cmdsub-as-script.
        inner = _CMDSUB_ECHO_AS_BODY.match(text)
        if inner is not None:
            queue.append(_normalize_indirect_body(inner.group(1)))
        # Mask here-doc bodies BEFORE computing contexts or running
        # patterns: ``cat <<'EOF'\nrm -rf /\nEOF`` is data being read
        # by ``cat``, not commands. The mask replaces body chars with
        # spaces so downstream offset-based maps still align with
        # the original.
        text = _mask_heredoc_bodies(text)
        contexts, escaped = _position_contexts(text)
        hit = _hardline_match(text, contexts, escaped)
        if hit is not None:
            return hit
        # Descend into ``sh -c '...'`` bodies that live in real shell
        # context. A literal ``echo "sh -c 'rm -rf /'"`` only *prints*
        # the nested-shell example — its body is never executed, so
        # treating it as a script body would create a false positive.
        # Inside double quotes, however, ``$(...)`` and `` `...` `` ARE
        # executed: ``echo "$(sh -c 'echo ok; rm -rf /')"`` runs the
        # wrapped script, so when the match opens a substitution
        # (head char is ``$`` or `` ` ``) we still descend.
        for m in _SHELL_SCRIPT_WRAPPER.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            sq_dollar = m.group(1)
            sq_body = m.group(2)
            dq_dollar = m.group(3)
            dq_body = m.group(4)
            if sq_body is not None:
                body = sq_body
                if sq_dollar == "$":
                    # ``bash -c $'...'`` — ANSI-C-quoted body. Decode
                    # ``\n`` / ``\t`` / etc. so embedded separators
                    # become real whitespace before the recursive
                    # check sees them.
                    body = _decode_ansi_c(body)
            elif dq_body is not None:
                # ``bash -c $"..."`` (locale string): runtime gettext
                # translation can't introduce destructive intent that
                # wasn't already in the source, so we treat it
                # identically to ``"..."`` here. ``dq_dollar`` is captured
                # by the regex but unused intentionally.
                body = dq_body
            else:
                body = None
            if body:
                queue.append(body)
        # ``bash <<< 'rm -rf /'`` — here-string feeds the body to the
        # interpreter on stdin. The body is recursive shell, so we
        # treat it identically to ``-c <body>``.
        for m in _HERESTRING_TO_SHELL.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            sq_dollar = m.group(1)
            sq_body = m.group(2)
            dq_body = m.group(4)
            if sq_body is not None:
                body = _decode_ansi_c(sq_body) if sq_dollar == "$" else sq_body
            elif dq_body is not None:
                body = dq_body
            else:
                body = None
            if body:
                queue.append(body)
        # ``echo ARGS | sh`` / ``printf ARGS | bash`` — echo writes
        # ARGS to stdout, the right-hand shell reads stdin as a
        # script. The captured ARGS still carries its outer quoting,
        # so normalize before recursion: ``"rm -rf /"`` → ``rm -rf /``,
        # which then matches the rm pattern as if at top level.
        for m in _ECHO_PIPE_TO_SHELL.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            args = m.group(1)
            if args:
                queue.append(_normalize_indirect_body(args))
        # ``source <(echo SCRIPT)`` / ``bash <(echo SCRIPT)`` — process
        # substitution feeds a fifo containing SCRIPT to the shell
        # loader. Same recursion shape as the pipe form.
        for m in _PROCSUBST_TO_SHELL.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            args = m.group(1)
            if args:
                queue.append(_normalize_indirect_body(args))
        # Cmdsub-of-echo at command position: ``$(echo rm -rf /)`` or
        # ``` `echo rm -rf /` ``` typed directly. Bash captures the
        # echo output and re-parses it as a command — the args ARE the
        # script. ``_CMDSUB_ECHO_AS_BODY`` (whole-text match) covers
        # the case where this is queued from a wrapper extraction; this
        # ``finditer`` covers the standalone form sitting at top level
        # of an outer command.
        for m in _CMDSUB_ECHO_AT_CMDPOS.finditer(text):
            if not _is_real_shell_pos(text, contexts, escaped, m.start()):
                continue
            args = m.group(1)
            if args:
                queue.append(_normalize_indirect_body(args))
    return None
