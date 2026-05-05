# 权限地板化方案

**状态：** 实施计划，rev 3。2026-05-03 起草，跨 4 轮评审。**PR 1（P0 正确性修复）、PR 2（P1 可选 hardline 层）、PR 3–5（P2/P3 便利项与敏感写入预设）均已于 2026-05-04 全部合入。** 本计划已收口；后续加固工作另行追踪。详见 §9 各 PR 落点；§10 保留遗留的开放追踪项（主要是 PR 5 正则层向 `bashlex` 解析的演进）。
**读者：** 接手实施的 agentao 维护者；后续 PR 的评审者。
**配套：**
- `docs/design/embedded-host-contract.md` — `PermissionDecisionEvent` 在此定义
- `docs/design/path-a-roadmap.md` — 锁定本计划必须遵守的 "embedded harness" 定位
- `docs/design/metacognitive-boundary.md` — round 4 重新对齐的 "schema + default + host-override" 原则
- `docs/features/TOOL_CONFIRMATION_FEATURE.md` — 当前确认管线
- `docs/design/permission-hardening-plan.md` — 英文版

---

## 1. 为什么写这份计划，以及为什么它一直在错

Round 1 是 Hermes-借鉴 sweep——"Agentao 该从 `hermes-agent` 近期更新里采纳哪些？"。Round 2 修正了对 Agentao 实际代码的技术错误。Round 3 修正了由此产生的方案中的架构错误。Round 4——故意针对 Agentao 锁定定位的反向评审——发现 round 1–3 都在回答错误的问题：

> "我们如何正确地复制 Hermes 的 hardline 地板？"

正确的问题，也是本 rev 3 终于在面对的：

> "鉴于 Agentao 是 embedded harness——不是策略权威，而 `agentao.host` 的存在恰恰是为了让宿主决定策略——Agentao 到底要不要采纳一个 hardline 地板？"

反向评审给的答案是：**部分采纳**。库应当**提供**一个安全默认开启的 hardline 层（这样 CLI 用户、或者没认真想清楚的宿主，能被保护免于 prompt 注入擦盘）。但它必须**对嵌入式宿主可关闭**——给那些自己承担策略责任的宿主——因为在一个本应可被嵌入的库里硬编码"agent 永远不能做 X"，跟整个 `agentao.host` 契约相矛盾。

本 rev 3 同时把**正确性修复**（不论 Agentao 怎么定位都需要）跟**策略选择**（取决于上面那个问题的答案）分开。正确性先发车，独立 PR，不带任何策略立场。

## 2. 已折入的评审修正

下面方案吸收了所有 4 轮的修正。列出来是为了让未来的评审者不要再重新讨论已经定下来的点。

**Round 2（针对 Hermes-借鉴笔记）：**

1. **MCP retry。** `agentao/mcp/client.py:135-156` 对**任何**第一轮异常都会重试一次，并非只在 `_session is None` 时。真正的问题是**错误分类**，不是漏掉 retry。
2. **ANSI 处理。** `agentao/tools/shell.py:40` 已经剥 ANSI 转义。只缺 OSC 序列——而且只有引入 shell 化文件读路径后才会撞到。
3. **Hardline 放置位置。** Hardline 检查**如果存在**，必须是 `PermissionEngine.decide_detail()` 里的前置检查，**不能**作为 `_PRESET_RULES` 的一行——否则 `full-access` 或用户 `allow` 规则会悄悄遮蔽它。
4. **Hardline 范围。** 如果采纳 hardline 地板，只装**不可恢复**操作。可恢复但代价高的命令（`git reset --hard`、`pip install`、`chmod -R 777 /tmp/x`）留在普通 preset 规则。
5. **具体漏掉的 bug。** `agentao/permissions.py:334` 在 `try/except (IOError, json.JSONDecodeError)` 里做 `data.get("rules", [])`。顶层非 dict 的 JSON 抛 `AttributeError`，**没被捕获**。

**Round 3（针对本文档 rev 1）：**

6. **模块形态。** `agentao/permissions.py` 是 21 KB 单文件，不是 package。把新逻辑**内联**进去；本次不做 package 拆分。
7. **`copy_context()` 放置错误。** 捕获要在**父**线程，`executor.submit()` 之前。在 worker 入口里调 `copy_context()` 复制的是 worker 自己的空上下文。配套测试必须包含 isolation 断言。
8. **事件分类不是用户 vs 策略 的判别器。** `PermissionDecisionEvent` 发在 Phase 2 确认**之前**。用户拒绝走 `ToolLifecycleEvent(cancelled)`。`reason` 字段是**策略来源分类**，不是用户 vs 策略 的字段。
9. **（被 round 4 推翻。）** Round 3 说敏感写入需要自己的地板层。Round 4 反转了这一点——见下面修正 11。
10. **Regex 覆盖。** 任何敏感写入 regex 匹配器都必须配正向和负向测试矩阵，并显式声明覆盖盲区。

**Round 4（针对 Agentao 定位的反向评审）：**

11. **FLOOR_ASK Tier 2 是 overreach。** `~/.bashrc`、`~/.zshrc`、`~/.netrc` 是 installer、devops 脚本和 shell-config 工具的**合法写入目标**。一个"不能被 `*` 自动放行"的前置检查层会迫使每个跑这类工作负载的嵌入式宿主与框架对抗。这种强度的敏感写入保护应该在**preset 规则**里（mode-scoped、可被宿主覆盖），不是在地板里。Tier 2 在 rev 3 删除——见 §7。
12. **Hardline (Tier 1) 不能硬编码进 `decide_detail()`。** Hermes 可以硬编码它的地板，因为 Hermes 是策略权威——一个 CLI-first 应用。Agentao 是 embedded harness。硬编码"无论宿主配置如何，agent 永远不能做 X"跟"宿主决定策略"的 `agentao.host` 契约相矛盾。Hardline 因此是**可关闭层**：默认 ON（保护 CLI 用户和"没想清楚"的宿主免于 prompt 注入），自担责任的宿主可以显式 OFF。见 §5。
13. **`tests/test_permissions.py:162` 那条断言不是干净的 bug。** 在 `enable_hardline=False`（字面 full-access）下，`rm -rf /` *应该*返回 `ALLOW`——这就是 mode 字面意思。Rev 3 不删这条测试；而是把它拆成默认开启情形（DENY）和显式关闭情形（ALLOW），保留两份契约。
14. **正确性修复不需要策略立场。** `isinstance` 防御、MCP 错误分类、`copy_context()` 传播是纯正确性——不论 hardline 这个问题怎么回答它们都适用。它们作为独立 PR（PR 1）发车，跟 hardline 工作（PR 2）解耦。

## 3. 优先级排序

按**正确性 ↔ 策略**轴重组。正确性项不带任何策略立场，先发车。

```
P0  正确性（无策略立场）
    ─ permissions.py 顶层 isinstance(dict) 防御
    ─ MCP 错误分类
    ─ ToolRunner worker copy_context() 传播

P1  可选 hardline 层（opt-out，默认 ON）
    ─ PermissionEngine 上的 enable_hardline flag
    ─ decide_detail() 里的 hardline 前置检查
    ─ 测试修正（双契约）
    ─ PermissionDecisionEvent 上的策略来源 reason 分类

P2  便利性和工程卫生
    ─ Windows UTF-8 stdout/stderr 兜底
    ─ mask_secret 规范 helper
    ─ OSC 序列剥离（推迟到 docker/remote shell 落地）
```

## 4. P0 — 正确性修复

这三项是纯正确性，作为一个 PR（PR 1）发车。它们对"Agentao 是否该采纳 hardline 地板"不持立场。

### 4.1 `permissions.py` `isinstance(dict)` 防御

`agentao/permissions.py:334` 当前：

```python
data = json.load(f)
return data.get("rules", []), True   # data 是 list/string 时抛 AttributeError
```

外面的 `try/except (IOError, json.JSONDecodeError)` **不**捕获 `AttributeError`。顶层是 list、string 或 null 的合法 JSON 会让引擎初始化崩溃。

修复：

```python
data = json.load(f)
if not isinstance(data, dict):
    return [], False
return data.get("rules", []), True
```

单行。跟 `mcpServers` 配置加载的防御看齐。

### 4.2 MCP 错误分类

**现状：** `agentao/mcp/client.py:135-156` 第一轮任何异常都 retry 一次，然后原样把错误字符串透传。不区分"会话过期，请重连"、"鉴权 token 无效，别 retry"和"工具参数错了，别来回踢连接"。

**方案：** 引入私有 helper：

```python
_SESSION_EXPIRED_MARKERS = (
    "session expired",
    "session not found",
    "unknown session",
    "session terminated",
)

def _is_session_expired_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _SESSION_EXPIRED_MARKERS)
```

`call_tool()` 之后：
- 若 `_is_session_expired_error(e)`：清 session、重连、重试一次
- 若像鉴权错误（`401 / 403 / "unauthorized" / "forbidden"`）：**不**重试，直接上抛
- 否则：直接上抛错误，**不**重连

是逻辑改动，非更大重构。约 25 行 + 测试。

### 4.3 ToolRunner worker `copy_context()` 传播

**现状：** `agentao/runtime/tool_runner.py` Phase 3 派发到 8-worker `ThreadPoolExecutor`。Worker 不传播父线程的 `ContextVar` 状态。

**方案——父线程捕获，worker 调用。** `copy_context()` 快照的是**调用方**线程的上下文。所以捕获要在父（提交）线程，`executor.submit()` 之前；worker 调用捕获到的 `ctx.run`：

```python
import contextvars

# ToolRunner Phase 3 派发处 —— 父线程：
ctx = contextvars.copy_context()
future = self._executor.submit(ctx.run, self._run_one_tool, plan)
```

在 worker 入口里调 `copy_context()` 复制的是 worker 自己的空上下文——静默失败。

**测试验收——必须两条断言，不是一条：**

1. **正向传播。** 父线程 `cv.set("X")`，派发一个 no-op 工具，worker 读到 `cv.get() == "X"`。
2. **隔离。** 父线程 `cv.set("X")`，worker 调 `cv.set("Y")`，worker 返回后父线程仍读到 `cv.get() == "X"`。验证 worker 跑在**副本**上，不是共享引用。

单条正向传播测试在某些 GIL 排序下，即使实现错了也能通过，掩盖 bug。隔离断言才能抓到错误实现。

**优先级老实交代。** Agentao 当前没有 `ContextVar` 写入方依赖这一点。它是给未来宿主（注入 OTel span 上下文、日志 session id、tracing baggage）的结构防线。现在发车是因为成本低、测试也是个有用的回归 guard，不是因为有当前 bug。

## 5. P1 — 可选 hardline 层

PR 1 落地后作为 PR 2 发车。跟正确性独立。

### 5.1 Hardline as opt-out

**这一层必须遵守的原则。** Agentao 是 embedded harness。宿主决定策略。一个硬编码"无论宿主配置如何，agent 永远不能做 X"的库跟这条原则相矛盾。Hardline 地板因此是：

- **默认 ON。** CLI 用户、或者没认真想清楚威胁建模的宿主，能被保护免于 prompt 注入擦盘。安全默认。
- **可显式 opt-out。** 自担策略责任的宿主——通常因为它把 Agentao 装在容器沙盒里，或者它合法需要完整系统访问——可以用 `enable_hardline=False` 关闭地板。字面 `full-access` 这时就是字面的全部 allow，跟 mode 承诺一致。

**API 形状：**

```python
class PermissionEngine:
    def __init__(
        self,
        mode: PermissionMode,
        *,
        enable_hardline: bool = True,
        ...
    ):
        ...

    def decide_detail(self, tool_name, tool_args):
        if self._enable_hardline:
            hit = _hardline_check(tool_name, tool_args)
            if hit is not None:
                return hit
        # ... 已有的 mode/preset/user-rule 路由
```

**放置位置：** 把 `_HARDLINE_PATTERNS` 和 `_hardline_check()` **内联**到 `agentao/permissions.py` 顶部。本 PR **不**做 `permissions.py` → package 的迁移。

**Pattern 集合** —— `hermes-agent tools/approval.py:HARDLINE_PATTERNS` 的 12 条，范围严格限制在**不可恢复**操作：

- `rm -rf` 针对 `/`、系统根目录（`/etc /usr /var /boot /bin /sbin /lib /home /root`）、`~` / `$HOME`
- `mkfs[.*]`
- `dd ... of=/dev/(sd|nvme|hd|mmcblk|vd|xvd)…` 以及 `> /dev/(sd|nvme|…)`
- Fork bomb `:(){ :|:& };:`
- `kill -1`、`kill -9 -1`
- 命令位的 `shutdown / reboot / halt / poweroff`
- `init [06]`、`telinit [06]`
- `systemctl (poweroff|reboot|halt|kexec)`

每条 pattern 用 `_CMDPOS` 锚定（行首、`;` `&&` `||` `` ` `` `$(` 之后、`sudo` `env` wrapper 之后），避免 `echo "reboot logs"` 误命中。

`git reset --hard`、`pip install`、`chmod -R 777`、`curl | sh` 故意不进 hardline——它们可恢复但代价高，归普通 `DANGEROUS` / preset 规则，让宿主有选择权放行。

**编译：** `_HARDLINE_PATTERNS_COMPILED = [(re.compile(p, re.IGNORECASE), desc) for p, desc in _HARDLINE_PATTERNS]` 模块 import 时编译。

**返回结果：** 在 `enable_hardline=True` 时命中 pattern，返回 `PermissionDecisionDetail(decision=DENY, reason=f"hardline:{description}")`。`reason` 字段是用于审计与调试的**策略来源分类**——`hardline:*`、`mode-preset:*`、`user-rule:*`——不是用户 vs 策略 的判别器（用户拒绝在 `ToolLifecycleEvent(cancelled)` 上——见 §5.3）。

### 5.2 测试修正（双契约）

`tests/test_permissions.py:162` 当前断言 `full-access` 下 `rm -rf /` 返回 `ALLOW`。Rev 3 把它保留为"显式 opt-out"契约；新增一条测试断言"默认安全"契约。

```python
def test_full_access_default_blocks_hardline_commands():
    """默认构造 hardline ON——保护 CLI 用户和未配置宿主。"""
    e = PermissionEngine(mode=PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf /",
        "rm -rf /home/*",
        "shutdown -h now",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
        "kill -9 -1",
        "systemctl poweroff",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, cmd

def test_full_access_with_hardline_off_honors_literal_contract():
    """显式 opt-out 给嵌入式宿主保留 full-access 字面语义。"""
    e = PermissionEngine(mode=PermissionMode.FULL_ACCESS, enable_hardline=False)
    assert e.decide("run_shell_command", {"command": "rm -rf /"}) == PermissionDecision.ALLOW

def test_reason_uses_policy_source_prefix():
    """reason 字段是策略来源分类，不是用户行为判别器。"""
    e = PermissionEngine(mode=PermissionMode.FULL_ACCESS)
    detail = e.decide_detail("run_shell_command", {"command": "rm -rf /"})
    assert detail.decision == PermissionDecision.DENY
    assert detail.reason.startswith("hardline:")

def test_workspace_write_unaffected_by_hardline_flag():
    """Hardline 在其他层之下——workspace-write 已有的 deny 规则照常生效。"""
    e = PermissionEngine(mode=PermissionMode.WORKSPACE_WRITE, enable_hardline=False)
    # mode 已有的 "rm -rf|sudo|mkfs|dd if=" deny 规则仍命中。
    assert e.decide("run_shell_command", {"command": "rm -rf /tmp/x"}) == PermissionDecision.DENY
```

### 5.3 `PermissionDecisionEvent` 上的策略来源 `reason` 分类

`PermissionDecisionEvent` 已存在。审计策略来源是否发出可区分的 `reason` 前缀：

- `hardline:<description>` — opt-out 地板拒绝
- `mode-preset:<rule_id>` — preset 规则命中
- `user-rule:<rule_id>` — 用户 JSON 规则命中

用户主动拒绝**不在**这个分类里。它通过另一类事件 `ToolLifecycleEvent(cancelled)` 观察，因为当前事件顺序是：`PermissionDecisionEvent(ASK) → prompt → ToolLifecycleEvent(cancelled|started)`。需要"用户拒绝 vs 策略拒绝"UI 区分的宿主，应订阅两类事件，不要解析 `reason`。不引入新事件类型。

这项工作随 PR 2（hardline）一起发车，让字段可选值从开始发出 `hardline:*` 那一刻起就稳定——做成后续 PR 意味着字段可选值在发布后才补全，对宿主而言是破坏性的事件形状变更。

## 6. P2 — 便利性和工程卫生

### 6.1 Windows UTF-8

把 `hermes_cli/__init__.py::_ensure_utf8()` 原样移植到 `agentao/__init__.py`。33 行，门控 `sys.platform == "win32"`，POSIX 无影响。

### 6.2 `mask_secret()` helper

新建 `agentao/redact.py::mask_secret(value, head=4, tail=4, floor=12, placeholder="(not set)")`。在 P0/P1 过程中遇到的零散 secret-masking 调用点迁移过来。

### 6.3 OSC 序列剥离（推迟）

推迟到 shell 化的文件读路径落地（docker / remote 执行器）后再做。今天 `read_file` 用 `Path.read_text()`，OSC 泄漏到不了它。届时移植 Hermes 的 `_strip_terminal_fence_leaks`。

## 7. 已考虑并拒绝：Tier 2 FLOOR_ASK

写这一节是为了让下一个评审者不要再提一遍、再走一遍同一段路。

**提案（rev 2）：** 第二个地板层——`~/.bashrc`、`~/.zshrc`、`~/.profile`、`~/.bash_profile`、`~/.zprofile`、`~/.netrc`、`~/.pgpass`、`~/.npmrc`、`~/.pypirc`——永远至少 ASK，永远不能被 `*` allow 自动放行。

**为什么它有吸引力。** 它堵上 shell 重定向（`echo X >> ~/.bashrc`）绕过 `write_file` 的 `PathPolicy` 这个缺口。Hermes 的 `69dd0f7cf` 正是覆盖这个 case。我们没经思考就借鉴了。

**为什么它对 Agentao 是错的。**

1. **`~/.bashrc` 是合法的写入目标。** Homebrew、pyenv、nvm、rustup 都写 shell rc 文件。碰 `.zshrc` 的 devops 脚本是常态。一个跑这类工作负载的嵌入式宿主在每次操作上都会跟框架对抗。
2. **"不能被 `*` 自动放行"违反 host-overrides-defaults。** Agentao 整个设计就是宿主组合策略。一个说"任何宿主都不能关掉这个"的地板，正是 `agentao.host` 想要避免的东西。
3. **`full-access` 变成谎言。** 一个宣称"全部 allow"但偷偷在常见目标上 ASK 的 mode，比两种替代方案都糟——它给宿主制造意外。
4. **真正的担忧（shell 绕过 PathPolicy）由 preset 规则解决。** 一条在 shell-RC 写入时 ASK 的 `workspace-write` preset 规则形状刚好对：mode-scoped（用户选了 workspace-write 才激活）、host-overridable（宿主可以替换规则）、显式（宿主读引擎 active rules 时能看到）。

**改成发车什么。** 一条 `workspace-write` preset 规则，在 shell 重定向 / `tee` / `cp` / `mv` / `sed -i` 写入 shell-RC 和凭证文件时 ASK。这是给 `_PRESET_RULES` 加一条的 PR-3-或-之后改动，配 rev 2 §4.2 的标准 regex 测试矩阵。它**不是 P0**、**不是 P1**、**也不是地板**。它就是一条普通规则，宿主想替换就替换。

**锁定拒绝的哨兵测试。** PR 2 包含：

```python
def test_no_floor_ask_tier_exists():
    """rev 3 §7：没有 Tier 2 地板。~/.bashrc 写入走普通 mode 规则。"""
    e = PermissionEngine(mode=PermissionMode.FULL_ACCESS, enable_hardline=False)
    # 字面 full-access + 关闭 hardline 必须放行写入——没有隐藏的地板。
    assert e.decide(
        "run_shell_command",
        {"command": "echo X >> ~/.bashrc"},
    ) == PermissionDecision.ALLOW
```

如果未来某个改动重新引入 FLOOR_ASK 层，这个测试会失败——迫使该改动要么自证合理，要么撤销。

## 8. 不在范围内

- 单独的 "YOLO" 模式。Agentao 的 `enable_hardline=False` + `full-access` 就是等价物。
- 每会话 sudo 缓存（Hermes `de03a332f`）。Agentao 的 terminal 工具目前不提示 sudo。等交互式 sudo 落地再回来看。
- 插件 pre/post 审批钩子机制。Agentao 选择 host event；不要引入并行插件机制。
- 把 `agentao/permissions.py` 迁成 package。Hardline 内联进去。拆分是另一个独立重构。
- 任何未来重新引入 "Tier 2 FLOOR_ASK" 地板——见 §7。换成普通 preset 规则可以；恢复无条件地板不行。

## 9. PR 分批

```
PR 1 (P0)  permissions 正确性          ✓ 2026-05-03 已合入
                                          ─ isinstance(dict) 防御、
                                          MCP 错误分类、
                                          ToolRunner copy_context() 传播
                                          + 传播/隔离测试。
                                          零策略立场。先发车。

PR 2 (P1)  可选 hardline 层            ✓ 2026-05-03 已合入
                                          ─ enable_hardline flag、
                                          _hardline_check() 前置检查
                                          (实际落在 agentao/permissions_hardline.py，
                                          permissions.py 只导入入口；规则评估
                                          按 §5.1 仍内联)、
                                          双契约测试（默认开启 DENY +
                                          opt-out ALLOW + §7 哨兵测试）、
                                          PermissionDecisionEvent
                                          策略来源 reason 分类。

PR 3 (P2)  windows utf-8 兜底         ✓ 2026-05-04 已合入
                                          ─ agentao/__init__.py::_ensure_utf8()
                                          强制 CP_UTF8 + 重配置
                                          stdin/stdout/stderr；
                                          sys.platform == "win32" 才走；
                                          POSIX 契约测试在
                                          tests/test_init_utf8.py。

PR 4 (P2)  mask_secret + redact       ✓ 2026-05-04 已合入
                                          ─ agentao/redact.py::mask_secret；
                                          P0/P1 阶段排查未发现需要迁移的
                                          ad-hoc 调用点。helper 是面向
                                          PermissionDecisionEvent 投影
                                          + 未来 /provider UI 的前置铺垫。

PR 5 (P3)  shell 敏感写入 preset      ✓ 2026-05-04 已合入
                                          ─ _SHELL_SENSITIVE_WRITE_RE +
                                          workspace-write preset 规则。
                                          ASK，非 DENY。仅 mode-scoped：
                                          full-access 故意不带这条规则
                                          (字面 full-access 原则，§5.1)。
                                          覆盖盲区（间接赋值、字面展开
                                          路径）作为 §10 的 bashlex
                                          follow-up 跟踪。

```

PR 1 和 PR 2 故意解耦。PR 1 是无争议的正确性，不需要任何人在策略上达成一致就发车。PR 2 是策略选择，但双契约测试把唯一的歧义点解决之后，第二天就跟着发车。PRs 3–5 在第二天作为便利批合入。

Rev 2 方案把 hardline 捆进 PR 1 是因为两者都被打了 P0 标签。Round 4 把它们拆开：正确性不带策略立场，hardline 是宿主可以 opt out 的策略选择。保留独立 PR 让这个区分在 git log 里也保留。

## 10. 开放追踪项与发车后续

下面这些问题来自 rev 3 发车前的清单，标注了已落地内容与剩余工作。

- **`enable_hardline` 给终端用户怎么配？** **按暂定答案敲定（不通过 `.agentao/permissions.json`）。** PR 2 仅暴露构造函数参数；没有加 `--no-hardline` CLI flag —— CLI 实际用例没有迫切需求，凭空加会跟"嵌入式 harness、宿主决定策略"的原则相左。CLI 用户真的要关，就走嵌入入口设 `enable_hardline=False`；如果将来出现真实工作流缺口，再重开此问题。
- **`sudo` 下的 `HOME` 解析。** **保留为开放项。** Hardline pattern 仍只在语法层匹配 `~ / $HOME / ${HOME}`。`sudo rm -rf $HOME` 在特权进程里 `$HOME` 会被重新求值为 root 的家目录，但 regex 不论求值结果都会捕获字面 token —— 现实风险反而是反向案例：`sudo` 用的家目录变量名不同。等到收到真实 bug 报告再修；不做臆测性补强。
- **容器后端绕过。** **按暂定答案敲定（显式宿主 opt-out）。** Agentao 不做容器自检；做沙箱的宿主自己设 `enable_hardline=False`。要重新讨论，必须有具体宿主后端能证明运行时检测有正向收益。
- **Reason 受众。** **按暂定答案敲定（面向运营）。** `reason="hardline:recursive delete of root filesystem"` 当作审计 / 调试文案。给用户看的措辞继续由宿主负责 —— ACP 的 `request_permission` 载荷照样把原始 reason 字符串往上抛，宿主自己决定怎么渲染。
- **PR 5 的 `bashlex` 演进。** **唯一遗留的开放追踪项。** PR 5 的 regex 命中常见形态（重定向、tee、cp/mv、sed -i）针对 `~`/`$HOME` 前缀的敏感文件。无法命中：
    1. shell 变量间接赋值：`dst=~/.bashrc; echo X > "$dst"`。
    2. 字面展开路径：`/Users/<u>/.bashrc`、`/home/<u>/.bashrc`。
    3. 进程替换包装：`tee >(cat > ~/.bashrc)`。
    用 `bashlex` 解析 —— 跟 hardline shell 安全扫描器处理 `rm -rf` 间接赋值的方案一样 —— 能解决 (1) 和 (3)。(2) 需要运行时知道用户家目录，会引入宿主耦合，先放一放，等具体攻击面浮现再补。
    不阻塞 —— workspace-write 对所有未在只读白名单的命令本来就 ASK，所以今天的 regex 层是"文档 + 未来防御"，并不是关键的拦截门。
