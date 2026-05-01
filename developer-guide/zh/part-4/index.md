# 第四部分 · 事件层与 UI 集成

Agent 运行时与你的用户界面之间的唯一接口就是 **Transport**。这部分教会你完整地把 Agent 的事件流桥接到任意 UI 形态（CLI / Web / 手机原生 / 后台批处理）。

::: info 本部分关键词
- **Transport** — 推送接口（`emit` / `ask_confirmation` / `ask_user` / `bailout`）；运行时和 UI 之间的唯一缝隙 · [§4.1](/zh/part-4/1-transport-protocol)、[G.2](/zh/appendix/g-glossary#g-2-扩展点)
- **AgentEvent** — 内部事件类型（文本片段、工具启动/完成、LLM 调用）—— 仅供调试，**版本间不保证稳定** · [§4.2](/zh/part-4/2-agent-events)、[G.6](/zh/appendix/g-glossary#g-6-事件类型速查)
- **HostEvent** — Pydantic 类型的生命周期事件（tool / permission / subagent）；**稳定**，附 schema 快照 · [§4.7](/zh/part-4/7-host-contract)、[G.1](/zh/appendix/g-glossary#g-1-核心概念)
- **`agent.events()`** — 落在**稳定** `agentao.host` 表面上的异步 pull 迭代器；审计 / SIEM / 计费用 · [§4.7](/zh/part-4/7-host-contract)
- **`active_permissions()`** — 当前生效策略的 JSON 化快照，给"谁能做什么"UI 用 · [§4.7](/zh/part-4/7-host-contract#active-permissions-策略快照)、[G.5](/zh/appendix/g-glossary#g-5-安全术语)
:::

## 本部分覆盖

- [**4.1 Transport Protocol**](./1-transport-protocol) — 四个方法、三种实现路径、线程与异步要点
- [**4.2 AgentEvent 事件清单**](./2-agent-events) — UI、工具、LLM、replay 与状态变更事件
- [**4.3 SdkTransport 快速桥接**](./3-sdk-transport) — 官方回调实现的最佳实践与陷阱
- [**4.4 构建流式 UI**](./4-streaming-ui) — SSE / WebSocket 端到端示例
- [**4.5 工具确认 UI**](./5-tool-confirmation-ui) — CLI / Web 模态 / 手机 / 批处理四种形态
- [**4.6 最大迭代数兜底策略**](./6-max-iterations) — 五种策略 + 卡死检测启发式
- [**4.7 嵌入式 Harness 合约**](./7-host-contract) — `agent.events()` + `active_permissions()` —— 给生产审计 / 可观测流水线用的**稳定宿主 API**

## 开始之前

- [2.2 构造器完整参数表](/zh/part-2/2-constructor-reference) — `transport` 参数语义
- [2.3 生命周期管理](/zh/part-2/3-lifecycle) — `chat()` 的阻塞特性

## 心智模型

> Transport 是你的"UI 代言人"——
> Agent 只通过它与外界发生任何互动：
> 推事件、问确认、反问用户、报告兜底。
> 你实现得越扎实，UI 体验就越稳。

→ [4.1 开始 →](./1-transport-protocol)
