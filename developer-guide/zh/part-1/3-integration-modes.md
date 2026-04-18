# 1.3 两种集成模式

Agentao 提供**两条稳定的嵌入路径**。选哪条取决于你的宿主是不是 Python、你对隔离度的要求、以及你的分发形态。

## 模式 A · Python 进程内 SDK

```
┌─────────────────────────────────────┐
│  你的 Python 进程                     │
│                                     │
│   ┌─────────────┐     import       │
│   │ 你的业务代码  │◄─────────────────┤
│   └──────┬──────┘                   │
│          │                          │
│          ▼                          │
│   ┌─────────────┐                   │
│   │  Agentao()  │   同一进程 同一堆    │
│   └─────────────┘                   │
└─────────────────────────────────────┘
```

**原理**：`from agentao import Agentao` 直接拿到运行时，通过方法调用驱动。

**优势**：
- ✅ 零协议开销：方法调用 = 函数调用
- ✅ 事件透明：`SdkTransport` 直接把 Python 对象丢给你
- ✅ 调试最容易：可以直接断点进 Agent 内部

**劣势**：
- ⚠️ 与你的进程共享内存/崩溃/依赖：Agent 挂了你的服务也挂了
- ⚠️ 仅限 Python 宿主
- ⚠️ 依赖锁定：你必须兼容 `agentao` 的 `openai` / `mcp` / `httpx` 版本

## 模式 B · ACP 协议（跨语言 / 跨进程）

```
┌─────────────────────────┐       stdio NDJSON         ┌────────────────────────┐
│  你的宿主（任意语言）      │   JSON-RPC 2.0 双向       │  Agentao 子进程         │
│                         │◄──────────────────────────►│  agentao --acp --stdio │
│  Node / Go / Rust / …   │                           │                        │
└─────────────────────────┘                           └────────────────────────┘
```

**原理**：Agentao 作为子进程，以 NDJSON（每行一条 JSON）JSON-RPC 2.0 对外。宿主通过 stdin/stdout 发送 `initialize` / `session/new` / `session/prompt` 等请求，接收 `session/update` / `session/request_permission` 通知。

**优势**：
- ✅ 语言无关：Node / Go / Rust / Java / 任何能启子进程的宿主都可用
- ✅ 进程隔离：Agent 崩溃不影响宿主；可独立升级 / 沙箱化
- ✅ 协议标准：兼容 Zed / Claude Code 等已支持 ACP 的客户端

**劣势**：
- ⚠️ 协议开销：序列化 + stdio 往返
- ⚠️ 调试略复杂：要看 stdio trace
- ⚠️ 工具确认需要额外 UI 桥接

## 对比矩阵

| 维度 | Python SDK | ACP |
|------|-----------|-----|
| 宿主语言 | 仅 Python | 任意 |
| 进程隔离 | 无（同进程） | 有（子进程） |
| 延迟 | ~0 ms（函数调用） | 1–5 ms（stdio + JSON） |
| 崩溃影响 | 宿主一起挂 | 子进程重启即可 |
| 依赖冲突 | 有（共享依赖） | 无（独立 Python 环境） |
| 调试难度 | 低 | 中 |
| 工具确认 UI | 直接回调 | 通过 `session/request_permission` 通知 |
| 流式事件 | Python 事件对象 | JSON 通知 |
| 协议标准 | Agentao 私有 | 公开的 ACP |
| 适合场景 | SaaS 后端、批处理、Python 数据服务 | IDE 插件、非 Python 产品、多租户隔离 |

## 决策树

```
你的宿主是 Python？
 ├─ 是 ─┬─ 需要进程隔离 / 多租户安全？
 │      │   ├─ 是 → ACP
 │      │   └─ 否 → Python SDK（推荐）
 │      └─ 会频繁加载/卸载 Agent？（比如无服务器）
 │          ├─ 是 → ACP（冷启动成本可接受）
 │          └─ 否 → Python SDK
 └─ 否（Node/Go/IDE/...）→ ACP（唯一路径）
```

## 混合模式

两种模式**可以同时用**。典型做法：

- 你的 Python 后端用 SDK 嵌入 Agentao 做主流程
- 主流程中再通过 `ACPManager` 反向调用**另一个** ACP Server（比如专门做代码审查的）

这让你能把长期状态放在 SDK 侧、把一次性重任务丢给隔离的 ACP 子进程。

## 本指南后续如何组织

- **第 2 部分** 专讲 Python SDK
- **第 3 部分** 专讲 ACP（双方向：作为 Server / 作为 Client）
- **第 4 - 8 部分** 两种模式的共同内容

下一节：[1.4 5 分钟 Hello Agentao →](./4-hello-agentao)
