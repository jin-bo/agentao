# Agentao 文档中心

这里是 Agentao 的文档入口页。

如果你要了解产品概览、安装路径和第一次运行体验，优先看根目录的 [README.zh.md](../README.zh.md)。如果你已经确定要深入 `docs/` 中的某类文档，这一页可以帮你更快找到入口。

## 从这里开始

按目标选择阅读路径：

- 第一次安装和启动：[QUICKSTART.md](QUICKSTART.md)
- 日常命令速查：[QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- ACP 服务端模式：[ACP.md](ACP.md)
- 日志与调试：[LOGGING.md](LOGGING.md)
- 模型和 provider 切换：[MODEL_SWITCHING.md](MODEL_SWITCHING.md)
- 技能系统使用：[SKILLS_GUIDE.md](SKILLS_GUIDE.md)
- 查看具体功能说明：见下方[功能文档](#功能文档)
- 宿主稳定面（事件流、权限快照、Schema 快照）：见下方 [API 参考](#api-参考)
- 嵌入 harness 的设计记录：见下方 [设计记录](#设计记录)
- 查看实现与设计资料：见下方[实现说明](#实现说明)

## 文档目录

### 用户文档

- [QUICKSTART.md](QUICKSTART.md)
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- [ACP.md](ACP.md)
- [LOGGING.md](LOGGING.md)
- [MODEL_SWITCHING.md](MODEL_SWITCHING.md)
- [SKILLS_GUIDE.md](SKILLS_GUIDE.md)
- [DEMO.md](DEMO.md)

### 功能文档

- [features/memory-quickstart.md](features/memory-quickstart.md)
- [features/memory-management.md](features/memory-management.md)
- [features/acp-client.md](features/acp-client.md)
- [features/TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md)
- [features/DATE_CONTEXT_FEATURE.md](features/DATE_CONTEXT_FEATURE.md)
- [features/CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md)
- [features/funds-data-cleaning-parallelism.md](features/funds-data-cleaning-parallelism.md)

### API 参考

- [api/host.md](api/host.md) — `agentao.host` 宿主稳定面：`ActivePermissions`、`ToolLifecycleEvent`、`SubagentLifecycleEvent`、`PermissionDecisionEvent`、`EventStream`
- [api/host.zh.md](api/host.zh.md) — 上文中文镜像
- [schema/host.events.v1.json](schema/host.events.v1.json) — 公共事件 + 权限快照面的 JSON schema 快照
- [schema/host.acp.v1.json](schema/host.acp.v1.json) — 宿主面 ACP 载荷的 JSON schema 快照

### 设计记录

- [design/embedded-host-contract.md](design/embedded-host-contract.md)
- [design/metacognitive-boundary.md](design/metacognitive-boundary.md)

### 贡献者与内部资料

- [implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md](implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md)
- [implementation/TOOL_CONFIRMATION.md](implementation/TOOL_CONFIRMATION.md)
- [implementation/ACP_CLIENT_PROJECT_SERVERS.md](implementation/ACP_CLIENT_PROJECT_SERVERS.md)
- [implementation/PLUGIN_SYSTEM_MVP_PLAN.md](implementation/PLUGIN_SYSTEM_MVP_PLAN.md)
- [implementation/MARKETPLACE_PLUGIN_ORGANIZATION.md](implementation/MARKETPLACE_PLUGIN_ORGANIZATION.md)
- [implementation/SKILL_INSTALL_UPDATE_PLAN.md](implementation/SKILL_INSTALL_UPDATE_PLAN.md)
- [implementation/ACP_GITHUB_EPIC.md](implementation/ACP_GITHUB_EPIC.md)
- [implementation/READCHAR_IMPLEMENTATION.md](implementation/READCHAR_IMPLEMENTATION.md)
- [implementation/CLEAR_RESETS_CONFIRMATION.md](implementation/CLEAR_RESETS_CONFIRMATION.md)

### 历史资料

- [releases/](releases/)
- [updates/](updates/)
- [dev-notes/](dev-notes/)

## 推荐阅读路径

### 面向使用者

1. [../README.zh.md](../README.zh.md)
2. [QUICKSTART.md](QUICKSTART.md)
3. [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
4. 再根据任务进入一篇更深的文档：
   [ACP.md](ACP.md)、[MODEL_SWITCHING.md](MODEL_SWITCHING.md)、[SKILLS_GUIDE.md](SKILLS_GUIDE.md) 或 `features/` 下对应文件

### 面向贡献者

1. [../README.md](../README.md)
2. [../README.zh.md](../README.zh.md)
3. 先看相关用户文档，确认对外行为
4. 再进入 `implementation/` 下的对应实现说明
5. 如有发布上下文需求，再看 `releases/` 下对应版本说明

## 用户文档

这些是 `docs/` 中优先级最高的用户文档。

| 文档 | 适用场景 |
|------|----------|
| [QUICKSTART.md](QUICKSTART.md) | 你要最快跑通安装和首次启动 |
| [QUICK_REFERENCE.md](QUICK_REFERENCE.md) | 你想快速查常用命令和操作 |
| [ACP.md](ACP.md) | 你要把 Agentao 作为 ACP 服务端运行 |
| [LOGGING.md](LOGGING.md) | 你在排查会话、工具调用或模型行为 |
| [MODEL_SWITCHING.md](MODEL_SWITCHING.md) | 你要切换 provider 或模型 |
| [SKILLS_GUIDE.md](SKILLS_GUIDE.md) | 你要创建、安装或管理技能 |
| [DEMO.md](DEMO.md) | 你要走一遍演示或 Demo 流程 |

## 功能文档

这些文档针对具体功能做更深入的说明。

| 文档 | 范围 |
|------|------|
| [features/memory-quickstart.md](features/memory-quickstart.md) | 从用户视角快速理解记忆系统 |
| [features/memory-management.md](features/memory-management.md) | 记忆系统行为与实现细节 |
| [features/acp-client.md](features/acp-client.md) | 项目级 ACP 客户端/服务管理 |
| [features/TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md) | 工具确认与安全模型 |
| [features/DATE_CONTEXT_FEATURE.md](features/DATE_CONTEXT_FEATURE.md) | 日期时间上下文注入 |
| [features/CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md) | 项目指令自动加载 |
| [features/funds-data-cleaning-parallelism.md](features/funds-data-cleaning-parallelism.md) | 一个特定功能工作流说明 |

## API 参考

这些文档构成嵌入 Agentao 的稳定宿主合约。只面向这一层的宿主可以在版本升级中保持前向兼容。内部运行时类型（`AgentEvent`、`ToolExecutionResult`、`PermissionEngine`）刻意不在该面内 —— 边界划分见下方[设计记录](#设计记录)。

| 文档 | 范围 |
|------|------|
| [api/host.md](api/host.md) | `agentao.host` 包：`ActivePermissions`、`ToolLifecycleEvent`、`SubagentLifecycleEvent`、`PermissionDecisionEvent`、`EventStream`、schema 导出辅助；运行时身份契约；事件投递语义 |
| [api/host.zh.md](api/host.zh.md) | 上文中文镜像 |
| [schema/host.events.v1.json](schema/host.events.v1.json) | 公共事件 + 权限快照面的发布期 schema 快照；`tests/test_host_schema.py` 强制字节相等 |
| [schema/host.acp.v1.json](schema/host.acp.v1.json) | 宿主面 ACP 载荷的发布期 schema 快照 |

Schema 快照已 check-in。任何改变 wire form 的 model 变更必须在同一 PR 内同时更新 Pydantic 模型与快照。

## 设计记录

这些文档记录架构决定与宿主面合约。它们本身不是实现计划，但实现计划在涉及对外行为时应当回链到这里。

| 文档 | 范围 |
|------|------|
| [design/embedded-host-contract.md](design/embedded-host-contract.md) | 宿主面 harness 合约：schema 纪律、事件流 MVP、CLI 与 harness 边界 |
| [design/metacognitive-boundary.md](design/metacognitive-boundary.md) | 宿主可注入的"自我 vs 项目"边界协议 |

## 实现说明

这些文档面向贡献者，偏设计、实现和工程内部说明。有些是计划稿或实现记录，不应直接当作当前对外文档。

把它们当作工程上下文，而不是规范用户入口：

- [implementation/TOOL_CONFIRMATION.md](implementation/TOOL_CONFIRMATION.md)
- [implementation/ACP_CLIENT_PROJECT_SERVERS.md](implementation/ACP_CLIENT_PROJECT_SERVERS.md)
- [implementation/PLUGIN_SYSTEM_MVP_PLAN.md](implementation/PLUGIN_SYSTEM_MVP_PLAN.md)
- [implementation/MARKETPLACE_PLUGIN_ORGANIZATION.md](implementation/MARKETPLACE_PLUGIN_ORGANIZATION.md)
- [implementation/SKILL_INSTALL_UPDATE_PLAN.md](implementation/SKILL_INSTALL_UPDATE_PLAN.md)
- [implementation/ACP_GITHUB_EPIC.md](implementation/ACP_GITHUB_EPIC.md)
- [implementation/READCHAR_IMPLEMENTATION.md](implementation/READCHAR_IMPLEMENTATION.md)
- [implementation/CLEAR_RESETS_CONFIRMATION.md](implementation/CLEAR_RESETS_CONFIRMATION.md)

## 历史资料

这些目录适合做历史追溯、版本对照和旧上下文检索，但不是当前行为的首选来源。

- [releases/](releases/)：版本发布说明
- [updates/](updates/)：历史更新记录
- [dev-notes/](dev-notes/)：开发过程中的归档笔记

## 文档维护约定

新增或修改文档时，建议遵循这些规则：

1. 当前用户可见行为优先写在根目录 `README.md` / `README.zh.md`，并在 `docs/` 中补对应专题文档。
2. 重要功能放在 `docs/features/`。
3. 设计草稿、实现说明、工程计划放在 `docs/implementation/`。
4. 版本发布说明放在 `docs/releases/`。
5. 历史过程记录放在 `docs/updates/` 或 `docs/dev-notes/`，不要混进主入口路径。

## 相关入口

- [../README.md](../README.md)：英文项目首页
- [../README.zh.md](../README.zh.md)：中文项目首页
- [README.md](README.md)：英文文档入口页
