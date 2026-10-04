# Pi 1.0 更新：Agentao 值得借鉴的设计

**Status:** 分析记录，2026-10-02。建议优先级不代表已批准的实施计划。

**当前状态（2026-10-03）：** 第 8 节的 MCP Resources 与远程 Skills 已实现并随 0.5.10 发布（PR #401、#403），不再是待办。2026-10-03 按评审意见收窄了第 2、3、5、6、9 节的推进路线。

**分析基线：** Pi `v1.0.0`（2026-10-01 发布，`a13d35a742c6ef8462812a28fbe1d8c8b7431c32`）；本地 `../pi-mono/` 为 `v1.0.0` 后 17 个提交，HEAD 为 `eeac84ca92498ac18b6832754d01aef1d3c5f654`。Agentao HEAD 为 `cd3a24a805fa853fe2f7558b1a036eec34fde087`。结论以当时的源码和文档为依据。

**Related:** [MCP Resources 方案](mcp-resources.md)、[MCP Skills 方案](mcp-skills.md)、[此前的 Pi 借鉴评审](pi-mono-borrow-review.zh.md)、[LLM API adapters](llm-api-adapters.zh.md)。Resources 和 Skills 方案当前另行维护；本文不替代其决策和验收要求。

## 1. 结论与范围

最值得借鉴的是工具按需加载、可靠的执行恢复和 MCP 调用边界。全屏 TUI 是明显的产品变化，但不应仅因 Pi 改了默认值就提高其优先级。

本文区分三类内容：1.0 的直接更新、理解这些更新所需的已有机制，以及 Agentao 的改进建议。工具发现、Codemode 和普通 MCP Resources 并非全部首次出现在 1.0；1.0 对 Codemode 的提示词和错误恢复做了进一步优化。Unreleased 中的改动不计入 1.0。

主要版本依据：[coding-agent changelog](https://github.com/earendil-works/pi/blob/v1.0.0/packages/coding-agent/CHANGELOG.md)、[agent-core changelog](https://github.com/earendil-works/pi/blob/v1.0.0/packages/agent/CHANGELOG.md)、[MCP changelog](https://github.com/earendil-works/pi/blob/v1.0.0/packages/mcp/CHANGELOG.md)、[Durable changelog](https://github.com/earendil-works/pi/blob/v1.0.0/packages/durable/CHANGELOG.md)。

## 2. 工具按需暴露：最明确的上下文收益

Pi 将常用工具直接声明给模型，大量 MCP 工具保留在工具目录中，由 `tool_search` 加载。搜索覆盖工具名、描述、参数和命名空间说明；加载后的工具加入下一次模型请求，状态记录到会话并随恢复和分支保留。1.0 还缩短了 Codemode 的全局说明和工具调用说明，将详细模型 API 文档留给按需阅读。

Agentao 的 [ToolRegistry.to_openai_format](../../agentao/tools/base.py) 当前输出所有已注册工具，只有计划模式专用工具例外；工具按名称排序，已保证声明顺序稳定。MCP 和插件数量增长后，完整定义仍会占用上下文。

建议先测量，再决定是否实现。[tool-search.md](tool-search.md) 已决定“采纳设计、推迟实现”，直到满足其中的触发条件；本文不改变这一决定：

- 先统计代表性任务的工具定义 Token，包括连接多个 MCP 服务器的配置。
- 只有确认存在明显的上下文压力，才实现最小版本：常用工具直接声明，其他工具提供简短目录和搜索入口；发现工具不能绕过权限和启用状态。
- 恢复、服务器重连、工具撤回和命名冲突等处理，按实际需求逐步扩展，不在首期一并实现。

Pi 的 BM25 分词主要处理英文字符；Agentao 面向中文查询时需要调整，不能直接移植。Pi 报告的约 5,300 → 3,300 提示词 Token 是特定模型和配置的结果，不是 Agentao 的预期降幅。

参考：[Pi tool-search](https://github.com/earendil-works/pi/blob/v1.0.0/packages/coding-agent/src/extensions/tool-search/tool.ts)。

## 3. MCP 重试：先判断是否可能已经执行

Pi 明确区分资源读取与工具调用：读取和列举资源可以在指定的临时 HTTP 错误后重试一次，工具调用不自动重试，因为服务器可能已经执行了操作。

Agentao 的 [McpClient.call_tool](../../agentao/mcp/client.py) 当前对部分会话过期和传输断开错误重连并重试一次。需要进一步审计这些异常是否能证明调用未执行。服务器创建工单后、返回响应前连接断开，是可能重复副作用的具体场景；本文没有复现这一故障，不能将所有重连路径都判定为缺陷。

当前代码中，`TRANSPORT_DROPPED`（连接重置、`EndOfStream`、`BrokenResourceError` 等）发生在 SDK 工具调用内部时，客户端会重连并重新发送同一调用。这些异常不能证明请求未到达服务器。

首期采用一个简单、可实现的边界：

- 调用前发现会话不可用，可以先恢复连接，再发起调用。
- 进入 SDK 工具调用后发生传输异常，返回“结果未知”，不自动重发。
- 服务器以“会话不存在或已过期”明确拒绝本次请求时，请求未被执行，可以保留重连重试。复现时需确认分类器只在这类拒绝响应上命中。
- 资源读取不会产生副作用，保留现有的重连重试。

暂不新增幂等策略配置。服务器的 `readOnlyHint` / `idempotentHint` 可以作为以后的参考，但不构成执行安全的保证。

验证应使用可记录副作用的测试服务器，在执行后主动断开连接，检查客户端是否重复发起调用，并覆盖调用前断开与资源读取的情况。

**实施状态（0.5.11）：** 已按上述边界实施。复现结果：服务器执行调用后退出，原实现在新连接上把同一调用又执行了一次。修复后只执行一次，返回“结果未知”，下一次调用重新连接。另发现 mcp 2.x 服务器对执行中被终止的请求返回 `-32603 Session terminated before the request completed`，原分类器把它当成会话过期并重试；现按传输断开处理。代价：stdio 服务器空闲时退出，客户端要到下一次调用才发现，这次调用会报告“结果未知”，尽管它并未执行。两个 SDK 主版本都不记录接收循环已结束，客户端无法提前判断。详见 `CHANGELOG.md`。

参考：[Pi 1.0 MCP 文档](https://github.com/earendil-works/pi/blob/v1.0.0/packages/coding-agent/docs/mcp.md)。

## 4. OAuth 账号隔离：范围明确的改进

Pi 1.0 将 OAuth 凭据索引从 URL 改为服务器名与 URL 的组合，使同一地址的多个服务器配置能使用不同账号。旧的 URL 凭据迁移给第一个接管它的服务器，其他配置重新登录。它还修复了追加授权只请求新增 scope、导致原有权限丢失的问题，并加强授权响应的 issuer 检查。

Agentao 的 [oauth_store.py](../../agentao/mcp/oauth_store.py) 当前按规范化服务器 URL 保存凭据。如果工作账号与个人账号连接同一 MCP 地址，独立凭据身份具有实际价值。

可考虑服务器名与 URL，或显式 `credential_profile` 与 URL 的组合。后者能将账号身份与配置名称分开，避免改名就失去登录状态；这是 Agentao 的设计选项，不是 Pi 1.0 的实现。

**实施状态（0.5.11）：** 已按后一种方式实施，配置键为 `oauth.profile`。不设档案时凭据仍只按 URL 保存，文件名不变，无需迁移；设了档案的条目不回退到无档案的凭据。见 [mcp-oauth.md](mcp-oauth.md) §6.4。追加 scope 与 issuer 检查未在此次改动范围内。

Agentao 已有跨进程锁、刷新保护和 issuer 兼容处理，应保留这些能力。追加 scope、空或 null 可选字段、issuer 检查需要结合不同 MCP SDK 版本验证，不能只根据 Pi 的修复记录认定 Agentao 存在同样问题。

参考：[Pi 1.0 OAuth 实现](https://github.com/earendil-works/pi/blob/v1.0.0/packages/coding-agent/src/extensions/mcp/oauth.ts)。

## 5. Durable：从恢复语义开始

1.0 新发布的 `pi-durable` 将实验性持久化 harness 从 agent-core 分离出来；agent-core 收敛为 Agent、循环、代理流和相关类型。Durable 在执行工具前提交最终参数和执行意图，执行后提交结果；崩溃恢复时，只有保存的策略与当前工具声明都允许安全重放才重跑，否则返回“可能已部分执行”的中断结果。输入的 `requestId` 用于去重。

Agentao 已保存后台任务状态，但 [BackgroundTaskStore.recover](../../agentao/agents/bg_store.py) 将遗留的 `pending/running` 记录标为失败，错误为 `process exited before task finished`。保存任务记录与恢复未完成执行仍是不同能力。

当前将遗留任务标为失败，是明确的中断处理策略，不是缺陷。同时保存消息、执行阶段、待处理工具和确认状态，已接近持久化执行框架，首期范围过大。

建议先确认真实的续跑需求。需要时，先支持从最近完成的轮次恢复会话；遇到结果未知的工具调用就暂停，交给用户或宿主处理。暂缓工具级的自动重放。恢复前重新检查当前权限，不能因历史审批或旧策略自动执行新的副作用操作。也不能声称仅靠日志就能保证外部操作恰好执行一次。

现有 [ReplayRecorder](../../agentao/replay/recorder.py) 会脱敏、截断并容忍记录失败，适合审计。恢复执行需要完整且可靠的状态存储，不能直接将 Replay 日志当作唯一事实来源。

Pi Durable 仍标为实验性，值得借鉴提交顺序、重放策略与去重语义，暂不需要照搬整个 Chord/任务框架或重构 Agentao。

参考：[Durable README](https://github.com/earendil-works/pi/blob/v1.0.0/packages/durable/README.md)、[工具状态机](https://github.com/earendil-works/pi/blob/v1.0.0/packages/durable/src/harness/tool.ts)。

## 6. Codemode：适合实验，不适合直接加 Python exec

Pi Codemode 在 QuickJS/WASM 虚拟机中执行模型生成的 JavaScript，仅暴露宿主注入的能力。脚本可以并行调用工具、筛选大结果，只将显式输出和返回值送回模型；内部调用仍通过宿主工具执行入口。

1.0 为不存在的工具或 API 成员提供相近名称、发现入口和修正提示，为参数错误、存储超限及生成图片却未展示等情况提供恢复指导。原则是让模型知道下一步如何修正，而非只返回异常名称。Agentao 已有工具名称修复和部分带操作建议的错误信息，应在这些基础上完善，不必重复实现。

Codemode 对批量 MCP 操作和大结果处理有价值，但 Python 直接 `exec()` 无法提供同样的能力边界。引入时必须设计隔离、取消、资源限制，并保留每个内部工具调用的授权、审计、错误和用量统计。脚本存储成功不意味着外部工具副作用可以回滚。

Codemode 与工具按需加载是两项独立能力。可以直接用现有工具和真实任务验证批量调用与大结果处理是否值得引入脚本运行时，无须先建设工具搜索。分类器、图像生成及统一模型运行入口可以作为后续能力，不应因 Pi 支持就一起纳入。

参考：[Codemode README](https://github.com/earendil-works/pi/blob/v1.0.0/packages/codemode/README.md)、[1.0 错误恢复实现](https://github.com/earendil-works/pi/blob/v1.0.0/packages/codemode/src/runtime/prelude-source.ts)。

## 7. CLI 与提示词：改善细节，保留现有基础

Pi 1.0 默认全屏 TUI，同时保留 `--tui-mode regular`；`quietStartup: "header"` 仅显示版本和快捷键等必要信息。Agentao 可以先借鉴简洁启动、明确的运行状态和后台任务操作，再考虑可选全屏。

全屏模式更适合频繁管理多个 Agent、切换任务日志和审批。主要使用方式仍是一问一答时，滚动、复制、焦点和终端兼容的成本可能大于收益。Pi 的默认值不能替代 Agentao 的需求判断。

Agentao 已有 [稳定提示词前缀、动态尾部和分节 Token 诊断](../../agentao/prompts/builder.py)，工具定义也已稳定排序。后续重点应是减少重复说明、量化工具定义成本，并验证缓存命中，不能把这些基础能力误记为待补齐。

## 8. MCP Resources 与远程 Skills 必须区分

Pi 提供普通 MCP 资源工具：`list_mcp_resources`、`list_mcp_resource_templates`、`read_mcp_resource`，包含服务器来源、分页以及文本、图片、二进制内容处理。Agentao 当前缺少相应资源发现和读取入口；已有的工具结果资源块处理不能替代它们。

Pi 1.0 没有 `skills/list`、`skills/get` 或 `io.modelcontextprotocol/skills` 的宿主集成；本地 MCP 客户端支持版本列表最高为 `2025-11-25`。本地 `SKILL.md` 支持、普通资源读取与 MCP Skills 扩展是三件不同的事。Pi 可以作为 Resources 的参考，远程 Skills 需要按扩展规范另行设计。

参考：[Pi Resources 实现](https://github.com/earendil-works/pi/blob/v1.0.0/packages/coding-agent/src/extensions/mcp/resources.ts)、[Pi 协议类型](https://github.com/earendil-works/pi/blob/v1.0.0/packages/mcp/src/protocol/types.ts)。后续方案见 [mcp-resources.md](mcp-resources.md) 和 [mcp-skills.md](mcp-skills.md)。

## 9. 建议推进顺序

| 顺序 | 工作 | 先验证什么 |
| --- | --- | --- |
| 1 | 复现并修正 MCP 重试边界（已实施，见第 3 节） | 执行后断线是否造成重复调用 |
| 2 | 测量工具定义成本（已测量，见 tool-search.md“测量”一节；未满足触发条件） | 代表性任务的工具定义 Token |
| 3 | 按真实需求决定工具搜索或 OAuth 多账号隔离（OAuth 多账号已实施，见第 4 节；工具搜索仍推迟） | 是否满足 tool-search.md 的触发条件；是否有同 URL 双账号需求 |

后台任务恢复（Durable）、Codemode 和可选全屏 TUI 保留为观察项，出现真实需求后再评估。MCP Resources 与远程 Skills 已在 0.5.10 发布，不在此列。

这是当时的比较建议，不覆盖后续用户授权、产品需求或专项设计决定。实施前应按当前源码重新核实差距，避免重复建设或引用已经过时的行为。
