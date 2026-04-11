# 06 CLI Commands And Status Surface

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

为 project-local ACP servers 提供显式 CLI 控制面和状态可见性。

## Scope

- `/acp`
- `/acp list`
- `/acp start <name>`
- `/acp stop <name>`
- `/acp restart <name>`
- `/acp send <name> <message>`
- `/acp cancel <name>`
- `/acp status <name>`
- `/acp approve <name> <request-id>`
- `/acp reject <name> <request-id>`
- `/acp reply <name> <request-id> <text>`
- `/status` 中的 ACP 摘要

## Deliverables

- CLI 集成：
  - `agentao/cli/app.py`
  - `agentao/cli/commands.py`
  - `agentao/cli/commands_ext.py`
  - `agentao/cli/_utils.py`
- CLI tests

## Dependencies

- 01
- 02
- 04
- 05

## Design Notes

- `/acp` 默认显示 overview
- `/status` 可增加：
  - `ACP servers: x/y running`
  - `ACP inbox: n queued`
- 如果存在待用户处理的 interaction，`/acp` 或 `/status` 应显示计数摘要
- 错误信息要指向具体 server 名称
- `send` 走统一高层 API，不在 CLI 里拼握手细节
- CLI 已拆分为多个模块，因此：
  - `app.py` 负责交互壳、PromptSession、toolbar 和主循环接缝
  - `commands.py` / `commands_ext.py` 负责 `/acp` 与 `/status` 命令分发
  - `_utils.py` 负责命令枚举与补全提示

## Tests

- 命令解析正确
- list/status 输出包含必要状态字段
- send 在未启动时自动启动
- stop/restart 行为稳定
- 无配置时输出清晰
- approve/reject/reply 能路由到正确 request

## Acceptance Criteria

1. 用户可以完全通过显式命令使用 v1 功能
2. `/status` 能反映 ACP 总体状态
3. 不影响现有非 ACP CLI 工作流

## Out Of Scope

- 自动路由到 ACP server
- 全局配置命令
