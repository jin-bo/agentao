# 第三部分 · ACP 协议嵌入

**跨语言的嵌入路径**：任何能启动子进程 + 读写 stdio 的语言（Node / Go / Rust / Kotlin / Swift / C# / Java …）都能把 Agentao 作为 ACP Server 驱动。

::: info 本部分关键词
线协议层反复出现的词汇 —— 完整词汇表见 [附录 G.3](/zh/appendix/g-glossary#g-3-acp-相关术语)。
- **ACP** — Agent Client Protocol；宿主（Client）↔ Agent 运行时（Server）的 stdio JSON-RPC 2.0 协议 · [§3.1](/zh/part-3/1-acp-tour)、[G.3](/zh/appendix/g-glossary#g-3-acp-相关术语)
- **NDJSON** — 换行分隔的 JSON，每行一个完整对象；stdout 被污染 = 协议崩 · [§3.1](/zh/part-3/1-acp-tour#协议特征)
- **session/prompt** — 宿主发起的请求，启动一轮对话（返回时带 `stopReason`） · [§3.2](/zh/part-3/2-agentao-as-server#发送提示-session-prompt)
- **session/update** — Server 推送的**通知**（无 `id`，禁止回复）—— 流式文本、思考、工具事件 · [§3.2](/zh/part-3/2-agentao-as-server#流式更新-session-update通知)
- **session/request_permission** — Server 推送的**请求**（带 `id`，宿主必须响应），用于工具审批 · [§3.2](/zh/part-3/2-agentao-as-server#工具确认-session-request-permission请求)
:::

## 本部分覆盖

- [**3.1 ACP 协议速览**](./1-acp-tour) — 协议定位、与 MCP 的关系、消息四象限、ACP v1 能力边界
- [**3.2 Agentao 作为 ACP Server**](./2-agentao-as-server) — 启动命令、全套方法清单、完整消息线、最小 Client 示例
- [**3.3 宿主作为 ACP Client 的典型架构**](./3-host-client-architecture) — 子进程生命周期、三回路 IO、权限 UI 桥接、TypeScript + Go 参考实现
- [**3.4 反向调用外部 ACP Agent**](./4-reverse-acp-call) — `ACPManager.prompt_once()`、委派子 agent、`.agentao/acp.json`
- [**3.5 Zed / IDE 集成范例**](./5-zed-ide-integration) — Zed 配置、线协议轨迹、多 workspace、升级路径

## 开始之前

- [1.3 两种集成模式](/zh/part-1/3-integration-modes) — 确认 ACP 适合你的宿主
- [1.4 Hello Agentao · 示例 B](/zh/part-1/4-hello-agentao#示例-b-acp-协议任意语言) — 手工喂协议消息

## 预备心智模型

> ACP 是 **"agent 界的 LSP"**——
> 你的宿主启动 `agentao --acp --stdio` 子进程，
> 双方在同一对 stdio 上以 NDJSON JSON-RPC 2.0 对话。
> 所有消息都可见、可审计、可 replay。

→ [3.1 开始 →](./1-acp-tour)
