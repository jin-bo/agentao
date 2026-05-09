"""Regex constants and compiled hardline-pattern table.

Pure data — no behavior — so the scanner / heredoc / contexts modules
can each pull the patterns they need without forcing a circular import.

Public-via-package:
  REASON_HARDLINE — the stable ``"hardline"`` source tag re-exported
    from ``agentao.permissions_hardline`` for hosts and audit displays.
"""

from __future__ import annotations

import re
from typing import List, Tuple


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


# ---------------------------------------------------------------------------
# Indirect-execution extractors (sh -c body, here-string, echo-pipe-to-shell,
# process substitution, cmdsub-as-body, cmdsub-at-cmdpos).
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Heredoc-related patterns (used by _heredoc.py).
# ---------------------------------------------------------------------------

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
