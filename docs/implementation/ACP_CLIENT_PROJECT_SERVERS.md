# ACP Client And Project-Local Servers

## Status

这份文件现在作为 ACP client 与 project-local ACP servers 设计的总纲和入口页，不再承载全部实施细节。

canonical 文档已经拆分到：

- issue docs:
  - `docs/implementation/acp-client-project-servers/issues/`

后续设计更新应优先落到 issue 文档，避免母稿和实施文档漂移。

## Canonical Entry Points

issue 入口：

- [acp-client-project-servers/issues/README.md](acp-client-project-servers/issues/README.md)

## MVP Summary

目标是在 Agentao 中建立一套 project-local ACP client runtime，使当前 CLI 能管理并连接外部 ACP servers。

核心边界：

- 配置来源仅限：
  - `<cwd>/.agentao/acp.json`
- 使用方式：
  - 显式 `/acp ...` 命令
  - `/acp send` 可按需自动启动 server
- 通信模型：
  - 本地 stdio 子进程
  - JSON-RPC / ACP methods
  - 异步接收、队列缓存、CLI 空闲时展示
- 用户交互模型：
  - ACP server 请求用户确认或用户输入时，不抢占当前 CLI 输入框
  - 统一进入 pending interaction
  - 由用户通过显式 `/acp ...` 命令响应
  - 自由文本输入采用 Agentao ACP extension method: `_agentao.cn/ask_user`
  - `max_iterations` 不做 ACP extension，ACP 模式下保持保守降级
- 返回消息展示形式：
  - `<message from="server-name">...</message>`
- v1 安全边界：
  - 不默认自动给 ACP server 发消息
  - 不把 ACP 返回自动注入当前 Agentao 对话上下文
  - 不引入多 agent 自动编排

## Document Structure

issue 文件位于：

- [acp-client-project-servers/issues/README.md](acp-client-project-servers/issues/README.md)

当前拆分覆盖：

- 配置模型与加载
- 子进程生命周期与 runtime 状态机
- JSON-RPC client 与握手
- prompt / cancel 流程
- inbox 与 idle flush
- CLI 命令与状态展示
- 日志与诊断
- 测试覆盖
- 文档与 operator notes

## Source Of Truth Policy

为了避免设计漂移，后续按以下规则维护：

1. 任务拆解以 `docs/implementation/acp-client-project-servers/issues/*.md` 为真源
2. 本文件只保留：
   - 总目标
   - 关键边界
   - 导航入口
3. 不再把完整细节继续堆回本文件

## Recommended Workflow

设计演进时：

1. 先更新对应 issue 文档
2. 若需要，只在本文件调整摘要或入口

实现时：

1. 从 issue 文档进入
2. 按推荐顺序推进
3. 不再把本文件当作详细实施规格
