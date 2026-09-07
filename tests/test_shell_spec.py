"""The shell spec, the launch it produces, and the record that binds them to a decision.

The property under test throughout is that **one answer governs the whole call**. Which
interpreter will read the command is decided once, at planning time, and everything after
that — the floor's grammar, the permission rule's dialect label, the process that is
actually started, the description the model was given — reads that one answer rather than
re-deriving its own from ``sys.platform``.

Each of those used to be a separate derivation, which is why the shapes here are so
insistent: a spec the executor declares, a launch that names its target completely, and a
frozen record the tool cannot bypass.
"""

from __future__ import annotations

import dataclasses
import sys
from types import MappingProxyType

import pytest

from agentao.capabilities.shell import LocalShellExecutor, ShellRequest, ShellResult
from agentao.capabilities.shell_spec import (
    PASS,
    AbsPath,
    DecidedCall,
    Deny,
    Exhausted,
    LaunchRefused,
    LegacyLaunch,
    ShellBlock,
    ShellDialect,
    ShellSpec,
    default_spec,
    display_name,
    validate,
)
from agentao.tools.base import ToolRegistry
from agentao.tools.shell import ShellTool
from tests.support.launch import interpreter_of


def posix_spec(**over) -> ShellSpec:
    return ShellSpec(dialect=ShellDialect.POSIX, **over)


# ------------------------------------------------------------------- the spec


def test_an_unknown_dialect_is_refused_before_any_rule_matches():
    """``UNKNOWN`` is what a host executor naming no dialect arrives with.

    Refusing on it is the whole point of having the value: the alternative is scanning one
    shell's syntax with another's patterns, which returns a clean result rather than failing.
    """
    assert validate(dataclasses.replace(posix_spec(), dialect=ShellDialect.UNKNOWN)) == (
        "hardline:unknown-dialect-opaque"
    )
    assert validate(posix_spec()) is None


def test_a_spec_cannot_be_assigned_to_after_construction():
    """Re-resolution builds a new object; it never edits the one a call is holding."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        posix_spec().dialect = ShellDialect.CMD  # type: ignore[misc]


def test_the_default_is_todays_shell_on_both_platforms():
    """Nothing configured means nothing changes: cmd on Windows, the POSIX shell elsewhere.

    ``interpreter`` is ``None`` on both, and that ``None`` is load-bearing — it is what makes
    the launch fall through to ``%COMSPEC%`` / ``resolve_shell_executable()``, which is the
    launch that shipped.
    """
    win = default_spec(windows=True)
    assert (win.dialect, win.interpreter) == (ShellDialect.CMD, None)
    other = default_spec(windows=False)
    assert (other.dialect, other.interpreter) == (ShellDialect.POSIX, None)


def test_the_local_executor_answers_one_spec_object_per_call():
    """A call holds one spec until re-resolution swaps it.

    Minting a fresh one on every read would put PowerShell discovery — a filesystem walk —
    on the permission path of every shell command.
    """
    ex = LocalShellExecutor()
    assert ex.shell_spec is ex.shell_spec


def test_a_tool_reads_the_executors_spec_not_its_own_guess():
    """The executor is the party that knows: a Docker or remote one starts a different shell."""

    class RemoteExecutor:
        shell_spec = Exhausted("nothing usable here")

        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = RemoteExecutor()
    assert tool.shell_spec == Exhausted("nothing usable here")


def test_an_executor_predating_this_member_still_gets_todays_default():
    """A host that changed nothing must keep working, so an absent declaration is not a refusal."""

    class OldExecutor:
        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = OldExecutor()
    spec = tool.shell_spec
    assert isinstance(spec, ShellSpec) and spec.interpreter is None


def test_an_executor_predating_the_spec_member_still_satisfies_the_protocol():
    """``ShellExecutor`` is ``@runtime_checkable``, and a non-method member breaks that.

    It would make ``issubclass()`` raise ``TypeError`` for everyone and flip ``isinstance()``
    to ``False`` for every executor written before the member existed — which is why the
    declaration is an optional companion protocol instead.
    """
    from agentao.capabilities.shell import ShellExecutor

    class OldExecutor:
        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    assert issubclass(OldExecutor, ShellExecutor)
    assert isinstance(OldExecutor(), ShellExecutor)
    assert isinstance(LocalShellExecutor(), ShellExecutor)


def test_a_raising_provider_is_a_failure_not_an_absent_declaration():
    """``getattr(x, "shell_spec", None)`` swallows an ``AttributeError`` raised *inside* the
    property, which reads as "declares nothing" and quietly reports the platform default for
    an executor whose resolution actually failed. The planner turns a raise into ``Exhausted``.
    """
    from agentao.runtime.tool_planning import _shell_spec_of

    class Broken:
        name = "run_shell_command"

        @property
        def shell_spec(self):
            raise RuntimeError("resolution failed")

    answer = _shell_spec_of(Broken())
    assert isinstance(answer, Exhausted) and "resolution failed" in answer.reason


def test_the_fallback_spec_is_one_object_per_executor():
    """Memoised per executor, and re-minted when the executor is swapped underneath it."""
    tool = ShellTool()

    class Old:
        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool.shell = Old()
    first = tool.shell_spec
    assert tool.shell_spec is first
    tool.shell = Old()
    assert tool.shell_spec is not first


# --------------------------------------------------------- the registration guard


def test_a_replacement_shell_tool_without_a_spec_is_refused_by_name():
    """The floor gates on this tool's name, so the name is where the guard belongs."""

    class BareTool:
        name = "run_shell_command"

    with pytest.raises(ValueError, match="TOOL-01"):
        ToolRegistry().register(BareTool())  # type: ignore[arg-type]


def test_the_guard_does_not_evaluate_the_provider_while_registering():
    """A provider that walks the filesystem can be slow, and one that raises is not "absent"."""

    class Exploding:
        name = "run_shell_command"

        @property
        def shell_spec(self):
            raise RuntimeError("resolution failed")

    ToolRegistry().register(Exploding())  # type: ignore[arg-type]


def test_the_real_shell_tool_satisfies_its_own_guard():
    """The rule has to admit the thing it was written for, or it is only a wall."""
    ToolRegistry().register(ShellTool())


# ----------------------------------------------------------- the decided record


def decided(body: str, cwd: str, verdict=None, spec=None) -> DecidedCall:
    return DecidedCall(
        spec=default_spec() if spec is None else spec,
        body=body,
        cwd=AbsPath(cwd),
        verdict=verdict or PASS,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX exit code in the probe")
def test_the_launch_runs_the_body_that_was_judged_not_the_argument():
    """Re-reading ``command`` at the launch would be a second source for the text.

    The two disagree here on purpose. A tool that prefers its own argument is a channel that
    gets one command approved and runs another under the same verdict.
    """
    out = ShellTool().execute(
        command="exit 3", working_directory=".", timeout=30, _decided=decided("exit 7", ".")
    )
    # Both halves have to be load-bearing. ``"7" in out`` is satisfied by a stray digit in an
    # error string, which would let an implementation that ran *neither* body pass.
    assert "Exit code: 7" in out
    assert "Exit code: 3" not in out


def test_a_record_carrying_a_deny_refuses_at_the_launch():
    """A record being present is not the same as this call having been allowed."""
    out = ShellTool().execute(
        command="echo hi",
        working_directory=".",
        timeout=30,
        _decided=decided("echo hi", ".", verdict=Deny("permission:denied")),
    )
    assert out.startswith("Error: permission:denied")


def test_the_launch_carries_the_spec_the_decision_froze_not_a_second_read():
    """One spec governs the decision *and* the launch.

    Re-reading the provider inside the launch builder is the same second source the body is
    protected from, one field over: the process would start under whatever re-resolution had
    swapped in, while the verdict was computed against the frozen spec.
    """
    frozen = posix_spec(interpreter=AbsPath("/bin/decided"))
    seen = []

    class Recording:
        shell_spec = posix_spec(interpreter=AbsPath("/bin/current"))

        def run(self, request):
            seen.append(request.launch)
            return ShellResult(returncode=0, stdout=b"", stderr=b"", timed_out=False)

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool = ShellTool()
    tool.shell = Recording()
    tool.execute(
        command="echo hi", working_directory=".", timeout=5,
        _decided=decided("echo hi", ".", spec=frozen),
    )
    # Read through the helper rather than a field: on Windows a POSIX interpreter is a
    # ``WindowsLaunch``, which names the image somewhere else. The claim under test is the
    # interpreter, not the shape.
    assert interpreter_of(seen[0]) == "/bin/decided"


def test_a_hook_rewrite_moves_the_record_with_the_arguments():
    """Replaced whole. Swapping the arguments alone would launch the original.

    That is the one outcome the rewrite path names as the thing it must never do: a hook
    that replaces a command has already said the original must not run.
    """
    from agentao.runtime.tool_planning import ToolCallDecision
    from agentao.runtime.tool_runner import ToolRunner

    tool = ShellTool()
    plan = type("P", (), {})()
    plan.tool = tool
    plan.function_name = "run_shell_command"
    plan.function_args = {"command": "rm -rf /tmp/x", "working_directory": "."}
    plan.decision = ToolCallDecision.ALLOW
    plan.permission_detail = None
    plan.decided = decided("rm -rf /tmp/x", ".")

    runner = ToolRunner.__new__(ToolRunner)
    runner._planner = _PlannerStub()
    runner.readonly_mode = False
    runner._logger = _LoggerStub()
    ToolRunner._apply_updated_input(
        runner, plan, {"command": "echo safe", "working_directory": "."}
    )

    assert plan.function_args["command"] == "echo safe"
    assert plan.decided.body == "echo safe"


class _PlannerStub:
    def _decide(self, tool, fn, args, readonly, shell_spec=None, decided=None):
        from agentao.runtime.tool_planning import ToolCallDecision

        return ToolCallDecision.ALLOW, None


class _LoggerStub:
    def warning(self, *a, **k):
        pass


# ------------------------------------------------------------- what the floor sees


def test_an_unresolvable_shell_denies_before_any_pattern_is_matched():
    """The reason names why no interpreter was established, not what the text contained.

    The tool stays registered through this, deliberately: telling the model the call was
    refused is a different and better answer than telling it shells do not exist.
    """
    from agentao.permissions_hardline import hardline_check

    reason = hardline_check(
        "run_shell_command", {"command": "echo hi"}, shell_spec=Exhausted("no powershell here")
    )
    assert reason == "hardline:no-shell-opaque:no powershell here"


def test_an_illegal_spec_reaching_the_floor_is_refused_there_too():
    """Construction checks the specs agentao builds; this catches the ones it does not."""
    from agentao.permissions_hardline import hardline_check

    smuggled = dataclasses.replace(posix_spec(), dialect=ShellDialect.UNKNOWN)
    assert hardline_check("run_shell_command", {"command": "echo hi"}, shell_spec=smuggled) == (
        "hardline:unknown-dialect-opaque"
    )


def test_a_legal_spec_does_not_change_what_the_floor_says_about_the_text():
    """The floor still reads the body — naming the dialect must not become a bypass."""
    from agentao.permissions_hardline import hardline_check

    args = {"command": "rm -rf /"}
    assert hardline_check("run_shell_command", args) is not None
    assert hardline_check("run_shell_command", args, shell_spec=posix_spec()) is not None


# -------------------------------------------------------------- the prompt's dialect


@pytest.mark.parametrize(
    "dialect,present,absent",
    [
        ("posix", "/tmp/out.log", "%TEMP%"),
        ("cmd", "%TEMP%", "/tmp/out.log"),
        ("powershell", "Select-String", "/tmp/out.log"),
    ],
)
def test_the_guidelines_speak_the_dialect_that_will_run_them(dialect, present, absent):
    """Advice in the wrong shell's syntax teaches a command that fails.

    The model then spends its next turn recovering from what this prompt told it, which is
    worse than saying nothing shell-specific at all.
    """
    from agentao.prompts.sections import build_operational_guidelines

    text = build_operational_guidelines(dialect=dialect)
    assert present in text
    assert absent not in text


def test_an_unknown_dialect_falls_back_to_what_the_text_said_before():
    """A prompt is advice. It never fails a turn, and it never renders an empty idiom."""
    from agentao.prompts.sections import build_operational_guidelines

    assert build_operational_guidelines(dialect="klingon") == build_operational_guidelines(
        dialect="posix"
    )


# ----------------------------------------------------------------- the description


def test_the_description_names_the_interpreter_the_spec_resolved(monkeypatch):
    """The description is the model's only statement of which syntax to write.

    Saying ``cmd /c`` while PowerShell reads the text is not a cosmetic mismatch: cmd and
    PowerShell disagree about quoting, redirection and the name of every builtin, so the
    model writes the wrong thing on every call.
    """
    tool = ShellTool()

    class Powershell:
        shell_spec = ShellSpec(
            dialect=ShellDialect.POWERSHELL,
            interpreter=AbsPath(r"C:\Program Files\PowerShell\7\pwsh.exe"),
        )

        def run(self, request):  # pragma: no cover - never reached
            raise AssertionError

        def run_background(self, request):  # pragma: no cover - never reached
            raise AssertionError

    tool.shell = Powershell()
    assert "pwsh.exe" in tool.description
    assert "cmd /c" not in tool.description
    assert "pwsh.exe" in tool.parameters["properties"]["command"]["description"]


def test_the_display_name_never_invents_an_interpreter():
    """An unresolved shell still has to be described, and the platform default is the honest
    answer — not a blank, and not the name of something nobody found."""
    assert display_name(Exhausted("nope"), windows=True) == "cmd"
    assert display_name(None, windows=False) == "sh"
    assert display_name(ShellSpec(dialect=ShellDialect.POWERSHELL), windows=True) == "powershell"


# ------------------------------------------------------------- launch refusals


def test_a_launch_refusal_is_not_reported_as_a_failed_start(tmp_path):
    """A launch-stage denial must never read as a transient failure.

    Both faces used to catch it in their broad ``except Exception`` and hand back
    ``Error starting command: …`` — the shape of something the model retries, which is the
    one response a denial must not invite.
    """

    class Refusing(LocalShellExecutor):
        def run(self, request):
            raise LaunchRefused(Deny("command not launchable: too long"))

        def run_background(self, request):
            raise LaunchRefused(Deny("command not launchable: too long"))

    tool = ShellTool()
    tool.shell = Refusing()
    for out in (
        tool._run_foreground("echo hi", tmp_path, 5),
        tool._run_background("echo hi", tmp_path),
    ):
        assert out.startswith("Error: command not launchable")
        assert "starting" not in out


def test_a_legacy_launch_carries_exactly_what_it_needs_and_nothing_else():
    """Four fields, and ``executable`` is the one that changed: the interpreter the user
    named used to stop at the spec and never reach the spawn."""
    names = {f.name for f in dataclasses.fields(LegacyLaunch)}
    assert names == {"command", "cwd", "env", "executable"}
    launch = LegacyLaunch(command="x", cwd=AbsPath("/"), env=MappingProxyType({}))
    assert launch.executable is None


def test_a_shell_request_shows_the_command_for_either_launch_shape():
    """Displays and the background handle read one projection, so the two cannot drift."""
    from agentao.capabilities.shell_spec import WindowsLaunch

    legacy = ShellRequest(
        launch=LegacyLaunch(command="echo hi", cwd=AbsPath("/"), env=MappingProxyType({}))
    )
    named = ShellRequest(
        launch=WindowsLaunch(
            application_name=AbsPath("pwsh.exe"),
            command_line="pwsh.exe -EncodedCommand AAA=",
            cwd=AbsPath("/"),
            env=MappingProxyType({}),
        )
    )
    assert legacy.command == "echo hi"
    assert named.command == "pwsh.exe -EncodedCommand AAA="


def test_an_unconfigured_block_is_the_same_answer_as_no_block():
    """A ``shell`` key that names nothing must not change what runs."""
    assert default_spec(ShellBlock(), windows=True) == default_spec(windows=True)
    assert default_spec(ShellBlock(), windows=False) == default_spec(windows=False)
