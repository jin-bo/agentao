# Agentao Headless Runtime Issues

本文件把 `HEADLESS_RUNTIME_PLAN.md` 拆成可执行 issue backlog。默认按 Week 1-4 排序，单个 issue 尽量保持"一个明确产出 + 一组验收条件"。

每个 issue 带显式 `Dependencies`，用来避免认领顺序踩坑。developer-guide（中英双语）的修订按周随改动同步，每周最后一个 issue 专门负责 doc。

---

## Week 1

### Issue 1. Add a runnable headless sample consumer

**Goal**

新增一个最小、可运行的样板消费者文件，作为后续每周回归入口。

**Dependencies**

- Issue 2（知道调哪个入口）
- Issue 3（知道读哪个 snapshot）

**Deliverable**

- `examples/headless_worker.py`

**Requirements**

- 使用 `ACPManager`
- 覆盖一次非交互调用
- 输出一次 status snapshot
- 演示错误路径和取消路径各一次（不是"或"）
- 接入 CI 作为 smoke job，失败即红；不是 demo

**Acceptance**

- 可从仓库根目录直接运行
- 不依赖内部私有模块
- CI 中作为 smoke 任务运行，后续任何 PR 破坏样板消费者都会被挡
- 后续 issue 可把它作为回归样板复用

### Issue 2. Decide the public headless runtime surface

**Goal**

明确 `prompt_once`、`send_prompt`、`send_prompt_nonblocking` 的产品归属。

**Dependencies**

- 无

**Requirements**

- 在文档中明确三者用途
- 对 `send_prompt_nonblocking` 做二选一决策：
  - 正式 public
  - internal / unstable
- 如保留为 public，补足完成/取消语义说明

**Acceptance**

- 外部使用者不读实现也知道该用哪个入口
- 文档与导出面一致
- `get_status()` 的 typed contract 已在 surface 文档中明确写出

### Issue 3. Ship the minimum status snapshot v1

**Goal**

Week 1 就落地最小状态快照，而不是只写 schema。

**Dependencies**

- Issue 2（若 nonblocking 升为 public，snapshot 可能需要新增 `has_pending_nonblocking` 等字段）

**Current state**

`ACPManager.get_status() -> List[Dict[str, Any]]` 已存在（`manager.py:1631`），返回 CLI-friendly 字典列表，字段包括 `name` / `state` / `pid` / `last_error` / `last_activity` / `inbox_pending` / `interactions_pending` / `description` / `stderr_lines`。`docs/features/acp-embedding.md` 也已经把 dict 列表写成了 embedding contract。

本 issue 的目标是**把 `get_status()` 一次性收敛为类型化 contract**，不再保留双轨 status surface。考虑到当前真实 embedders 仍少，这个变更现在做的迁移成本最低。

**API signature（冻结）**

```python
@dataclass(frozen=True)
class ServerStatus:
    server: str              # 对齐 Week 2 字段名；内部从现有 "name" 迁移
    state: str               # state enum 的 .value
    pid: int | None
    has_active_turn: bool    # 新增；由 manager 的活跃 turn 槽派生

ACPManager.get_status() -> list[ServerStatus]
```

**Minimum fields（Week 1 必须稳定且类型化）**

- `server`
- `state`
- `pid`
- `has_active_turn`

**Name-vs-server decision**

当前字典用 `"name"`，Week 2 Issue 7 要加 `"server"`——二者是同一语义。**Week 1 决定：统一用 `server`**。字段 `name` 从 schema 中移除；内部实现可保留 alias 至 CLI 迁移完成，但样板消费者必须读 `server`。

**Requirements**

- 引入 `ServerStatus` 数据类，并将 `get_status()` 直接改为返回 `list[ServerStatus]`
- 不新增 `get_status_typed()` 之类的双轨 API
- CLI、样板消费者、文档和测试统一迁移到 typed contract
- shape 冻结：Week 2 只能给 dataclass 加字段，不改 `get_status()` 的已有字段语义
- `has_active_turn` 必须由 manager 的**活跃 turn 槽**派生（例如 `_active_turns`），不能仅靠 `busy` / `waiting_for_user` 之类的 handle state 推断
- 在 changelog 和 migration 文档中明确这是一次有意识的 API 收敛，而不是静默变更

**Acceptance**

- `get_status()` 返回 `list[ServerStatus]`，4 个核心字段类型正确且非 None（`pid` 除外）
- CLI 和样板消费者完成迁移，无残留 dict 依赖
- 文档、developer-guide、changelog 对新返回类型表述一致
- `has_active_turn=True` 在 in-flight interaction 阶段依然成立，不会因 handle 进入 `WAITING_FOR_USER` 而误报空闲

### Issue 4. Add baseline smoke tests for headless behavior

**Goal**

在进入 Week 2-4 改造前，用测试 pin 住现有行为。

**Dependencies**

- Issue 3（需要读 snapshot 验证状态）
- Issue 5（并发合约与 `SERVER_BUSY` 复用定位）

**Required scenarios**

- cancel 后可继续接单
- 非交互拒绝后不污染后续 turn
- timeout 后可恢复
- session reuse 正常
- 单 server 并发提交返回 `AcpErrorCode.SERVER_BUSY`

**Acceptance**

- 测试可稳定复现当前行为
- 后续迭代可直接作为回归护栏

### Issue 5. Document and pin the concurrency contract

**Goal**

把并发语义前置写死。**注意：`AcpErrorCode.SERVER_BUSY` 已存在**（`agentao/acp_client/client.py:54`），`prompt_once` / `send_prompt` / `send_prompt_nonblocking` 三个入口在 busy 时已抛此错误（`manager.py:962`、`client.py:681`、`client.py:768`）。本 issue 的目标是**文档化并用测试 pin 住现有行为**，不是新建错误类型。

**Dependencies**

- 无

**Required contract**

- 每个 server 单活跃 turn
- 不排队
- 第二个并发请求返回 `AcpErrorCode.SERVER_BUSY`（复用现有错误码）

**Acceptance**

- 文档、测试、实现表述一致
- 样板消费者和 smoke tests 按此合约工作

### Issue 6. Publish Week 1 headless runtime doc + developer-guide surface revisions

**Goal**

发一份 operator-facing 的 headless runtime 文档，并同步更新 `developer-guide` 中已涉及 ACP surface 的页面，使代码、developer-guide、headless 文档三者一致。

**Dependencies**

- Issue 2、3、5（三者决定了要写什么）

**Deliverables**

- 新文档：`docs/features/headless-runtime.md`
  - 样板消费者用法（链接 `examples/headless_worker.py`）
  - 三个入口（`prompt_once` / `send_prompt` / `send_prompt_nonblocking`）的产品归属
  - 并发合约
  - status snapshot v1 字段说明
- developer-guide 同步修订（中英双语）：
  - `developer-guide/{en,zh}/part-3/4-reverse-acp-call.md` — surface 更新，`send_prompt_nonblocking` 定位
  - `developer-guide/{en,zh}/part-1/3-integration-modes.md` — 若 headless 视为独立集成模式，新增一节
  - `developer-guide/{en,zh}/appendix/a-api-reference.md` — surface 与 snapshot 入口
  - `developer-guide/{en,zh}/appendix/g-glossary.md` — "Headless runtime" 术语
  - `developer-guide/{en,zh}/appendix/d-error-codes.md` — `SERVER_BUSY` 的 headless 语义说明
  - `CHANGELOG.md` — 记录 `get_status()` 收敛为 typed contract 的 breaking change

**Acceptance**

- 中英双语同步
- 与 Issue 2 的决策一致
- 样板消费者在 developer-guide 入口页有可达链接
- `get_status()` 返回类型变化在 user-facing docs 和 changelog 中都可见

---

## Week 2

### Issue 7. Extend status snapshot without breaking shape

**Goal**

在 Week 1 最小 snapshot 基础上扩展诊断字段。

**Dependencies**

- Issue 3（基础 shape）

**Add fields**（均加到 `ServerStatus` dataclass 上）

- `active_session_id: str | None`
- `last_error: str | None` —— 保留最近一次错误的人类可读描述
- `last_error_at: datetime | None` —— tzinfo=UTC；在错误**存入 manager 状态**的时刻赋值，不是 raise 时刻
- `inbox_pending: int`
- `interaction_pending: int` —— 对齐 Issue 7 单数命名；现有 dict 中的 `interactions_pending` 作为序列化 alias 保留一个版本周期
- `config_warnings: list[str]` —— Issue 12 的 deprecation 出口

**Not added**

- `server` 已由 Issue 3 冻结为核心字段，无需重复列入

**Acceptance**

- `ServerStatus` 方法签名不变，仅新增字段
- 样板消费者可读取扩展字段
- `last_error_at` 时间来源语义在测试中被断言（存入时刻而非 raise 时刻）

### Issue 8. Add readiness and diagnostics surfaces

**Goal**

让 headless 宿主不必直接读 stderr 原文也能判断系统可用性。

**Dependencies**

- Issue 7

**Deliverables**

- readiness 判断接口
- logs/status 的稳定输出
- stderr tail 访问接口整理

**Acceptance**

- 宿主可以区分"ready / busy / failed"
- 失败时能拿到最近诊断信息

### Issue 9. Define state-vs-error contract

**Goal**

明确 `state`、`last_error`、`last_error_at` 的消费语义。

**Dependencies**

- Issue 7

**Requirements**

- 文档写明消费顺序：**先看 `state`，再看 `last_error` / `last_error_at`**
- `last_error` 保留最近一次错误描述；成功 turn 不清空、由新错误覆盖或显式 reset 清空
- `last_error_at` 格式：`datetime` 对象（`tzinfo=timezone.utc`）；**赋值时机 = 错误被存入 manager 状态的那一刻**，而不是 raise 时刻。测试要断言这一点
- 文档建议消费者用 `last_error_at` 判断陈旧度（例如"超过 N 秒前的错误配上 `state=ready` 视为历史信息，不阻塞调度"）

**Acceptance**

- 样板消费者和测试按统一合约使用
- 时间语义可被测试复现（注入时钟或使用 `datetime` 比较）

### Issue 10. Developer-guide revisions for observability surface

**Goal**

把 Week 2 新增的 snapshot 字段、readiness、state-vs-error 合约同步到 developer-guide。

**Dependencies**

- Issue 7、8、9

**Deliverables（中英双语）**

- `developer-guide/{en,zh}/appendix/a-api-reference.md` — snapshot 完整字段表、readiness 接口
- `developer-guide/{en,zh}/appendix/d-error-codes.md` — state-vs-error 合约
- `developer-guide/{en,zh}/appendix/f-faq.md` — "如何判断 server 是否可用 / 为什么失败"
- `docs/features/headless-runtime.md` — 同步更新 snapshot v2

**Acceptance**

- 中英双语同步
- 无遗漏 Week 2 变动

---

## Week 3

### Issue 11. Introduce a minimal interaction policy model

**Goal**

把 non-interactive 行为从二元字符串升级为最小策略模型，但不做大跃进。

**Dependencies**

- Issue 2（只有当 `send_prompt_nonblocking` 在 Week 1 被定为 public 时，才允许在它的函数签名上新增 kwarg）

**Scope**

- 仅 interaction policy
- 仅两层优先级：
  - server default
  - per-call override

**Override mechanism（冻结）**

- 已确定为 public 的入口必须新增同名 kwarg。
- `prompt_once` / `send_prompt` 必定新增：

  ```python
  interaction_policy: InteractionPolicy | Literal["reject_all", "accept_all"] | None = None
  ```

- `None` = 回退到 server default（`.agentao/acp.json` 中的 `nonInteractivePolicy`）
- 如果 Week 1 决定 `send_prompt_nonblocking` 也是 public，则它必须使用**同名同语义**的 `interaction_policy=` kwarg；如果它保持 internal，本 issue **不得**修改其公开签名，也不得在文档中把它当正式 surface 描述
- 明确**不用** `call_options` / `CallOptions` 包装对象：只有一个维度要覆盖时不值得引入；待第二个维度（timeout 等）出现再迁移

**Out of scope**

- timeout policy
- 多层 precedence
- 按工具家族拆策略
- `CallOptions` 聚合对象

**Acceptance**

- 样板消费者可通过 server default **或** per-call `interaction_policy=` 改行为
- 所有 public 入口的 kwarg 签名一致；internal 入口不被错误写入 public contract
- 不引入额外无用抽象

### Issue 12. Preserve backward compatibility for legacy config

**Goal**

升级策略模型时，不打断现有 `.agentao/acp.json`。

**Dependencies**

- Issue 7（写入 `config_warnings`）
- Issue 11（新模型本体）

**Requirements**

- 对旧格式：`reject_all` / `accept_all`（string 形态的 `nonInteractivePolicy`）直接报配置错误
- 错误发生在配置加载阶段，不延迟到 prompt 执行期
- 错误消息必须包含明确迁移指引，并指向 migration 文档
- 不保留旧格式到新模型的静默映射或隐式兼容
- migration 文案在配置错误、Issue 14 的 guide/migration、CHANGELOG 中保持一致

**Acceptance**

- legacy string config 在本轮直接失效，并以显式配置错误暴露
- 调用方无需等到 runtime 执行阶段才发现配置问题
- 文档、migration note、CHANGELOG 对错误行为和迁移方式表述一致

### Issue 13. Add tests for policy override behavior

**Goal**

锁住最小策略模型的行为边界。

**Dependencies**

- Issue 11、12

**Required scenarios**

- server default 生效
- per-call override 可覆盖 server default
- legacy string config 在配置加载阶段报错
- 错误消息包含迁移指引

**Acceptance**

- 不依赖日志文件验证 migration 行为

### Issue 14. Developer-guide revisions for policy + migration

**Goal**

把 interaction policy 升级和 legacy 兼容路径落到 developer-guide。

**Dependencies**

- Issue 11、12、13

**Deliverables（中英双语）**

- `developer-guide/{en,zh}/part-3/4-reverse-acp-call.md` — 新 interaction policy 描述、per-call override 示例
- `developer-guide/{en,zh}/appendix/b-config-keys.md` — 新配置键 + 旧字符串格式已移除说明
- `developer-guide/{en,zh}/appendix/e-migration.md` — legacy 字符串 → 新模型的迁移示例
- `developer-guide/{en,zh}/appendix/f-faq.md` — "为什么我的 `reject_all` 配置现在报错？"
- `docs/features/headless-runtime.md` — 同步更新

**Acceptance**

- 中英双语同步
- 迁移示例可直接复用
- 配置错误文案在 developer-guide、migration 文档和实现中一致

---

## Week 4

### Issue 15. Deterministic cleanup after cancel/timeout

**Goal**

确保同进程内 turn 失败后状态机能稳定复位。

**Dependencies**

- Issue 7（状态字段作为验证面）

**Scope**

- turn slot cleanup
- lock release
- pending request cleanup
- active state reset

**Acceptance**

- cancel/timeout 后下一个任务可正常进入
- 不残留 busy/locked 状态

### Issue 16. Recovery after client/process death

**Goal**

明确并实现 `client/process` 死亡后的恢复路径。

**Dependencies**

- Issue 15

**Requirements**

- 区分可恢复 vs 致命失败
- 可恢复时重建 client/session
- 致命时保留 failed state，等待显式 restart

**Recoverable-vs-fatal decision matrix（Deliverable 的一部分）**

| Trigger | Classification | Action |
|---|---|---|
| idle 状态下 process 正常退出（exit code 0） | recoverable | 下次调用时懒重启；不记 `last_error` |
| idle 状态下 process 非零退出 | recoverable with retry cap | 连续 N 次（建议 N=3）内按 recoverable 处理；超过 cap 转 fatal |
| active turn 期间 process 死亡 | recoverable | 当前 turn 失败并写 `last_error`；下次调用重建 client/session |
| stdio pipe 损坏 / EOF | recoverable | 重建 client；不重启进程（若进程仍在） |
| 进程被 OOM 或 SIGKILL（exit code 137 / 被信号终止） | fatal | 不自动复活，避免雪崩；标 failed，等显式 restart |
| 重启后 handshake 连续失败 | fatal | 视为配置/环境问题而非瞬态；标 failed，等显式 restart |
| 样板消费者显式 cancel 导致的终止 | 非此 issue 处理 | 由 Issue 15 的确定性清理负责 |

- 上述 retry cap `N` 的具体值在实现时可调，但必须有常量 + 配置入口，不能硬编码
- 每一行都必须在 Issue 17 的回归套件里有对应 scenario

**Acceptance**

- 样板消费者能区分恢复性故障和致命故障（通过 `state` + `last_error` 组合判定）
- 恢复逻辑不误清理致命态（fatal 标记在显式 restart 前持久）
- 决策矩阵与实现、回归套件三者一致

### Issue 17. Build a daemon-style regression suite

**Goal**

把前 3 周改动纳入长期回归。

**Dependencies**

- Issue 1-16 全部

**Required scenarios**

- 长时间复用 session
- interaction reject 后继续接单
- cancel 后继续接单
- timeout 后恢复
- process/client 死亡后的恢复或失败暴露

**Acceptance**

- 样板消费者可贯穿回归路径
- 生命周期改动不回退 Week 1-3 contract

### Issue 18. Developer-guide revisions for lifecycle + final integration doc

**Goal**

在 Week 4 实现收尾时完成 developer-guide 的 lifecycle 章节与 headless runtime 文档终版。

**Dependencies**

- Issue 15、16、17

**Deliverables（中英双语）**

- `developer-guide/{en,zh}/part-3/4-reverse-acp-call.md` — 生命周期 / 恢复小节
- `developer-guide/{en,zh}/part-7/5-batch-scheduler.md` — 用新生命周期合约更新 batch scheduler 示例
- `developer-guide/{en,zh}/appendix/f-faq.md` — "cancel / timeout / 崩溃后如何恢复"
- `docs/features/headless-runtime.md` — 终版（含 lifecycle、regression suite 入口）

**Acceptance**

- 中英双语同步
- developer-guide 与 headless runtime 文档一致
- 完整闭环：样板消费者 → API surface → observability → policy → lifecycle

---

## Cross-Cutting Notes

- 本阶段不做 `LocalSocketTransport`
- 本阶段不做 TCP daemon
- 本阶段不扩 ACP 协议
- 所有设计都以 `examples/headless_worker.py` 为第一消费者验证，不为抽象宿主预支复杂度
- developer-guide 修订一律中英双语同步（`developer-guide/en/**` + `developer-guide/zh/**`）
- `docs/features/headless-runtime.md` 随每周变动渐进更新，不一次写完
- 本文件位置 `docs/HEADLESS_RUNTIME_ISSUES.md` 保留至评审结束；签字后建议按 `docs/implementation/acp-client-project-servers/issues/` 先例展开为目录 + 每 issue 一文件 + `README.md` 索引
