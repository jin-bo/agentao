# 宿主 FS 策略:单一 chokepoint(含 shell)上的路径域写边界

> 状态:**设计提案**,源自**两个**独立嵌入宿主的需求(一个知识库宿主 + 一个多房间聊天宿主,均嵌入
> `Agentao(...)`)。记录两个宿主当前都被迫自行重造的写边界,并把它提为一条 host-contract 原语。按
> **(a) 路径域泛化 /
> (b) 可热切换的生命周期 / (c) shell enforcement** 拆为三段,各自可独立落地;
> **(a)+(b) 不依赖 (c)**。
> Roadmap 背景:`docs/design/path-a-roadmap.md` —— 一个 **demand-gated 的 P1 *候选***,**尚非** §4
> 已追踪的 P1 工作项(§4 表只有 P1.1–P1.3)。两个内部嵌入宿主已验证需求,但 §4 的 P1 触发条件是
> *外部*采纳证据,故在 checkpoint(§16.1)提升前本提案仅为 **P1-adjacent**。注:(c) 对应 roadmap 的
> **非目标 / P2** 条目 —— 见 (c)。
>
> **更新 —— 找到 host-side interim path(只覆盖*一个* facet),优先级*下调*:****cwd 内的 immutable
> 挖洞**(guanlan/KB 宿主的 facet)**今天**就能满足、零 agentao 改动 —— 注入一个会查策略的
> `FileSystem` 能力(`Agentao(filesystem=...)`),对 **leaf 解引用后的 effective target** 做成员判定
> —— 见*interim adoption path*。**但 chahua 的 external-root facet 对原生 `write_file` 不行**:内置
> 工具在能力*之前*先跑单根 `PathPolicy` 前置检查(`file_ops.py:197,368`),写到 cwd 之外的根在 wrapper
> 上游就被拒 —— 需要 host 的 extra_tool、或 option-2 的 gate 下推(agentao 改动)。这让把 `fs_policy=` /
> `set_fs_policy` 建成新一等公民 API **进一步 demand-gated、而非现在做**:仅在 (i) **第三个**宿主开始
> 复制粘贴该 wrapper、(ii) wrapper 因 `FileSystem` / `_bind_and_register` 内部重构而**碎掉**、或
> (iii) 真要 **(c)**(shell,filesystem wrapper 证明做不到的唯一一件事)时才提升。在其一触发前,诚实的
> 建议是**发布 wrapper 配方、不建 `fs_policy=` API** —— 唯一注意:配方必须判定 **leaf 解引用后的
> target**(逐根复用单根 `contain_file` 对 immutable carve 是 *fail-open*,见*interim adoption path*),
> 而那个把 resolve 收敛起来的小 helper `PathPolicy.contain_any` 值得先落,因为它是正确 wrapper 与
> fail-open wrapper 的分界。

## 摘要

嵌入宿主需要在**构造期一次性**声明:在一棵默认可写的 `working_directory` 里,某些子路径**确定性只读**
(如 `raw/`、config 文件),**和/或**某些 cwd *之外*的额外根可写(聊天宿主的 `share/`、`tasks/`)——
并让这条边界覆盖**结构化写工具**,且可像 `/mode` 那样热切换姿态。**近期范围仅限结构化文件工具**:shell
enforcement 属于 (c),对应 roadmap 的 **P2** 条目、可能永不落地,故在那之前 shell 仍归宿主(宿主自己已
在跑的快照哨兵 / OS 沙箱)。

agentao 已有两个对的内核,但都没泛化、也没接线到这个用途:

- `security/path_policy.py` —— 只有单根 containment。
- `sandbox/` —— 单一可写子树(`_RW1`),无 immutable 子集,默认禁用,且仅 darwin。

于是每个嵌入宿主都在自己代码里重造这条边界(逐路径正则 deny-rules + 每轮快照 diff +
为绕开「run-rules 不可删」而打的 `set_mode` 补丁)。本提案请 agentao 把这条边界作为嵌入面原语提供。

## 两个驱动宿主 —— 同一缺口的两个 facet

两个真实嵌入宿主从相反方向撞上单根上限。它们不是两条需求,而是一个泛化谓词的两个 facet(见 (a))。

| 宿主 | 形状 | 当前被迫的重造 |
|---|---|---|
| 知识库宿主 | **根内只读挖洞** —— cwd 可写,`raw/`+config 挖为**确定性只读**(是只读*子集*,非 cwd 白名单 —— 见下文 Augment) | 在 `file_path` 上挂逐路径正则 deny-rules(脆弱;写错 arg 名即 fail open) |
| 多房间聊天宿主(chahua) | **外部多根** —— per-guest cwd(`<room>/guests/<name>/`)+ 物理分离的房间级树(`<room>/share/`、`<room>/tasks/<active>/artifacts/`),经 `symlink`-into-cwd 够到 | 造了专门的 `TaskWriteArtifactTool`(`task_tools.py:326`)**绕开 `PathPolicy`** 直接落盘,因为 `contain_file` 解析 `./task` 软链后判定目标在 `working_directory` 之外而拒 |

chahua 这例是「单根逼出绕过工具」更尖锐的证据:它的 symlink(`./task` → `<room>/tasks/<id>/artifacts/`)
解析到 guest cwd **之外**,于是原生 `write_file('./task/x')` 被拒、而 `read_file('./task/x')` 能读
—— 一个读写不对称,宿主只能用绕过工具补,**还得在工具 description 里教模型**。同一宿主还在传输层
手写 `allowed_root = (room_dir/"share").resolve()` 做 containment(`server_inbound_io.py`)——
对同一条边界的第二次独立重造。

## 现状(已对 `main` grep 核对)

| 缺口 | 证据 |
|---|---|
| 写边界只有单根,无路径域 | `security/path_policy.py` —— `@dataclass(frozen=True) class PathPolicy` 仅持有 `project_root: Path`;`contain_file` 只断言落在这一个根内。无「可写根集 / 只读子集」词汇。 |
| shell 写完全旁路路径门禁 | `path_policy.py` docstring 明文:*「Shell command **arguments** are not inspected — only the cwd is contained.」* `echo>` / `mv` / `python -c` 可写到 OS 允许的任意位置。 |
| per-run deny-rules 只增、且按 arg 正则匹配 | `permissions.py` —— `add_run_rules(deny=…)` 追加进 `_run_scope_rules`;**无** `remove_run_rules` / `clear_run_rules`。`_matches` 用 `re.search(arg_pattern, str(tool_args.get(arg_key,"")))`(`permissions.py:527`)—— 调用方须知道精确 arg 名(`file_path`),写错/改名即静默漏拦(fail open)。 |
| sandbox 存在,但不是「可写域」面 | `sandbox/` 有 `SandboxPolicy` + `workspace_root` + macOS profiles。`embedding/factory.py:187-188` **确实**构造了 `SandboxPolicy(project_root=wd)` —— 已接入工厂。缺的是:`enabled` 默认 `False`(`sandbox/policy.py:56`)、`platform: "darwin"` 单平台(无 landlock/bubblewrap 实现)、profile 只表达**单一**可写子树 `_RW1`(`workspace-write.sb:18-19`)且**无 immutable-subpath** 概念、也没有从声明的可写域推导 `_RW1` 的 kwarg。 |

## (a) 泛化 `PathPolicy`:单根 → 声明的**根集**(writable ∪ immutable)

**范围:**仅结构化写工具(`write_file`、`replace`、未来任何文件写工具)。纯路径语义、零平台依赖。
**独立——最先落。**

- 把 `PathPolicy`(或在同一 chokepoint 处增设一个旁路同伴)从 `project_root: Path` 扩展为
  `writable: list[Path]` ∪ `immutable: list[Path]` —— **根的集合,且每个根可在 `working_directory`
  之外**(多房间宿主需要 `<room>/share` 和 `<room>/tasks`,它们是 guest cwd 的兄弟而非子目录)。
- 复用现有 resolve + 跟随 symlink + `..`-safe 逻辑(`_resolve_for_write` / `_assert_inside`)。
  谓词从 `is_relative_to(project_root)` 泛化为:**`resolved ∈ (working_directory ∪ ⋃ writable)` 且
  `resolved ∉ ⋃ immutable`**,在 symlink 解析后的目标上判定(cwd 隐式在 writable 集内 —— 见下文
  Augment)。**containment 语义,非正则**——这正是相对当前 `re.search`-by-arg deny-rule 路线的关键改进。
- `immutable` 优先于 `writable`(落在可写根内、但处于 immutable 子路径下的路径,仍只读)。这是
  **知识库宿主**的 facet。
- 根可在 `working_directory` 之外,覆盖**多房间宿主**的 facet:目标解析进一个已声明外部根的 symlink
  放行。这正是退掉它绕过工具的关键。

### chokepoint 实际在哪(「单一 chokepoint」是设计要求,不是今天的现实)

今天**并不存在**单一 chokepoint。`PathPolicy.for_tool(...)` 是在*每个内置写工具自己的* `execute()`
里调用的 —— grep 核对仅在 `tools/file_ops.py:197,368` 与 `tools/shell.py:230`,**别无他处**。每个工具
被绑定的那个共享能力是 `agent.filesystem`(`tooling/registry.py:78` —— `_bind_and_register` 对**内置与
`extra_tools` 一视同仁**地设 `tool.filesystem = agent.filesystem`)。而那个能力**不**查 `PathPolicy`。于是:

- 宿主注入的 `extra_tool` 若调用 `self.filesystem.write_text(...)`,就**直接落盘、绕过门禁** —— 它继承
  了绑定,却没有内置工具手写的那次 `for_tool` 调用。
- MCP 工具(`mcp_*`)完全在这条路径之外。

这改变了 (a) 的契约,故须明说。两个选项:

1. **把 FsPolicy 范围限定在内置文件工具** —— 诚实且小,但那样「所有结构化写工具都 enforce 边界」对
   注入/MCP 工具就是**假的**,文档必须言明。
2. **把 enforcement 下沉进 `FileSystem` 能力**(`agent.filesystem`)—— 让 `write_text`(今天
   `FileSystem` 协议上唯一的写方法,`filesystem.py:73`,外加未来任何写方法)去查策略,**并替换掉内置工具
   自己那道单根 `PathPolicy.for_tool(...).contain_file(...)` 前置检查**(`file_ops.py:197,368`),让能力
   成为*唯一*门禁。
   - **别只是*删掉*前置检查 —— 它身兼两职。**`contain_file` 今天既 (1) 把(可能相对的)`file_path` 解析成
     交给 `write_text` 的绝对 `Path`、又 (2) 做安全检查;而 `FileSystem` 要求**绝对**路径
     (`filesystem.py:46`)。所以工具必须先用 `Tool._resolve_path(file_path)`(cwd 绑定,`base.py:61`)做
     解析这一半,再把结果交给**带策略的** `write_text` 做最终 leaf-deref 放行/拒绝。删掉前置检查却不补回
     解析步骤,会让相对路径落回进程 cwd(或原样进 FS)—— 正好把 `PathPolicy` 要堵的 containment 洞又打开。
   因为 `_bind_and_register` 把*同一个*能力交给 `extra_tools`,这才让「单一 chokepoint」
   *成真*:经绑定 filesystem 写盘的注入工具自动被覆盖,内置工具手写的 `for_tool` 调用也收敛进来。
   **推荐** —— 这是唯一与本文其余处「单一 chokepoint」措辞一致的选项。(MCP 工具仍绕过 —— 它们不用
   `agent.filesystem` —— 故封闭 allow-set 的论断永远须限定为「经 agentao filesystem 能力的写」,而非
   「任意工具」。)

### augment 而非 replace —— 以及 `fs_policy` 从不门禁什么(抄示例前先读)

- **`working_directory` 恒隐式可写(augment 语义)。**`writable=[...]` 声明的是*额外*根 —— 通常在
  cwd *之外*;它**不替换** cwd。宿主永不需要为「让 agent 写自己的工作区」而重列自己的 cwd,漏列反而
  是个静默 footgun。
- **agentao 自身的持久化完全在 `fs_policy` 范围之外。**`PathPolicy` 只在写*工具*里施加:
  `PathPolicy.for_tool(...)` 仅出现在 `tools/file_ops.py:197,368` 与 `tools/shell.py:230`
  (grep 核对 —— 这是它仅有的调用点)。`MemoryManager`(`memory/manager.py`)与 `session.py` 不 import
  `PathPolicy`,经直接 sqlite/file I/O 写 `.agentao/memory.db` / `sessions/`,从不经过该 chokepoint
  —— 所以 `fs_policy` 在任何语义下都既保护不了也破坏不了它们。(这纠正一个诱人的误读:声明
  `writable=[share, tasks]` **不会**危及 guest 的 `.agentao/` 持久化。*replace* 式 allow-list 真正会
  破坏的,是 guest 经 `write_file` 往自己 cwd 写的 scratch 文件 —— 而 augment 语义保住了它。)

**刻意:augment 是无条件的 —— 没有 cwd 内白名单模式。**cwd 默认可写,`immutable` 从中挖只读子路径
(deny-list 姿态)。为什么默认走 deny-list 而非 cwd 内白名单?**不是**因为两者「一样脆弱」—— 它们的
失败方向*相反*,而对一个 security boundary,这个不对称很要紧:

- cwd 白名单(`writable=[wiki,workspace,graph]`)漏了 `graph` → graph 静默只读 → 写**响亮失败**。
  fail-*safe*(可用性失败)。
- deny-list(`immutable=[raw,config]`)漏列某敏感目录 → 它保持**可写** → agent 可静默覆写。
  fail-*open*(安全失败)—— 对本文反复自称的「确定性安全边界」,这是更糟的失败模式。

诚实的理由是**保持现状,而非 footgun 对称。**deny-list 默认 = 今天的行为(单根包含 ⇒ cwd 全可写)
**加上**一个新的只读 carve —— 它*不放松任何东西*。cwd 内白名单是**更严的新**姿态(cwd 大部分变只读)。
所以默认走 deny-list 只是*增*能力;想要更严的闭合白名单是新需求 —— demand-gate 它。漏列 `immutable`
的 fail-open 风险**由 host 自负,且与今天 cwd 全可写的基线完全相同** —— 本提案并未新引入它。两个
motivating 宿主都不要白名单 —— KB 接受 deny-list 挖洞、多房间要 cwd 全可写 —— 故两者跑*同一条*无条件
谓词。一个*条件式* augment(「声明 cwd 内 writable 根就把 cwd 翻成白名单」)曾被考虑并否决:一条声明
就静默改变整棵 cwd 的默认姿态,是个出人意料的非局部效应。若未来某宿主真需要 cwd 内闭合白名单,届时作
显式 opt-in 增补(如 `FsPolicy(closed_cwd=True)`)—— demand-gated,不投机预建。

### 安全不变量(不要松动 PathPolicy 存在的理由)

`PathPolicy` 存在的全部理由就是挡 symlink / 绝对路径逃逸(`write_file('/etc/passwd')`、
`write_file('../outside')` —— 见其 docstring)。泛化到外部根**绝不能**把这个洞放回来:

- **默认(无 `fs_policy`)保持今天的单根 containment。**外部根是 opt-in 逃生舱,绝非新默认。
- allow-set 是**封闭**的:`resolved ∈ (working_directory ∪ ⋃ 已声明根)`。我们授权的是**目的地根**,
  不是「symlink 指向的任何地方」。chahua 的 `./task` 软链被放行,**是因为它的目标解析进已声明的
  `<room>/tasks` 根**—— policy 信任的是那棵根,symlink 只是够到它的 ergonomic 手段。框成「信任
  symlink 目标」会把 `/etc/passwd`-via-symlink 逃逸放回来。**没有「写到任意处」模式** —— 那是
  `PermissionMode` 的正交轴(故旧的 `deny_outside_root` flag 删除:写边界*本就*永远是封闭 allow-set;
  该 flag 既冗余,且一旦根可外部,名字本身自相矛盾)。
- `immutable` 在整个解析空间内仍然优先(deny 胜),宿主可声明外部可写根**并**在其中挖只读子路径。
- **不引入新 TOCTOU 面。**外部根仍走 `resolve()`-then-check,与今天单根 `PathPolicy` 完全一致;
  check 与 write 之间换 symlink 的经典缝隙,既不因允许外部根而变大也不变小。
- **相对根条目相对 `working_directory` 解析,且在构造期规范化。**KB 示例用 `immutable=["raw",
  "AGENTAO.md"]`(相对)、而 `writable` 可能写外部*绝对*根 —— 故相对-vs-绝对语义须钉死,不能交给
  `Path` 默认。规则:相对 `writable`/`immutable` 条目 join 到 `working_directory`(与
  `_resolve_for_write` 现有的「相对则 join 到 `project_root`」一致,`path_policy.py:119-120`),**不**
  相对进程 cwd。所有根在 **`FsPolicy` 构造期**做 `expanduser().resolve()` 规范化并校验(安全边界应在
  根畸形时响亮失败,而非到首次写才静默出错)。**用两条规则拆掉自引用**(早期草稿写「经 `..` 逃逸即被拒,
  除非它*同时*落进某个已声明外部根」—— 这需要外部根集合已经构造完才能判定):**相对**条目一律 cwd-相对、
  且**不得**逃出 cwd —— 经 `..` 逃逸的相对条目在构造期直接被拒;**外部**根必须用**绝对**(或 host 预解析)
  `Path` 声明。「这是不是外部根」于是由条目自身的**形式**语法判定,而非回头去查正在构造的那个集合。

`embedding/factory.build_from_environment` 新增构造 kwarg:

```python
# 知识库宿主 —— cwd 内的只读挖洞(immutable facet):
agent = build_from_environment(
    working_directory=kb,                              # 隐式可写
    fs_policy=FsPolicy(
        immutable=["raw", "AGENTAO.md", "SCHEMA.md"],  # cwd 下其余一律仍可写
    ),
)

# 多房间聊天宿主(chahua)—— 额外外部根(writable facet):
agent = build_from_environment(
    working_directory=room / "guests" / name,           # 隐式可写:guest 自己的区域、
                                                         # .agentao/ 持久化、经 write_file 的 scratch
    fs_policy=FsPolicy(
        writable=[room / "share", room / "tasks"],       # 追加根,在 guest cwd 之外
    ),
)
# 注(isolation):上面 cwd 这行假设 chahua 默认 room-isolation
# (`<room>/guests/<name>/`)。isolation="global" 时 cwd 在
# `<user_data_root>/guests/<name>/`(config.py:220)—— 与 `<room>/share`、
# `<room>/tasks` 不相干的两棵树,连「兄弟」都算不上。这反而强化外部根论证:
# 声明的根是真正跨树的。
```

**待定取舍(多房间宿主):**声明 `<room>/tasks` **父**目录为可写可让 policy 保持静态 ——
`./task` 软链在 `open/set_active/close` 间重指,policy 永不变(于是 (b) 热切换**不需要**)。代价是
**所有** task 的 artifacts 都可写,而非仅 active 那个。若「仅 active task 可写」是安全要求,则需具体
声明 active-task artifacts 目录并用 (b) 在重指时切换 policy。父根方案用 active-only 精度换 policy
稳定;由宿主拍板。

**交付物:**经 agentao `FileSystem` 能力的每一次写都在单一 chokepoint enforce `FsPolicy`(按「chokepoint
实际在哪」:选项 2 —— 在 `agent.filesystem` 里 enforce —— 才让这对注入的 `extra_tools` 也成立,而非仅
内置;MCP 工具留在范围外)—— 宿主不必知道每个工具的 arg 名、也不必逐工具加写正则。**同时**退掉知识库
宿主的正则 deny-rule 层**和**多房间宿主的 `PathPolicy`-绕过工具(连同其读写不对称与工具 description 里
那条面向模型的说明)。

## (b) 让策略可热切换 / run-rules 可装卸

**范围:**per-run 边界的生命周期。独立于 (a) 和 (c)。

- 增加 `agent.set_fs_policy(FsPolicy(...))` + `agent.fs_policy` getter,让宿主 `/mode` 翻姿态通过
  *替换*策略完成,不留残规则。
- **切换时须让 `for_tool` 缓存失效 —— 现有缓存只按 cwd 做 key。**`PathPolicy.for_tool` 用
  `tool._path_policy_cache = (wd, policy)` 记忆,并在 `cached[0] == wd` 时返回缓存策略
  (`path_policy.py:48-53`)。由于 key **仅是 `working_directory`**,一次保持 cwd 不变的
  `set_fs_policy(...)`(常见情形 —— 只改 `immutable`/`writable`)会让每个已绑定工具继续服务**陈旧**
  策略。故 `set_fs_policy` 必须要么把 policy 身份/版本并入缓存 key,要么在切换时主动清掉各工具缓存。
  这是 (a) 的缓存与 (b) 的生命周期之间的具体耦合 —— 实现时须点名,它是个容易静默的 bug。
- 即便没有 `FsPolicy` 也独立有用:给 `_run_scope_rules` 补公开的 `remove_run_rules` /
  `clear_run_rules`,让现有只增 API 不再逼出「构造期永久注入、`set_mode` 不装卸」的补丁。

**正交性(保持干净):**`FsPolicy` 回答*写能落到哪*;`PermissionMode` / `readonly_mode` 回答
*能不能写 / 跑 shell*。二者组合,宿主声明一次 FS 域即可。我们刻意**不**让 `FsPolicy` 去重新推导
read-only —— 那仍是 Mode 的职责。

**反模式(一个真实宿主差点这么发版):****不要**用 `set_fs_policy(FsPolicy(writable=[]))` 表达
「只读」。空 writable 集不是表达只读姿态的方式 —— `FsPolicy` 从不回答*能不能写*。宿主 `/mode` 翻只读
是 `PermissionMode` / `readonly_mode` 的变更(permission engine + tool runner 的两点姿态),**不是**
`FsPolicy` 替换。`set_fs_policy` 只用于切换哪些域可写/只读;「agent 到底能不能写」交给 Mode。

**并非总是需要:**(b) 只在宿主需要可写集**中途变化**时才承重。声明一个稳定父根、并在其下重指 symlink
的宿主(多房间宿主的 `./task` 模式,见 (a) 的取舍)policy 保持静态,根本不需要 `set_fs_policy`。

## Interim adoption path —— host-side wrapper(只覆盖 immutable facet)

本提案想要的执行点 —— 「chokepoint 实际在哪」的选项 2,一个会查策略的 `FileSystem` 能力 ——
**今天就已是可注入的 host-contract 面**:`Agentao(filesystem=...)`(`agent.py:87`)被
`_bind_and_register` 绑给每个工具(`registry.py:78`)。宿主**当下**就能注入一个 wrapper、零 agentao 改动:

> **范围先说清 —— wrapper 只能*收紧*,绝不能*扩张*。**内置 `write_file` / `replace` 在调
> `self.filesystem.write_text` **之前**先跑自己那道单根 `PathPolicy.for_tool(...).contain_file(...)`
> (`file_ops.py:197,368`)。wrapper 在那道前置检查的*下游*,所以:
> - **cwd 内 immutable 挖洞(guanlan/KB 宿主):完全可行。**`cwd/raw/secret` 在 cwd 内 → *过*前置检查
>   → 到 wrapper → wrapper 拒。从已可写的 cwd 里挖只读子路径,正是「在已允许的范围内收紧」。✅ 零 agentao
>   改动、原生 `write_file`。
> - **cwd 之外的外部根(chahua):原生 `write_file` 不行。**`./task/x → <room>/tasks/...` 解析到 cwd 外
>   → 前置检查在**上游就拒**,wrapper 根本没机会跑。wrapper 无法*放行*前置检查已拒的东西。宿主只能
>   (a) 把外部写走 `extra_tool`(chahua 已有 `TaskWriteArtifactTool`),其 `self.filesystem` 是 wrapper
>   —— 这让那条 bypass *纳入门禁*,但**不退役**它、也**不**让原生 `write_file` 够到外部根;或 (b) option-2
>   的 gate 下推,那是 agentao 代码改动。所以 external-root facet **不是**「零改动 host 可满足」—— 只有
>   immutable facet 是。

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class _Rule:                          # 不可变:整体替换引用,绝不逐字段改
    writable: tuple[Path, ...]        # cwd 之外追加的根;cwd 是隐式的(见 ctor)
    immutable: tuple[Path, ...]

def _effective_target(raw: str) -> Path:
    """open() 实际写到哪:parent 链解析(..-safe)、leaf symlink 跟随。
    成员判定只能基于它,而非字面路径(见下文说明)。"""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # FileSystem 只收绝对路径(filesystem.py:46);相对解析是工具的职责
        # (Tool._resolve_path)。在此拒绝,免得相对路径被静默地按*进程* cwd 解析、
        # 而非 agent 的 working_directory。
        raise PermissionError(f"FsPolicy: non-absolute path reached the filesystem: {raw}")
    t = p.parent.resolve(strict=False) / p.name      # 跟随 parent 链 symlink
    if t.is_symlink():                               # open() 也会跟随 leaf symlink
        t = t.resolve(strict=False)
    return t

def _under(root: Path, target: Path) -> bool:
    root = root.resolve()
    return target == root or root in target.parents

class PolicyFileSystem:
    def __init__(self, inner, working_directory: Path, rule: _Rule):
        self._fs = inner
        self._wd = working_directory.resolve()       # cwd 恒隐式可写(augment)
        self._rule = rule
    def set_policy(self, rule: _Rule):                # host 侧的 set_fs_policy 等价物
        self._rule = rule                             # 原子换引用(GIL)—— 无撕裂读
    def _check(self, raw: str):
        rule = self._rule                             # 每次写取一次本地快照
        t = _effective_target(raw)                    # 解析一次,再做成员判定
        if not any(_under(w, t) for w in (self._wd, *rule.writable)):
            raise PermissionError(f"FsPolicy: outside writable roots: {t}")
        if any(_under(m, t) for m in rule.immutable): # immutable 优先 —— leaf-symlink-safe
            raise PermissionError(f"FsPolicy: immutable: {t}")
    def write_text(self, path, data, *, append=False):
        self._check(str(path)); return self._fs.write_text(path, data, append=append)
    def __getattr__(self, name): return getattr(self._fs, name)

# KB 宿主:cwd 隐式可写,raw/ + config 挖为只读:
agent = build_from_environment(
    working_directory=kb,
    filesystem=PolicyFileSystem(LocalFileSystem(), kb,
        _Rule(writable=(), immutable=(kb / "raw", kb / "AGENTAO.md"))),
)
```

**调用方必须给 wrapper 传绝对路径。**wrapper *本身*是个 `FileSystem` 实现,协议只收绝对路径
(`filesystem.py:46`)—— 相对解析是*工具*的职责(`Tool._resolve_path`,`base.py:61`)。内置
`write_file` / `replace` 已经给 `write_text` 传绝对路径(其上游 `contain_file` 解析过),故 wrapper 从它们
那里收到的都是绝对路径。但服务 chahua external-root 路线的**被门禁的 `extra_tool`** 必须**自己**在
`self.filesystem.write_text(...)` *之前*调 `self._resolve_path(file_path)`;否则相对的 `./task/x` 会被按
**进程** cwd 解析、而非 agent 的 `working_directory`,检查与实际写入就会分叉。上面的 `not p.is_absolute()`
守卫把这种情况变成响亮拒绝、而非静默的 process-cwd 写。(宿主*也可*在 wrapper 内把相对路径绑到 `_wd`,
但那会复制协议刻意分给工具的解析职责 —— 优先在工具里解析。)

**为什么不直接逐根复用 `PathPolicy.contain_file`(早期草稿这么写 —— 它 fail-open)。**`contain_file`
**不是**成员判定。它在解引用 leaf symlink **之前**就先断言 *parent-resolved* 路径落在 `project_root` 内
(`path_policy.py:76-85`)。于是 `cwd/scratch/link → cwd/raw/secret` 能过 writable-cwd 检查,但
immutable-`raw` 检查会 **fail open**:parent(`cwd/scratch`)不在 `raw` 下,故 `contain_file(project_root=raw)`
在跟随 link 进入 `raw` **之前**就抛 —— wrapper 会错误放行,而 `open()` 随后经 symlink 改写 `raw/secret`,
违反「immutable wins」。正确依据是 **leaf 解引用后的 effective target**,用 `is_relative_to` 对每个根判定
(上面的 `_effective_target`)。这**确实**在宿主侧重写了 `PathPolicy` resolve 逻辑的 ~6 行 —— 而那恰是
安全关键部分,也恰恰说明:正确的「多根 + immutable」谓词**今天无法**从单根原语组合出来(见下文
*让 wrapper 正确的 agentao 侧 helper*)。宿主不必从零推导算法,但「复用 `contain_file` 就安全」是错的 —— 必须说清。

**热切换有效 —— 但只能改共享实例,绝不能换对象。**`_bind_and_register` 在注册时把 `agent.filesystem`
拷进每个工具*自己的* `tool.filesystem`(`registry.py:78`;工具随后读 `self.filesystem`,
`tools/base.py:43-48`)。所以 `agent.filesystem = NewWrapper()` 对已注册工具**不可见**。生效机制:
每个工具都指向*同一个* wrapper 实例,故 `wrapper.set_policy(...)` 下次写时全员可见。把 policy 存成单个
frozen `_Rule`、原子替换引用,以避免撕裂读(writable 新 + immutable 旧)。附带好处:因为 wrapper 每次
调用都现查,它**绕开了** (b) 在内置路径里必须修的 `for_tool` 缓存陈旧坑。

**wrapper 仍做不到的**(与正文一致):MCP 工具(`mcp_*`)不经 `agent.filesystem`;shell
(`run_shell_command` 里的 `echo > f`)走的是 `shell` 能力、非 `filesystem` —— 那是 (c),任何
filesystem wrapper 都够不到。

### 对优先级的影响 —— 两个 facet 要分开

两个 facet **不**同等地「host 可满足」,所以给不同建议:

- **immutable 挖洞(guanlan/KB 宿主):**今天 wrapper 完全可满足(leaf-deref 成员判定、原生
  `write_file`)。这*确实*是「cwd 内边界 host 可满足」的 demand 证据,**降低**了为这个 facet 建新 agent
  API 的理由。
- **外部根(chahua):**对原生工具**不是**零改动 host 可满足 —— 内置单根前置检查(`file_ops.py:197,368`)
  在 wrapper 上游。宿主只能 (a) 保留一个 `extra_tool`(现在被 wrapper 纳入门禁,但未退役)或 (b) option-2
  gate 下推(agentao 代码改动)。所以这个 facet **保留了一个真正 agentao 形状的内核**:让内置写工具遵守
  声明的多根策略(即 (a) 的 gate 下推)是退役 bypass、让原生 `write_file` 够到外部根的*唯一*办法。

故在以下具体触发时才往 wrapper 之外提升:

- **第三个**嵌入宿主需要这条边界(wrapper 在被复制粘贴),或
- wrapper 因 `FileSystem` / `_bind_and_register` 内部变动而**碎掉**,或
- 某宿主需要原生外部根写 / 要退役 bypass 工具(gate 下推),或
- 某宿主真要 **(c)**(shell)—— 任何 filesystem wrapper 都做不到。

诚实的建议:**现在不建 `fs_policy=` 生命周期 API。**为 immutable facet 发布 wrapper 配方;把
external-root 的 gate 下推当作「当 chahua(或第三个宿主)真要原生外部写、而非一个被门禁的 `extra_tool`
时」第一个值得建的切片。

### 让 wrapper *正确*(而不只是更短)的 agentao 侧 helper

因为单根 `contain_file` 无法充当多根成员判定(见上),每个宿主 wrapper 都得手写 `_effective_target`
—— 安全关键的 resolve 逻辑,而一部分宿主*会*写错(本文存在的全部理由就是消除这个 footgun)。所以在现有
原语上加一个 leaf-deref 成员 helper **不是** ergonomics 糖,而是**正确复用面**:

```python
# 解析 leaf 解引用后的目标,并对每个根判定;逃逸/命中 immutable 即抛
PathPolicy.contain_any(raw, writable=[...], immutable=[...])
```

仍然很小 —— 在现有 resolve 内核上加一个 classmethod,**没有** agent 生命周期 API、**没有**新的
host-contract 对象。但这是**即便 `fs_policy=` API 仍 demand-gated、也值得先落的唯一一刀**:它是正确
wrapper 与 fail-open wrapper 的分界。interim wrapper 没有它*也*能用(上面的 `_effective_target` 配方
是正确的),所以这是强烈建议、非阻塞项 —— 尽早落,免得每个宿主都重写一遍安全关键的 resolve。

## (c) 把 `FsPolicy` enforce 到 shell —— 最难、平台门控的一环

**范围:**唯一宿主自己真的做不到的部分。宿主**不应** block 在这一环——可保留 best-effort 快照哨兵
直到它落地。

**与 roadmap 的调和(引用回链前先读这条):**(c) **就是** roadmap §2.3 的显式非目标 ——
「✗ Cross-platform strong sandbox — embedding hosts already isolate at process level」—— 以及其 §5
**P2** 条目「Sandbox backend interface(… linux-bubblewrap …)」。所以 (c) 是**本提案最靠后的一刀
—— P2 而非 P1,且可能永不落地**。这是自洽而非矛盾:提案的近期价值是 (a)+(b),二者**不依赖 (c)**
(抬头已声明)。在 (c) 存在前,把边界 enforce *到 shell* 仍归宿主;而**一旦 (a) 的 gate 下推落地**,
提案的 (a)+(b) 设计就覆盖每个*结构化*写工具(仅 interim wrapper 只覆盖 immutable facet —— 见
*interim adoption path*)。

- **优先——OS 沙箱:**把 `FsPolicy` 接进 `sandbox/`:可写参数 ← `FsPolicy.writable`,并增加
  **immutable-subpath** 能力。
  - 具体缺口 1 —— *单参数 vs. 可写**集***:现有 profile 只取**一个** `(param "_RW1")`
    (`workspace-write.sb:18-19`)。`FsPolicy.writable: list[Path]` **映射不到**单个 `_RW1` —— 要么多
    参数(`_RW1`、`_RW2`…),要么**生成式** profile,对每个声明的可写根 emit 一条 `(subpath …)`。
    「从 `writable` 推导 `_RW1`」低估了它;这是 profile *生成*,不是一行参数替换。
  - 具体缺口 2 —— *immutable 挖洞*:要在 cwd 可写的同时 enforce `immutable=["raw"]`,需要一个
    deny-after-allow 排序的新 profile 形态,如
    `(allow file-write* (subpath _RW1)) (deny file-write* (subpath _RAW))`。SBPL 支持,但这是新的
    profile 工作量。
  - 具体缺口 3 —— *封闭 allow-set 在 OS 层其实并不封闭*:profile 还无条件允许写 `/tmp`、`/var/tmp`、
    `/private/tmp`、`/private/var/tmp`、`/private/var/folders/…`(`workspace-write.sb:18-26`,供
    `npm`/`pip`/构建临时用)。所以 shell 层的可写集 = `FsPolicy.writable ∪ {临时目录}`,**宽于** (a)
    对结构化工具 enforce 的集合。要么把临时目录写作对封闭-allow-set 不变量的显式、有意例外,要么把该
    不变量的「封闭」论断限定到结构化工具层、并称 shell 层为「除构建临时外封闭」。别让文档暗示沙箱
    enforce 的是与 (a) *相同*的封闭集 —— 它不是。
  - Linux 当前**无**实现(`grep landlock` 无匹配)—— landlock/bubblewrap 是净新增。
- **跨平台兜底(无 OS 沙箱):**提供尽可能确定性的 fallback —— 通过执行前/后路径校验或 diff 拒绝
  shell 写到声明的 `immutable` 域,或至少**暴露一个宿主可订阅的「写越界」信号**(契合现有
  `host` / `EventStream` 契约)。
- **诚实措辞:**「确定性」只在 OS 沙箱层成立。无沙箱时,前/后 diff 对并发或对抗性 shell 只是
  best-effort —— 文档应写作 *沙箱内确定性,否则 best-effort 哨兵 + 越界信号*,而非笼统保证。

**detection ≠ enforcement:**(c) 是写*边界*。若某宿主的每轮扫盘只为*感知哪些文件变了*(多房间宿主的
artifact `diff-scan`,不是拦越界写),那么 (c) 对它是**新增**边界、非简化 —— 它的扫盘为自己的目的而留。
不要假设每个带轮末 diff 的宿主都想要 (c)。

## 为什么这条该在 agentao(边界论证)

「写能落到哪」是确定性安全边界,而 `PathPolicy` 本就是 agentao 里这个家族的原语。本提案是对一个
现有 host-contract 原语的自然泛化(`project_root: Path` → 一个声明的 `writable`/`immutable` 根集),
外加接线到另一个现有原语(`sandbox/`)——**不是**新增产品关切,且要求 agentao 学习**零**宿主领域
语义。两个独立宿主已从相反方向各自重造它(根内细分 + 外部多根),这正是 roadmap P1 门禁要的需求证据。
shell enforcement 尤其**无法**在宿主侧实现,除非重造一套 agentao 已经拥有的沙箱。每个嵌入宿主都受益。

## 仍归宿主侧(agentao 范围之外)

- **哪些**路径可写/只读 —— 宿主策略,经 `FsPolicy` 声明,非内置。
- 领域门禁(如写后 frontmatter / 断链校验、ingest 快照 gate)—— 业务校验。
- **传输层路径授权**(如多房间宿主校验远端 guest 经线传来的 `./share/x` 引用)—— 与进程内工具写是
  不同的边界;`FsPolicy` 落地后仍归宿主。
- **artifact 检测**(轮末 diff-scan 感知*哪些*文件变了)—— 归宿主;与写边界正交(见 (c))。
- 单写者并发(写锁)—— 宿主进程模型。
- 可选的不变量哨兵快照 —— (c) 落地后留作纵深防御合理。

## 排序建议

0. **现在 —— host-side、零 agentao 改动(仅 immutable facet):**为 **cwd 内只读挖洞**(guanlan/KB
   宿主)注入 `PolicyFileSystem` wrapper(见*interim adoption path*)。wrapper 必须判定 **leaf 解引用后的
   effective target**(不是逐根复用单根 `contain_file` —— 那对 immutable carve 是 fail-open)。这就删掉了
   KB 宿主的正则 deny-rule 层与 `set_mode` 补丁,无需等任何 agentao 改动。**chahua 的 external-root facet
   在此*不*解锁** —— 内置单根前置检查在 wrapper 上游拒掉 cwd 外的写;chahua 保留一个(现在被门禁的)
   `extra_tool`,直到 step 1。
0.5 **落 `PathPolicy.contain_any(raw, writable, immutable)`** —— 很小、无新 API 面,但它把安全关键的
   leaf-deref resolve 收敛起来,让每个宿主的 wrapper 不会写错。这是即便其余仍 demand-gated、也值得先落的
   *那一刀*。
1. **external-root facet 的 gate 下推**(option 2:内置写工具经 `FileSystem` 能力遵守声明的多根策略;
   删掉它们的单根前置检查)—— 当某宿主需要**原生**外部根写 / 要退役 bypass 工具时,这是第一个值得*建*的
   切片。完整的 `fs_policy=` / `set_fs_policy` 生命周期 API 在此之外仍 **demand-gated**(第三个宿主、
   wrapper 被重构搞碎、或 (c) —— 见*对优先级的影响*)。
2. **(c)** 单独走自己的轨;在它落地前,快照哨兵仍是 Linux 上**唯一**的 shell 写防线 ——
   应注明它在那里是承重的,而非「可选」。
