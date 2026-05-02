# Embedding vs. ACP——我该用哪一面？

**面向读者：** 任何要把 Agentao 集成到另一个系统的人。
**TL;DR：** "ACP" 在本仓库里指三件不同的事，它们和 in-process embedding **正交**而非互斥，可以任意组合使用。

English: [embedding-vs-acp.md](embedding-vs-acp.md).

## 四种使用面

| 使用面 | 含义 | 入口 | 参考 |
|---|---|---|---|
| **In-process embedding** | 把 Agentao 当 Python 库放进自己的进程。用 `Agentao.arun()` 驱动一轮；用 `agent.events()` 观测；用权限引擎做拦截。 | `from agentao import Agentao` | [EMBEDDING.md](../EMBEDDING.md)、[api/host.md](../api/host.md) |
| **ACP server** | 把 Agentao 当独立子进程运行，通过 stdio 收发 JSON-RPC 2.0。外部客户端（Zed、Cursor、IDE 插件）来启动并驱动它。 | `agentao --acp --stdio` | [ACP.md](../ACP.md) |
| **ACP client** | 在自己的 runtime 里嵌入 Agentao，**同时**让它再去调用其它 ACP agent（Claude Code、Codex 等）作为某些角色的后端。 | `from agentao.acp_client import ACPManager` | [features/acp-client.md](../features/acp-client.md) |
| **ACP schema surface** | ACP wire payload 的版本化 Pydantic 类型，加上签入的 `host.acp.v1.json` 快照。**只在**你的 in-process 宿主自己也对外讲 ACP 时才用得到。 | `from agentao.host import export_host_acp_json_schema` | [api/host.md §Schema snapshot policy](../api/host.md#schema-snapshot-policy) |

## 决策树

```
你掌控一个 Python 进程，想把 Agentao 放进去吗？
│
├── 是 → In-process embedding。
│        用 `Agentao.arun()` 驱动，`agent.events()` 观测，
│        权限引擎拦截。除非下面任一支也适用，否则到此为止。
│
│        可选：你还要把嵌入的 Agentao 通过 ACP wire 再转给
│        自己的客户端吗？
│        ├── 否 → 完成。
│        └── 是 → 再 import ACP schema surface 拿版本化的
│                 Pydantic 类型。
│
└── 否，我的宿主不是 Python 进程——它是编辑器、IDE 扩展、
    沙箱 runner、其他语言写的评测 harness 等。
    │
    ├── 我想驱动 Agentao  → ACP server。把
    │                        `agentao --acp --stdio` 拉成子进程，
    │                        用 ACP 跟它对话。
    │
    └── 我想让 Agentao    → ACP client。在 runtime 里嵌入
        替我去驱动别的 agent  `ACPManager`，在 `.agentao/acp.json`
                              里配上游 agent。
```

组合用法很常见。例如一个 Kanban 风格的 workflow runtime 可能**同时**做：把 Agentao **in-process 嵌**进来，再通过 **ACP client** 把 "reviewer" 角色委派给 Codex 后端。

## 为什么会有这种混淆

三条不同的代码路径都叫 "ACP"：

1. `agentao/acp/`——*服务端* 包，实现 `agentao --acp --stdio`。
2. `agentao/acp_client/`——*客户端* 包（`ACPManager`），workflow runtime 用它来代理其他 agent。
3. `agentao/host/`——只 re-export ACP 的 Pydantic 模型，让那些自己也讲 ACP 的嵌入式宿主拿到版本化类型。**这不是和 Agentao 对话的方式**——Agentao 已经在你进程里了。

如果你只做 in-process embedding，(1)、(2) 和 (3) 里的 ACP 导出**全部可以忽略**。你真正要用的 host contract 三支柱是：`agent.events()`、`agent.active_permissions()` 和生命周期事件模型。

## 另请参阅

- [docs/EMBEDDING.md](../EMBEDDING.md)——in-process embedding 教程。
- [docs/api/host.md](../api/host.md)——公共 host API 参考。
- [docs/design/embedded-host-contract.md](../design/embedded-host-contract.md)——host contract 设计记录。
- [docs/ACP.md](../ACP.md)——ACP 服务端参考。
- [docs/features/acp-client.md](../features/acp-client.md)——ACP 客户端参考。
