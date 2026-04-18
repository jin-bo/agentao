# 附录 E · 从 LangChain / AutoGen / CrewAI 迁移

如果你已经在其他框架上建过 agent，Agentao 的心智模型大部分会让你觉得"眼熟"，但有几处承重点非常不同。本附录就是那本对照字典。

## E.1 一页心智对照表

| 概念 | LangChain | AutoGen | CrewAI | Agentao |
|------|-----------|---------|--------|---------|
| Agent 单元 | `AgentExecutor`（+ chain / graph） | `ConversableAgent` | `Agent` 角色 | `Agentao` 实例 |
| 工具 | `BaseTool` | 带 docstring 的函数 | `tool` 装饰器 | `Tool` ABC |
| 多 agent | LangGraph | `GroupChat` | `Crew` | 再起一个 `Agentao` 或 ACP 反向调另一个 |
| 记忆 | `ConversationBufferMemory` / 向量库 | agent 上的 `memory` | agent 上的 `memory` | `MemoryManager`（SQLite，项目+用户） |
| 流式 | callback / LCEL `astream` | `register_hook` | event hook | `Transport` + `AgentEvent` |
| 工具审批 | 用 `interrupt` 的 HITL | `a_human_input_mode` | `human_input=True` | `Transport.confirm_tool` |
| 外部模型上下文 | MCP 适配器 | function calling | 无 | 一等 MCP（stdio + SSE） |
| 宿主进程隔离 | 无（进程内） | 无（进程内） | 无（进程内） | **ACP**（子进程） |

## E.2 从 LangChain 迁移

### 一致的部分

- **工具**——样板几乎一样。LC 的 `BaseTool.name/description/args_schema/_run` 与 Agentao 的 `Tool.name/description/parameters/execute` 一一对应
- **回调式流式**——LC 的 callback handler 和 Agentao 的 `Transport.emit(AgentEvent)` 目的相同
- **提示词拼装**——LC 的 system prompt 片段 → Agentao 的 `AGENTAO.md` + 技能

### 不同的部分

- **没有 LCEL / 图** —— Agentao 是一条执行回路，不是可组合流水线。如果你建了 chain DAG，先塌平成"一条系统提示 + 工具"。分支逻辑交给 LLM，而非框架
- **检索器不能当工具硬塞** —— 包一个薄的自定义工具，在 `execute()` 里调你现成的 `.get_relevant_documents()`
- **记忆不是向量库** —— Agentao 的记忆是结构化键值 SQLite。如果你依赖向量召回，留着向量库——把它以 MCP 服务器或自定义工具暴露出来，让 LLM 去调
- **默认跑很久** —— LC 有 `max_iterations`；Agentao 也有（`chat(max_iterations=)`）默认 100。为控本请调低

### 迁移食谱

1. 先移工具。`_run(self, **kw)` → `execute(self, **kw)`；`args_schema` Pydantic → `parameters` JSON Schema（或用 `pydantic.TypeAdapter` 生成）
2. 提示词文本放 `AGENTAO.md` + 技能文件。每请求的动态上下文仍放在用户消息里
3. `AgentExecutor(..., memory=ConversationBufferMemory())` 换成 `Agentao(...)`；历史自动存在 `agent.messages`
4. 接流式：callback handler 换成 `SdkTransport(on_event=…)`
5. RAG：向量库以 MCP 服务器或 `Tool` 子类暴露

## E.3 从 AutoGen 迁移

### 一致的部分

- **对话循环** —— AG 的"agent 说话，工具被调，结果返回"与 Agentao 的内循环一致
- **异步友好** —— 两者都能在 `asyncio.to_thread` / 事件循环下工作

### 不同的部分

- **没有 `GroupChat`** —— AG 的强项是多 agent 编排。Agentao 支持子 agent（一个 `Agentao` 启另一个，或 ACP 反向调不同服务器），但没有内置的"群聊管理"。多角色场景请模型化为"技能 + 单 agent"，或自建协调器
- **工具调用仅 OpenAI 风格** —— AG 支持多家 LLM，格式各异；Agentao 统一到 OpenAI 兼容的 tool-call schema。非 OpenAI 家也得说这个格式
- **人类介入** —— AG 的 `human_input_mode` ≈ `Transport.confirm_tool` + `Transport.ask_user`
- **没有 `UserProxyAgent`** —— 用户在循环之外，通过 `chat()` 调用与 agent 通信。宿主代码就是"用户代理"

### 迁移食谱

1. 找 AG 里最"自主"的那个 agent——它就是你的 `Agentao`
2. 其他 `ConversableAgent` 折叠成：无状态 → 工具；塑造行为 → 技能
3. `GroupChat` 管理器变成你的宿主代码（FastAPI 接口、调度循环），决定何时调 `agent.chat()`
4. `register_function` 调用迁到 `Tool` 子类

## E.4 从 CrewAI 迁移

### 一致的部分

- **role / goal / backstory** —— CrewAI 每 agent 的 `role` + `goal` + `backstory` → Agentao 的 `AGENTAO.md` + 激活的技能
- **工具是独立单元** —— CrewAI `@tool` ≈ Agentao `Tool` 子类

### 不同的部分

- **没有 `Crew` 编排器** —— CrewAI 的显式 `tasks` / `process` 管线与 Agentao 风格相反。Agentao 里由 LLM 根据"工具 + 技能 + 用户消息"决定下一步。多步工作流放提示里或放宿主侧循环，不放框架配置
- **层级 vs 扁平** —— CrewAI 的 `Process.hierarchical` / `sequential` 变成"一个超级 agent + 技能"或宿主侧编排
- **没有管理器 agent 抽象** —— 有的话，把它重塑成宿主代码，多次以不同提示调 `agent.chat()`

### 迁移食谱

1. 挑最有用的 CrewAI agent——连带工具先移过来
2. `Process.sequential`：写一个宿主侧函数调 `agent.chat("step 1 …")`，检查输出，再 `agent.chat("step 2 …")`
3. `Process.hierarchical`：管理器变宿主代码；worker agent 变成额外 `Agentao` 实例（隔离）或技能（仅改语气/思路时）
4. 移工具
5. `memory=True` 换成 `MemoryManager`（见 [5.5](/zh/part-5/5-memory)）

## E.5 决策矩阵——要不要迁

**不要**迁如果：

- 你重度依赖 LangGraph DAG（留 LC）或 AutoGen 的 group chat（留 AG）
- RAG 管道很深且不愿包装成 MCP
- 只要 Python 进程内，跨语言不是目标，并且老框架上运维已经成熟

**想**迁如果：

- 你需要可嵌入（Python SDK + 非 Python 宿主用 ACP）
- 你需要严格沙箱 + 权限（见 [Part 6](/zh/part-6/)）
- 你需要一等 MCP 和一个小而可审的内核
- 你想要确定性生命周期（`chat()` → `close()`）而非长生命周期链

## E.6 平滑迁移的模式

| 模式 | LC / AG / CrewAI | Agentao |
|------|------------------|---------|
| "调我们 API 的工具" | BaseTool | `Tool` 子类 |
| "把公司政策注入提示" | system message | `AGENTAO.md` |
| "任务特定行为切换" | 系统提示分支 | 技能（按需激活） |
| "写操作需人审" | HITL 回调 | `Transport.confirm_tool` |
| "按用户区分的对话记忆" | 按用户的 `ConversationBufferMemory` | 按用户的 `working_directory` → 项目作用域记忆 |
| "文档 RAG" | retriever 工具 | MCP filesystem / 自定义 retriever 工具 |
| "取消进行中的轮" | LCEL abort / AG cancel | `CancellationToken` |

## E.7 迁移时的常见坑

1. **过度抽象** —— 不需要 DAG，信 LLM + 工具
2. **低估工具描述的重要性** —— Agentao 没有思维链脚手架；工具描述和 AGENTAO.md **就是**行为计划，要写厚
3. **把用户和项目记忆混用** —— 默认就是租户隔离（见 [6.4](/zh/part-6/4-multi-tenant-fs)）；user 作用域仅限单用户场景
4. **不开确认** —— 其他框架常常默认"什么都问"。Agentao 让你白名单——请在审过工具的爆炸半径之后再白名单

---

附录至此结束。
