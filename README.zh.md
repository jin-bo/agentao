# Agentao（Agent + 道）

```
   ___                      _
  / _ \ ___ _ ___  ___  ___| |_  ___  ___
 /  _  // _` / -_)| _ \/ _ \  _|/ _` / _ \
/_/ |_| \__, \___||_// \___/\__|\__,_\___/
        |___/        (The Way of Agents)
```

> **"秩序生于混沌，路径藏于智能。"**
>
> **Agentao** 是一套**Governed Agent Runtime（受治理的 Agent 运行时）** —— 面向 Python 宿主、本地优先、私有优先、可嵌入的 agent harness。权限、协议、记忆、插件、多会话控制都是一等公民。

[English README.md](README.md)

---

## 📚 文档 — 先读这里

完整手册在 `developer-guide/`（VitePress，双语）。生产站点：**[agentao.cn](https://agentao.cn)**。

| 受众 | 阅读 | 站点 |
|---|---|---|
| **CLI 用户** —— 终端跑 `agentao` | [`developer-guide/zh/cli/`](developer-guide/zh/cli/)（12 章：slash 命令 · plan 模式 · 记忆 · 回放 · …） | [agentao.cn/zh/cli/](https://agentao.cn/zh/cli/) |
| **嵌入式开发者** —— 把 Agentao 嵌进自己的 app | [`developer-guide/zh/`](developer-guide/zh/)（Part 1–7 + 附录） | [agentao.cn/zh/](https://agentao.cn/zh/) |
| **English** | [`developer-guide/en/cli/`](developer-guide/en/cli/) · [`developer-guide/en/`](developer-guide/en/) | [agentao.cn/en/cli/](https://agentao.cn/en/cli/) · [agentao.cn](https://agentao.cn) |

本地浏览：

```bash
cd developer-guide && npx vitepress dev
```

要 schema 级参考（每个配置文件、每个环境变量、每个公开 API），权威文档是 [`docs/reference/configuration.md`](docs/reference/configuration.md)。其余内容都在 Developer Guide。

---

## 30 行嵌入

```bash
pip install agentao
```

```python
from pathlib import Path
from agentao import Agentao
from agentao.llm import LLMClient
from agentao.transport import NullTransport

agent = Agentao(
    working_directory=Path("/tmp/agent-run-1"),
    llm_client=LLMClient(
        api_key="sk-...",
        base_url="https://api.openai.com/v1",
        model="gpt-5.4",
    ),
    transport=NullTransport(),
)
reply = agent.chat("总结今天的日志。")
print(reply)
agent.close()
```

构造出来的 agent **不读环境变量、不开隐式网络、没有全局状态** —— 所有输入由宿主显式提供。要走环境发现路径（CLI 内部就是这个），改用 `agentao.embedding.build_from_environment(working_directory=...)`。

嵌入细节：[Developer Guide · Part 2（生命周期）](https://agentao.cn/zh/part-2/) 与 [Part 4（Host 合约）](https://agentao.cn/zh/part-4/7-host-contract)。

---

## CLI 速通

```bash
pip install 'agentao[cli]'

# .env 放在你的项目下（三个变量都必填）：
printf "OPENAI_API_KEY=sk-your-key\nOPENAI_BASE_URL=https://api.openai.com/v1\nOPENAI_MODEL=gpt-5.4\n" > .env

# 烟雾测试 —— 非交互
agentao -p "Reply with the single word: OK"

# 交互式 REPL
agentao
```

> **从 0.4.x 升级？** 0.5.0 移除了 0.4.x 期间已弃用的东西 —— `agentao.harness`（改用 `agentao.host`）、`agentao.session`（改用 `agentao.embedding.sessions`）、`Agentao(...)` 上的八个回调参数（改用 `transport=`，或 `build_compat_transport`）—— 另有两处从未告警：会话函数的 `project_root` 变为必填，`Agentao(...)` 只有前五个参数可以按位置传。详见 [docs/migration/0.4.x-to-0.5.0.zh.md](docs/migration/0.4.x-to-0.5.0.zh.md)。
>
> **从 0.3.x 升级？** 0.4.0 起 CLI 依赖移到了 `[cli]` extra 里。零行为变更升级用 `pip install 'agentao[full]'`。详见 [docs/migration/0.3.x-to-0.4.0.md](docs/migration/0.3.x-to-0.4.0.md)。

REPL 起来后最先用的几个命令：

```text
/help       全部 slash 命令 + agent 工具清单
/status     模型、模式、token 用量、激活的 skills
/model      在当前 provider 下切换模型
/mode       切换权限模式（read-only · workspace-write · full-access · plan）
/plan       进入 plan 模式（只读思考，落 .agentao/plan.md）
/memory     查看持久记忆
/mcp list   MCP 服务器状态
/exit       干净地退出（不要 Ctrl+C）
```

CLI 手册：**[agentao.cn/zh/cli/](https://agentao.cn/zh/cli/)** —— 12 章覆盖每一个 slash 命令以及背后的心智模型。

---

## 为什么是 Agentao？

名字本身就是设计：*Agent*（能力）+ *Tao*（治理）。受治理的 agent 运行时三大支柱：

| 支柱 | 含义 | Agentao 怎么做的 |
|---|---|---|
| **约束**（Constraint） | Agent 不能未经同意就动手 | 工具确认 · 四种权限模式（`read-only` / `workspace-write` / `full-access` / `plan`）· macOS `sandbox-exec` |
| **连接**（Connectivity） | Agent 必须能伸到训练数据之外的世界 | MCP（stdio / Streamable HTTP / SSE）· ACP（完整 agent 之间的 JSON-RPC）· 插件 · hooks |
| **可观测**（Observability） | Agent 必须摆出它做了什么 | 实时思考显示 · 流式工具输出 · 完整 LLM 日志 · JSONL 回放 |

---

## 能力总览

| 领域 | 你拿到的 | 深读 |
|---|---|---|
| **治理** | 工具确认、四种权限模式、plan 模式、macOS 沙箱 | [CLI 第 3 章](developer-guide/zh/cli/3-permissions-modes.md) · [第 4 章](developer-guide/zh/cli/4-plan-mode.md) |
| **上下文** | token 跟踪、LLM 摘要式压缩、溢出恢复、文件重注入 | [CLI 第 7 章](developer-guide/zh/cli/7-context-status.md) |
| **记忆** | SQLite 持久记忆、两个作用域（user / project）、自动召回、jieba 中文分词 | [CLI 第 6 章](developer-guide/zh/cli/6-memory.md) |
| **Skills** | `skills/` 自动发现、GitHub 安装（`agentao skill install owner/repo[:path][@ref]`）、`/crystallize` 工作流 | [CLI 第 5 章](developer-guide/zh/cli/5-skills-crystallize.md) |
| **协议** | MCP（stdio / Streamable HTTP / SSE）接工具 · ACP（stdio JSON-RPC）接整个 agent · 插件生命周期 | [CLI 第 8 章](developer-guide/zh/cli/8-mcp-acp-plugins.md) |
| **子 Agent** | 内建 `codebase-investigator` / `generalist`、自定义 `.agentao/agents/<name>.md`、前/后台仪表盘 | [CLI 第 11 章](developer-guide/zh/cli/11-sessions-agents.md) |
| **回放与输出** | JSONL 会话录制（`.agentao/replays/`）· markdown 切换 · `/copy` 最近回复 | [CLI 第 9 章](developer-guide/zh/cli/9-replay-output.md) |
| **嵌入** | `Agentao(...)` 构造器 · `events()` 流 · `active_permissions()` · 能力注入 · ACP Pydantic schema | [DG Part 2](https://agentao.cn/zh/part-2/) · [Part 4](https://agentao.cn/zh/part-4/) |

---

## 安装

```bash
# 嵌入式宿主（Python `from agentao import Agentao`）—— 最小依赖闭包
pip install agentao

# CLI 用户（`agentao` 命令行）—— 加上 rich/prompt-toolkit/readchar/pygments
pip install 'agentao[cli]'

# 从 0.3.x 平迁、行为零变化
pip install 'agentao[full]'
```

**要求 Python：** 3.10+。**必填环境变量：** `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` —— 三个都必须有，否则启动直接 `ValueError`。

要用 Anthropic / Gemini / DeepSeek / 任意 OpenAI 兼容 provider，配 `<NAME>_API_KEY` + `<NAME>_BASE_URL` + `<NAME>_MODEL`，再用 `LLM_PROVIDER` 或运行时 `/provider` 切。要原生对接 Anthropic 自家 API（Messages API —— 真正生效的提示缓存、签名 thinking），再加一行 `<NAME>_API_FORMAT=anthropic-messages`；要走 OpenAI 的 Responses API（推理模型的推理跨请求保留），用 `<NAME>_API_FORMAT=openai-responses`。它从不根据 URL 或名字推断。完整清单：[`docs/reference/configuration.md`](docs/reference/configuration.md)。

---

### A2Agent 配置示例

沿用现有通用供应商配置和 OpenAI Chat Completions 协议。在本地 `.env` 中添加以下内容，使用前替换两个占位符：

```dotenv
LLM_PROVIDER=A2AGENT
A2AGENT_API_KEY=your-api-key-here
A2AGENT_BASE_URL=https://api.a2agent.me/v1
A2AGENT_MODEL=your-explicit-model-id
A2AGENT_API_FORMAT=openai-completions
```

从 [A2Agent 模型目录](https://a2agent.me/models) 选择准确的模型 ID。SDK 使用 Bearer 身份验证，在基础 URL 后追加 `/chat/completions`；详见 [API 文档](https://docs.a2agent.me/api-reference/chat-completions)。运行 `uv run agentao` 启动，或在已有会话中使用 `/provider A2AGENT` 切换。供应商名称可以自行更换，只需保持环境变量前缀一致。本示例保留显式模型配置，不增加模型发现或供应商专属运行逻辑。请勿在提交、Issue 或共享日志中包含真实密钥。

在源码目录运行无需密钥的回归测试：

```bash
uv sync --extra cli
uv run python -m pytest tests/test_a2agent_compatibility.py
```

测试通过真实 SDK 和模拟 HTTP 响应，覆盖文本流式输出、分片工具调用及结果续传，以及不同密钥、接口地址和模型 ID 的供应商切换。它们验证通用客户端约定，**不代表已经完成 A2Agent 实际兼容性验证**，也不覆盖工具执行或多代理行为。

真实接口的文本流式冒烟测试默认跳过。若要主动开启，请在 shell 中显式设置 `AGENTAO_TEST_A2AGENT_LIVE=1`、`A2AGENT_API_KEY` 和 `A2AGENT_MODEL`，再运行 `uv run python -m pytest tests/test_a2agent_compatibility.py -k live`。该测试不会加载 `.env`，会向 `https://api.a2agent.me/v1` 发送合成提示词，并可能产生 API 费用。真实工具支持和其它模型能力仍需单独验证。

## 给贡献者

```bash
git clone https://github.com/jin-bo/agentao
cd agentao
uv sync
cp .env.example .env

# 从源码跑 CLI
uv run agentao
# 或
./run.sh

# 测试
uv run python -m pytest tests/
```

入口：

| 想做什么 | 看哪里 |
|---|---|
| 项目结构、编码约定 | [`CLAUDE.md`](CLAUDE.md) |
| 加 tool / agent / skill | [Developer Guide · Part 5](https://agentao.cn/zh/part-5/) |
| 插件作者指南 | [Developer Guide · §5.7](https://agentao.cn/zh/part-5/7-plugin-hooks) |
| 嵌入合约与 ACP schema | [Developer Guide · Part 4](https://agentao.cn/zh/part-4/) |
| 例子（skills · personas · 集成蓝图） | [`examples/`](examples/) |

---

## 设计原则

1. **极简（Minimalism）** —— `pip install agentao` 就能跑。没有数据库依赖，没有云依赖。
2. **透明（Transparency）** —— 推理链实时打到屏幕上。每次 LLM 调用、每次工具调用都写进 `agentao.log`。
3. **完整（Integrity）** —— 上下文不会悄悄丢：LLM 摘要式压缩、自动记忆召回、跨重启的会话连续性。

---

## 词源

**Agentao** = *Agent* + *道*（Tao）—— 万物背后的自然秩序。三层互相缠绕的含义：

- **法则（Laws）** —— 约束和塑形行为的规则
- **方法（Methods）** —— 完成目标的路径与技巧
- **路径（Paths）** —— 事物流动与连接的通道

无道之 agent，强大但不可预期。*Agentao* 就是让那种力量值得被信任的结构。

---

## License

开源。可自由使用和修改。

## 致谢

- LLM 客户端：[OpenAI Python SDK](https://github.com/openai/openai-python) · [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)
- CLI：[Rich](https://github.com/Textualize/rich) · [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) · [readchar](https://github.com/magmax/python-readchar)
- `web_fetch` 的可选本地 JS 渲染：[Playwright](https://github.com/microsoft/playwright-python)
- MCP：[Model Context Protocol SDK](https://github.com/modelcontextprotocol/python-sdk) —— 架构灵感来自 [Gemini CLI](https://github.com/google-gemini/gemini-cli)
- 灵感来源：[Claude Code](https://github.com/anthropics/claude-code)
