"""An injectable :class:`IdentityOracle` for the trusted-resolution tests.

Every PR-4 gate row in ``docs/design/powershell-support-gates.zh.md`` runs on ubuntu with a
stubbed oracle, because the four questions IMG-06 asks — access masks, reparse points,
Authenticode, the target's own environment — are answers about a machine, and the machine
under test is not the one the rule is written for. The stub is the seam that lets the rule be
graded here; the Windows job (PR-6) grades the native answers.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Set, Tuple

from agentao.capabilities.shell_spec import (
    AbsDir,
    AbsFile,
    AbsPath,
    DriveSpec,
    FsId,
    HashPin,
    InterpreterIdentity,
    LauncherIdentity,
    PinnedEnv,
    Platform,
    ResolvedImage,
    RootRelPath,
    Rung,
    Sha256,
    ShellDialect,
    Subject,
)
from agentao.permissions_hardline._trust import ReparseResult, ReparseState, SessionConfig

SUBJECT = Subject("1000")


def windows_pinned(**overrides) -> PinnedEnv:
    """A complete Windows ``PinnedEnv`` — the shape ``shapes_ok`` accepts, before damage."""
    fields = dict(
        system_root=AbsDir("C:\\Windows"),
        windir=AbsDir("C:\\Windows"),
        system_drive=DriveSpec("C:"),
        program_data=AbsDir("C:\\ProgramData"),
        program_files=AbsDir("C:\\Program Files"),
        common_program_files=AbsDir("C:\\Program Files\\Common Files"),
        all_users_profile=AbsDir("C:\\ProgramData"),
        public=AbsDir("C:\\Users\\Public"),
        com_spec=AbsFile("C:\\Windows\\System32\\cmd.exe"),
        home=AbsDir("C:\\Users\\me"),
        user_profile=AbsDir("C:\\Users\\me"),
        home_drive=DriveSpec("C:"),
        home_path=RootRelPath("\\Users\\me"),
        appdata=AbsDir("C:\\Users\\me\\AppData\\Roaming"),
        local_appdata=AbsDir("C:\\Users\\me\\AppData\\Local"),
        temp=AbsDir("C:\\Users\\me\\AppData\\Local\\Temp"),
        tmp=AbsDir("C:\\Users\\me\\AppData\\Local\\Temp"),
    )
    fields.update(overrides)
    return PinnedEnv(**fields)  # type: ignore[arg-type]


def image(path: str, *, subject: Subject = SUBJECT, sha: str = "aa") -> ResolvedImage:
    return ResolvedImage(
        canonical_path=AbsPath(path),
        filesystem_identity=FsId(f"fs:{path.lower()}"),
        execution_subject=subject,
        content_identity=HashPin(path=AbsPath(path), sha256=Sha256(sha)),
    )


def interpreter(
    path: str = "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
    *,
    edition: str = "Core",
    version: str = "7.4.6",
    pshome: Optional[str] = None,
    session_config: Optional[str] = None,
    img: Optional[ResolvedImage] = None,
) -> InterpreterIdentity:
    resolved = img if img is not None else image(path)
    home = pshome if pshome is not None else path.rsplit("\\", 1)[0]
    return InterpreterIdentity(
        image=resolved,
        launcher_hash=Sha256("aa"),
        edition=edition,
        version=version,
        pshome=AbsPath(home),
        session_config=session_config,
    )


class FakeOracle:
    """A complete oracle whose every answer is a field, so a test can damage exactly one."""

    def __init__(
        self,
        *,
        target: Platform = Platform.WINDOWS,
        subject: Subject = SUBJECT,
        local: Optional[bool] = True,
        writable: Optional[Set[str]] = None,
        reparse: Optional[Dict[str, ReparseResult]] = None,
        resolvable: Optional[Set[str]] = None,
        signers: Optional[Dict[str, str]] = None,
        trusted_publishers: Optional[Set[str]] = None,
        base_env: Optional[Mapping[str, str]] = None,
        path_entries: Tuple[AbsPath, ...] = (),
        project_root: Optional[AbsPath] = AbsPath("C:\\repo"),
        pinned: Optional[PinnedEnv] = None,
        discovered: Optional[Dict[Rung, ResolvedImage]] = None,
        identities: Optional[Dict[str, LauncherIdentity]] = None,
        pshome: Optional[AbsPath] = None,
        session: Optional[str] = None,
        preflight_ok: bool = True,
        canonical: Optional[Dict[str, str]] = None,
    ) -> None:
        self._target = target
        self.subject = subject
        self._local = local
        self.writable = writable if writable is not None else set()
        self.reparse = reparse or {}
        self.resolvable = resolvable
        self.signers = signers or {}
        self.trusted_publishers = trusted_publishers or set()
        self.base_env = base_env if base_env is not None else {}
        self.path_entries = path_entries
        self.project_root = project_root
        self.pinned = pinned if pinned is not None else windows_pinned()
        self.discovered = discovered or {}
        self.identities = identities or {}
        self._pshome = pshome
        self.session = session
        self.preflight_ok = preflight_ok
        self.canonical = canonical or {}
        self.platform_calls = 0
        self.wrong_subject: List[str] = []

    # -- SPEC-05: one oracle, one subject ---------------------------------
    def _for(self, subject: Subject, method: str) -> bool:
        if subject != self.subject:
            self.wrong_subject.append(method)
            return False
        return True

    def canonicalize(self, path: str) -> Optional[AbsPath]:
        if not path or "\x00" in path:
            return None
        if path in self.canonical:
            mapped = self.canonical[path]
            return AbsPath(mapped) if mapped else None
        return AbsPath(path)

    def subject_can_replace(self, path: AbsPath, subject: Subject) -> bool:
        if not self._for(subject, "subject_can_replace"):
            return True  # refusing to answer is not "no" (fail closed)
        return str(path) in self.writable

    def resolve_reparse(self, path: AbsPath) -> ReparseResult:
        return self.reparse.get(str(path), ReparseResult(ReparseState.not_reparse))

    def resolves_on_target(self, path: AbsPath) -> bool:
        return self.resolvable is None or str(path) in self.resolvable

    def publisher_trusted(self, path: AbsPath) -> bool:
        return str(path) in self.trusted_publishers

    def image_signer(self, path: AbsPath) -> Optional[str]:
        return self.signers.get(str(path))

    def content_hash(self, path: AbsPath) -> Sha256:
        return Sha256("aa")

    def target_base_env(self, subject: Subject) -> Optional[Mapping[str, str]]:
        return dict(self.base_env) if self._for(subject, "target_base_env") else None

    def target_path_entries(self, subject: Subject) -> Optional[Tuple[AbsPath, ...]]:
        return self.path_entries if self._for(subject, "target_path_entries") else None

    def target_project_root(self) -> Optional[AbsPath]:
        return self.project_root

    def target_platform(self) -> Platform:
        self.platform_calls += 1
        return self._target

    def target_filesystem_is_local(self) -> Optional[bool]:
        return self._local

    def target_pinned_env(self, subject: Subject) -> Optional[PinnedEnv]:
        return self.pinned if self._for(subject, "target_pinned_env") else None

    def resolve_image(self, path: AbsPath, subject: Subject) -> Optional[ResolvedImage]:
        if not self._for(subject, "resolve_image"):
            return None
        canonical = self.canonicalize(str(path))
        return None if canonical is None else image(str(canonical), subject=subject)

    def discover(self, rung: Rung, subject: Subject) -> Optional[ResolvedImage]:
        return self.discovered.get(rung) if self._for(subject, "discover") else None

    def read_identity(
        self, img: ResolvedImage, dialect: ShellDialect
    ) -> Optional[LauncherIdentity]:
        found = self.identities.get(str(img.canonical_path))
        if found is None:
            return None
        # IMG-07: the identity that comes back embeds the image just attested.
        if isinstance(found, InterpreterIdentity):
            return InterpreterIdentity(
                image=img,
                launcher_hash=found.launcher_hash,
                edition=found.edition,
                version=found.version,
                pshome=found.pshome,
                session_config=found.session_config,
            )
        return LauncherIdentity(image=img, launcher_hash=found.launcher_hash)

    def resolve_pshome(self, img: ResolvedImage) -> Optional[AbsPath]:
        if self._pshome is not None:
            return self._pshome
        parent = str(img.canonical_path).rsplit("\\", 1)[0]
        return AbsPath(parent)

    def read_config_sources(self, pshome: AbsPath, subject: Subject) -> SessionConfig:
        if not self._for(subject, "read_config_sources"):
            return SessionConfig(session="unanswerable")
        return SessionConfig(session=self.session)

    def preflight(self, identity: InterpreterIdentity, prelude: str) -> bool:
        return self.preflight_ok
