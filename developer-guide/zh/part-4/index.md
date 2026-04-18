# 第四部分 · 事件层与 UI 集成

Agent 运行时与你的用户界面之间的唯一接口就是 **Transport**。这部分教会你完整地把 Agent 的事件流桥接到任意 UI 形态（CLI / Web / 手机原生 / 后台批处理）。

## 本部分覆盖

- [**4.1 Transport Protocol**](./1-transport-protocol) — 四个方法、三种实现路径、线程与异步要点
- [**4.2 AgentEvent 事件清单**](./2-agent-events) — 10 种事件的触发时机、payload、典型用法
- [**4.3 SdkTransport 快速桥接**](./3-sdk-transport) — 官方回调实现的最佳实践与陷阱
- [**4.4 构建流式 UI**](./4-streaming-ui) — SSE / WebSocket 端到端示例
- [**4.5 工具确认 UI**](./5-tool-confirmation-ui) — CLI / Web 模态 / 手机 / 批处理四种形态
- [**4.6 最大迭代数兜底策略**](./6-max-iterations) — 五种策略 + 卡死检测启发式

## 开始之前

- [2.2 构造器完整参数表](/zh/part-2/2-constructor-reference) — `transport` 参数语义
- [2.3 生命周期管理](/zh/part-2/3-lifecycle) — `chat()` 的阻塞特性

## 心智模型

> Transport 是你的"UI 代言人"——
> Agent 只通过它与外界发生任何互动：
> 推事件、问确认、反问用户、报告兜底。
> 你实现得越扎实，UI 体验就越稳。

→ [4.1 开始 →](./1-transport-protocol)
