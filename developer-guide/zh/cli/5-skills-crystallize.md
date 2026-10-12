# 5. Skills 与 Crystallize

**Skill** 是一份 markdown 文件，教 agent 如何处理某一类特定任务。Agent 看到相关请求会自动激活对应 skill，你也可以手动激活。**Crystallize** 走的是反向 — 把一次成功的对话蒸馏成一份能复用的 skill。

## Skill 长什么样（30 秒）

每个 skill 是一个带 `SKILL.md` 的目录，放在三个位置之一（见 [Skill 文件去哪儿了](#skill-文件去哪儿了)）。`SKILL.md` 长这样：

```markdown
---
name: pdf-to-markdown
description: 用 marker_single 把 PDF 转成 markdown。命中"convert PDF to markdown"等触发词。
---

# PDF to Markdown

用户要 PDF → markdown 时：
1. 跑 `marker_single <path> --output_format markdown`
2. ...
```

YAML frontmatter 是 agent 决定要不要激活时读的。激活后，整份 SKILL.md body 进入每次请求的 `<system-reminder>`（易变尾部，不在缓存的系统提示里）。`references/`、`assets/`、`scripts/` 下的文件不会内联：激活时只按绝对路径列出，agent 需要时用 `read_file` 读取。

你不需要知道有哪些 skill — `/skills` 列得出，agent 自己识别。只在你想强制 / 抑制 / 新建一个 skill 时才介入。

## `/skills` — 列、激活、停用、reload

```text
> /skills

Available Skills (3):

  • pdf-to-markdown - PDF to Markdown
    用 marker_single 把 PDF 转成 markdown。命中"convert PDF to markdown"等触发词。...
  • skill-creator - Skill Creator
    ...

Active Skills:
  • pdf-to-markdown: 转换 report.pdf
```

列表给出每个已启用 skill 的名字、标题和描述。**Active Skills** 列出本会话已激活的 skill，以及激活时对应的任务。已禁用的 skill 单独成段。

| 子命令 | 作用 |
|---|---|
| `/skills` | 列出所有 skill，再列出已激活的 |
| `/skills activate <name>` | 强制激活，从此每次请求都带上正文 |
| `/skills deactivate <name>` | 从激活集移除，正文撤掉 |
| `/skills disable <name>` | **持久** — 写到 `skills_config.json`，重启后也不加载 |
| `/skills enable <name>` | 撤销 disable，让 skill 可加载 |
| `/skills reload` | 重扫全部三个 skill 目录。改了某个 `SKILL.md` 或加了新 skill 文件夹后用 |

`/clear` 和 `/new` 会取消所有已激活的 skill。`/status` 显示当前激活了几个。

`skills_config.json` 可以放心手改，两个 Agentao 进程也能共用。每次 `disable` / `enable` 在锁内只改一个名字，文件里其他内容原样保留。文件写坏了，命令会拒绝写入并报出路径——先修好文件再重跑命令。细节见 [5.2 节《`skills_config.json` 怎样写入》](/zh/part-5/2-skills#skills-config-json-怎样写入)。

### 来自 MCP server 的 Skill

MCP server 也能提供 skill，在 `.agentao/mcp.json` 里按 server 打开：

```json
{ "mcpServers": { "docs": { "url": "https://docs.example/mcp", "skills": true } } }
```

之后 `/skills` 里会多出一个 **MCP Skills** 分组：按 server 列出它的 skill，名字是完整的 `mcp:<server>:<SKILL.md URI>`，比如 `mcp:docs:skill://pdf-processing/SKILL.md`，并给出每个无法加载的 skill 的原因。和本地 skill 相比：

- **要你批准。** 模型激活时，每次对话问你一次，提示里有 server、描述、文件数和总大小。批准绑定的是 server 列出的那批文件；server 改了文件，会再问一次。`/skills activate mcp:<server>:<uri>` 本身就算你的同意，不会再问。
- **要校验。** `SKILL.md` 以及之后读取的每个文件，都必须和 server 公布的大小、SHA-256 摘要、frontmatter 一致，对不上就不用。
- **文件留在 server 上。** 模型用 `read_skill_file` 读取，从不读磁盘。
- **会收紧权限。** 从加载第一个 MCP skill 起直到 `/clear`，shell 命令、能跑 shell 的子代理、读取其他 server 的资源都会先问你，`full-access` 下也一样。取消激活并不会解除。

限制：需要 mcp 2.x、协议版本 2026-07-28 及以上的 server；每个 skill 最多 512 个文件、16 MiB；`SKILL.md` 最大 100,000 字节。超限的 skill 和 `"dynamic"` skill 在 `/skills` 里显示为不可用。宿主侧细节见 [5.3 节 MCP skills](/zh/part-5/3-mcp#mcp-skills)。

::: tip activate vs. enable
- **activate / deactivate** 是会话级 — 临时，只对*当前*这次对话生效
- **enable / disable** 是持久级 — 配置层面，影响所有未来会话

别混。`disable` 是更强的拒绝；`deactivate` 只是说"现在不要"。
:::

## 什么时候手动激活

多数情况不用。Agent 会读 skill 描述自己激活。你手动激活的场景：

- Agent 没识别出触发词（"激活 xlsx skill 来改我的电子表格"）
- 你自己想看 skill 的文档（你接下来会让 agent 跟着做）
- 在测试一个新写的 skill 能不能正常加载

激活成本低，但不是零：skill 处于激活状态时，SKILL.md body 每次请求都会不走缓存地重发。用完就 `/skills deactivate <name>`。skill 列表本身在缓存的系统提示里，激活不会改动它。

## `/crystallize` — 把这次会话做成 skill

一次会话里你和 agent 摸索出一个不显然的做法 — 跑 `/crystallize`。CLI 看一遍对话，起草一份 SKILL.md，让你迭代后再保存。

### 工作流

```
/crystallize           ──→  从会话里起草一份 skill
       │
       ├── /crystallize feedback "..."  ──→  按你的指引重写
       ├── /crystallize revise           ──→  交互式让你打反馈
       ├── /crystallize refine           ──→  交给 skill-creator 做结构性改进
       ├── /crystallize status           ──→  显示当前草稿
       ├── /crystallize clear            ──→  丢弃草稿
       └── /crystallize create [name]    ──→  存到 <作用域>/.agentao/skills/<name>/SKILL.md
```

### 子命令

| 命令 | 作用 |
|---|---|
| `/crystallize`（或 `/crystallize suggest`） | 分析会话，生成草稿。首次：纯分析。已有草稿：从头重新生成。 |
| `/crystallize feedback <text>` | 一句反馈（"触发词更具体些"）让它重写草稿。可以反复来。 |
| `/crystallize revise` | 同 `feedback`，但 CLI 弹交互让你输入。 |
| `/crystallize refine` | 把草稿交给 `skill-creator` skill 做结构化处理 — 修 frontmatter、收紧触发词、精炼正文。 |
| `/crystallize status` | 显示当前待保存的草稿和进行中的状态。 |
| `/crystallize clear` | 丢弃草稿（不保存）。 |
| `/crystallize create` | 保存草稿。默认名取自草稿 frontmatter 的 `name:`。 |
| `/crystallize create my-name` | 以 `my-name` 为名保存，名字会被规整成 slug。 |

`create` 会问你作用域：按 `g` 存到 `~/.agentao/skills/<name>/SKILL.md`，按 `p` 存到 `<项目>/.agentao/skills/<name>/SKILL.md`。`create` 后新 skill 自动加载，立刻可用 — `/skills` 看得到，agent 下一轮就能自激活。

### 什么时候值得 crystallize

| 情况 | 值得吗 |
|---|---|
| 花了 5 轮以上把 agent 推到一个特定做法上 | 值 — 把这种做法固化下来 |
| 写了一段长 prompt，跑通了一次 | 可复用就值；一次性的别 |
| 会话用到一个 agent 不熟的小众工具 / API | 值 — SKILL.md 就是 agent 的参考资料 |
| 做了一次普通重构 / bug 修复 | 不值 — 太特定或太通用，没什么可复用 |
| 用 `/plan` 出了一份漂亮计划 | 看情况 — 模板化的计划做成 skill 比留作一次性 plan 更有用 |

### 容易踩的坑

- **空会话上跑首次 `/crystallize` 没用** — 没素材。先做完事再 crystallize。
- **`feedback` 是累积的** — 每次 `/crystallize feedback` 都在带上以前所有反馈的基础上重写。一份草稿里没有 undo；要从头来就 `/crystallize` 重新生成。
- **`refine` 会盖掉你手编的部分** — 如果你已经手动改过草稿，再跑 `refine` 会让 LLM 走一遍 skill-creator pass，可能把你的改动磨平。`refine` 在手编**之前**用。
- **重名冲突** — `/crystallize create existing-name` 会直接覆盖该作用域里已有的 `SKILL.md`，不会提示。先跑 `/skills`，挑一个没用过的名字。

## 从 GitHub 安装 Skill

在 slash 命令之上，Agentao 还有一个顶层 shell 子命令用来管理来自公开 repo 的 skills。这个命令跑在 **REPL 之外** — 在你的 shell 提示符下用，不是 `>` 后面。

```bash
agentao skill install owner/repo[:path][@ref]
```

ref 格式：

| 形式 | 示例 | 意思 |
|---|---|---|
| `owner/repo` | `myorg/my-skill` | repo 根目录的 `SKILL.md`，或唯一一个含 `SKILL.md` 的子目录。有多个这样的子目录时视为有歧义，拒绝安装 |
| `owner/repo:path` | `anthropics/skills:skills/pdf` | 仓库内特定子目录 |
| `owner/repo@ref` | `myorg/myskills@v1.2.0` | 钉到 tag、分支或 commit SHA |
| `owner/repo:path@ref` | `anthropics/skills:skills/pdf@main` | 两个都要 |

包里的 `SKILL.md` 必须有 `name` 和 `description` frontmatter，否则拒绝安装。Anthropic 官方 skill 在 [`anthropics/skills`](https://github.com/anthropics/skills) 里，比如 `skills/pptx`、`skills/docx`、`skills/xlsx`、`skills/pdf`、`skills/doc-coauthoring`。

作用域（scope）：

```bash
agentao skill install anthropics/skills:skills/pdf --scope global
agentao skill install myorg/internal-skills:billing      --scope project
```

| Scope | 装到哪 | 什么时候用 |
|---|---|---|
| `global` | `~/.agentao/skills/` | 个人、跨项目 |
| `project` | `<项目根>/.agentao/skills/` | 团队共享、签入 repo |

省略 `--scope` 时 CLI 自动判断：从 cwd 向上能找到项目根（有 `.agentao`、`.git`、`pyproject.toml` 或 `package.json` 标记）就是 `project`，否则 `global`。

`--force` 覆盖同名已存在的 skill（默认拒绝覆盖）。

### 其他三个子命令

```bash
agentao skill list                  # Agentao 已知的全部
agentao skill list --installed      # 仅 'skill install' 管理的那些
agentao skill list --json           # 机器可读

agentao skill remove pdf            # 按名卸载
agentao skill remove pdf --scope global

agentao skill update pdf            # 检查更新并拉取
agentao skill update --all          # 跨作用域检查所有受管 skill
```

`update` 只对 `source_type` ≠ `manual` 的 skill 起作用 — 也就是 `skill install` 装进来的那些。手写 skill 不会被动到。

### 装完之后：让运行中的会话看见

`skill install` 把 SKILL.md 落到磁盘上；正在运行的 CLI 会话默认看不到，得告诉它一声。两个办法：

- **会话内**：`/skills reload` — 重扫 skills 目录
- **否则**：重启 `agentao` — 下次启动自然加载

可见后，这个 skill 的行为跟手写的完全一样：`/skills activate <name>` 把它拉进 prompt，或让 agent 在描述匹配时自激活。

### 容易踩的坑

- **未认证请求很快命中 GitHub 限流** — 一次装多个时设个 `GITHUB_TOKEN` 到 env
- **`@ref` 钉版本是你的朋友** — 不写的话每次 `update` 都重解析默认分支最新 commit，可能把意料之外的改动拉进环境
- **`skill install` 不激活** — 只把文件落盘，`/skills activate`（或自激活）仍是独立一步
- **两个作用域可能互相遮蔽** — 同名的 `pdf`，global 和 project 都装了 — 项目级胜出。用 `agentao skill list` 看哪个生效
- **运行时依赖不会自动装** — skill 可能需要 LibreOffice、Poppler、Node 或 Python 包，它的 `SKILL.md` 会写明。`skill install` 只复制 skill 本身的文件。
- **损坏的 `skills_registry.json` 绝不会被覆盖** — skill 命令会告警并把它当空文件读，但 `install`、`update`、`remove` 拒绝写入，记录因此不会丢。修好文件或把它挪开后再继续。单条不可用的记录会被跳过并告警，但仍留在文件里。

## 本仓自带的 Skill Gallery

本仓在 [`examples/skills/`](https://github.com/jin-bo/agentao/tree/main/examples/skills) 下带了一个小型 gallery — 与宿主无关、可直接拷进任意 discovery 位置使用的 skill。当起点用，或当写自己 skill 时的参考。

| Skill | 做什么 | 触发词 | 需要 |
|---|---|---|---|
| [`zootopia-ppt/`](https://github.com/jin-bo/agentao/tree/main/examples/skills/zootopia-ppt) | 把演讲稿做成 *拟人动物 3D 动画* 风格的 PPT 图集。流水线：大纲 → 每页图 prompt → 批量生成。 | "做这套 deck 用 Zootopia / 3D 动画风格" | `TENSORLAB_API_KEY`（默认后端）— 或 Gemini / Qwen / OpenRouter 的备选 |
| [`pro-ppt/`](https://github.com/jin-bo/agentao/tree/main/examples/skills/pro-ppt) | 同一条流水线，换成 *精英商务编辑* 风格（浅灰底 + 金色点缀 + 深海蓝，麦肯锡 / Apple-Keynote 调性）。**复用** `zootopia-ppt` 的脚本，要一起装。 | "做这套 deck 用商务 / 咨询 / 编辑风格" | 与 `zootopia-ppt` 相同 |
| [`ocr/`](https://github.com/jin-bo/agentao/tree/main/examples/skills/ocr) | 用 Qwen-VL 对单张图做 OCR。 | "OCR 这张截图" / "把这张图里的文字提出来" | `.env` 里 `QWEN_API_KEY` + `QWEN_BASE_URL` |

**安装**用 cp 或 symlink（这些不通过 `skill install` 从 GitHub 拉，它们就在本仓里）：

```bash
# 全局装（你从任何项目启动 agentao 都能看到）
cp -R examples/skills/ocr ~/.agentao/skills/

# 或者只装到一个项目
cp -R examples/skills/zootopia-ppt /path/to/your/project/.agentao/skills/
cp -R examples/skills/pro-ppt      /path/to/your/project/.agentao/skills/

# 两个 PPT skill 带 requirements.txt — Python 依赖装一次
# （ocr 不需要：脚本内置 PEP 723 行内元数据，`uv run` 自动解析）
pip install -r examples/skills/zootopia-ppt/requirements.txt
```

然后 `/skills reload`（或重启），新 skill 会出现在 `/skills` 列表里。

::: tip 图像生成 skill 是要花钱的
`zootopia-ppt` 和 `pro-ppt` 调付费图像生成 API（TensorsLab / Gemini / DashScope-Wan / OpenRouter）。生成 30 页前先跑个大纲看 token + 图量成本。所有 key 都不硬编码 — 读自 `.env` 或 `--api-key`。
:::

完整 gallery README — 包括嵌入式 harness 视角下"宿主耦合 skill vs 宿主无关 skill"的讨论 — 在 [`examples/skills/README.md`](https://github.com/jin-bo/agentao/blob/main/examples/skills/README.md)。

## Skill 文件去哪儿了

| 路径 | 用途 |
|---|---|
| `<项目>/skills/<name>/SKILL.md` | skill 本体（frontmatter + body）。这一层优先于下面两层 |
| `skills/<name>/references/`、`assets/`、`scripts/` | 按需读取的文件，激活时按绝对路径列出 |
| `~/.agentao/skills/<name>/` | 同样的目录结构，global 作用域（受管安装） |
| `<项目根>/.agentao/skills/<name>/` | 同样的目录结构，project 作用域（受管安装） |
| `~/.agentao/skills_registry.json` · `<项目根>/.agentao/skills_registry.json` | 跟踪受管安装（source ref、版本、scope） |
| `.agentao/skills_config.json`（项目） | 持久化的 enable/disable 状态 — 见 [10. 配置文件参考](./10-config-reference) |
| [`examples/skills/`](https://github.com/jin-bo/agentao/tree/main/examples/skills) | 仓库自带 gallery（拷进上面任一位置使用） |

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 看激活的 skill 占了多少上下文 | [7. 上下文与状态](./7-context-status) |
| 不用 crystallize，从头写 skill | [Part 5.2 · Skills](/zh/part-5/2-skills) |
| 用 Anthropic 的 `skill-creator` 写 skill | 激活 `skill-creator` 然后直接让它做 |

---

::: info 这一章在体系里的位置
Skill manager 是 `agentao.skills.manager.SkillManager`，挂在 agent 上是 `agent.skill_manager`。嵌入式宿主可以直接 `activate_skill()` / `deactivate_skill()` / 读 `available_skills`。SKILL.md 格式 CLI 和嵌入两条路径完全一致。
:::

::: tip 真相源头
命令语法：`/help`。Skill 列表：[`agentao/cli/ui.py:list_skills`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/ui.py)。Crystallize 逻辑：[`agentao/cli/commands_ext/crystallize/`](https://github.com/jin-bo/agentao/tree/main/agentao/cli/commands_ext/crystallize)。Skill manager：[`agentao/skills/manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/skills/manager.py)。
:::
