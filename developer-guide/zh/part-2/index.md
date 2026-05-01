# 第二部分 · Python 进程内嵌入

Python 宿主的**最短集成路径**：直接 `from agentao import Agentao` 拿到运行时，用方法调用驱动，没有任何协议层开销。

::: info 本部分关键词
五个反复出现的核心词汇 —— 完整词汇表见 [附录 G](/zh/appendix/g-glossary)。
- **Agentao 实例** — 一次构造 = 一段会话；`close()` 是义务 · [§2.3](/zh/part-2/3-lifecycle)、[G.1](/zh/appendix/g-glossary#g-1-核心概念)
- **Working directory（cwd）** — 文件工具根目录、MCP cwd、`AGENTAO.md` 查找路径；构造时冻结 · [§2.2](/zh/part-2/2-constructor-reference)、[G.1](/zh/appendix/g-glossary#g-1-核心概念)
- **Transport** — 推送式回调（`emit(event)`），驱动流式 UI · [§2.7](/zh/part-2/7-fastapi-flask-embed)、[G.2](/zh/appendix/g-glossary#g-2-扩展点)
- **CancellationToken** — 宿主侧的取消句柄，用于中断进行中的 `chat()` · [§2.6](/zh/part-2/6-cancellation-timeouts)、[G.1](/zh/appendix/g-glossary#g-1-核心概念)
- **extra_mcp_servers** — 会话级 MCP 注入（不同租户 → 不同 token） · [§2.2](/zh/part-2/2-constructor-reference#第-2-档-生产常用-再加-8-个)
:::

## 本部分覆盖

- [**2.1 安装与包导入**](./1-install-import) — 选版本、包 extras、懒加载特性
- [**2.2 构造器完整参数表**](./2-constructor-reference) — 全部参数语义、工作目录冻结、会话级 MCP、生产嵌入模板
- [**2.3 生命周期管理**](./3-lifecycle) — `chat()` / `clear_history()` / `close()`、运行时换模型、并发模式、FastAPI 完整示例
- [**2.4 会话状态**](./4-session-state) — 四块状态、持久化/还原配方、记忆自动恢复
- [**2.5 运行时切换 LLM**](./5-runtime-llm-switch) — `set_provider()` / `set_model()`、路由模式、级联退路
- [**2.6 取消与超时**](./6-cancellation-timeouts) — `CancellationToken`、断连接线、硬超时、`max_iterations`
- [**2.7 嵌入 FastAPI / Flask 示例**](./7-fastapi-flask-embed) — 生产级模板：SSE 流式、会话池、鉴权、取消

## 开始之前

确保你已读过：

- [1.2 核心概念](/zh/part-1/2-core-concepts) — Agent / Tool / Transport / Working Directory 等名词
- [1.3 两种集成模式](/zh/part-1/3-integration-modes) — 确认 Python SDK 是你的选择
- [1.4 Hello Agentao](/zh/part-1/4-hello-agentao#示例-a-python-sdk-约-20-行) — 20 行可跑样板

## 预备心智模型

> Agent 不是一个"函数调用"，而是一个**有状态的进程组件**。
> 一次 `Agentao(...)` 对应一段会话；
> 多会话 = 多实例；
> `close()` 是义务，不是建议。

→ [2.1 开始 →](./1-install-import)
