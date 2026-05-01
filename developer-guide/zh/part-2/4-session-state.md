# 2.4 会话状态与持久化

> **本节你会学到**
> - `Agentao` 实例承载的 4 块状态分别在哪里
> - 哪些必须由宿主持久化才能跨进程重启
> - 三种部署形态（长驻池 / 每请求重建 / 混合）以及怎么选

一个运行中的 `Agentao` 实例承载的远不止"上一句 assistant 回复"。**清楚每一块状态分别存在哪里**，决定了你的服务是"重启后用户不丢上下文"还是"每次重启都从零开始"。

本节回答三个问题：

1. 一个 `Agentao` 实例到底握着哪些状态？
2. 哪些必须由宿主持久化才能扛住重启？
3. 怎么优雅地还原它们？

## 2.4.1 四个状态桶

每个 `Agentao` 实例有四块独立存储——**彼此不同**，持久化时必须分别对待。

| 桶 | 存放位置 | `close()` 后还在吗？ | 谁负责落盘 |
|----|---------|--------------------|-----------|
| **对话消息** | `agent.messages`（内存列表） | ❌ 否 | 宿主 |
| **记忆（持久化）** | `.agentao/memory.db` + `~/.agentao/memory.db`（SQLite） | ✅ 是 | Agentao |
| **会话摘要** | 项目 SQLite 的 `session_summaries` 表 | ✅ 是 | Agentao |
| **技能激活状态** | `agent.skill_manager.active_skills`（内存字典） | ❌ 否 | 宿主（恢复时需重激活） |

一句话：**落在 SQLite 的 Agentao 会管；落在 Python 列表/字典的要你自己管。**

## 2.4.2 `agent.messages` — 逐轮对话流水

对话的核心状态。OpenAI 风格消息字典列表：

```python
agent.messages = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！有什么可以帮忙？"},
    {"role": "user", "content": "跑一下 git status"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "run_shell_command",
                    "arguments": '{"command":"git status"}',
                },
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_abc123", "content": "On branch main…"},
    {"role": "assistant", "content": "你在 main 上，工作区干净。"},
]
```

### `agent.messages` 里**不**包含的东西

- 系统提示——每次 `chat()` 都会由 `AGENTAO.md` + 日期 + 激活的技能 + 记忆块重建，**不要**落盘
- 工具 schema——每次调用都会从工具注册表重建
- 工作目录——构造时固定，之后不可变

### 持久化的硬规矩

1. **成对保留**：带 `tool_calls` 的 `assistant` 消息和配对的 `{role: "tool", tool_call_id: ...}` **要存都存，要丢都丢**。只存一半会破坏 OpenAI tool-call schema，下一次 `chat()` 直接报错
2. **用 JSON 序列化**：每条都是普通 dict，`json.dumps(agent.messages)` 安全可用
3. **一个都别滤**：不要以"太吵"为由过滤掉 tool 消息——LLM 需要它们理解自己做过什么

## 2.4.3 存储+还原配方

最小模式——每个会话一行，消息列表整体存成 JSON：

```python
import json
from pathlib import Path
from agentao import Agentao

def save_session(agent: Agentao, session_id: str, db) -> None:
    """每次 chat() 后调用。"""
    db.upsert(
        session_id,
        {
            "messages": json.dumps(agent.messages),
            "active_skills": list(agent.skill_manager.get_active_skills().keys()),
            "working_directory": str(agent.working_directory),
            "model": agent.get_current_model(),
        },
    )

def load_session(session_id: str, db) -> Agentao:
    """宿主启动时或请求到达时调用。"""
    row = db.get(session_id)
    if row is None:
        raise KeyError(session_id)

    agent = Agentao(
        working_directory=Path(row["working_directory"]),
        model=row["model"],
    )

    # 回放消息——add_message 不会触发 LLM。
    for msg in json.loads(row["messages"]):
        agent.messages.append(msg)  # 或：agent.add_message(msg["role"], msg["content"])

    # 重新激活技能。激活操作幂等。
    for name in row["active_skills"]:
        agent.skill_manager.activate_skill(name)

    return agent
```

### 为什么直接 append 到 `agent.messages`？

`add_message(role, content)` 是公开 helper——但只处理纯文本。如果某条消息带有 `tool_calls` 或 `tool_call_id`，直接写 `agent.messages` 能保住完整结构。两条路径都支持。

## 2.4.4 记忆会自愈

你**不需要**手动持久化记忆——Agentao 已经在做了。

```python
agent = Agentao(working_directory=Path("/app/users/alice"))
# MemoryManager 会自动打开 /app/users/alice/.agentao/memory.db
# 上次会话的持久化记忆自动加载。
```

只要同一租户跨重启使用同一个 `working_directory`，记忆就能接上。这也是为什么[多租户隔离](/zh/part-6/4-multi-tenant-fs)强调**每用户一个 `working_directory`**——它同时决定了记忆的作用域。

## 2.4.5 会话摘要 — 别动它

当上下文窗口满了，Agentao 的压缩管线会往 `agent.messages` 里写入 `[Conversation Summary]` 块，同时把同一份摘要存到 `session_summaries`（SQLite）。这一切对你透明：

- 被压缩的消息仍然留在 `agent.messages` 里，所以你落盘的 JSON 依然可来回复盘
- 重启后不需要另外拉 `session_summaries`——摘要块已经嵌在 `messages` 里了

**宿主代码不要直接读写 `session_summaries` 表**。那是压缩管线的内部管子，不是对接点。

## 2.4.6 还原到 ACP 服务端

如果你走的是 ACP 路径，改用 `session/load`——上面那段 SDK 食谱不适用。宿主把之前抓下来的 `{role, content}` 通过线协议发回去：

```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "session/load",
  "params": {
    "sessionId": "sess-restored",
    "cwd": "/app/users/alice",
    "history": [
      {"role": "user",      "content": [{"type": "text", "text": "上次的问题"}]},
      {"role": "assistant", "content": [{"type": "text", "text": "上次的回答"}]}
    ]
  }
}
```

字段格式**与 SDK 列表不同**——ACP 把内容包成"类型化块"数组。完整 schema 见[附录 C · session/load](/zh/appendix/c-acp-messages#c-7-session-load)。

## 2.4.7 常见错误

| 错误 | 症状 | 修 |
|------|------|---|
| 落盘时把 tool-call 对拆散 | 下一次 `chat()` 报 `tool_call_id not found` | 整条列表存；不要过滤 |
| 把系统提示也持久化了 | 日期过期、新技能不生效 | 只存 `agent.messages`，系统提示自会重建 |
| 还原时用了**不同**的 `working_directory` | 记忆像是没了 | 每租户固定 `working_directory`，跟消息一起存 |
| 忘了重激活技能 | 还原后 agent 忘了"自己是谁" | 存 `active_skills` 列表；load 时重激活 |
| `clear_history()` 后再回放 | `clear_history()` **同时**会取消激活技能，这点容易忽略 | 要么完整重建 agent；要么清完之后再手动激活 |

## 2.4.8 热池 vs. 按需重建

| 模式 | 适用 | 优点 | 缺点 |
|------|------|------|------|
| **热池**（agent 在内存里过多轮） | 聊天 UI、IDE 集成 | 零重建延迟；MCP / 技能状态已热 | 活跃会话越多 RAM 越多；崩溃会吞没未落盘的轮次 |
| **每请求重建**（每次从 DB 加载） | Serverless、稀疏请求 | Pod 无状态，易扩缩 | 每轮多 ~50–200 ms 用于回放 + 重开 MCP |
| **混合**（热池 + 回落 DB） | SaaS 聊天机器人 | 热会话快，冷会话自愈 | 代码量多一些 |

生产部署常选混合模式——详见 [7.2 无状态 vs 有状态服务](/zh/part-7/2-stateless-vs-stateful)。

## TL;DR

- 状态分布在 **4 个桶**：`messages`、记忆 DB、MCP 子进程、`working_directory` 内容。
- 跨重启持久化：把 `agent.messages` 序列化进你的 DB；记忆和工作目录靠磁盘自然保留。
- 恢复路径：重建 agent → 用 `add_message(role, content)` 逐条回放历史 → 像往常一样调 `chat()`。
- 三种部署形态：**长驻池**（低延迟、要粘性会话）、**每请求重建**（无状态，每轮多 ~50–200 ms）、**混合**（热池 + DB 回落，SaaS 常用）。

---

下一节：[2.5 运行时切换 LLM →](./5-runtime-llm-switch)
