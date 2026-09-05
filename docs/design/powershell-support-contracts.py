"""PowerShell 支持 —— 数据契约与判定流水线（规范 §3、§4、§5 的定义点）。

本文件是 `powershell-support-spec.zh.md` 的一部分：规范 §3（数据契约）、§4（权限判定流水线）与
§5（各 rung 的启动矩阵）的正文就是这里的类型与函数，规范里那三节只指向本文件。它是**规范，不是实现**：
不被 `agentao` 导入（放在 docs/ 下，不在包内），由 `tests/test_design_set.py` 用 `mypy --strict` 检查，
好让「返回类型装不下规则要的东西」「对冻结对象赋值」「解引用可能为 None 的 launcher」这一类缝在写下时
就报错，而不是等下一轮评审（评审记录方法规则 16）。**类型不是行为：** 有实体的那些函数另由
`tests/test_powershell_contracts.py` 按行为钉住（选级顺序、交叉不变量、效果标志、可信根走法、`<W>` 编码、
计量之前的拒绝），每条测试点名它钉的规则 ID；只有实体函数进那份测试，接缝一律打桩、绝不断言。

约定：
- 每条规则 ID 以 `# FAM-NN` 注释锚在它产生裁定或约束的那一处；`scripts/check_design_set.py` 核规范定义的
  每个 ID 在本文件至少锚一次、本文件提到的每个 ID 与门槛存在。
- 规则的**内容**只在规范 §2 定义；这里的注释只说「哪条规则、哪个分支」，不转述规则。
- 有身体的函数是流水线（裁定顺序、分支、返回什么）；只有 `raise Unspecified(...)` 的函数是**接缝**：
  行为由注释点名的规则全文规定，实现按规则写，这里只钉签名与类型。
- 与 `agentao` 没有 import 关系：字段名由门槛 G01、G24 的测试钉住，落地的 dataclass / Protocol 镜像本文件。
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import NewType, Protocol


class Unspecified(NotImplementedError):
    """接缝：签名是规范，身体由注释点名的规则规定，落地时按规则写。"""


# --------------------------------------------------------------------- 原子类型

AbsPath = NewType("AbsPath", str)  # 规范化绝对路径（IMG-06：8.3、大小写、尾随点空格、\\?\ 展开；ADS 拒绝）
AbsDir = NewType("AbsDir", str)  # 规范化绝对目录
AbsFile = NewType("AbsFile", str)  # 规范化绝对文件路径（ComSpec 是文件，不是目录）
DriveSpec = NewType("DriveSpec", str)  # 形如 "C:"，不是绝对路径
RootRelPath = NewType("RootRelPath", str)  # 形如 "\\Users\\x"，根相对，不是绝对路径（HOMEPATH）
Sha256 = NewType("Sha256", str)
FsId = NewType("FsId", str)  # 文件系统身份：地板 stat 到的那一个（SPEC-04 的「同一路径」）
Subject = NewType("Subject", str)  # 子进程将要以之运行的那个 token；IMG-01 的主语
EnvKey = NewType("EnvKey", str)  # ENV-06d 的用户授权是「**点名**把某个键加回」：字面键名，不含 *，一条 `*` 会把封闭集整个打开

Env = Mapping[str, str]  # 任意来源的环境映射（输入）
FrozenEnv = Mapping[str, str]  # 冻结的子进程环境（SPEC-07）；运行期是 MappingProxyType


class ShellDialect(Enum):  # SPEC-01
    POSIX = "posix"
    POWERSHELL = "powershell"
    CMD = "cmd"
    UNKNOWN = "unknown"


class Rung(Enum):  # SPEC-02
    pwsh = "pwsh"
    powershell = "powershell"
    cmd = "cmd"
    legacy_cmd = "legacy_cmd"  # 仅翻转前（LADDER-05）
    git_bash = "git_bash"
    system_posix = "system_posix"


class Platform(Enum):
    WINDOWS = "windows"
    POSIX = "posix"


LEGAL_PAIRS: Mapping[ShellDialect, frozenset[Rung]] = MappingProxyType(  # SPEC-02；其余一律拒绝
    {
        ShellDialect.POWERSHELL: frozenset({Rung.pwsh, Rung.powershell}),
        ShellDialect.CMD: frozenset({Rung.cmd, Rung.legacy_cmd}),
        ShellDialect.POSIX: frozenset({Rung.git_bash, Rung.system_posix}),
    }
)
POLICY_OFF_RUNGS: frozenset[Rung] = frozenset({Rung.system_posix, Rung.legacy_cmd})  # SPEC-03
POWERSHELL_RUNGS: frozenset[Rung] = frozenset({Rung.pwsh, Rung.powershell})
GIT_BASH_RELEASED = False  # LADDER-04：PR-7 只在 G20 绿时置真；为假时 allow_git_bash 与显式 posix 来源都被拒（CFG-02）
LADDER_FLIPPED = False  # LADDER-05：翻转（PR-7）之前阶梯不运行，Windows 默认报 CMD × legacy_cmd；PR-7 置真并删除 legacy_cmd


def dialect_of(rung: Rung) -> ShellDialect:
    for dialect, rungs in LEGAL_PAIRS.items():
        if rung in rungs:
            return dialect
    return ShellDialect.UNKNOWN


# --------------------------------------------------------------------- 裁定

@dataclass(frozen=True)
class Deny:  # TOOL-03：DENY 是地板唯一的裁定；不透明永远是 DENY，永远不是 ASK；不可被 allow:* 遮蔽
    reason: str  # 规范 §3 的 reason 词表


@dataclass(frozen=True)
class Pass:
    """放行 = 地板不拒绝，交给带 dialect 标注的权限规则（TOOL-02），再交给工具自身的确认设置。"""


Verdict = Deny | Pass
PASS = Pass()


def opaque(dialect: ShellDialect, rule: str, detail: str | None = None) -> Deny:
    """`hardline:<dialect>-opaque:<原因>`：<原因> 是 LOWER-01 的步骤号、产生不透明的规则 ID、IMG-02 的哪一半，或 `launch-<原因>`。"""
    return Deny(f"hardline:{dialect.value}-opaque:{detail or rule}")


@dataclass(frozen=True)
class Exhausted:  # LADDER-03：走空的阶梯 / 被拒的显式来源；ShellSpecProvider.shell_spec 暴露它而不是 spec
    reason: str


# --------------------------------------------------------------------- 映像与身份

@dataclass(frozen=True)
class HashPin:  # IMG-03 content pin：测「正是这个文件被换掉」
    path: AbsPath
    sha256: Sha256

    def matches(self, img: ResolvedImage) -> bool:
        return img.canonical_path == self.path and isinstance(img.content_identity, HashPin) and img.content_identity.sha256 == self.sha256


@dataclass(frozen=True)
class PublisherTrust:  # IMG-03 publisher trust：只证发布者
    signer: str  # IMG-03b：与 oracle.image_signer() 答出的**有效**签名者逐字相等才成立；host_identity_ok 是唯一消费点


class ReparseState(Enum):  # IMG-06c：三态；`AbsPath | None` 装不下第三个，None 曾同时表示「不是」与「答不出」
    not_reparse = "not_reparse"  # 这条路径不是 junction / symlink / app execution alias
    resolved = "resolved"  # 是，且解析到了 target
    error = "error"  # 是，但解析不了（权限、离线卷、损坏的 reparse 数据）⇒ fail closed


@dataclass(frozen=True)
class ReparseResult:  # IMG-06c
    state: ReparseState
    target: AbsPath | None  # 只有 state 是 resolved 时非 None


@dataclass(frozen=True)
class ResolvedImage:  # IMG-06 规范化 + FsId + 主体 + 内容身份；每处收 img 的都是它，不是一条路径
    canonical_path: AbsPath
    filesystem_identity: FsId
    execution_subject: Subject
    content_identity: HashPin | PublisherTrust | None


@dataclass(frozen=True)
class LauncherIdentity:  # IMG-07；政策开启的每一级都有（cmd 与 git_bash 也是）
    image: ResolvedImage  # 完整的证明对象：构造时由 trusted_image() 认证通过，进 Analysis.attested 的就是它（类型闭合）
    launcher_hash: Sha256  # 构造时记录，spawn 前重算（launch-rehash）

    @property
    def path(self) -> AbsPath:  # 便捷投影；LAUNCH-09 的起始目录是它的目录
        return self.image.canonical_path


@dataclass(frozen=True)
class InterpreterIdentity(LauncherIdentity):  # IMG-07、IMG-08；PowerShell 级另加四项，全部宿主侧从映像读出
    edition: str  # <E>
    version: str  # <V>
    pshome: AbsPath  # <H>；安装根，不是 launcher 所在目录
    session_config: str | None  # <C>；生效的控制台会话配置名；None = 三来源都没发现配置（5.1 按构造如此，IMG-08、LAUNCH-06）


@dataclass(frozen=True)
class SessionConfig:  # IMG-08：AllUsers / CurrentUser / Group Policy 三来源合成的生效配置
    session: str | None  # None = 默认（没有控制台会话配置）


# --------------------------------------------------------------------- 钉值环境

@dataclass(frozen=True, kw_only=True)
class PinnedEnv:  # ENV-06 (1)：固定字段、封闭键集，不是任意映射；本机从 OS 求，非本机由 oracle.target_pinned_env 答
    # 系统那一类：构造 spec 之前逐项过 IMG-01（DriveSpec 对它的盘根求值），形态不符或多出任何键 ⇒ 拒绝该 rung。
    # Windows 目标之外这些字段为 None（平台适配）
    system_root: AbsDir | None
    windir: AbsDir | None
    system_drive: DriveSpec | None
    program_data: AbsDir | None
    program_files: AbsDir | None
    program_files_x86: AbsDir | None
    program_w6432: AbsDir | None
    common_program_files: AbsDir | None
    common_program_files_x86: AbsDir | None
    all_users_profile: AbsDir | None
    public: AbsDir | None
    com_spec: AbsFile | None  # ComSpec 是文件，不是目录
    # profile 那一类：只查形态，不过 IMG-01（按定义主体可写，ENV-06）
    home: AbsDir
    user_profile: AbsDir | None
    home_drive: DriveSpec | None
    home_path: RootRelPath | None
    appdata: AbsDir | None
    local_appdata: AbsDir | None
    temp: AbsDir
    tmp: AbsDir
    tmpdir: AbsDir | None  # POSIX 目标；Windows 目标为 None
    unknown_keys: frozenset[str] = frozenset()  # 收到的映射里本表没登记的键（oracle 塞进来的）；非空 ⇒ 拒绝该 rung

    @property
    def has_unknown_keys(self) -> bool:
        return bool(self.unknown_keys)

    def shapes_ok(self, target: Platform) -> bool:
        """ENV-06f：逐字段按声明的形态查（AbsDir / DriveSpec / RootRelPath / AbsFile），核平台专属字段在另一平台为 None，
        并核这个平台上**该有的字段一个都不缺** —— `child_env` 对 `None` 的处理是「这个键不出现」，
        于是少答一个 `SystemRoot` 会静默地交出一份没人验证过的子进程环境（ENV-06a 的理由逐字适用）。"""
        windows_only: tuple[str | None, ...] = (
            self.system_root, self.windir, self.system_drive, self.program_data, self.program_files, self.program_files_x86,
            self.program_w6432, self.common_program_files, self.common_program_files_x86, self.all_users_profile, self.public,
            self.com_spec, self.user_profile, self.home_drive, self.home_path, self.appdata, self.local_appdata,
        )
        if target is Platform.POSIX and any(v is not None for v in windows_only):
            return False
        if target is Platform.WINDOWS and self.tmpdir is not None:
            return False
        if target is Platform.WINDOWS:
            required: tuple[str | None, ...] = (  # ENV-06f：Windows 目标上答不出其中任何一个 ⇒ 拒绝该 rung
                self.system_root, self.windir, self.system_drive, self.program_data, self.program_files,
                self.common_program_files, self.all_users_profile, self.public, self.com_spec,
                self.user_profile, self.home_drive, self.home_path, self.appdata, self.local_appdata,
            )
            # `ProgramFiles(x86)` / `ProgramW6432` / `CommonProgramFiles(x86)` 不在这份清单里：它们由 WOW64 设，
            # 32 位 Windows 上根本不存在 —— 缺席是平台事实，不是「答不出」。给了值的仍要过形态与 IMG-01
            if any(v is None for v in required):
                return False
        elif self.tmpdir is None:
            return False  # ENV-06f：POSIX 目标的 `TMPDIR` 同样由 target_pinned_env 求出，答不出 ⇒ 该 rung 未认证
        # 两个平台都必答的三个。声明成非 Optional 挡不住它：这份记录由 oracle 的答案构造，而 dataclass 不在运行期
        # 强制注解；漏掉这一问，`child_env` 会静默交出一份没有 `HOME` 的子进程环境（ENV-06f、方法规则 19）
        if any(v is None for v in (self.home, self.temp, self.tmp)):
            return False
        dirs = (
            self.system_root, self.windir, self.program_data, self.program_files, self.program_files_x86, self.program_w6432,
            self.common_program_files, self.common_program_files_x86, self.all_users_profile, self.public, self.home,
            self.user_profile, self.appdata, self.local_appdata, self.temp, self.tmp, self.tmpdir,
        )
        return (
            all(v is None or is_abs_dir(v, target) for v in dirs)
            and all(v is None or is_drive_spec(v) for v in (self.system_drive, self.home_drive))
            and (self.home_path is None or is_root_relative(self.home_path))
            and (self.com_spec is None or is_abs_file(self.com_spec, target))
        )

    def system_paths(self) -> tuple[AbsPath, ...]:
        r"""系统那一类的路径集合，构造 spec 前逐项过 IMG-01；DriveSpec 取它的盘根。

        `public`（`C:\Users\Public`）**不在这里**（ENV-06g）：它是共享的**用户数据**目录、设计上人人可写，
        没有一处规则从它加载或读配置。留在这一类里，IMG-06a 的 `FILE_ADD_FILE` 一条就让 `attested_spec`
        拒掉每一个政策开启的 rung —— 一个凑数的环境键关掉整条阶梯。`program_data` / `all_users_profile`
        留着（工具链确实从 `ProgramData` 读配置），但出厂 ACL 是否让它们过得了 IMG-01 未实测，见规范 §7.3 q14。
        """
        paths: list[AbsPath] = []
        for v in (
            self.system_root, self.windir, self.program_data, self.program_files, self.program_files_x86, self.program_w6432,
            self.common_program_files, self.common_program_files_x86, self.all_users_profile, self.com_spec,
        ):
            if v is not None:
                paths.append(AbsPath(v))
        if self.system_drive is not None:
            paths.append(drive_root(self.system_drive))
        return tuple(paths)


def is_abs_dir(path: str, target: Platform) -> bool:
    raise Unspecified("形态谓词：按目标平台的路径规则判绝对目录（ENV-06 (1)）")


def is_abs_file(path: str, target: Platform) -> bool:
    raise Unspecified("形态谓词：按目标平台的路径规则判绝对文件（ENV-06 (1)）")


def is_drive_spec(value: str) -> bool:
    return len(value) == 2 and value[0].isalpha() and value[1] == ":"


def is_root_relative(value: str) -> bool:
    return value.startswith("\\") and not value.startswith("\\\\")


def drive_root(drive: DriveSpec) -> AbsPath:
    return AbsPath(drive + "\\")


# --------------------------------------------------------------------- 配置

class RuleDialect(Enum):  # TOOL-02：权限规则的可选 dialect 标注；标注是方言、不是 rung
    posix = "posix"
    cmd = "cmd"
    powershell = "powershell"
    any = "*"


@dataclass(frozen=True)
class PermissionRule:  # 只列本规范新增的字段；其余见 agentao/permissions.py
    dialect: RuleDialect | None  # None = absent = unspecified（TOOL-02）


Allowlist = tuple["HashPin | PublisherTrust", ...]  # IMG-03；顺序有意义（首个匹配取胜），所以是元组不是集合


def allowlist_entry_for(allowlist: Allowlist, path: AbsPath) -> HashPin | None:
    """IMG-03：allowlist 只对它点名的（规范化后的）路径附加条件；PublisherTrust 条目不点名路径，由 IMG-05 (a) 消费。"""
    for entry in allowlist:
        if isinstance(entry, HashPin) and entry.path == path:
            return entry
    return None


@dataclass(frozen=True, kw_only=True)
class ShellBlock:  # 用户级 shell 块 / 构造 spec（CFG-01：来源只有用户级与宿主，永远不是工作区；CFG-02）
    path: AbsPath | None = None  # IMG-05 (b)：免签名，不免位置；与 dialect 成对，只给其一 ⇒ Exhausted
    dialect: ShellDialect | None = None  # rung 不是字段：按 CFG-02 的表从（dialect, 目标平台, 映像身份）导出
    allow_git_bash: bool = False  # LADDER-02
    allowlist: Allowlist = ()  # IMG-03
    env_passthrough: tuple[EnvKey, ...] = ()  # ENV-06 (2)：默认集之外要透传的**字面键名**；ENV-03 的保留键在这里无效，含 * 的条目丢弃

ConstructorSpec = ShellBlock  # 构造参数（shell_dialect= / shell_path=）与用户级块同形；CFG-02 按来源整体取胜


@dataclass(frozen=True)
class PermissionConfig:  # CFG-03；不可变，穿过每个 composition root，子代理工厂不读文件
    rules: tuple[PermissionRule, ...]
    sources: tuple[str, ...]
    shell: ShellBlock


# --------------------------------------------------------------------- oracle 与 spec

class IdentityOracle(Protocol):  # IMG-06；宿主侧，可注入；非本机时是执行器的（SPEC-05）—— 缺任一方法 ⇒ 该 rung 未认证（G24-11 按本清单参数化）
    # 绑定一个执行主体（SPEC-05）：下面每一个收 subject 的方法，收到与绑定值不同的主体 ⇒ 拒绝作答（该 rung 未认证）
    def canonicalize(self, path: str) -> AbsPath | None: ...  # 8.3、大小写、尾随点空格、\\?\；ADS ⇒ None（拒绝）
    def subject_can_replace(self, path: AbsPath, subject: Subject) -> bool: ...  # 访问掩码语义见 IMG-06；对一条路径求值，不含祖先
    def resolve_reparse(self, path: AbsPath) -> ReparseResult: ...  # IMG-06c：junction / symlink / app execution alias；三态，失败不等于「不是」
    def resolves_on_target(self, path: AbsPath) -> bool: ...  # 目标机上解不解析得到
    def publisher_trusted(self, path: AbsPath) -> bool: ...  # 宿主自己的信任存储说这个映像的签名可信
    def image_signer(self, path: AbsPath) -> str | None: ...
    # IMG-03b：**有效**签名的签名者（链验得过才答），验不过或没签名 ⇒ None。它与 publisher_trusted 是两问：
    # 前者是宿主信任存储的裁定，后者让 allowlist 点名一个签名者 —— 没有它，`PublisherTrust` 条目无处可消费
    def content_hash(self, path: AbsPath) -> Sha256: ...
    def target_base_env(self, subject: Subject) -> Env | None: ...  # 非本机：该主体在目标上的基础环境；child_env 从它算（ENV-06）
    def target_path_entries(self, subject: Subject) -> tuple[AbsPath, ...] | None: ...  # 非本机：该主体在目标上的 PATH 条目（ENV-01 在目标上过滤）
    def target_project_root(self) -> AbsPath | None: ...  # 非本机：目标上的项目根；value_ok 的「项目根之内」按它算；答不出 ⇒ 未认证
    def target_platform(self) -> Platform: ...  # CFG-02：rung 按目标平台导出，不按宿主
    def target_filesystem_is_local(self) -> bool | None: ...
    # SPEC-04a：子进程打开的那条路径是不是地板 stat 过的那条；spec 的字段由它写。**`None` = 答不出**，
    # 与方法缺席同读作 `false`：SPEC-04a 明写这两种都读作更严的那一侧，而 `-> bool` 给不出第二种的落脚处，
    # 只剩下抛异常一条路 —— 那会在政策关闭的两级都还没选出来之前就穿出 `select_rung`（方法规则 22）
    def target_pinned_env(self, subject: Subject) -> PinnedEnv | None: ...  # ENV-06 (1)：目标上的系统目录与该主体的 profile 目录
    def resolve_image(self, path: AbsPath, subject: Subject) -> ResolvedImage | None: ...  # 规范化 + FsId + 该主体 + 内容身份
    def discover(self, rung: Rung, subject: Subject) -> ResolvedImage | None: ...  # IMG-05 (a)：已知安装位置；PATH 命中不是候选
    def read_identity(self, img: ResolvedImage, dialect: ShellDialect) -> LauncherIdentity | None: ...
    # IMG-07：从映像读，不启动；返回的身份内嵌这个 img（SPEC-07 的冻结成立）。收方言、不收 rung ——
    # powershell 方言的 rung 要靠读出来的 edition 才能定（CFG-02）；该方言下返回 InterpreterIdentity
    def resolve_pshome(self, img: ResolvedImage) -> AbsPath | None: ...  # IMG-08：正在执行的 System.Management.Automation.dll 所在目录
    def read_config_sources(self, pshome: AbsPath, subject: Subject) -> SessionConfig: ...  # IMG-08：三来源，在目标上读
    def preflight(self, identity: InterpreterIdentity, prelude: str) -> bool: ...  # IMG-09：用同一段前奏启动一次核对 → closed_env_established


ORACLE_METHODS: tuple[str, ...] = (  # SPEC-05c：G24-11 按这份清单逐个缺一次；给 IdentityOracle 加方法就要同时加到这里
    "canonicalize", "subject_can_replace", "resolve_reparse", "resolves_on_target", "publisher_trusted", "image_signer",
    "content_hash", "target_base_env", "target_path_entries", "target_project_root", "target_platform",
    "target_filesystem_is_local", "target_pinned_env", "resolve_image", "discover", "read_identity",
    "resolve_pshome", "read_config_sources", "preflight",
)


SELECTION_METHODS: tuple[str, ...] = ("target_platform",)  # 选级本身只要这一问；locality 按 SPEC-04a 有默认答案


def oracle_answers(oracle: IdentityOracle | None, methods: Sequence[str]) -> bool:
    return oracle is not None and all(callable(getattr(oracle, m, None)) for m in methods)


def target_is_local(oracle: IdentityOracle) -> bool:
    """SPEC-04a：答不出读作 `false`（更严的一侧），**不是**拒绝该 rung。

    这一问不进 `SELECTION_METHODS`：SPEC-04a 明写缺席与答不出都读作 `false`，而把它算进选级的必答项，
    一个只缺这个方法的默认 POSIX 执行器就会走空 —— SPEC-05c 的末句恰恰保住政策关闭的两级照旧运行。
    """
    if not oracle_answers(oracle, ("target_filesystem_is_local",)):
        return False  # 方法缺席
    return oracle.target_filesystem_is_local() is True  # `None`（答不出）与 `False` 同读作 false


def oracle_complete(oracle: IdentityOracle | None) -> bool:
    """SPEC-05c 的全式：oracle **缺席、或缺任一方法** ⇒ 该 rung 未认证。

    `Protocol` 是静态的，不是运行期契约 —— 非本机执行器递进来的是它自己的对象，少一个方法在类型检查里看不见，
    要到 `launch()` 的重哈希才抛 `AttributeError`，而那时这次调用已经被判为放行；一个异常也不是 DENY 通道上的裁定。
    所以这一问在**构造**（`select_rung`）与**判定**（`decide`）两处各答一次，与 `validate()` 同一手法。

    **它约束的只有政策开启的 rung。** SPEC-05c 的末句是「只有政策关闭的 rung 照旧运行」，而那两级恰恰不问 oracle
    （`legacy_spec`）—— 把这一问放在 `select_rung` 进门处，一个缺 `preflight` 的执行器就会让阶梯走空，
    LADDER-03 再把走空变成每次 shell 调用 DENY，正是 LADDER-05 承诺「与今天逐段相同」的反面。
    所以选级只要 `SELECTION_METHODS` 那一问（`target_platform`），完整性在两个**政策开启**的入口各答一次。
    """
    return oracle_answers(oracle, ORACLE_METHODS)


@dataclass(frozen=True, kw_only=True)
class ShellSpec:  # SPEC-07：整张对象图深度不可变；每个字段构造时写入，重解析产生新对象；构造在每一项预检之后
    dialect: ShellDialect  # SPEC-01
    rung: Rung  # SPEC-02：构造时按 LEGAL_PAIRS 校验，失败点名配对
    filesystem_is_local: bool = False  # SPEC-04；线上缺席即 False，但两个构造器都显式写入 oracle 的答案（SPEC-04a）
    execution_subject: Subject  # 子进程将要以之运行的 token；IMG-01 的主语
    identity_oracle: IdentityOracle | None  # 非本机时由执行器提供（SPEC-05）；测试里注入；不进指纹
    closed_env_established: bool = False  # SPEC-06；IMG-09 写入；PowerShell rung 之外恒 False
    launcher: LauncherIdentity | None  # IMG-07；政策开启的每一级必有，关闭的两级为 None（SPEC-03）；PowerShell 级是 InterpreterIdentity
    pinned_env: PinnedEnv | None  # ENV-06 (1)：同上，政策关闭的两级为 None（它们走 LegacyLaunch，用今天的环境）
    env_passthrough: tuple[EnvKey, ...]  # ENV-06 (2)：用户级 shell 块与构造参数点名加回的键（字面键名），构造时冻结
    allowlist: Allowlist  # IMG-03a：判定期生效的那一份，构造时冻结并进指纹；IMG-03 的加签条件只有这一个来源
    explicit_shell: AbsPath | None  # CFG-02c：显式来源导出政策关闭的一级时，用户点名的那个可执行文件；政策开启时恒 None
    target_platform: Platform  # oracle.target_platform() 的快照；child_env 与 CFG-02 的导出都用它，不现问
    policy_enabled: bool  # SPEC-03；= rung ∉ POLICY_OFF_RUNGS，validate() 核
    fingerprint: Sha256  # SPEC-08、SPEC-07：fingerprint_projection() 的哈希


def fingerprint_projection(spec: ShellSpec) -> tuple[object, ...]:
    """SPEC-07 的规范投影：按声明顺序取，排除 fingerprint 自身与 identity_oracle（运行期对象，无规范序列化形式）。

    路径取规范化绝对路径，集合先排序，映射按键的字典序编码键与值。
    """
    return (
        spec.dialect.value,
        spec.rung.value,
        spec.filesystem_is_local,
        spec.execution_subject,
        spec.closed_env_established,
        spec.launcher,
        spec.pinned_env,
        tuple(sorted(spec.env_passthrough)),
        spec.allowlist,
        spec.explicit_shell,  # CFG-02c：`/bin/bash` 与 `/bin/zsh` 两份 spec 必须指纹不同  # IMG-03a：只在 allowlist 上不同的两份配置必须产出不同的指纹（SPEC-08）
        spec.target_platform.value,
        spec.policy_enabled,
    )


def fingerprint_of(projection: tuple[object, ...]) -> Sha256:
    raise Unspecified("规范投影的 sha256：冻结记录按字段序、路径按规范形式、映射按键序编码（SPEC-07）")


def validate(spec: ShellSpec) -> str | None:
    """SPEC-01 / SPEC-02 / SPEC-03 的 fail-closed 校验；返回 reason 或 None。构造时跑，漏到地板的 decide() 再跑一次。"""
    if spec.dialect not in LEGAL_PAIRS or spec.dialect is ShellDialect.UNKNOWN:
        return "hardline:unknown-dialect-opaque"  # SPEC-01
    if spec.rung not in LEGAL_PAIRS[spec.dialect]:
        return "hardline:unknown-rung-opaque"  # SPEC-02
    # SPEC-03 的三条交叉不变量（不成立按 SPEC-02 的 reason）：
    if spec.policy_enabled != (spec.rung not in POLICY_OFF_RUNGS):
        return "hardline:unknown-rung-opaque"  # policy_enabled ⇔ rung ∉ {system_posix, legacy_cmd}
    if spec.policy_enabled and (spec.launcher is None or spec.pinned_env is None):
        return "hardline:unknown-rung-opaque"  # policy_enabled ⇒ launcher 与 pinned_env 都在
    if not spec.policy_enabled and (spec.launcher is not None or spec.pinned_env is not None):
        return "hardline:unknown-rung-opaque"  # ¬policy_enabled ⇒ 两者都是 None
    if spec.rung in POWERSHELL_RUNGS and not isinstance(spec.launcher, InterpreterIdentity):
        return "hardline:unknown-rung-opaque"  # IMG-07：PowerShell 级的 launcher 是 InterpreterIdentity（类型上是子类，值上要核）
    if spec.policy_enabled and spec.explicit_shell is not None:
        return "hardline:unknown-rung-opaque"  # CFG-02c：政策开启的一级由 launcher 说了算，不许有第二个「哪个可执行文件」
    return None


class ShellSpecProvider(Protocol):  # TOOL-01：run_shell_command 名下注册的任何工具都必须实现它；TOOL-04 每次调用读一次
    @property
    def shell_spec(self) -> ShellSpec | Exhausted: ...  # LADDER-03：走空时是 Exhausted，不是 None、不是回退 spec


@dataclass(frozen=True, kw_only=True)
class DecidedCall:  # SPEC-08a：这次调用被判定下来的输入与结论，一个冻结记录；launch() 没有第二个来源
    spec: ShellSpec  # SPEC-08：TOOL-04 为这次调用读到的那一个对象
    body: str  # 地板逐字节扫过的那段文本
    cwd: AbsPath  # 判定用的规范化工作目录绝对路径
    verdict: Verdict  # SPEC-08b：地板给这份输入的裁定；DENY 的记录 launch() 同样拒绝，不只「记录缺席」才拒
    child_env: FrozenEnv | None  # ENV-06：decide() 算一次；政策关闭的两级为 None
    attested_images: tuple[ResolvedImage, ...]  # LAUNCH-01：判定证明过的就是启动要复核的


@dataclass
class ToolCallPlan:  # 只列本规范用到的字段；今天的 ToolCallPlan 在 agentao/runtime/tool_planning.py
    decided: DecidedCall | None = None  # SPEC-08a：decide() 写一次（hook 重判整体替换）；None = 没经过 decide ⇒ 不启动


# --------------------------------------------------------------------- 可信根与可信映像

def ancestors_to_volume_root(path: AbsPath, target: Platform) -> tuple[AbsPath, ...]:
    raise Unspecified(
        "从 path 的父目录到卷根的每一个祖先，按**目标平台**的路径规则（IMG-01、IMG-06）—— 平台是参数，"
        "不是从字符串猜出来的，也不是现问 oracle 的：G18-14 断言 `target_platform()` 一次调用只问一次，"
        "而这一支在选级的循环里逐条跑"
    )


def path_within(path: AbsPath, root: AbsPath, target: Platform) -> bool:
    raise Unspecified(
        "两条**已规范化**的绝对路径的包含关系，按目标平台的路径规则（Windows 大小写不敏感、`/` 与 `\\` 等价）："
        "path == root 或 root 是 path 的祖先；比的是路径段，不是字符串前缀 —— `C:\\repo-evil` 不在 `C:\\repo` 内。"
        "IMG-05a 的「在项目根之外」与 ENV-01 / ENV-06 的「工作目录与项目根之内」是同一个谓词"
    )


MAX_REPARSE_DEPTH = 32  # IMG-06c：reparse 链的深度上限；junction 成环时它与 following 一起兜底，绝不无限递归


def trusted_root_chain(path: AbsPath, subject: Subject, oracle: IdentityOracle, target: Platform,
                       following: frozenset[AbsPath] = frozenset(), depth: int = 0) -> bool:
    r"""IMG-01：映像与到卷根的每一个祖先，主体都不能修改 / 删除 / 替换 / 重命名；链上的 reparse 目标同样要过。

    `following` 是**当前这趟 reparse 遍历**的入口集合，**不是**「权限已经查过的祖先」集合。两者混成一个，
    一个指向自己父目录的可信 junction（`C:\Trusted\alias` → `C:\Trusted`）就会被判成环：父目录在查
    `alias` 时已经进了集合，而跟着 junction 走过去第一件事就是撞见它。那条链解析得完、每一问都通过，
    却被排除在 launcher 选择与 PATH 过滤之外（IMG-06c）。
    """
    if depth > MAX_REPARSE_DEPTH or path in following:
        return False  # IMG-06c：成环或过深 ⇒ 拒绝（fail closed），不是「查过了，通过」
    chain = (path, *ancestors_to_volume_root(path, target))
    if any(oracle.subject_can_replace(p, subject) for p in chain):
        return False
    for p in chain:  # junction / symlink / app execution alias
        result = oracle.resolve_reparse(p)  # IMG-06c：三态
        if result.state is ReparseState.error:
            return False  # 解析失败 ⇒ 拒绝；把它当成「不是 reparse」就是对一条没查过的链放行
        if result.state is ReparseState.resolved:
            if result.target is None or not trusted_root_chain(result.target, subject, oracle, target, following | {path}, depth + 1):
                return False  # 只记这一趟的入口：A → B → A 在第三层撞上 A 而停，深度上限再兜一次底
    return True


def trusted_image(img: ResolvedImage, subject: Subject, allowlist: Allowlist, oracle: IdentityOracle,
                  target: Platform) -> bool:
    """IMG-01 + IMG-02（映像半）+ IMG-03。收 allowlist 本身，不收「生效的块」—— 判定期的那一份冻在 spec 上（IMG-03a）。"""
    if not oracle.resolves_on_target(img.canonical_path):
        return False
    if not trusted_root_chain(img.canonical_path, subject, oracle, target):
        return False
    pin = allowlist_entry_for(allowlist, img.canonical_path)
    return pin is None or pin.matches(img)  # IMG-03：没点名的映像不因此不可信（G23-05），也不因此可信


def host_identity_ok(img: ResolvedImage, allowlist: Allowlist, oracle: IdentityOracle) -> bool:
    """IMG-05 (a)：任何启动之前的宿主侧身份检查，三条路各走一次（IMG-03、IMG-03b）。"""
    if oracle.publisher_trusted(img.canonical_path):
        return True  # (1) 宿主自己的信任存储
    signer = oracle.image_signer(img.canonical_path)  # (2) allowlist 点名的签名者；IMG-03b：链验不过 ⇒ None
    if signer is not None and any(isinstance(e, PublisherTrust) and e.signer == signer for e in allowlist):
        return True
    pin = allowlist_entry_for(allowlist, img.canonical_path)  # (3) 绝对路径 + 内容哈希
    return pin is not None and pin.matches(img)


# --------------------------------------------------------------------- 启动请求

@dataclass(frozen=True, kw_only=True)
class _Attested:  # AttestedLaunch 两个变体共享的四个字段（LAUNCH-01）；LegacyLaunch 没有它们
    workdir: AbsPath  # 本次调用的工作目录，规范化绝对路径；交给方言的编码形态 <W> 只出现在 argv / command_line 里
    env: FrozenEnv  # 完整的子进程环境，即 child_env 的结果（ENV-06）；执行器原样设定，设定不了 ⇒ 拒绝
    execution_subject: Subject
    attested_images: tuple[ResolvedImage, ...]  # 证据，供执行器必须复核（LAUNCH-01 的 launch-attest）；本机的强制是 ENV-01
    spec_fingerprint: Sha256  # SPEC-08：判定时读到的那个 ShellSpec 的指纹


@dataclass(frozen=True, kw_only=True)
class PosixLaunch(_Attested):  # argv 含 sandbox-exec 包装（macOS）
    executable: AbsPath
    argv: tuple[str, ...]
    cwd: AbsPath  # = launcher 所在目录（LAUNCH-09）


@dataclass(frozen=True, kw_only=True)
class WindowsLaunch(_Attested):
    application_name: AbsPath  # lpApplicationName（LAUNCH-03）
    command_line: str  # 序列化后交给 CreateProcessW 的那个字符串（列表形式先经 list2cmdline）
    cwd: AbsPath  # = launcher 所在目录（LAUNCH-09）


AttestedLaunch = PosixLaunch | WindowsLaunch  # 政策开启的每一级


@dataclass(frozen=True, kw_only=True)
class LegacyLaunch:  # 政策关闭的两级（LADDER-05、SPEC-03）：逐字段等价于今天的 ShellRequest；不受 LAUNCH-01 的复核义务
    command: str  # 今天的命令字符串（%COMSPEC% /c … 或 POSIX 主机今天的 shell）
    cwd: AbsPath  # 本次调用的工作目录（不是 launcher 目录，它没有 launcher）
    env: FrozenEnv  # 今天的环境：build_child_env() 的继承减凭据
    spec_fingerprint: Sha256


LaunchRequest = AttestedLaunch | LegacyLaunch  # LAUNCH-01；两组字段不同，不共用任何字段基类


CREATEPROCESS_MAX_UNITS = 32767  # LAUNCH-08 (i)：含结尾 NUL；正文至多 32766
CMD_MAX_CHARS = 8191  # LAUNCH-08 (ii)：cmd 命令行正文，不含 NUL（§3.22）；也是 cmd 逐条丢弃继承环境变量的上限
POINTER_BYTES = 8  # LAUNCH-08 (iii)：每条 argv / envp 一个指针的开销 —— 推理（64 位目标），PR-4 按目标内核核


@dataclass(frozen=True)
class PosixLimits:  # LAUNCH-08 (iii)：运行期查，不写死
    arg_max: int  # sysconf(ARG_MAX)
    max_arg_strlen: int  # PAGE_SIZE * 32：4 KiB 页 131072，16 KiB 页 524288


def posix_limits(spec: ShellSpec) -> PosixLimits:
    raise Unspecified(
        "本机 = os.sysconf；非本机该由目标答（SPEC-05 的目标侧原则），由 oracle 的哪个方法答**未定** —— "
        "POSIX 分支端到端要等 q4（G18-12），定案时补接口（LAUNCH-08）"
    )


def has_lone_surrogate(s: str) -> bool:
    """LAUNCH-08e：含未配对的代理项（`\ud800`–`\udfff` 落单）。

    JSON 的 `\ud800` 解码后原样留在 Python 字符串里，而三套计量都要先编码 —— UTF-16 与 UTF-8 都拒绝它，
    于是 `floor()` 在**任何分析之前**抛 `UnicodeEncodeError`。一个异常不是 DENY 通道上的裁定：它绕过理由词表、
    绕过 TOOL-03 的「地板的 DENY 不可被规则遮蔽」，在 ACP 上还可能变成一次工具错误让模型重试。
    """
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in s)


def createprocess_units(command_line: str) -> int:
    """LAUNCH-08 (i)：交给 CreateProcessW 的命令行的 UTF-16 code unit 数，含结尾 NUL；非 BMP 字符计 2。"""
    return len(command_line.encode("utf-16-le")) // 2 + 1


def cmd_line_chars(command_line: str) -> int:
    """LAUNCH-08 (ii)：cmd 命令行正文的字符数（UTF-16 code unit），不含结尾 NUL。"""
    return len(command_line.encode("utf-16-le")) // 2


def bytes_with_nul(s: str) -> int:
    """LAUNCH-08 (iii)：POSIX 逐条计量，目标编码的字节数含结尾 NUL。"""
    return len(s.encode("utf-8", errors="surrogateescape")) + 1


def execve_total_units(req: PosixLaunch) -> int:
    """LAUNCH-08 (iii)：全部 argv 与 envp 的字节数之和，每条含结尾 NUL，另计每条一个指针的开销（推理）。"""
    strings = [*req.argv, *(f"{k}={v}" for k, v in req.env.items())]
    return sum(bytes_with_nul(s) for s in strings) + POINTER_BYTES * len(strings)


def command_line_of(req: AttestedLaunch) -> str:
    """Windows 上列表形式一律被 list2cmdline 再序列化一次（LAUNCH-02）；量的是序列化后的那个字符串。"""
    if isinstance(req, WindowsLaunch):
        return req.command_line
    return subprocess.list2cmdline(req.argv)


PS_SINGLE_QUOTES = "\u2018\u2019\u201a\u201b"  # LAUNCH-09e：PowerShell 词法把这四个与 ASCII `'` 一同当作单引号定界符


def encode_workdir(cwd: AbsPath, dialect: ShellDialect) -> str | None:
    """LAUNCH-09：<W> 按该方言的字面量规则编码；编码不了 ⇒ None（launch-cwd）。

    模板里的引号属于 LAUNCH-02 至 LAUNCH-05 的拼法，这里只产出代入引号之间的文本。
    """
    if "\x00" in cwd or has_lone_surrogate(cwd):
        return None  # 任何命令行都装不下 NUL；落单的代理项按 LAUNCH-08e 同样拒（编码不出来）
    if dialect is ShellDialect.POWERSHELL:
        # LAUNCH-09e：`Set-Location -LiteralPath '<W>'` 的字面量以单引号定界，而 PowerShell 认五个单引号字符。
        # 只双写 ASCII 那一个，`C:\’; Start-Process calc; Write-Output ‘` 就闭合了字面量、把其后文本接进前奏，
        # 而地板只扫 body、不扫 agentao 自己生成的前奏 —— LOWER-01 一步都到不了这里。另外四个能否同样靠双写转义**未实测**，
        # 所以取拒绝那一侧（LAUNCH-09b 的 `launch-cwd`）
        if any(ch in cwd for ch in PS_SINGLE_QUOTES):
            return None
        return cwd.replace("'", "''")  # LAUNCH-05：单引号字面量，内嵌 ' 双写
    if dialect is ShellDialect.POSIX:
        return cwd.replace("'", "'\\''")  # LAUNCH-04：单引号字面量，内嵌 ' 写作 '\''
    if dialect is ShellDialect.CMD:
        # LAUNCH-03、LAUNCH-09b：含任一即拒绝该次调用。换行也在内 —— cmd 的 /c 字符串里一个 CR/LF 就把命令行切开，
        # 其后的文本作为另一条命令运行，落在 `/s` 的首尾引号之外，也落在地板分析过的那份结构之外
        return None if any(ch in cwd for ch in '"%^&|<>\r\n') else cwd
    return None


def request_for(spec: ShellSpec, launcher: LauncherIdentity, body: str, workdir_literal: str, env: FrozenEnv, cwd: AbsPath,
                attested_images: tuple[ResolvedImage, ...]) -> AttestedLaunch:
    raise Unspecified(
        "组装启动请求：pwsh / powershell 按 LAUNCH-02（前奏按 LAUNCH-05、LAUNCH-06、LAUNCH-07，一个元素）、"
        "cmd 按 LAUNCH-03（单字符串 + executable=）、git_bash 按 LAUNCH-04；cwd = launcher 所在目录、workdir = 本次调用的工作目录（LAUNCH-09）；"
        "floor() 与 launch() 用同一个 env，floor 时 attested_images 为空、只为计量"
    )


def prelude_for(identity: InterpreterIdentity, workdir_literal: str) -> str | None:
    raise Unspecified(
        "LAUNCH-05 的前奏，四段：**(1) 身份守卫**（只用 Core 与 .NET 静态方法 —— `Get-Item` 属 Management，"
        "而第 3 段正是让它不可用的那一步；windows-latest 实测：`$PSModuleAutoLoadingPreference='None'` 下 "
        "`pwsh -NoProfile -Command` 里 `Get-Item`/`Set-Location`/`Write-Output`/`Get-Date`/`Get-ChildItem` 一个都解析不到）；"
        "**(2) 显式导入** `Microsoft.PowerShell.Management` 与 `Microsoft.PowerShell.Utility`（在身份验过之后、关门之前，"
        "从刚验过的安装根来；导不进 = 启动状态未认证，同样 exit 97）；**(3) 关自动加载并复查**；**(4) 切到 <W>**。"
        "<E> <V> <H> 取自 identity，<W> 由调用方按 encode_workdir 编码后传进来 —— "
        "**它是逐次调用的，identity 是逐 rung 的**，只收 identity 的签名没有地方放它（LAUNCH-05、LAUNCH-09a）；"
        "<C> 不得省略（LAUNCH-06），身份四项或 <W> 编码不出来 ⇒ None（拒绝该 rung / 该次调用，绝不换转义方式）；"
        "预检（IMG-09）用同一段；次序按 LAUNCH-09a —— 守卫在前、切到 <W> 紧接在 body 之前；"
        "它改变的启动状态限于 LAUNCH-07a 列出的那几项"
    )


# --------------------------------------------------------------------- token、效果标志、可信表

@dataclass(frozen=True)
class Literal:  # TOK-01
    text: str


@dataclass(frozen=True)
class Dynamic:  # TOK-01：不透明是 token 与 AST 节点 kind 的属性，按方言分
    kind: str


Token = Literal | Dynamic


class EffectFlag(Enum):  # EFF-01；空集 = 惰性
    rebinds_after = "rebinds_after"
    executes_input = "executes_input"
    rebinds_caller = "rebinds_caller"


class EntryKind(Enum):  # NAME-01 / NAME-02 / NAME-03
    cmdlet = "cmdlet"
    function = "function"
    alias = "alias"
    internal = "internal"
    builtin = "builtin"
    keyword = "keyword"
    application = "application"


IN_PROCESS_KINDS: frozenset[EntryKind] = frozenset(  # IMG-02：进程内条目的映像半绑定已认证的 launcher
    {EntryKind.cmdlet, EntryKind.function, EntryKind.internal, EntryKind.builtin, EntryKind.keyword}
)


@dataclass(frozen=True)
class ArgPattern:  # EFF-08：参数形状（git -c core.pager=、python -c）
    pattern: str

    def matches(self, args: Sequence[Token]) -> bool:
        raise Unspecified("参数形状匹配（EFF-08）；位置上出现 Dynamic 按 EFF-06 已在调用方判不透明")


@dataclass(frozen=True, kw_only=True)
class TrustedEntry:  # EFF-08：数据，不是代码
    name: str  # 归一后的命令词（basename、cmdlet、function、别名、内建）
    dialect: ShellDialect
    kind: EntryKind
    alias_target: str | None  # kind = alias 时指向的条目
    reenters: bool  # EFF-07 的 `=`：本方言求值器，字面串可按 WRAP-04 4a 重新进入
    rung_scope: frozenset[Rung]  # NAME-02 的表按解释器身份分
    execution_triggers: tuple[ArgPattern, ...]  # 命中 ⇒ executes_input
    rebind_triggers: tuple[ArgPattern, ...]  # 命中 ⇒ rebinds_after
    caller_scope: bool  # EFF-07 的 `+`：这条条目的效果落在**调用方**作用域 —— 执行进去的输入与它自己的重绑都算
    predicate_positions: frozenset[int]  # EFF-06：这些位置 Dynamic ⇒ 不透明
    source: str  # 每条断言的出处

    def flags(self, args: Sequence[Token]) -> frozenset[EffectFlag]:  # EFF-01；由登记字段推出，没有别的来源（EFF-08）
        out: set[EffectFlag] = set()
        if any(p.matches(args) for p in self.execution_triggers):
            out.add(EffectFlag.executes_input)
            if self.caller_scope:
                out.add(EffectFlag.rebinds_caller)  # EFF-07 的 `+` 标在 **executes_input 表**上：iex 执行进的就是调用方作用域
        if any(p.matches(args) for p in self.rebind_triggers):
            out.add(EffectFlag.rebinds_after)
            if self.caller_scope:
                out.add(EffectFlag.rebinds_caller)
        return frozenset(out)
        # `caller_scope` 只挂在 rebind_triggers 上不行：`iex` 没有内在的重绑触发（重绑发生在被它执行的那段字面 body 里），
        # 于是 EFF-03 要并入的退出态永远拿不到（G04-32 中者漏放行）。反过来给它一条无条件的重绑触发也不行 —— 那会让
        # `iex 'Get-Date'; git status` 直接带上 rebinds_after 而污染后继（G04-18 漏拒）。两个门槛同时成立只有这一种写法：
        # `rebinds_caller` 自身**不**污染，它只说「被执行进去的那段 body 的退出态是我的退出态」，惰性的内层照旧并出惰性


@dataclass(frozen=True)
class ExitState:  # EFF-03：这段 body 退出时留没留下被重绑的名字
    tainted: bool

    def merge(self, other: ExitState) -> ExitState:
        return ExitState(self.tainted or other.tainted)


@dataclass(frozen=True)
class Analysis:  # EFF-03：递归分析的返回类型
    verdict: Verdict
    exit_state: ExitState
    attested: tuple[ResolvedImage, ...]  # 判定过程中证明过的每一个映像，递归时并集合并；launch() 的证明集只能来自这里（LAUNCH-01）


class CommandKind(Enum):
    simple = "simple"
    interpreter_launch = "interpreter_launch"  # WRAP-01 的包装体
    spawner = "spawner"  # WRAP-05


@dataclass(frozen=True, kw_only=True)
class Command:  # analyse() 切出的一条简单命令
    word: Token
    args: tuple[Token, ...]
    kind: CommandKind = CommandKind.simple
    callee_dialect: ShellDialect | None = None  # interpreter_launch：WRAP-02 / WRAP-03 解析出的被调方方言
    inner_body: str | None = None  # interpreter_launch：交给被调方的 body（-EncodedCommand 已解码）
    literal_target: str | None = None  # executes_input 的目标若是不含 Dynamic 的字面串（WRAP-04 4a）；文件 / 管道 / 动态 ⇒ None


@dataclass(frozen=True)
class Opaque:  # analyse() 在形成任何命令之前就拒的结果
    reason: str


def analyse(dialect: ShellDialect, body: str) -> tuple[Command, ...] | Opaque:
    raise Unspecified(
        "CMD：regex + Token 化，任何位置的任何 Dynamic ⇒ 不透明（TOK-02 的 CMD 那一句），控制流与分组 ⇒ 不透明（CMD-01）；"
        "POSIX（git_bash）：BASH-01 的语法闸（含 BASH-01a 的会改变 argv 的未引用展开），再今天的 regex 地板 + Token 化（TOK-01）；"
        "POWERSHELL：LOWER-01 的十步（第 5 步 = LOWER-02 的 21 kind，第 8 步 = LOWER-03 的源码保真），"
        "任一步失败 ⇒ hardline:powershell-opaque:<步骤>；语料门槛 LOWER-04；"
        "`& …` / `. …` 的 command_name_expr 形式在第 5 步就已不透明，到不了 WRAP-04；"
        "PowerShell 与 POSIX 只有**命令词** Dynamic 在这里拒（TOK-02 的第一句），其余位置的 Dynamic 照常发出，"
        "由 analyse_body 按条目登记的 predicate_positions 判（EFF-06）—— 在这里一刀切会把 `git log $ref` 也变成拒绝"
    )


def lookup(word: str, spec: ShellSpec) -> TrustedEntry | None:
    raise Unspecified(
        "NAME-01（cmd 内部表 → PATHEXT 搜索）/ NAME-02（alias → function → cmdlet → 外部；以 SPEC-06 为条件）/ "
        "NAME-03（bash 先解析掉的词不透明，除非在惰性内建集）；显式路径按 IMG-04"
    )


def resolve(name: str, spec: ShellSpec, oracle: IdentityOracle, search_path: tuple[AbsPath, ...]) -> ResolvedImage | None:
    raise Unspecified(
        "外部程序：在 `search_path` 上（**不是**现问出来的一份）于目标上解析到 ResolvedImage（IMG-06、SPEC-05）；"
        "search_path 就是 decide() 算给子进程 `PATH` 的那一份（ENV-01a）—— 各算各的，判定证明的映像与子进程打开的映像可以是两个"
    )


def launcher_image(spec: ShellSpec) -> ResolvedImage | None:
    return spec.launcher.image if spec.launcher is not None else None  # IMG-02、IMG-07：launcher 未认证 ⇒ 映像半不成立


def alias_resolves_in_process(entry: TrustedEntry, spec: ShellSpec) -> bool:
    target = lookup(entry.alias_target, spec) if entry.alias_target is not None else None
    return target is not None and target.kind in IN_PROCESS_KINDS


def dangerous(entry: TrustedEntry, args: Sequence[Token]) -> str | None:
    raise Unspecified("危险表：§3.5 的 18 类与 §3.6 的 Windows 类；返回 `hardline:<class> …` 或 None")


def names_provider_drive(args: Sequence[Token]) -> bool:
    raise Unspecified("EFF-05：参数匹配 ^[A-Za-z][A-Za-z0-9]*: 且不是盘符路径")


def reenter_spec(spec: ShellSpec, callee: ShellDialect) -> ShellSpec:
    raise Unspecified("WRAP-01 规则 1：按被调方的方言重新进入，只为了理由；嵌套启动本身不透明（规则 2）")


def spawner_reason(cmd: Command) -> str:
    raise Unspecified("WRAP-05 / WRAP-06：生成进程者的理由归属（目标按 IMG-04 与其方言的裸词规则）")


# --------------------------------------------------------------------- 子进程环境

@dataclass(frozen=True, kw_only=True)
class EnvInputs:  # ENV-06：child_env 的全部外部输入，每次调用读一次，不在函数里现问
    base: Env  # 本机 = 剥离 provider 凭据的进程环境；非本机 = oracle.target_base_env(subject)
    path_entries: tuple[AbsPath, ...]  # 本机 = 本机 PATH 条目；非本机 = oracle.target_path_entries(subject)
    cwd: AbsPath  # 本次调用的工作目录（value_ok 的「工作目录之内」按它算）
    project_root: AbsPath  # value_ok 的「项目根之内」按它算；非本机取 oracle.target_project_root()，不是宿主那条路径（SPEC-05）


def read_env_inputs(spec: ShellSpec, cwd: AbsPath) -> EnvInputs | Exhausted:
    raise Unspecified(
        "本机：剥离 provider 凭据的进程环境 + 本机 PATH 条目 + 宿主的项目根；"
        "非本机：oracle 的 target_base_env(subject) / target_path_entries(subject) / target_project_root()，都对 spec.execution_subject 答；"
        "每次调用读一次（ENV-06）；任一答不出 ⇒ Exhausted（该 rung 未认证）"
    )


def filtered_path_entries(subject: Subject, entries: Sequence[AbsPath], cwd: AbsPath, project_root: AbsPath,
                          target: Platform, oracle: IdentityOracle) -> tuple[AbsPath, ...]:
    raise Unspecified(
        "ENV-01：只留主体写不了的目录 —— IMG-01 同一谓词，就是 trusted_root_chain(dir, subject, oracle)，"
        "所以这一步收 oracle 与目标平台：ACL 与 reparse 只有 oracle 答得出，非本机时更是（SPEC-05），"
        "而祖先链与包含判定按目标的路径规则算，不按宿主的；"
        "每个条目先过 oracle.canonicalize()（IMG-06），答不出就剔除 —— PATH 条目是环境里的原始字符串，"
        "而 path_within() 收的是两条**已规范化**的路径，不归一 `..`、短名与符号链接就能绕开下面两道包含判定；"
        "再剔除空的、相对的、工作目录与项目根内的条目；agentao 自己搜索，不用 shutil.which"
    )


def join_path(entries: Sequence[AbsPath], target: Platform) -> str:
    return (";" if target is Platform.WINDOWS else ":").join(entries)  # ENV-01：子进程 `PATH` 的字符串形态


def pinned_psmodulepath(spec: ShellSpec) -> str:
    raise Unspecified("ENV-05：只含满足 IMG-01 的安装根模块目录；纵深防御，不作机制")


def value_ok(key: str, value: str, spec: ShellSpec, inputs: EnvInputs) -> bool:
    raise Unspecified(
        "ENV-06 (2)：匹配该键登记的形状（描述性键：单个 token，不含路径分隔符；代理键：URL / 主机列表）；"
        "且不含相对路径、不含归一后落在 inputs.cwd 或 inputs.project_root 内的路径；路径列表分隔符按 spec.target_platform"
    )


def fold_key(key: str, target: Platform) -> str:
    return key.upper() if target is Platform.WINDOWS else key  # Windows 大小写不敏感：Popen(env=) 的映射按大写折叠（§3.22）


def fold_keys(keys: Iterable[str], target: Platform) -> frozenset[str]:
    return frozenset(fold_key(k, target) for k in keys)


def fold_base(base: Env, target: Platform) -> dict[str, str]:
    """键先按目标平台归一再做集合运算（ENV-06）；折叠后碰撞且取值不同 ⇒ 该键移除并诊断一次，绝不由字典顺序偶然决定。"""
    folded: dict[str, str] = {}
    dropped: set[str] = set()
    for k, v in base.items():
        fk = fold_key(k, target)
        if fk in dropped:
            continue
        if fk in folded and folded[fk] != v:
            del folded[fk]
            dropped.add(fk)  # 诊断一次：实现记日志，这里只钉行为
            continue
        folded[fk] = v
    return folded


def matches_any(key: str, patterns: frozenset[str]) -> bool:
    return any((p.endswith("*") and key.startswith(p[:-1])) or key == p for p in patterns)


def child_env(spec: ShellSpec, pinned: PinnedEnv, inputs: EnvInputs, search_path: tuple[AbsPath, ...]) -> FrozenEnv:  # ENV-06：封闭集，三类；判据是「值是不是一条路径」
    # 规范 §2 的 ENV-* 叫它 ChildEnv。rung / 主体 / 目标平台 / 钉值 / 用户授权键全部取自 spec（构造时冻结，SPEC-07）；其余外部输入全在
    # inputs 里 —— 没有任何隐式来源。每次调用只算一次：decide() 算出后冻进 plan.decided.child_env，LAUNCH-08 的长度守卫与 launch() 交出去的
    # 请求用的是同一个对象。pinned 就是 spec.pinned_env，由调用方收窄 Optional（SPEC-03：政策开启 ⇒ 在）
    T = spec.target_platform
    env: dict[str, str] = {}
    # (1) 钉值 —— 值来自 spec.pinned_env（`PinnedEnv` 的固定字段），绝不抄 inputs.base；系统那一类构造 spec 时已逐项过 IMG-01，
    #     profile 那一类按定义主体可写、不过 IMG-01（ENV-06：挡重定向，不挡写入）；字段为 None 的键不出现
    env["PATH"] = join_path(search_path, T)  # ENV-01a：decide() 算好的那一份，这里只是把它写成字符串；绝不在这里重算
    env["PATHEXT"] = ".COM;.EXE"  # ENV-02（每一级）
    pinned_fields: dict[str, str | None] = {
        # 系统那一类 → 环境键（Windows 目标；POSIX 目标上为 None）
        "SystemRoot": pinned.system_root,
        "windir": pinned.windir,
        "SystemDrive": pinned.system_drive,
        "ProgramData": pinned.program_data,
        "ProgramFiles": pinned.program_files,
        "ProgramFiles(x86)": pinned.program_files_x86,
        "ProgramW6432": pinned.program_w6432,
        "CommonProgramFiles": pinned.common_program_files,
        "CommonProgramFiles(x86)": pinned.common_program_files_x86,
        "ALLUSERSPROFILE": pinned.all_users_profile,
        "PUBLIC": pinned.public,
        "ComSpec": pinned.com_spec,
        # profile 那一类；本机：系统 API + getpwuid(subject) / token profile；非本机：oracle.target_pinned_env(subject) 在目标上答
        "HOME": pinned.home,
        "USERPROFILE": pinned.user_profile,
        "HOMEDRIVE": pinned.home_drive,
        "HOMEPATH": pinned.home_path,
        "APPDATA": pinned.appdata,
        "LOCALAPPDATA": pinned.local_appdata,
        "TEMP": pinned.temp,
        "TMP": pinned.tmp,
        "TMPDIR": pinned.tmpdir,
    }
    for key, value in pinned_fields.items():
        if value is not None:  # 缺席（None）的字段不出现在环境里
            env[key] = value
    if spec.rung is Rung.cmd:
        env["NoDefaultCurrentDirectoryInExePath"] = "1"  # ENV-04（cmd）
    if spec.rung in POWERSHELL_RUNGS:
        env["PSModulePath"] = pinned_psmodulepath(spec)  # ENV-05（pwsh / powershell）；只含 IMG-01 目录
    if spec.rung is Rung.git_bash:
        env["MSYS_NO_PATHCONV"] = "1"  # LAUNCH-04（git_bash）
    pinned_keys = fold_keys(env, T)  # (1) 已决定的键不再从 base 抄，用户扩展也覆盖不了钉值
    # (2) 透传 —— 只有非路径的描述性键，逐键登记形状，value_ok 不过就移除、不改写
    DESCRIPTIVE = frozenset({  # 形状：单个 token，不含路径分隔符
        "USERNAME", "USERDOMAIN", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
        "USER", "LOGNAME", "LANG", "LC_*", "TZ", "TERM", "COLUMNS", "LINES", "NO_COLOR",
    })
    PROXY = frozenset({  # 形状：URL / 主机列表；留在默认集里是一次明写的选择：代理改变流量去哪，不改变子进程跑什么
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    })
    RESERVED = frozenset({  # ENV-03：任何来源都透传不进来
        "BASH_ENV", "ENV", "BASH_FUNC_*", "SHELLOPTS", "BASHOPTS", "PATH", "PATHEXT", "PSModulePath",
        "NoDefaultCurrentDirectoryInExePath", "ComSpec", "MSYS_NO_PATHCONV",
    })
    # ENV-06d 的用户授权是逐键点名：含 `*` 的条目丢弃并诊断一次 —— 一条 `env_passthrough: ["*"]` 会把
    # 除 ENV-03 保留键外的整份继承环境放回来，正是这一版关掉的那条链（GIT_CONFIG_*、NODE_OPTIONS、LD_PRELOAD、XDG_*）
    granted = frozenset(k for k in spec.env_passthrough if "*" not in k)
    keep = fold_keys(DESCRIPTIVE | PROXY | granted, T)
    reserved = fold_keys(RESERVED, T)
    for k, v in fold_base(inputs.base, T).items():
        if matches_any(k, reserved) or k in pinned_keys:
            continue
        if matches_any(k, keep) and value_ok(k, v, spec, inputs):
            env[k] = v
    # (3) 移除 —— 每一个把「去哪读配置 / 信任什么」交给环境的键，以及不在 (1)(2) 里的一切：
    #     REMOVED ⊇ { XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_RUNTIME_DIR SSL_CERT_FILE SSL_CERT_DIR }
    #     XDG_*：消费者缺席时按 HOME 推默认（git 读 $XDG_CONFIG_HOME/git/config，§3.22），移除零成本；SSL_CERT_*：信任根不是便利项
    # 门槛用例（G18-08、G18-10）：SHELLOPTS BASHOPTS GIT_CONFIG_GLOBAL GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 NODE_OPTIONS PYTHONPATH LD_PRELOAD
    #                              XDG_CONFIG_HOME SSL_CERT_FILE
    return MappingProxyType(env)


# --------------------------------------------------------------------- §4 权限判定流水线

def decide(provider: ShellSpecProvider, body: str, cwd: AbsPath, plan: ToolCallPlan) -> Verdict:  # TOOL-04、SPEC-08
    """地板在 PermissionEngine.decide_detail 内部、任何规则匹配之前运行；它的 DENY 不可被规则遮蔽（TOOL-03）。

    这是 `body` 与 `cwd` 进入本流水线的**唯一**入口（SPEC-08a）：判定用的那两份连同结论冻进
    `plan.decided`，`launch()` 只读那个记录 —— 它收不到第二份文本，也读不到工具此刻的参数。
    """
    plan.decided = None  # SPEC-08c：先作废上一次的记录 —— 下面任何一条早退都不许给 launch() 留下一份**别的调用**判过的输入
    state = provider.shell_spec  # 读一次
    if isinstance(state, Exhausted):
        return Deny("hardline:no-trusted-rung-opaque:" + state.reason)  # LADDER-03；先于任何方言 / rung 检查
    bad = validate(state)  # 在碰任何 oracle 或环境之前（SPEC-01 / SPEC-02 / SPEC-03 的 reason 原样发出）
    if bad is not None:
        return Deny(bad)
    env: FrozenEnv | None = None
    search: tuple[AbsPath, ...] = ()  # ENV-01a：政策关闭的两级不过滤 PATH（走 LegacyLaunch，用今天的环境）
    if state.policy_enabled:
        if state.launcher is None or state.pinned_env is None:
            return Deny("hardline:unknown-rung-opaque")  # validate 已排除；类型上仍要收窄（SPEC-03）
        oracle = state.identity_oracle  # `is None` 那一半在这里只为收窄类型，判据整个在 oracle_complete 里
        if oracle is None or not oracle_complete(oracle):
            return opaque(state.dialect, "SPEC-05c")  # 缺席或缺任一方法 ⇒ 该 rung 未认证；ENV-01 的过滤谓词无人作答
        inputs = read_env_inputs(state, cwd)
        if isinstance(inputs, Exhausted):
            return opaque(state.dialect, "ENV-06")  # 外部输入答不出 ⇒ 该 rung 未认证，这次调用不透明
        search = filtered_path_entries(  # ENV-01a：过滤后的 PATH 只算一次
            state.execution_subject, inputs.path_entries, inputs.cwd, inputs.project_root,
            state.target_platform, oracle,
        )
        env = child_env(state, state.pinned_env, inputs, search)  # ENV-06：算一次；政策关闭的两级不算（走 LegacyLaunch）
    result = floor(state, body, cwd, env, search)
    launcher = (state.launcher.image,) if state.launcher is not None else ()
    # LAUNCH-01：launcher 无条件在证明集里 —— 它就是要被启动的那个直接目标，与 body 里出现了什么无关（空 body、只有外部命令的 body 都一样）
    images = merge_images(launcher + result.attested)
    verdict = result.verdict if images is not None else opaque(state.dialect, "IMG-02", "image")
    plan.decided = DecidedCall(  # SPEC-08a：body 与 cwd 与结论一起冻结，一次写入；hook 重判整体替换这个记录
        spec=state, body=body, cwd=cwd, verdict=verdict, child_env=env, attested_images=images or ()
    )
    return verdict


def merge_images(images: Sequence[ResolvedImage]) -> tuple[ResolvedImage, ...] | None:
    """同一 `canonical_path` 只留一条（LAUNCH-01d 的执行器按路径找条目）；两条对同一路径说法不一 ⇒ `None`，fail closed。

    去重不能是 `setdefault`：构造时冻下的 launcher 记录与分析期新解析出的同一路径若身份不同，保留先见的那条
    等于让执行器拿一份**过时**的身份去复核 —— 那正是 LAUNCH-01d 要抓的情形，不是一条可以静默丢弃的重复。
    """
    seen: dict[AbsPath, ResolvedImage] = {}
    for img in images:
        prior = seen.get(img.canonical_path)
        if prior is None:
            seen[img.canonical_path] = img
        elif prior != img:
            return None
    return tuple(seen.values())


def todays_floor(body: str) -> Verdict:
    raise Unspecified("今天的 18 类 regex 地板，含 §2.7 记录的 fail-open 与 §2.4 的空转；不查表、不打标志、不检查映像（SPEC-03）")


def floor(spec: ShellSpec, body: str, cwd: AbsPath, env: FrozenEnv | None, search_path: tuple[AbsPath, ...]) -> Analysis:
    """收那份算好的环境与那份算好的搜索路径；validate 已在 decide 跑过。LAUNCH-08 的长度守卫在任何分析之前。"""
    no_state = ExitState(tainted=False)
    if not spec.policy_enabled:  # SPEC-03：system_posix / legacy_cmd —— 直到 q4 定案 / PR-7 删除 legacy_cmd
        return Analysis(todays_floor(body), no_state, ())
    launcher = spec.launcher
    if launcher is None or env is None:
        return Analysis(Deny("hardline:unknown-rung-opaque"), no_state, ())  # SPEC-03：政策开启却没有 launcher / 环境
    if has_lone_surrogate(body) or any(has_lone_surrogate(k) or has_lone_surrogate(v) for k, v in env.items()):
        return Analysis(opaque(spec.dialect, "LAUNCH-08e", "lone-surrogate"), no_state, ())  # 计量之前，别让编码抛异常代替裁定
    W = encode_workdir(cwd, spec.dialect)  # LAUNCH-09
    if W is None:
        return Analysis(opaque(spec.dialect, "LAUNCH-09", "launch-cwd"), no_state, ())
    req = request_for(spec, launcher, body, W, env, cwd, ())  # 组装后的最终请求；三套计量各管各的（LAUNCH-08）
    if spec.target_platform is Platform.WINDOWS:
        if createprocess_units(command_line_of(req)) > CREATEPROCESS_MAX_UNITS:  # (i) 只量命令行，环境不计入
            return Analysis(opaque(spec.dialect, "LAUNCH-08", "launch-oversize"), no_state, ())
        if spec.rung is Rung.cmd and cmd_line_chars(command_line_of(req)) > CMD_MAX_CHARS:  # (ii) 与 (i) 同时成立，取先触发的
            return Analysis(opaque(spec.dialect, "LAUNCH-08", "launch-oversize"), no_state, ())
        if spec.rung is Rung.cmd and any(cmd_line_chars(f"{k}={v}") > CMD_MAX_CHARS for k, v in env.items()):
            return Analysis(opaque(spec.dialect, "LAUNCH-08", "launch-env-oversize"), no_state, ())  # cmd 逐条丢弃超长继承变量（§3.22）
    elif isinstance(req, PosixLaunch):  # (iii) POSIX：总量 + 逐条；端到端要等 q4（G18-12）
        limits = posix_limits(spec)
        if execve_total_units(req) > limits.arg_max:
            return Analysis(opaque(spec.dialect, "LAUNCH-08", "launch-oversize"), no_state, ())
        if any(bytes_with_nul(s) > limits.max_arg_strlen for s in req.argv):
            return Analysis(opaque(spec.dialect, "LAUNCH-08", "launch-oversize"), no_state, ())
        if any(bytes_with_nul(f"{k}={v}") > limits.max_arg_strlen for k, v in env.items()):
            return Analysis(opaque(spec.dialect, "LAUNCH-08", "launch-env-oversize"), no_state, ())
    else:  # 三套计量都不适用（POSIX 目标上的 WindowsLaunch）—— 没有量过的命令行不放行，别静默跳过这道闸
        return Analysis(opaque(spec.dialect, "LAUNCH-08", "launch-oversize"), no_state, ())
    return analyse_body(spec, body, search_path)


MAX_ANALYSIS_DEPTH = 16  # EFF-03：重新进入的深度上限；body 是不可信输入，无界递归会栈溢出，而栈溢出不是一次拒绝（同 MAX_REPARSE_DEPTH）


def analyse_body(spec: ShellSpec, body: str, search_path: tuple[AbsPath, ...], depth: int = 0) -> Analysis:  # EFF-03 的递归单位；返回裁定 + 退出态 + 证明集
    if depth > MAX_ANALYSIS_DEPTH:
        return Analysis(opaque(spec.dialect, "EFF-03", "reenter-depth"), ExitState(tainted=False), ())  # fail closed
    commands = analyse(spec.dialect, body)
    if isinstance(commands, Opaque):
        return Analysis(Deny(commands.reason), ExitState(tainted=False), ())
    oracle = spec.identity_oracle
    state = ExitState(tainted=False)  # EFF-02 / EFF-03 的退出态
    attested: tuple[ResolvedImage, ...] = ()  # LAUNCH-01 的证明集
    for cmd in commands:  # 按 body 顺序
        if state.tainted:
            return Analysis(opaque(spec.dialect, "EFF-02", "rebinds_after"), state, attested)
        if isinstance(cmd.word, Dynamic):
            return Analysis(opaque(spec.dialect, "TOK-02"), state, attested)
        if cmd.kind is CommandKind.interpreter_launch:  # WRAP-01 的包装体；WRAP-02 / WRAP-03 解析它的启动面
            inner = analyse_body(reenter_spec(spec, cmd.callee_dialect or ShellDialect.UNKNOWN), cmd.inner_body or "", search_path, depth + 1)
            verdict = inner.verdict if isinstance(inner.verdict, Deny) else opaque(spec.dialect, "WRAP-01", "nested-launch")
            return Analysis(verdict, state, attested)  # 只为了理由：危险的嵌套 body 按自己的理由拒
        if cmd.kind is CommandKind.spawner:
            return Analysis(opaque(spec.dialect, "WRAP-05", spawner_reason(cmd)), state, attested)  # WRAP-06 归属理由
        entry = lookup(cmd.word.text, spec)  # NAME-01 / NAME-02 / NAME-03；显式路径按 IMG-04
        if entry is None:
            return Analysis(opaque(spec.dialect, "EFF-04"), state, attested)
        if any(isinstance(a, Dynamic) for i, a in enumerate(cmd.args) if i in entry.predicate_positions):
            # EFF-06 / TOK-02 的第二句「命令词在表内但谓词读取位置 Dynamic ⇒ 不透明」（G05-02）。必须在
            # flags() 与 dangerous() **之前**：两者都逐 token 比字面形状，ArgPattern.matches 明写它假定调用方已经判过，
            # 于是漏掉这一步时 `Remove-Item $flags C:\` 一个触发都不命中，被当作惰性放行
            return Analysis(opaque(spec.dialect, "EFF-06"), state, attested)
        if entry.kind in IN_PROCESS_KINDS or (entry.kind is EntryKind.alias and alias_resolves_in_process(entry, spec)):
            # IMG-02 映像半：进程内条目绑定**已认证的** launcher（IMG-07）—— IMG-02 说的就是「已认证的 launcher 映像」，
            # 认证发生在 attested_spec 的 trusted_image，这里不逐条重走 ACL 链（每个命令词一次 O(祖先) 的 oracle 往返）。
            # 代价写在规范 §1 的残留行：spec 构造之后安装根**变得可写**（内容未改）这一段窗口无人复核 —— spawn 前的
            # 重哈希只比内容（IMG-07），执行器的复核只比文件系统身份与内容身份（LAUNCH-01d），两者都不看访问掩码
            img = launcher_image(spec)
            if img is None or oracle is None:  # SPEC-05c：launcher 未认证 / oracle 缺席 ⇒ 这一半不成立
                return Analysis(opaque(spec.dialect, "IMG-02", "image"), state, attested)
        else:
            if oracle is None:
                return Analysis(opaque(spec.dialect, "IMG-02", "image"), state, attested)  # SPEC-05：oracle 缺席 ⇒ 需要映像的命令词不透明
            img = resolve(entry.alias_target or cmd.word.text, spec, oracle, search_path)  # 指向外部程序的 alias 解析它的目标，不是别名本身
            if img is None or not trusted_image(img, spec.execution_subject, spec.allowlist, oracle, spec.target_platform):
                return Analysis(opaque(spec.dialect, "IMG-02", "image"), state, attested)  # 名字半 / 映像半 / IMG-03
        attested = (*attested, img)  # LAUNCH-01：判定证明过的就是启动要复核的
        danger = dangerous(entry, cmd.args)  # §3.5 的 18 类 + §3.6
        if danger is not None:
            return Analysis(Deny(danger), state, attested)
        if spec.dialect is ShellDialect.POWERSHELL and names_provider_drive(cmd.args):
            return Analysis(opaque(spec.dialect, "EFF-05"), state, attested)  # EFF-05：查表之后、标志之前；不是一个标志
        effects = entry.flags(cmd.args)  # EFF-01、EFF-06；前缀运行者按 WRAP-07 带 executes_input
        if EffectFlag.executes_input in effects:
            if entry.reenters and cmd.literal_target is not None:  # WRAP-04 4a：只有本方言求值器 + 不含 Dynamic 的字面串
                inner = analyse_body(spec, cmd.literal_target, search_path, depth + 1)  # EFF-03 唯一的递归点
                attested = (*attested, *inner.attested)  # 证明集并起来：递归里证明过的映像照样要复核
                if isinstance(inner.verdict, Deny):
                    return Analysis(inner.verdict, state, attested)
                if EffectFlag.rebinds_caller in effects:
                    state = state.merge(inner.exit_state)  # EFF-03：并入退出态；不带 rebinds_caller 则丢弃
            else:
                return Analysis(opaque(spec.dialect, "EFF-02", "executes_input"), state, attested)  # 文件目标、管道供给、非本方言
        if EffectFlag.rebinds_after in effects:
            state = ExitState(tainted=True)
    return Analysis(PASS, state, attested)  # 交给带 dialect 标注的权限规则（TOOL-02），再交给工具自身的确认设置


# --------------------------------------------------------------------- §5 各 rung 的启动矩阵

def powershell_parser_available() -> bool:
    raise Unspecified("LADDER-01：tree-sitter-powershell 在场；缺失使 PowerShell 不可选")


def identity_measured(identity: InterpreterIdentity) -> bool:
    raise Unspecified(
        "IMG-07：身份属于 **NAME-02 的实测命令表**（`Get-Command -All` 在钉住启动状态里量出的 alias/function/cmdlet）；"
        "未确立 ⇒ 该 rung 的裸词全部不透明、显式路径仍按 IMG-04 服务（NAME-02），**不是拒绝该 rung**。"
        "它与 CFG-02a 说的那张表不是一张：那张是 `derive_rung` 读的 **edition 表**（Core / Desktop），"
        "读不出 edition ⇒ 拒绝该来源。两张表在散文里同名「实测表」，把 NAME-02 那张的缺席当成拒绝理由，"
        "等于为一张**名字**表把 Windows 降到 `cmd` 那一级更粗的地板上"
    )


def derive_rung(dialect: ShellDialect, target: Platform, identity: LauncherIdentity) -> Rung | None:
    """CFG-02 的固定表：从（方言、目标平台、映像身份）导出 rung；导不出 ⇒ None（拒绝该来源）。"""
    if dialect is ShellDialect.CMD:
        # 目标平台在这一行也要读：cmd.exe 不在 POSIX 目标上存在，而政策关闭的 `legacy_cmd` 是翻转前 **Windows**
        # 的默认、不是 POSIX 的一级。导出一个政策开启的 `cmd` spec，三套计量没有一套适用它（floor 末支），
        # 于是每次调用都按「命令行超长」拒绝 —— 一个平台错误报成长度理由，且这份 spec 本就不该构造得出来
        return Rung.cmd if target is Platform.WINDOWS else None
    if dialect is ShellDialect.POSIX:
        return Rung.git_bash if target is Platform.WINDOWS else Rung.system_posix
    if dialect is ShellDialect.POWERSHELL and isinstance(identity, InterpreterIdentity):
        if identity.edition == "Core":
            return Rung.pwsh
        if identity.edition == "Desktop":
            return Rung.powershell
    return None  # UNKNOWN 方言、读不出 edition、或 edition 不在实测表


def select_rung(config: ShellBlock, oracle: IdentityOracle, subject: Subject) -> ShellSpec | Exhausted:
    """CFG-02、LADDER-01；本机与非本机都经 oracle（SPEC-05），oracle 绑定这个 subject。"""
    if not oracle_answers(oracle, SELECTION_METHODS):
        return Exhausted("SPEC-05c: oracle cannot answer the target platform")  # 连选哪一级都问不出来
    T = oracle.target_platform()  # 读一次，往下全传它；不在循环里重问（SPEC-07 的快照）
    local = target_is_local(oracle)  # SPEC-04a：同上读一次；两个构造器都显式写入，绝不靠字段默认值
    if (config.path is None) != (config.dialect is None):
        return Exhausted("shell block names only one of path / dialect")  # CFG-02：两者都给或都不给
    if config.path is not None and config.dialect is not None:  # 这个来源给出了整份 spec（CFG-02）
        if not oracle_complete(oracle):
            return Exhausted("SPEC-05c: incomplete oracle")  # 显式来源要先认证才知道它导出哪一级；被拒不落到 auto（LADDER-03）
        img = oracle.resolve_image(config.path, subject)  # 规范化在这里发生（IMG-06）
        if img is None or not trusted_root_chain(img.canonical_path, subject, oracle, T):
            return Exhausted("IMG-01: shell.path")  # IMG-05 (b)：免签名，不免位置
        root = oracle.target_project_root()  # IMG-05a 的第二半：「绝对且在项目根之外」；答不出 ⇒ 未认证（fail closed）
        if root is None or path_within(img.canonical_path, root, T):
            return Exhausted("IMG-05a: shell.path inside the project root")
            # 可信根链过了不等于位置对：一个**只读**检出里仓库自带的解释器，主体换不掉它，chain 因此答「可信」——
            # 而 IMG-04 说工作树整个不可信，IMG-05a 因此另立一条位置要求。两条都要过，不是二选一
        identity = oracle.read_identity(img, config.dialect)  # IMG-07；收方言不收 rung
        if identity is None:
            return Exhausted("IMG-07: launcher")  # 读不出身份就没有 rung 可导
        if identity.image is not img or img.execution_subject != subject:
            return Exhausted("IMG-07: identity does not bind this image/subject")  # 读回来的必须就是刚认证的那一个
        rung = derive_rung(config.dialect, T, identity)  # CFG-02；powershell 靠 identity.edition 定
        if rung is None:
            return Exhausted("CFG-02: no rung for this dialect / platform / identity")
        if rung is Rung.git_bash and not GIT_BASH_RELEASED:
            return Exhausted("LADDER-04: git_bash rung not released")  # 该级关着发布时显式来源也被拒（CFG-02）
        if rung in POLICY_OFF_RUNGS:  # 显式来源也可能导出政策关闭的一级（POSIX 目标 + posix 方言 ⇒ system_posix）
            # launcher = pinned_env = None（SPEC-03 的交叉不变量），但用户点名的那个可执行文件要带着走（CFG-02c）：
            # 它已经过了 IMG-01 的链、IMG-05a 的位置与 IMG-07 的身份，丢掉它就是把 CFG-02「高来源提供整份 spec」
            # 悄悄降级成「高来源提供除解释器之外的整份 spec」
            return legacy_spec(config.dialect, rung, T, subject, local, img.canonical_path)
        return attested_spec(rung, img, identity, config, oracle, T, subject, local)  # 身份已读出，不再重读；显式来源被拒不落到 auto
    # 只带 allow_git_bash / allowlist / env_passthrough 的块不是整份 spec：它参数化 auto，阶梯照跑（LADDER-02）
    # 政策关闭的两级是 `auto` 的默认，不是阶梯的一级 —— 没有这两条，POSIX 主机与翻转前的 Windows 都走空，
    # 而 LADDER-03 会把「走空」变成每次 shell 调用 DENY，与 LADDER-05 / SPEC-03 的「与今天逐段相同」相反。
    if T is Platform.POSIX:  # 现有 POSIX 主机今天的那个 shell（SPEC-03 政策关闭，直到 q4 定案）
        return legacy_spec(ShellDialect.POSIX, Rung.system_posix, T, subject, local)
    if not LADDER_FLIPPED:  # LADDER-05：翻转前 Windows 的默认执行器报 CMD × legacy_cmd，阶梯只在翻转后运行
        return legacy_spec(ShellDialect.CMD, Rung.legacy_cmd, T, subject, local)
    if not oracle_complete(oracle):
        return Exhausted("SPEC-05c: incomplete oracle")  # 阶梯每一级都要认证；政策关闭的两级已在上面返回（SPEC-05c 末句）
    ladder = [Rung.pwsh, Rung.powershell, *([Rung.git_bash] if config.allow_git_bash and GIT_BASH_RELEASED else []), Rung.cmd]  # LADDER-01、LADDER-02
    for rung in ladder:
        img = oracle.discover(rung, subject)  # IMG-05 (a)：已知安装位置；PATH 命中不是候选
        if img is None:
            continue
        if not trusted_root_chain(img.canonical_path, subject, oracle, T):  # IMG-01：映像与每一个祖先
            continue
        if not host_identity_ok(img, config.allowlist, oracle):  # IMG-05：签名 或 path+hash
            continue
        identity = oracle.read_identity(img, dialect_of(rung))  # IMG-07；这里 rung 已知，方言由它推出
        if identity is None:
            continue
        if identity.image is not img or img.execution_subject != subject:
            continue
        if derive_rung(dialect_of(rung), T, identity) != rung:
            continue  # 在 pwsh 候选位置读出 Desktop 身份 ⇒ 这一级不是它，换下一级；绝不按候选位置构造 spec
        spec = attested_spec(rung, img, identity, config, oracle, T, subject, local)
        if not isinstance(spec, Exhausted):
            return spec
    return Exhausted("every rung refused")  # LADDER-03


def legacy_spec(dialect: ShellDialect, rung: Rung, target: Platform, subject: Subject, local: bool,
                explicit_shell: AbsPath | None = None) -> ShellSpec:
    """政策关闭的一级（LADDER-05、SPEC-03）：不问 oracle、不求钉值、不读身份 —— 没有可认证的东西。

    `allowlist` 与 `env_passthrough` 同样空着：这一级不查映像、也不算子进程环境（LADDER-05 的「与今天逐段相同」），
    在这里存一份用不上的副本就是给同一个值开第二个家。`explicit_shell` 是唯一的例外，而它不是副本：
    显式来源在 `select_rung` 里已经过了 IMG-01 的可信根链、IMG-05a 的位置检查与 IMG-07 的身份读取，
    只是导出的 rung 恰好政策关闭（POSIX 目标 + posix 方言，CFG-02a）；丢掉它等于把用户点名的解释器
    静默换成今天那个（CFG-02c）。

    `local` 仍要显式写入（SPEC-04a）：它是执行器声明的事实，不是这一级有没有政策的推论。
    """
    draft = ShellSpec(
        dialect=dialect, rung=rung, filesystem_is_local=local, execution_subject=subject, identity_oracle=None,
        launcher=None, pinned_env=None, env_passthrough=(), allowlist=(), explicit_shell=explicit_shell,
        target_platform=target, policy_enabled=False,
        fingerprint=Sha256(""),
    )
    # `replace` and not a second field-by-field literal: the projection is over every field but this one, so two
    # lists that can drift are two chances to fingerprint a spec that is not the one being returned (SPEC-08).
    return replace(draft, fingerprint=fingerprint_of(fingerprint_projection(draft)))


def attested_spec(rung: Rung, img: ResolvedImage, identity: LauncherIdentity, config: ShellBlock, oracle: IdentityOracle,
                  target: Platform, subject: Subject, local: bool) -> ShellSpec | Exhausted:
    """预检全部完成之后一次性构造冻结对象（SPEC-07）；从不对 spec 赋值。target 是入口读的那一个平台快照。"""
    if not trusted_image(img, subject, config.allowlist, oracle, target):
        return Exhausted("IMG-02: launcher image")
    # identity 由 read_identity(img, …) 读出时就内嵌了这个 img（IMG-07）—— 这里不赋值，冻结对象不可变（SPEC-07）
    pinned = oracle.target_pinned_env(subject)  # ENV-06 (1)；本机 oracle 从 OS 求
    if pinned is None or pinned.has_unknown_keys or not pinned.shapes_ok(target):
        return Exhausted("ENV-06: pinned env")  # 封闭键集 + 逐字段形态（ENV-06）
    if any(not trusted_root_chain(p, subject, oracle, target) for p in pinned.system_paths()):  # 系统那一类逐项过 IMG-01；profile 那一类不查
        return Exhausted("ENV-06: pinned system dir")
    established = False
    if rung in POWERSHELL_RUNGS:
        if not powershell_parser_available():
            return Exhausted("no parser")  # LADDER-01
        if not isinstance(identity, InterpreterIdentity):
            return Exhausted("IMG-07: identity")  # PowerShell 级的身份必须带 edition / version / pshome / session_config
        pshome = oracle.resolve_pshome(img)
        if pshome is None:
            return Exhausted("IMG-08: no $PSHOME")  # 绝不退回 launcher 所在目录
        if identity.pshome != pshome:
            return Exhausted("IMG-08: identity $PSHOME is not the resolved install root")
        session = oracle.read_config_sources(pshome, subject).session
        if session is not None:
            return Exhausted("IMG-08: session config")  # 生效控制台会话配置不是默认 ⇒ 拒绝该 rung
        if identity.session_config != session:
            return Exhausted("IMG-08: identity session config is not the one read from the three sources")
        # 上面两条：`<H>` 与 `<C>` 有两个来源（read_identity 与 resolve_pshome / read_config_sources），
        # 而 launch() 的「spawn 前立刻重读」读的是 launcher.pshome、比的是 launcher.session_config（IMG-08a）。
        # 不在这里对齐，重读要么恒 DENY，要么读的是一个构造时从没验证过的安装根
        # `identity_measured` 故意**不**在这里拒：IMG-07 说未确立时该 rung 的**裸词**不透明，而 NAME-02
        # 早就为它的同族条件（封闭环境未确立）写下同一种降级 ——「每个 PowerShell 裸词不透明，rung 仍按
        # IMG-04 服务显式路径」。在这里拒等于为一张名字表把 Windows 降到 `cmd`，而 `cmd` 的地板更粗
        # IMG-09 用**同一段**前奏，所以它也要一个 <W>：空串会让 `Set-Location` 失败、子进程退出 98，
        # 于是每一个健康的解释器都读回「封闭环境未确立」。取 launcher 自己所在的目录 —— LAUNCH-09 的起始目录
        home = encode_workdir(AbsPath(ancestors_to_volume_root(identity.path, target)[0]), ShellDialect.POWERSHELL)
        prelude = prelude_for(identity, home) if home is not None else None
        established = prelude is not None and oracle.preflight(identity, prelude)
    draft = ShellSpec(  # SPEC-04a：filesystem_is_local 显式写入，两次都写；它在 fingerprint 投影里
        dialect=dialect_of(rung), rung=rung, filesystem_is_local=local, execution_subject=subject, identity_oracle=oracle,
        launcher=identity, pinned_env=pinned, env_passthrough=config.env_passthrough, allowlist=config.allowlist, explicit_shell=None, target_platform=target,
        policy_enabled=True, closed_env_established=established, fingerprint=Sha256(""),
    )
    # 同 legacy_spec：只换指纹，不再抄一遍字段 —— 两份字段列表会各自漂移，而投影正是「除指纹外的每个字段」（SPEC-08）
    spec = replace(draft, fingerprint=fingerprint_of(fingerprint_projection(draft)))
    bad = validate(spec)  # SPEC-02 / SPEC-03：构造时校验，失败点名
    return Exhausted(bad) if bad is not None else spec


def today_command(body: str, shell: AbsPath | None) -> str:
    raise Unspecified(
        "main@3537753 的启动：%COMSPEC% /c（或 POSIX 主机今天的 shell）—— LADDER-05 与 SPEC-03 的「与今天相同」；"
        "`shell` 非 None 时用它代替 `resolve_shell_executable()` 求出的那一个（CFG-02c），其余逐字段不变"
    )


def today_env() -> FrozenEnv:
    raise Unspecified("main@3537753 的 build_child_env()：继承减凭据")


def launch(plan: ToolCallPlan, provider: ShellSpecProvider) -> LaunchRequest | Deny:
    """LAUNCH-01、SPEC-08、LAUNCH-09。拒绝走与地板相同的 DENY 通道，理由按规范 §3 词表，绝不是一个模型会重试的工具错误。

    **没有 `body` 参数，也没有 `cwd` 参数（SPEC-08b）：** 两者只能是 `plan.decided` 里判定时冻下来的那一份。
    一个能在这里再收一次文本或工作目录的签名，就是一条判定过 `Get-Date`、启动另一段文本的通道。
    **也没有 `oracle` 参数（SPEC-05）：** oracle 绑定一个执行主体、随 spec 冻结，重哈希与重读只能由**这一份 spec 的**
    那个 oracle 作答；一个另外传进来的 oracle 是第二个来源，可以对着另一个主体答完这两问。
    """
    decided = plan.decided  # SPEC-08a：判定冻下来的那一份
    if decided is None:
        return Deny("hardline:unknown-rung-opaque")  # 没经过 decide() 的 plan 不启动
    spec = decided.spec
    if isinstance(decided.verdict, Deny):
        return decided.verdict  # SPEC-08b：判定过、判的是 DENY —— 记录在场不等于这次调用被放行过
    if provider.shell_spec is not spec:
        return opaque(spec.dialect, "SPEC-08", "launch-spec-changed")  # 判定与启动之间被重解析
    if not spec.policy_enabled:  # LADDER-05、SPEC-03：与今天逐段相同 —— 无 launcher / 无证明集 / 无 child_env
        return LegacyLaunch(command=today_command(decided.body, spec.explicit_shell), cwd=decided.cwd,
                            env=today_env(), spec_fingerprint=spec.fingerprint)
    launcher = spec.launcher
    env = decided.child_env
    oracle = spec.identity_oracle  # SPEC-05：spec 冻住的那一个，不是调用方递进来的
    if launcher is None or env is None or oracle is None:
        return Deny("hardline:unknown-rung-opaque")  # SPEC-03 / SPEC-05c：政策开启却没有 launcher / 环境 / oracle
    if oracle.content_hash(launcher.path) != launcher.launcher_hash:
        return opaque(spec.dialect, "IMG-07", "launch-rehash")  # 这一步之前被换掉 ⇒ agentao 自己发现；交出去之后 ⇒ 执行器复核，launch-attest
    if spec.rung in POWERSHELL_RUNGS:
        if not isinstance(launcher, InterpreterIdentity):
            return Deny("hardline:unknown-rung-opaque")
        if oracle.read_config_sources(launcher.pshome, spec.execution_subject).session != launcher.session_config:
            return opaque(spec.dialect, "IMG-08", "launch-reread")  # PowerShell 级才有三来源；spawn 前立刻重读
    W = encode_workdir(decided.cwd, spec.dialect)  # LAUNCH-09；<W> 进命令行，不进 workdir 字段
    if W is None:
        return opaque(spec.dialect, "LAUNCH-09", "launch-cwd")
    return request_for(spec, launcher, decided.body, W, env, decided.cwd, decided.attested_images)
    # cwd = launcher 所在目录、workdir = decided.cwd（LAUNCH-09）；env = decide() 算好的同一个对象，不重算（ENV-06）；
    # attested_images = decided.attested_images、spec_fingerprint = spec.fingerprint（LAUNCH-01、SPEC-08）


class ShellResult(Protocol):  # 今天的 agentao/capabilities/shell.py::ShellResult；本规范不改它的形状
    ...


class BackgroundHandle(Protocol):  # 今天的 agentao/capabilities/shell.py::BackgroundHandle
    ...


@dataclass(frozen=True, kw_only=True)
class ShellRequest:  # 今天的 agentao/capabilities/shell.py::ShellRequest；PR-1 之后它携带启动请求
    launch: LaunchRequest  # LAUNCH-01：命令、环境、主体、证明映像与两个目录全在它里面
    timeout: float | None = None  # 与启动形态无关的传输参数（`on_chunk` 同类），本规范不动它们


class ShellExecutor(Protocol):  # LAUNCH-01e：两个交付面，一份请求；`ShellRequest.launch` 是两者唯一的启动来源
    def run(self, request: ShellRequest) -> ShellResult: ...
    def run_background(self, request: ShellRequest) -> BackgroundHandle: ...


def deliver(plan: ToolCallPlan, provider: ShellSpecProvider, executor: ShellExecutor,
            background: bool, timeout: float | None = None) -> ShellResult | BackgroundHandle | Deny:
    """LAUNCH-01e：`is_background` 只选最后一步调哪个方法 —— 它选不了另一条不做复核的启动路径。"""
    built = launch(plan, provider)  # 前后台共用：同一份请求、同一套 spec 核对 / 重哈希 / 重读 / 复核
    if isinstance(built, Deny):
        return built
    request = ShellRequest(launch=built, timeout=timeout)
    return executor.run_background(request) if background else executor.run(request)
