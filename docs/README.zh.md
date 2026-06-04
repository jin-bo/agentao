# Agentao 文档中心

这里是 Agentao 的文档入口页。

如果你要了解产品概览、安装路径和第一次运行体验，优先看根目录的 [README.zh.md](../README.zh.md)。如果你已经确定要深入 `docs/` 中的某类文档，这一页可以帮你更快找到入口。

## 目录结构

```
docs/
  start/        首次安装、命令速查、演示
  guides/       任务导向的使用指南 + 各功能专题文档
  reference/    权威参考：配置、宿主 API、replay 策略
  design/       架构决策、宿主面合约记录、跨仓评审
  schema/       已 check-in 的 JSON Schema 快照（与代码耦合，测试强制相等）
  releases/     版本发布说明
  migration/    版本升级指南
  history/      已被取代的计划、开发笔记、旧更新记录（仅供追溯）
```

## 从这里开始

> **不确定该用哪一面？** 先看 [design/embedding-vs-acp.zh.md](design/embedding-vs-acp.zh.md)——它把 in-process embedding、ACP server、ACP client 以及 ACP schema surface 区分清楚（这四者可以任意组合）。

> **你是一个编码 agent**（Claude Code、Codex…）要把 Agentao 嵌入到另一个项目？先看精炼版手册 [guides/embed-for-agents.md](guides/embed-for-agents.md)，再顺着它进入 [guides/embedding.md](guides/embedding.md) 和 [reference/host-api.md](reference/host-api.md)。

按目标选择阅读路径：

- 第一次安装和启动：[start/quickstart.md](start/quickstart.md)
- 日常命令速查：[start/quick-reference.md](start/quick-reference.md)
- ACP 服务端模式：[guides/acp.md](guides/acp.md)
- 配置参考（每个文件、环境变量、公开 API）：[reference/configuration.md](reference/configuration.md)
- 日志与调试：[guides/logging.md](guides/logging.md)
- 模型和 provider 切换：[guides/model-switching.md](guides/model-switching.md)
- 技能系统使用：[guides/skills.md](guides/skills.md)

## start —— 入门

| 文档 | 适用场景 |
|------|----------|
| [start/quickstart.md](start/quickstart.md) | 你要最快跑通安装和首次启动 |
| [start/quick-reference.md](start/quick-reference.md) | 你想快速查常用命令和操作 |
| [start/demo.md](start/demo.md) | 你要走一遍演示或 Demo 流程 |

## guides —— 使用指南

任务导向的使用指南与各功能专题文档。

| 文档 | 适用场景 |
|------|----------|
| [guides/embed-for-agents.md](guides/embed-for-agents.md) | 你是编码 agent，要把 Agentao 嵌入另一个项目——精炼可复制手册 |
| [guides/embedding.md](guides/embedding.md) | 你是开发者，在 Python 宿主中嵌入 Agentao——完整参考 |
| [guides/acp.md](guides/acp.md) | 你要把 Agentao 作为 ACP 服务端运行 |
| [guides/acp-client.md](guides/acp-client.md) | 项目级 ACP 客户端/服务管理 |
| [guides/acp-embedding.md](guides/acp-embedding.md) | 专门嵌入 ACP 面 |
| [guides/logging.md](guides/logging.md) | 你在排查会话、工具调用或模型行为 |
| [guides/model-switching.md](guides/model-switching.md) | 你要切换 provider 或模型 |
| [guides/skills.md](guides/skills.md) | 你要创建、安装或管理技能 |
| [guides/memory-quickstart.md](guides/memory-quickstart.md) | 从用户视角快速理解记忆系统 |
| [guides/memory-management.md](guides/memory-management.md) | 记忆系统行为与实现细节 |
| [guides/session-replay.md](guides/session-replay.md) | 运行时事件的结构化 JSONL replay |
| [guides/macos-sandbox-exec.md](guides/macos-sandbox-exec.md) | macOS `sandbox-exec` shell 隔离 |
| [guides/tool-confirmation.md](guides/tool-confirmation.md) | 工具确认与安全模型 |
| [guides/date-context.md](guides/date-context.md) | 日期时间上下文注入 |
| [guides/chatagent-md.md](guides/chatagent-md.md) | 项目指令自动加载（`AGENTAO.md`） |
| [guides/headless-runtime.md](guides/headless-runtime.md) | 非交互 / headless 运行时行为 |
| [guides/funds-data-cleaning-parallelism.md](guides/funds-data-cleaning-parallelism.md) | 一个特定功能工作流说明 |

## reference —— 参考

权威参考资料。宿主 API 是稳定的宿主面合约——只面向这一层的宿主可以在版本升级中保持前向兼容。

| 文档 | 范围 |
|------|------|
| [reference/configuration.md](reference/configuration.md) | 每个配置文件、环境变量、公开 API——权威 schema 级参考（[English](reference/configuration.md)） |
| [reference/host-api.md](reference/host-api.md) | `agentao.host` 包：`ActivePermissions`、`ToolLifecycleEvent`、`SubagentLifecycleEvent`、`PermissionDecisionEvent`、`EventStream`、schema 导出辅助（[中文](reference/host-api.zh.md)） |
| [reference/replay-schema-policy.md](reference/replay-schema-policy.md) | replay JSONL schema 的稳定性策略 |
| [schema/host.events.v1.json](schema/host.events.v1.json) | 公共事件 + 权限快照面的发布期 schema 快照；`tests/test_host_schema.py` 强制字节相等 |
| [schema/host.acp.v1.json](schema/host.acp.v1.json) | 宿主面 ACP 载荷的发布期 schema 快照 |

Schema 快照已 check-in。任何改变 wire form 的 model 变更必须在同一 PR 内同时更新 Pydantic 模型与快照。

## 设计记录

这些文档记录架构决定、宿主面合约与跨仓评审。它们本身不是实现计划，但实现计划在涉及对外行为时应当回链到这里。完整集合见 [design/](design)。

| 文档 | 范围 |
|------|------|
| [design/embedded-host-contract.md](design/embedded-host-contract.md) | 宿主面 harness 合约：schema 纪律、事件流、CLI 与 harness 边界 |
| [design/embedding-vs-acp.md](design/embedding-vs-acp.md) | 该用哪一面：in-process 嵌入 vs ACP 服务端 vs ACP 客户端 |
| [design/metacognitive-boundary.md](design/metacognitive-boundary.md) | 宿主可注入的"自我 vs 项目"边界协议 |
| [design/codex-reverse-review.md](design/codex-reverse-review.md) | Codex 变更的反向评审：采纳 / 已完成 / 暂缓 |

## 发布与迁移

- [releases/](releases)——版本发布说明
- [migration/0.3.x-to-0.4.0.md](migration/0.3.x-to-0.4.0.md)——升级指南

## history —— 历史资料

已被取代的计划、归档开发笔记和旧更新记录。适合做历史追溯和上下文检索，**不是**当前行为的首选来源。

- [history/implementation/](history/implementation)——工程计划、GitHub epic 与逐 issue 拆解
- [history/dev-notes/](history/dev-notes)——归档的开发总结与修复笔记
- [history/updates/](history/updates)——历史更新记录
- `history/headless-runtime-issues.md`、`history/headless-runtime-plan.md`、`history/kanban-acp-embedded-client-issue.md`——更早的规划笔记

## 推荐阅读路径

### 面向使用者

1. [../README.zh.md](../README.zh.md)
2. [start/quickstart.md](start/quickstart.md)
3. [start/quick-reference.md](start/quick-reference.md)
4. 再根据任务进入一篇更深的文档：[guides/acp.md](guides/acp.md)、[guides/model-switching.md](guides/model-switching.md)、[guides/skills.md](guides/skills.md) 或 [guides/](guides) 下其他文件

### 面向贡献者

1. [../README.md](../README.md)
2. [../README.zh.md](../README.zh.md)
3. 先看 [guides/](guides) 或 [reference/](reference) 下相关文档，确认对外行为
4. 再进入 [design/](design) 下对应记录
5. 如有发布上下文需求，再看 [releases/](releases) 下对应版本说明

## 文档维护约定

新增或修改文档时，建议遵循这些规则：

1. 当前用户可见行为优先写在根目录 `README.md` / `README.zh.md`，并在 `docs/guides/` 中补对应专题文档。
2. 权威 schema 级参考（配置、宿主 API）放在 `docs/reference/`。
3. 架构决策与宿主面合约记录放在 `docs/design/`。
4. 版本发布说明放在 `docs/releases/`，升级指南放在 `docs/migration/`。
5. 已被取代的计划、开发笔记和旧更新记录放在 `docs/history/`，不要混进主入口路径。

## 相关入口

- [../README.md](../README.md)：英文项目首页
- [../README.zh.md](../README.zh.md)：中文项目首页
- [README.md](README.md)：英文文档入口页
