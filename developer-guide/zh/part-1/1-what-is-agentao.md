# 1.1 Agentao 是什么

> **本节你会学到**
> - 用 5 行代码看清楚 Agentao 到底是什么
> - 它带了什么 / 你的应用要负责什么
> - 它和 LangChain / 普通 chatbot / 通用 AI 助手的区别

**Agentao 是一个可嵌入的 Python Agent 运行时。** 3 行代码就能让你的应用拥有一个有状态、能调用工具的助手——它会读写文件、执行命令、调用你的 API，并跨轮次记住上下文：

```python
from pathlib import Path
from agentao import Agentao

agent = Agentao(working_directory=Path.cwd())
print(agent.chat("总结最近 5 次 commit"))
```

最少就这几行。不需要 Web 服务器，不需要额外服务。同一份运行时还可以通过 **ACP** stdio JSON-RPC 协议被非 Python 宿主（IDE 插件、Node、Go、Rust）驱动，宿主不必重新造轮子：

```bash
agentao --acp --stdio
```

## 开箱即用

- **内置工具** — 文件读 / 写 / 编辑、Shell、Web 抓取、搜索、glob/grep、MCP 桥接
- **权限管控** — 规则引擎 + 危险操作前的人机确认
- **持久化记忆** — SQLite 后端，项目级 + 用户级双作用域，跨会话保留
- **会话治理** — 对话压缩、`working_directory` 隔离、多实例友好
- **两条嵌入路径** — Python 直接 import（最短），或 stdio JSON-RPC（任意语言）
- **模型可移植** — OpenAI / Anthropic / Gemini / DeepSeek / vLLM / 任意 OpenAI 兼容端点
- **前向兼容的宿主合约** — `agentao.harness` 是冻结的、有 schema 快照的 API，生产代码跨版本升级不会断（[4.7](/zh/part-4/7-harness-contract)）

## 从 CLI 到可嵌入运行时

Agentao 起步于一个命令行工具——`uv run agentao` 启动终端 REPL。但 CLI 只是它的一个外壳。自 v0.2.10 起核心运行时被解耦，对外暴露两个稳定嵌入面：

- **Python 进程内 SDK** — `from agentao import Agentao` 拿到一个 Agent 实例直接驱动
- **ACP 协议服务器** — `agentao --acp --stdio`，任意语言通过 JSON-RPC 驱动

::: info 术语说明
本指南偶尔会出现 **Harness** 一词，它指的是把 LLM 循环、工具调用、权限、记忆、沙箱编排在一起的**运行时骨架**。你的应用提供"业务肌肉"（API、数据库、UI），Agentao 负责"神经中枢"（决策循环、状态、安全防线）。把它当成解释性术语即可，不是产品名。
:::

## 它不是什么

| 别把 Agentao 当作 | 原因 |
|------------------|------|
| LangChain / LlamaIndex 的替代 | 那些是"拼装工具箱"；Agentao 是"预装好的运行时" |
| 端到端 Agent 产品 | 它没有自己的 UI、用户系统、账务；需要你的应用把它"安装"进去 |
| 通用 AI 助手或单纯的 coding chatbot | CLI 只是一个交互入口，产品本体是其背后的治理型运行时 |
| 单一模型厂商绑定 | 支持 OpenAI / Anthropic / Gemini / DeepSeek 等所有 OpenAI 兼容接口 |
| 纯框架（零依赖） | 自带成套工具（文件/Shell/Web/搜索/记忆/MCP 桥接），开箱可用 |

## 为什么选择嵌入 Agentao

1. **开箱即用的工具集** — 文件读写、Shell、Web、搜索、代码编辑、MCP 桥接都已内置并经过实战打磨
2. **多层安全边界** — 工具确认、权限引擎、domain allowlist/blocklist、macOS sandbox-exec 层层可组合
3. **成熟的会话管理** — 对话压缩、记忆持久化、`working_directory` 隔离，多实例并发友好
4. **标准协议支持** — 原生 MCP 客户端 + ACP 服务器，与 Zed / Claude Code 等工具生态互通
5. **轻量可控** — Python 纯依赖，无 Web 服务器/数据库强要求，可直接 `pip install` 进你的应用

## 本指南如何组织

- **第 2 部分**：Python 宿主的直接嵌入（最短路径）
- **第 3 部分**：ACP 协议，供非 Python 宿主使用
- **第 4 部分**：事件层与 UI 集成（流式、确认、询问）
- **第 5 部分**：六大扩展点——让 Agentao 理解**你**的业务
- **第 6 - 7 部分**：安全、生产化部署
- **第 8 部分**：五个典型集成蓝图（Cookbook）

## TL;DR

- Agentao = **可嵌入的 Python Agent 运行时**。`from agentao import Agentao` 之后即拥有一个能调工具的有状态助手。
- 两条嵌入路径：**Python 进程内 SDK**（最短）或 **ACP stdio JSON-RPC**（任意语言）。
- 自带：工具、权限、记忆、会话、多租户工作目录、MCP 客户端。
- 你的应用提供"业务肌肉"（API / DB / UI），Agentao 负责"神经中枢"（决策循环、状态、安全）。

下一节：[1.2 核心概念 →](./2-core-concepts)
