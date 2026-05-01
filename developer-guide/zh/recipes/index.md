# Recipes — 高频任务，一键到答案

> 每条 recipe 都是 **你实际想做的事**，映射到对应的章节。如果你的任务不在这里，[完整目录](/zh/) 里大概率有。

## 我想……

### …把自己的业务 API 包成一个 Agent 工具

→ **[5.1 自定义工具](/zh/part-5/1-custom-tools)** — `Tool` 子类的 name / description / parameters / execute，文末有完整生产模板。**要点**：返回 JSON 字符串、有副作用的设 `requires_confirmation=True`、description 按 LLM 第一视角写。

### …把 Agent 输出流式推到浏览器

→ **[4.4 构建流式 UI](/zh/part-4/4-streaming-ui)** — SSE 和 WebSocket 双模板、用 `loop.call_soon_threadsafe` 跨线程桥接、keep-alive 帧。配合 **[2.7 FastAPI / Flask](/zh/part-2/7-fastapi-flask-embed)** 拿到可直接复制的生产 endpoint。

### …加一个能中途取消 chat() 的"停止"按钮

→ **[2.6 取消与超时](/zh/part-2/6-cancellation-timeouts)** — `CancellationToken`、客户端断连事件接线、`asyncio.wait_for` 的硬墙时钟。`chat()` 取消时**返回** `"[Cancelled: <reason>]"`——不要 try 异常。

### …在 Web UI 里弹工具确认对话框

→ **[4.5 工具确认 UI](/zh/part-4/5-tool-confirmation-ui)** — 用 `asyncio.run_coroutine_threadsafe` 同步→异步桥接、Web 弹窗模式、"本次允许 / 永久允许"的交互。配合 **[5.4 权限引擎](/zh/part-5/4-permissions)**，让 90% 的安全调用根本不走弹窗。

### …按 (tenant_id, session_id) 池化 Agent 实例

→ **[2.3 生命周期管理](/zh/part-2/3-lifecycle)** 给锁 + 线程模式；**[6.7 资源治理与并发](/zh/part-6/7-resource-concurrency)** 给 TTL + LRU 淘汰；**[7.1 SaaS 内置助手](/zh/part-7/1-saas-assistant)** 给一个整合的 FastAPI 完整示例。

### …让对话跨 Pod 重启不丢

→ **[2.4 会话状态与持久化](/zh/part-2/4-session-state)** — 实例上承载了什么、哪些要序列化（`agent.messages`）、用 `add_message(role, content)` 回放后再 `chat()`。

### …运行时切模型（便宜 / 贵的路由）

→ **[2.5 运行时切换 LLM](/zh/part-2/5-runtime-llm-switch)** — `set_provider` / `set_model`、便宜→贵 / 主+备 / A/B 三种路由模式。

### …给每个租户独立的凭据或 MCP token

→ **[2.2 构造器 · extra_mcp_servers](/zh/part-2/2-constructor-reference#第-2-档-生产常用-再加-8-个)** 做会话级 MCP 注入；**[6.4 多租户与文件系统](/zh/part-6/4-multi-tenant-fs)** 给租户隔离规则；**[7.1 SaaS 内置助手](/zh/part-7/1-saas-assistant)** 把这些串起来。

### …阻断 SSRF 或锁紧 `web_fetch`

→ **[6.3 网络与 SSRF 防护](/zh/part-6/3-network-ssrf)** — 默认黑名单覆盖范围、`.github.com`（后缀）vs `github.com`（精确）规则、禁重定向模式。**不要禁用默认黑名单**——只能扩。

### …用 Node / Go / Rust / IDE 驱动 Agentao

→ **[第 3 部分 · ACP 协议](/zh/part-3/)** — 先看 [3.1 60 秒快速尝鲜](/zh/part-3/1-acp-tour#60-秒快速尝鲜)，再看 [3.3 宿主作为 ACP Client 的典型架构](/zh/part-3/3-host-client-architecture)（含 TS + Go 骨架）。

### …让记忆按租户严格隔离

→ **[5.5 记忆系统](/zh/part-5/5-memory)** 讲作用域（项目 + 用户）和优雅降级；**[6.4 多租户与文件系统](/zh/part-6/4-multi-tenant-fs)** 讲跨租户陷阱。要么禁用用户作用域，要么按 `tenant_id+user_id` 索引条目。

### …用 Docker 部署但不让 runtime 镜像变胖

→ **[6.8 容器化、灰度与回滚](/zh/part-6/8-deployment)** — 多阶段 Dockerfile（用 `uv` 构建、runtime 只装 venv）、`StatefulSet` + PVC + `sessionAffinity` 做粘性会话、按维度灰度。

### …让我的宿主代码跨 Agentao 版本不断（审计流水线 / 可观测）

→ **[4.7 嵌入式 Harness 合约](/zh/part-4/7-host-contract)** —— `agentao.host` 是**稳定的**、附 schema 快照的宿主 API。审计 / SIEM / 计费用 `agent.events()`（异步 pull 迭代器），策略快照 UI 用 `agent.active_permissions()`。**不要在生产代码里直接用内部 `AgentEvent`**。

两个可直接跑的入口：[`examples/host_events.py`](https://github.com/jin-bo/agentao/blob/main/examples/host_events.py)（~50 行，打到 stdout）和 [`examples/host_audit_pipeline.py`](https://github.com/jin-bo/agentao/blob/main/examples/host_audit_pipeline.py)（完整 SQLite 审计循环）。

### …在 Jupyter Notebook 里嵌入 Agentao

→ **[`examples/jupyter-session/`](https://github.com/jin-bo/agentao/tree/main/examples/jupyter-session)** —— 每个 kernel 一个 `Agentao`，`agent.events()` 驱动 `IPython.display`。带一个能直接打开的 `session.ipynb` 和过测试的烟雾套件。配 **[1.3 两种集成模式](/zh/part-1/3-integration-modes)** 看进程内 SDK 背景。

### …做一个 Slack 或微信机器人

→ **[`examples/slack-bot/`](https://github.com/jin-bo/agentao/tree/main/examples/slack-bot)** 用 `slack-bolt` `app_mention` → 一轮，频道级权限。**[`examples/wechat-bot/`](https://github.com/jin-bo/agentao/tree/main/examples/wechat-bot)** 是微信轮询守护进程版本，联系人级权限。两个都是最小形态（离线烟雾测试，不要 API key）。

### …拿到密闭可用的 pytest fixture

→ **[`examples/pytest-fixture/`](https://github.com/jin-bo/agentao/tree/main/examples/pytest-fixture)** 提供可直接 drop-in 的 `agent` / `agent_with_reply` / `fake_llm_client` fixture。密闭、不需要 `OPENAI_API_KEY`。配 [附录 F.8](/zh/appendix/f-faq#f-8-开发与测试) 看断言模式。

## 没找到你的任务？

- **所有可跑的 examples** —— [`examples/README.md`](https://github.com/jin-bo/agentao/blob/main/examples/README.md) 列出每个样例的技术栈、运行命令、演示内容。
- **按角色**：看[首页](/zh/)的"按角色选起点"表。
- **搜索**：VitePress 内置搜索（右上角）支持本地 + 全文。
- **卡住了**：[附录 F · FAQ 与排错](/zh/appendix/f-faq) 按症状索引。
