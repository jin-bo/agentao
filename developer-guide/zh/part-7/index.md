# 第七部分 · 典型集成蓝图

前六部分是参考手册，这一部分是**实用蓝图**——把沙箱、权限、事件、技能等能力编织成七个真实客户场景。

每个蓝图都回答同样的四个问题：

1. **谁 & 为什么** —— 什么产品形态、解决什么痛点
2. **架构** —— Agentao 坐在哪、跟谁通信
3. **关键代码** —— 真正重要的那 50–150 行
4. **陷阱** —— 上线第二天最容易出问题的地方

::: info 本部分关键词
- **内嵌助手（In-product assistant）** —— 嵌入既有 SaaS UI 的对话/Agent；最常见的形态 · [§7.1](/zh/part-7/1-saas-assistant)、[G.4](/zh/appendix/g-glossary#g-4-集成模式)
- **IDE 插件（ACP）** —— 宿主 = 编辑器，Agent = 讲 ACP 的子进程；使用 `session/load` + `request_permission` · [§7.2](/zh/part-7/2-ide-plugin)、[G.3](/zh/appendix/g-glossary#g-3-acp-相关术语)
- **工单自动化** —— 从队列读消息的异步处理器；`prompt_once` 形式，无流式 UI · [§7.3](/zh/part-7/3-ticket-automation)、[G.4](/zh/appendix/g-glossary#g-4-集成模式)
- **数据工作台** —— 给分析师的交互会话；Shell + 沙箱 + 技能组合 · [§7.4](/zh/part-7/4-data-workbench)
- **批处理调度** —— cron 驱动的 `prompt_once`，跑离线/夜间任务，没有终端用户 · [§7.5](/zh/part-7/5-batch-scheduler)、[G.4](/zh/appendix/g-glossary#g-4-集成模式)
- **微信智能机器人（ilink-style）** —— 长轮询个人号 bot API；每条消息一个 agent；联系人级权限 · [§7.6](/zh/part-7/6-wechat-bot)
- **多 Agent 看板调度** —— 外部衍生项目 `agentao-kanban`；用看板驱动 `planner` / `worker` / `reviewer` 三个 sub-agent；每张卡独立 git worktree · [§7.7](/zh/part-7/7-kanban-multiagent)
:::

## 七个蓝图

| # | 蓝图 | 集成模式 | 明星扩展点 |
|---|------|----------|------------|
| [7.1](./1-saas-assistant) | SaaS 内置助手 | 进程内 SDK + FastAPI | 自定义工具 + PermissionEngine |
| [7.2](./2-ide-plugin) | IDE / 编辑器插件 | ACP stdio | session/load + request_permission |
| [7.3](./3-ticket-automation) | 客服 / 工单自动化 | 进程内 SDK | 打通 CRM 的自定义工具 |
| [7.4](./4-data-workbench) | 数据分析工作台 | 进程内 SDK | Shell + 沙箱 + 自定义技能 |
| [7.5](./5-batch-scheduler) | 离线批处理 / 定时任务 | `prompt_once` | 技能 + 调度器 |
| [7.6](./6-wechat-bot) | 微信智能机器人（ilink-style） | `asyncio` 长轮询 daemon | `WeChatClient` Protocol + 联系人级权限 |
| [7.7](./7-kanban-multiagent) | 多 Agent 看板调度 *(外部仓库)* | 外部 orchestrator 驱动 agentao sub-agent + ACP CLI | `planner`/`worker`/`reviewer` 路由 + 每卡 git worktree |

## 如何阅读本部分

- **场景已经明确**：直接跳到对应的蓝图
- **还在犹豫**：7.1 是最典型的情形（内嵌助手），其余五种是特化
- 每个蓝图都会回链到相关参考章节，方便你按需下钻

## 按产品形态选择

| 你正在做的产品 | 先读 | 为什么 |
|---------------|------|--------|
| SaaS 页面里的对话助手 | [7.1](./1-saas-assistant) | 覆盖 Web UI、FastAPI、工具和权限的主路径 |
| IDE、编辑器或桌面宿主 | [7.2](./2-ide-plugin) | ACP、stdio、权限请求和会话加载是核心差异 |
| 队列驱动的后台自动化 | [7.3](./3-ticket-automation) | 重点是幂等、重试、CRM 工具和无人值守策略 |
| 分析师工作台或 Notebook 类产品 | [7.4](./4-data-workbench) | 重点是 Shell、沙箱、文件隔离和分析技能 |
| 夜间任务、报表或离线处理 | [7.5](./5-batch-scheduler) | 重点是 `prompt_once`、调度、预算和失败处理 |
| IM / 微信 / 企业消息机器人 | [7.6](./6-wechat-bot) | 重点是长轮询、联系人级权限和每条消息的隔离 |
| 围绕"工作队列"的多 Agent 系统（CI、批量评估、自主研究） | [7.7](./7-kanban-multiagent) | 重点是外部 orchestrator、跨 sub-agent 与 ACP CLI 的角色路由、每任务隔离 |

## 可运行代码

7.1–7.6 以独立项目形式就放在主仓 [`examples/`](https://github.com/jin-bo/agentao/tree/main/examples) 下——每个子目录（`saas-assistant/`、`ide-plugin-ts/`、`ticket-automation/`、`data-workbench/`、`batch-scheduler/`、`wechat-bot/`）都是独立的 `uv run` / `npm run` 项目。每个蓝图页会链向它对应的子目录。

7.7 在独立仓库 [`jin-bo/agentao-kanban`](https://github.com/jin-bo/agentao-kanban)——它是按自己节奏发版的衍生项目，不在本仓 examples 下。

→ [从 7.1 SaaS 助手开始 →](./1-saas-assistant)
