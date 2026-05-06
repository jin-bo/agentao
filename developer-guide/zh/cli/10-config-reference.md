# 10. 配置文件参考

这是一个 CLI 视角的**索引**页 — CLI 会读的所有配置文件都在这。每个文件的 schema 详情都在 [`docs/CONFIGURATION.zh.md`](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md) 里，下表每行直接链到那边对应章节。

## 配置面总览

| 文件 | 项目路径 | 用户全局路径 | 谁在读 | Schema 参考 |
|---|---|---|---|---|
| **LLM 凭证** | `.env`（cwd） | shell env | `/model` `/provider` `/temperature` | [§2](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#2-env--llm-provider-配置) |
| **运行时设置** | `.agentao/settings.json` | — | `/mode` 持久化、`/replay on/off` | [§3](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#3-agentaosettingsjson--运行时模式--内置子代理) |
| **权限规则** | — *（项目文件被忽略）* | `~/.agentao/permissions.json` | `/mode` `/permission`、工具确认 UI | [§4](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#4-permissionsjson--工具权限规则) |
| **Shell 沙箱** | `.agentao/sandbox.json` | `~/.agentao/sandbox.json` | `/sandbox` | [Part 6.2](/zh/part-6/2-shell-sandbox) |
| **MCP 服务器** | `.agentao/mcp.json` | `~/.agentao/mcp.json` | `/mcp` | [§5](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#5-mcpjson--mcp-服务器注册表) |
| **ACP 服务器** | `.agentao/acp.json` | — | `/acp` | [§6](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#6-acpjson--acp-子代理注册表) |
| **Skill 启停** | `.agentao/skills_config.json` | — | `/skills enable` `/skills disable` | [§7](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#7-skills_configjson--每个项目的-skill-启停) |
| **项目说明** | `AGENTAO.md`（cwd） | — | 每轮的系统提示 | [§8](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#8-agentaomd--项目说明) |
| **记忆库** | `.agentao/memory.db` | `~/.agentao/memory.db` | `/memory`、`save_memory` 工具 | [§9](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#9-memorydb--持久化记忆库) |

## "改 X 改哪" 速查表

| 想改的 | 改这里 |
|---|---|
| 默认模型 / API key | `.env`（`OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL`） |
| 加第二个 provider | `.env`（`GEMINI_API_KEY` 等 — 见[第 2 章](./2-models-providers)） |
| 默认温度 | `.env`（`LLM_TEMPERATURE`） |
| 新会话默认权限模式 | `.agentao/settings.json` → `mode`*（"上次记住的模式"，[§3](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#3-agentaosettingsjson--运行时模式--内置子代理)）* |
| 加放行 / 拒绝的 shell 命令 | `~/.agentao/permissions.json` |
| 加放行 / 拒绝的 web 域名 | `~/.agentao/permissions.json` |
| 默认沙箱 profile（macOS） | `.agentao/sandbox.json` 或 `~/.agentao/sandbox.json` → `default_profile` |
| 默认上下文窗口大小 | 环境变量 `AGENTAO_CONTEXT_TOKENS` |
| 默认是否录 replay | `.agentao/settings.json` → `replay.enabled` |
| Replay 保留实例上限 | `.agentao/settings.json` → `replay.max_instances` |
| 加 MCP 服务器 | `.agentao/mcp.json`（或 `/mcp add` — 写到同一文件） |
| 加 ACP 服务器 | `.agentao/acp.json` |
| 全局禁用某个有问题的 skill | `.agentao/skills_config.json`（或 `/skills disable <name>` — 同一文件） |
| 项目级、agent 永远要遵守的事 | `AGENTAO.md`（或从 [`examples/personas/`](https://github.com/jin-bo/agentao/tree/main/examples/personas) 拷一个） |

## 运行时改动 vs. 配置文件

部分 slash 命令的改动是**会话级**的，不会写到磁盘：

| Slash 命令 | 是否持久化 | 持久化到哪 |
|---|---|---|
| `/model <name>` | 否 | —（用 `.env` 里 `OPENAI_MODEL` 设默认） |
| `/provider <name>` | 否 | —（设 provider env 三元组） |
| `/temperature <n>` | 否 | `.env`（`LLM_TEMPERATURE`） |
| `/mode <mode>` | 否 | `.agentao/settings.json`（"上次记住的"） |
| `/context limit <n>` | 否 | —（仅当前进程；重启后读 `AGENTAO_CONTEXT_TOKENS`） |
| `/sandbox profile <name>` | 否 | —（要持久化，改 `sandbox.json`） |
| `/replay on` / `/replay off` | **是** | `.agentao/settings.json`（`replay.enabled`） |
| `/skills disable <name>` / `/skills enable <name>` | **是** | `.agentao/skills_config.json` |
| `/skills activate` / `/skills deactivate` | 否 | — |
| `/mcp add` / `/mcp remove` | **是** | `.agentao/mcp.json`（项目） |
| `/memory delete` / `/memory clear` | **是** | `memory.db`（软删） |

绝大多数"感觉是临时的"命令就是设计成临时的。要让设置跨会话保留，去改文件。

## 优先级速读

三件事记住：

1. **Permissions 仅用户级**。项目级 `permissions.json` 一旦生效，clone 进来的任何 repo 都能覆盖你的安全策略，所以 loader **忽略**它（带 warning）。改个人规则就改 `~/.agentao/permissions.json`。
2. **MCP 合并，但项目级仅可新增**。项目级条目能声明*新的* server name，不能把用户级定义的 `github` 重定向到不同 transport。同名冲突会跳过项目项 + warning。
3. **Memory 按作用域独立**。Project DB 和 user DB 都注入 prompt，project 不覆盖 user。用 `/memory user` 和 `/memory project` 分别看。

完整优先级规则见 [`docs/CONFIGURATION.zh.md` §1](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md#1-配置面总览)。

## `AGENTAO.md` Persona Gallery

不想从空白开始写 `AGENTAO.md`，本仓在 [`examples/personas/`](https://github.com/jin-bo/agentao/tree/main/examples/personas) 下带了一个小型 persona gallery。每个 persona 就是一份 `AGENTAO.md`，拷进你的项目根目录即可。

| Persona | 风格 | 适合 |
|---|---|---|
| [`daily-driver/`](https://github.com/jin-bo/agentao/blob/main/examples/personas/daily-driver/AGENTAO.md) | 证据优先、隐私意识、工作目录井然有序 | 日常研究 / 编码助手 |
| [`kawaii-buddy/`](https://github.com/jin-bo/agentao/blob/main/examples/personas/kawaii-buddy/AGENTAO.md) | 可爱、中英混杂闲聊、关心你心情 | 情绪价值口袋助手 |

```bash
# 选一个，拷到你启动 agentao 的项目根
cp examples/personas/daily-driver/AGENTAO.md /path/to/your/project/AGENTAO.md
```

`AGENTAO.md` 每轮都会重新组进系统提示 — 改完下一条消息生效，不必重启。Gallery 当起点用，不是契约；放心改写。

## 内部状态文件

`.agentao/` 下存在但**不要**手动编辑的文件：

| 路径 | 用途 |
|---|---|
| `.agentao/background_tasks.json` | 子 agent 状态；内存里有镜像 |
| `.agentao/replay/*.jsonl` | Replay 录制 |
| `.agentao/sessions/` | 单次会话产物 |
| `.agentao/plan.md`、`.agentao/plan-history/` | Plan 模式状态 |
| `.agentao/tool-outputs/` | 工具输出缓存 |

CLI 跑着的时候改它们会让状态对不上。非要改，先停 CLI。

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 上面任意文件的完整 schema | [`docs/CONFIGURATION.zh.md`](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md) |
| 有意识地改默认权限规则 | [Part 5.4 · 权限引擎](/zh/part-5/4-permissions) |
| 给项目写一份 `AGENTAO.md` | [Part 5.6 · 系统提示定制](/zh/part-5/6-system-prompt) |

---

::: info 这一章在体系里的位置
嵌入式宿主加载这些文件的方式完全相同。`.env` 变成构造器 kwargs（你也可以直接传），`permissions.json` 由同一个 `PermissionEngine` 消费，`mcp.json` / `acp.json` 由同一个 manager 处理。"宿主以代码方式配" 与 "用户改文件" 之间的边界由你的应用自己决定。
:::

::: tip 真相源头
Schema：[`docs/CONFIGURATION.md`](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.md)（英文）· [`docs/CONFIGURATION.zh.md`](https://github.com/jin-bo/agentao/blob/main/docs/CONFIGURATION.zh.md)（中文）。Loader：见上表中的链接。Schema 文件是字段名和默认值的唯一权威，本索引只告诉你该看哪一行。
:::
