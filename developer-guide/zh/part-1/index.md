# 第一部分 · 起步与心智模型

这一部分帮你快速判断三件事：Agentao 是什么、它适不适合你的集成场景、以及用哪条路径开始。

::: info 本部分关键词
第一遍只需要掌握这些词。完整定义见 [附录 G](/zh/appendix/g-glossary)。
- **Agentao** — 可嵌入的 Python Agent 运行时；可被 Python 直接调用，也可通过 ACP 被其它语言驱动 · [§1.1](./1-what-is-agentao)
- **Tool / Skill / MCP** — 三类常用能力扩展：业务函数、LLM 侧指令、外部工具生态 · [§1.2](./2-core-concepts)、[第 5 部分](/zh/part-5/)
- **Transport** — 运行时和 UI 的桥：流式事件、工具确认、用户追问、最大迭代兜底 · [§1.2](./2-core-concepts)、[第 4 部分](/zh/part-4/)
- **Python SDK** — 进程内嵌入路径，适合 Python 后端、数据服务和批处理 · [§1.3](./3-integration-modes)、[第 2 部分](/zh/part-2/)
- **ACP** — stdio JSON-RPC 协议路径，适合 IDE 插件、Node/Go/Rust 宿主和进程隔离场景 · [§1.3](./3-integration-modes)、[第 3 部分](/zh/part-3/)
:::

## 本部分覆盖

- [**1.1 Agentao 是什么**](./1-what-is-agentao) — 产品定位、开箱能力、适合/不适合的边界
- [**1.2 核心概念**](./2-core-concepts) — Agent、Tool、Skill、Transport、Session、Working Directory
- [**1.3 两种集成模式**](./3-integration-modes) — Python SDK vs ACP，按宿主语言和隔离要求做选择
- [**1.4 5 分钟 Hello Agentao**](./4-hello-agentao) — 先跑通一个最小可用会话
- [**1.5 运行环境要求**](./5-requirements) — Python 版本、extras、凭据、OS、网络和磁盘布局

## 怎么读

| 你的状态 | 推荐路径 |
|---------|----------|
| 只想先跑起来 | [1.4 Hello](./4-hello-agentao) → [1.2 核心概念](./2-core-concepts) |
| 要判断是否适合项目 | [1.1 Agentao 是什么](./1-what-is-agentao) → [1.3 两种集成模式](./3-integration-modes) |
| 宿主是 Python | [1.4 Hello](./4-hello-agentao) → [第 2 部分](/zh/part-2/) |
| 宿主不是 Python / 需要进程隔离 | [1.3 两种集成模式](./3-integration-modes) → [第 3 部分](/zh/part-3/) |
| 准备部署生产 | [1.5 运行环境要求](./5-requirements) → [第 6 部分](/zh/part-6/) |

## 心智模型

> Agentao 不是一个聊天 UI，也不是一个单次函数调用。
> 它是你应用里的 agent runtime：
> 负责会话状态、工具调用、权限、记忆、事件流和跨语言协议。
> 你的宿主负责产品体验、业务权限和部署边界。

→ [从 1.1 开始 →](./1-what-is-agentao)
