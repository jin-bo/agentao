# 1.6 与厂商 Agent SDK 的对比

> **本节你将学到**
> - Agentao 与 Claude Agent SDK、OpenAI Agents SDK、Strands 的区别
> - 这几个方案各自在哪些情况下更合适
> - 选型前要先回答的问题

现在已经有好几家厂商提供可嵌入自己程序的 agent 循环。选型时要问的不是"谁的功能更多"——它们大量重叠——而是哪一套取舍适合你的宿主。本页就最常决定选型的几点，把 Agentao 和其中三家做对比。

::: info 核对日期：2026-10-01
关于其他项目的事实均取自它们当天的官方文档和仓库（版本见[来源](#来源)）。这些项目发布频繁，依赖某个细节前请点开链接再确认。发现错误欢迎提 issue。
:::

## 一览

| | **Agentao** | **Claude Agent SDK** | **OpenAI Agents SDK** | **Strands Agents**（AWS） |
|---|---|---|---|---|
| **agent 循环运行在哪里** | 你的 Python 进程内 | SDK 以子进程方式启动的、随包附带的 Claude Code CLI 中 | 你的 Python 进程内 | 你的进程内 |
| **语言** | Python；其他语言通过 ACP 接入 | Python、TypeScript | Python（另有独立的 JS/TS SDK） | Python、TypeScript |
| **模型** | 任何兼容 OpenAI Chat Completions 的端点，外加原生 Anthropic Messages 与 OpenAI Responses 线路；运行时可切换供应商 | 仅 Claude——经 Anthropic API、Bedrock、Vertex AI 或 Microsoft Foundry；不支持路由到非 Claude 模型 | 原生支持 OpenAI；任何兼容 OpenAI 的端点；LiteLLM / Any-LLM 适配器在文档中标为尽力而为的 beta | 默认 Bedrock；另有 Anthropic、OpenAI、Gemini、Ollama、LiteLLM 等 |
| **默认发给厂商的数据** | 无——没有遥测 | 沿用 Claude Code 的默认：使用 Claude API 时用量指标发往 Anthropic（在 Bedrock / Vertex / Foundry 上默认关闭）；可关闭 | trace 发往 OpenAI 的 tracing 后端；可关闭或改投 | OpenTelemetry 导出需主动开启；Strands harness 未配置时完全不碰遥测 |
| **审计 / 可观测性** | 本地 JSONL 回放文件和进程内事件流；没有 OpenTelemetry | OpenTelemetry 导出（需开启）；客户端侧的费用估算 | 可插拔 processor 的 tracing，第三方集成很多 | OpenTelemetry |
| **权限** | 四种模式（`read-only`、`workspace-write`、`full-access`、`plan`）；allow / deny / ask 规则；由宿主逐次审批 | 六种模式；allow / deny / ask 规则；`canUseTool` 回调 | 按工具设 `needs_approval`，可暂停/恢复；输入、输出和工具 guardrail | 用 interrupt 做人工审批；Strands harness 另有需主动开启的"interventions"（默认不询问就执行工具） |
| **Hooks** | 八个 shell hook 事件；能读取为 Claude Code 编写的 hook 文件（逐项列举的子集） | Python SDK 十个事件，TypeScript 更多 | run 与 agent 生命周期回调 | 带类型的 hook 事件 |
| **MCP** | stdio、Streamable HTTP、SSE；URL 类 server 支持 OAuth 登录 | 进程内 server、stdio、HTTP、SSE | 托管 MCP、Streamable HTTP、SSE、stdio | 支持 |
| **沙箱** | macOS `sandbox-exec`，默认关闭 | Claude Code 的沙箱：macOS、Linux 和 WSL2，默认关闭 | sandbox agent，可在本地 Unix 或 Docker 上运行，也可用托管服务（E2B、Modal、Daytona 等） | 可插拔的沙箱后端（Docker、SSH、自定义） |
| **ACP（agent ↔ 编辑器）** | 内置：`agentao --acp --stdio`（另有 ACP 客户端） | 通过 Agent Client Protocol 项目发布的独立适配器 | 文档未提及 | 仅 TypeScript 的 `strands` CLI（`--acp-server`）；Python 包中没有 |
| **许可证** | MIT | MIT，使用受 Anthropic 商业条款约束 | MIT | Apache-2.0 |

四者都支持子代理，以及可保存、可恢复的会话。

## Agentao 的不同之处

- **没有厂商的循环，也不经过厂商的数据通路。** 循环是 Agentao 自己的代码，跑在你的进程里，只连你配置的端点，不发送任何遥测。Claude Agent SDK 运行的是 Claude Code 本身（一个独立的二进制）；OpenAI Agents SDK 默认把 trace 导出给 OpenAI，除非你关掉。
- **供应商中立是设计目标，不是加一层适配器。** 三种线路协议都是原生实现，历史在三者之间保持同一格式，所以 `/provider` 或宿主的一次调用就能在会话中途切换模型或厂商。
- **审计记录是你自己的文件。** 每一轮都可以记录为 `.agentao/replays/` 下的 JSONL；宿主还能拿到带类型的事件流（`agentao.host`）。不依赖任何托管的 tracing 后端。
- **一套运行时，三种形态。** 同一个包既是 Python 库，也是 `agentao` CLI 和 ACP server，所以非 Python 宿主或 ACP 编辑器驱动的，和 Python 宿主嵌入的是完全同一套东西（[1.3 两种集成模式](./3-integration-modes)）。
- **中文支持。** 中英双语文档，记忆召回会对中文分词（jieba）。

## 什么情况下别的选择更合适

- **只用 Claude，并且要和 Claude Code 的行为完全一致** → Claude Agent SDK 运行的就是同一个 agent，hook 事件更多，有文件 checkpoint，Claude Code 的沙箱除 macOS 外也支持 Linux。
- **需要托管沙箱，或想用 OpenAI 的 tracing 生态** → OpenAI Agents SDK 提供 Docker 和托管沙箱后端、多种会话存储和 tracing 集成。
- **在 AWS 上，或需要 OpenTelemetry、评测（evals）包、多 agent 模式（graph、swarm、A2A）** → Strands 都有覆盖。
- **现在就需要 Linux 或容器沙箱，或者 OpenTelemetry** → Agentao 目前还没有。
- **需要生态** → 厂商 SDK 的社区大得多；Agentao 是个小项目。

## 选型前先回答

针对你的宿主回答以下问题，再做选择：

1. 提示词、代码和 trace 是否必须不经过除你所选模型端点之外的任何第三方服务？
2. 是否会用到多家厂商的模型，或需要不改代码就能换厂商？
3. 宿主是 Python 吗？还是需要进程边界（其他语言、编辑器）？
4. 是否需要 Linux 上的系统级隔离？还是逐次审批加上你自己的进程隔离就够了？

如果 1–3 大多是"是"、4 是"审批就够"，Agentao 正是为这种场景设计的。否则，请从上面"别的选择更合适"一节开始看。

## 来源

核对日期：2026-10-01。

- **Claude Agent SDK** 0.2.163（2026-09-30）：[概览](https://code.claude.com/docs/en/agent-sdk/overview)、[权限](https://code.claude.com/docs/en/agent-sdk/permissions)、[hooks](https://code.claude.com/docs/en/agent-sdk/hooks)、[MCP](https://code.claude.com/docs/en/agent-sdk/mcp)、[可观测性](https://code.claude.com/docs/en/agent-sdk/observability)、[LLM 网关](https://code.claude.com/docs/en/llm-gateway)、[沙箱](https://code.claude.com/docs/en/sandboxing)、[数据使用](https://code.claude.com/docs/en/data-usage)、[ACP 适配器](https://github.com/agentclientprotocol/claude-agent-acp)
- **OpenAI Agents SDK** PyPI 上为 0.22.3（2026-09-17）：[文档](https://openai.github.io/openai-agents-python/)、[模型](https://openai.github.io/openai-agents-python/models/)、[tracing](https://openai.github.io/openai-agents-python/tracing/)、[人工审批](https://openai.github.io/openai-agents-python/human_in_the_loop/)、[MCP](https://openai.github.io/openai-agents-python/mcp/)、[sandbox agents](https://openai.github.io/openai-agents-python/sandbox_agents/)、[仓库](https://github.com/openai/openai-agents-python)
- **Strands Agents** `strands-agents` 1.57.2（2026-10-01）与 `strands-harness` 0.1.2（2026-09-22）：[仓库](https://github.com/strands-agents/harness-sdk)、[用户指南](https://strandsagents.com/docs/user-guide/)
- **Agentao**：[配置参考](https://github.com/jin-bo/agentao/blob/main/docs/reference/configuration.zh.md)、[宿主 API](https://github.com/jin-bo/agentao/blob/main/docs/reference/host-api.md)

→ [第二部分 · 在 Python 中嵌入](/zh/part-2/)
