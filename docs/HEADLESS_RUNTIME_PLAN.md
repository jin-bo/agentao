# Agentao Headless Runtime Plan

## Goal

将 `agentao` 从“可被代码调用的 ACP embedding 能力”收敛为一个**逻辑上的 headless daemon-capable runtime**。

本阶段不做真正的 socket / TCP daemon，也不扩 ACP 协议；重点是把现有 `ACPManager` 路线产品化为一个稳定、可观测、可治理、可恢复的 headless runtime surface。

## Guiding Decisions

- 本阶段的目标是**收敛 surface**，不是“做一个新 daemon”。
- 继续以现有 `stdio + embedding` 路线为主，不引入新的 transport。
- 每个 server **单活跃 turn**。
- 并发提交**不排队**；第二个并发请求返回 `SERVER_BUSY`。
- 先服务一个真实样板消费者，而不是为抽象宿主设计。

## Sample Consumer

Week 1 必须落一个可运行的样板消费者文件：

- `examples/headless_worker.py`

这个样板消费者代表“项目内后台 worker / CI-style runner”，用于驱动整个计划的设计与验收。每周回归都要跑它，而不是基于抽象想象验收。

Week 1 结束时它必须进入 CI，作为 smoke job 运行；不是仅供人工演示的 demo。

样板消费者最小覆盖面：

- 初始化 `ACPManager`
- 执行一次非交互任务
- 查询 status snapshot
- 处理一次失败或取消路径

## Public Runtime Surface

Week 1 必须明确以下调用面的归属和支持级别：

- `prompt_once`
- `send_prompt`
- `send_prompt_nonblocking`

`send_prompt_nonblocking` 不能继续处于“代码里公开、计划里缺席”的状态。Week 1 必须二选一：

- 收编为正式支持的 public surface，并补文档、取消/完成语义
- 或显式标注为 internal / unstable，不建议 headless embedder 依赖

后续所有 per-call override 或 policy 扩展，只能作用于 **Week 1 已确认为 public 的入口**。如果 `send_prompt_nonblocking` 留在 internal，它不得被写进公开 contract。

## Status Snapshot Contract

Week 1 就落地最小 status snapshot，不只冻结 schema。

兼容策略：

- `ACPManager.get_status()` 直接收敛为 typed contract：`list[ServerStatus]`
- 不新增 `get_status_typed()`
- CLI 和其他调用方自行适配 `ServerStatus`，不再为 CLI 保留专门的 dict surface
- 这是一次有意识的 embedding surface 收敛；需要同步更新文档、developer-guide 和 changelog

最小字段：

- `server`
- `state`
- `pid`
- `has_active_turn`

Week 2 只能在此基础上加字段，不能改外层 shape。

扩展字段目标：

- `active_session_id`
- `last_error`
- `last_error_at`
- `inbox_pending`
- `interaction_pending`
- `config_warnings`

## Error / Status Semantics

状态语义采用以下约定：

- `state` 是一等判断信号
- `last_error` 保留最近一次错误
- `last_error_at` 提供错误时间
- 消费者应先看 `state`，再看 `last_error` / `last_error_at`
- `has_active_turn` 必须由 manager 的**活跃 turn 槽**派生，不能仅靠 `busy` / `waiting_for_user` 等 handle state 推断

旧配置格式的 deprecation 不能只打日志，必须进入状态面：

- `config_warnings: [...]`

## Policy Scope

本阶段只做最小 interaction policy，不做大而全治理系统。

范围限定：

- 只处理 non-interactive interaction policy
- 两层优先级：
  - server default
  - per-call override

per-call override 只作用于 Week 1 已确认为 public 的入口：

- `prompt_once`
- `send_prompt`
- `send_prompt_nonblocking` 仅在它被 Week 1 收编为 public surface 时才纳入

本阶段不纳入：

- timeout policy
- 多层 precedence
- 按工具家族拆复杂策略族

## Lifecycle Scope

Week 4 生命周期工作拆为两块，不混写：

1. `cancel/timeout` 后的确定性清理

- turn slot
- lock
- pending request
- active state

2. `client/process` 死亡后的恢复

- 区分可恢复和致命失败
- 可恢复时重建 client/session
- 致命时保留 failed state，等待显式 restart

## 4-Week Plan

### Week 1

- 产出 `examples/headless_worker.py`
- 接入 CI，把 `examples/headless_worker.py` 作为 smoke job 跑起来
- 冻结并实现最小 status snapshot
- 将 `get_status()` 直接改为 typed `ServerStatus` 返回，并同步修正文档与调用方
- 明确 `prompt_once` / `send_prompt` / `send_prompt_nonblocking` 的支持级别
- 写 headless runtime 文档初稿
- 加基线 smoke tests：
  - cancel 后可继续接单
  - 非交互拒绝后不污染后续 turn
  - timeout 后可恢复
  - session reuse 正常
- 文档中写死并发语义：单 server 单活跃 turn，第二个请求返回 `SERVER_BUSY`

### Week 2

- 扩展 status snapshot 与 diagnostics
- 增加 `last_error` / `last_error_at`
- 增加 `config_warnings`
- 补 readiness / logs / status 的稳定接口或统一输出
- 保持 Week 1 snapshot 形态不变

### Week 3

- 引入最小 interaction policy
- 支持 server default + per-call override（仅针对 Week 1 已确认为 public 的入口）
- 对旧字符串配置直接报配置错误：
  - `reject_all`
  - `accept_all`
- Week 3 一次性收口，不保留 legacy string config 兼容分支
- migration 信息进入错误消息与文档，不再通过 `config_warnings` 承担 legacy 兼容出口

### Week 4

- 实现 cancel/timeout 后的确定性清理
- 实现 client/process 死亡后的恢复策略
- 用样板消费者跑端到端回归
- 补 daemon-style regression tests

## Acceptance Criteria

- 样板消费者每周都可运行，并作为统一验收入口
- 样板消费者进入 CI，作为 smoke 入口而非仅人工示例
- 官方 headless runtime surface 完整且明确；`send_prompt_nonblocking` 的 public/internal 归属在 Week 1 被明确冻结
- status snapshot 在 Week 1 就可用，Week 2 只加字段不破形态
- `get_status()` 直接收敛为 typed `ServerStatus` contract，CLI、文档和调用方同步迁移
- non-interactive policy 只覆盖真实消费者需要的 interaction 场景
- 旧字符串配置不会被兼容保留，而是以显式配置错误失败，并附带迁移指引
- 单 server 单活跃 turn 语义在文档、实现、测试中一致
