# 第七部分 · 典型集成蓝图

前六部分是参考手册，这一部分是**实用蓝图**——把沙箱、权限、事件、技能等能力编织成五个真实客户场景。

每个蓝图都回答同样的四个问题：

1. **谁 & 为什么** —— 什么产品形态、解决什么痛点
2. **架构** —— Agentao 坐在哪、跟谁通信
3. **关键代码** —— 真正重要的那 50–150 行
4. **陷阱** —— 上线第二天最容易出问题的地方

## 五个蓝图

| # | 蓝图 | 集成模式 | 明星扩展点 |
|---|------|----------|------------|
| [7.1](./1-saas-assistant) | SaaS 内置助手 | 进程内 SDK + FastAPI | 自定义工具 + PermissionEngine |
| [7.2](./2-ide-plugin) | IDE / 编辑器插件 | ACP stdio | session/load + request_permission |
| [7.3](./3-ticket-automation) | 客服 / 工单自动化 | 进程内 SDK | 打通 CRM 的自定义工具 |
| [7.4](./4-data-workbench) | 数据分析工作台 | 进程内 SDK | Shell + 沙箱 + 自定义技能 |
| [7.5](./5-batch-scheduler) | 离线批处理 / 定时任务 | `prompt_once` | 技能 + 调度器 |

## 如何阅读本部分

- **场景已经明确**：直接跳到对应的蓝图
- **还在犹豫**：7.1 是最典型的情形（内嵌助手），其余四种是特化
- 每个蓝图都会回链到相关参考章节，方便你按需下钻

## 可运行代码

五个蓝图以独立项目形式就放在主仓 [`examples/`](https://github.com/jin-bo/agentao/tree/main/examples) 下——每个子目录（`saas-assistant/`、`ide-plugin-ts/`、`ticket-automation/`、`data-workbench/`、`batch-scheduler/`）都是独立的 `uv run` / `npm run` 项目。每个蓝图页会链向它对应的子目录。

→ [从 7.1 SaaS 助手开始 →](./1-saas-assistant)
