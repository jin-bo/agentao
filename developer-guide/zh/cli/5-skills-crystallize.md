# 5. Skills 与 Crystallize

**Skill** 是一份 markdown 文件，教 agent 如何处理某一类特定任务。Agent 看到相关请求会自动激活对应 skill，你也可以手动激活。**Crystallize** 走的是反向 — 把一次成功的对话蒸馏成一份能复用的 skill。

## Skill 长什么样（30 秒）

每个 skill 是 `skills/` 下的一个目录，里面有 `SKILL.md`：

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

YAML frontmatter 是 agent 决定要不要激活时读的。激活后，整份 SKILL.md body 注入系统提示，body 里引用的 `reference/*.md` 也按需加载。

你不需要知道有哪些 skill — `/help` 列得出，agent 自己识别。只在你想强制 / 抑制 / 新建一个 skill 时才介入。

## `/skills` — 列、激活、停用、reload

```text
> /skills
Available skills (12):
  ✓ pdf-to-markdown   — 把 PDF 转成 markdown
    canvas-design     — 创作视觉海报和设计
  ✓ webapp-testing    — Playwright 网页测试
    ...
```

- ✓ = 本会话已激活
- 无标记 = 可用但未激活

| 子命令 | 作用 |
|---|---|
| `/skills` | 列出所有 skill，标记激活状态 |
| `/skills activate <name>` | 强制激活，把文档塞进系统提示 |
| `/skills deactivate <name>` | 从激活集移除，文档撤掉 |
| `/skills disable <name>` | **持久** — 写到 `skills_config.json`，重启后也不加载 |
| `/skills enable <name>` | 撤销 disable，让 skill 可加载 |
| `/skills reload` | 重扫 `skills/` 目录。改了某个 `SKILL.md` 或加了新 skill 文件夹后用 |

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

激活成本很低 — 只是把 SKILL.md body 塞进系统提示多花点 token。

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
       └── /crystallize create [name]    ──→  存到 skills/<name>/SKILL.md
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
| `/crystallize create my-name` | 保存到 `skills/my-name/SKILL.md`。名字必须 slug-friendly。 |

`create` 后新 skill 自动加载，立刻可用 — `/skills` 看得到，agent 下一轮就能自激活。

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
- **重名冲突** — `/crystallize create existing-name` 会拒绝而不是覆盖。换名或者先 `/skills disable` 旧的。

## 从 GitHub 安装 Skill

在 slash 命令之上，Agentao 还有一个顶层 shell 子命令用来管理来自公开 repo 的 skills。这个命令跑在 **REPL 之外** — 在你的 shell 提示符下用，不是 `>` 后面。

```bash
agentao skill install owner/repo[:path][@ref]
```

ref 格式：

| 形式 | 示例 | 意思 |
|---|---|---|
| `owner/repo` | `anthropics/skills` | 整个 repo 的 `SKILL.md`（或顶层 `skills/`） |
| `owner/repo:path` | `anthropics/skills:document-skills/pdf` | 仓库内特定子目录 |
| `owner/repo@ref` | `myorg/myskills@v1.2.0` | 钉到 tag、分支或 commit SHA |
| `owner/repo:path@ref` | `anthropics/skills:document-skills/pdf@main` | 两个都要 |

作用域（scope）：

```bash
agentao skill install anthropics/skills:document-skills/pdf --scope global
agentao skill install myorg/internal-skills:billing      --scope project
```

| Scope | 装到哪 | 什么时候用 |
|---|---|---|
| `global` | `~/.agentao/skills/` | 个人、跨项目 |
| `project` | `<cwd>/skills/` | 团队共享、签入 repo |

省略 `--scope` 时 CLI 自动判断（cwd 下存在 `skills/` 就是 project，否则 global）。

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

# 每个 skill 带 requirements.txt — Python 依赖装一次
pip install -r examples/skills/zootopia-ppt/requirements.txt
pip install -r examples/skills/ocr/requirements.txt
```

然后 `/skills reload`（或重启），新 skill 会出现在 `/skills` 列表里。

::: tip 图像生成 skill 是要花钱的
`zootopia-ppt` 和 `pro-ppt` 调付费图像生成 API（TensorsLab / Gemini / DashScope-Wan / OpenRouter）。生成 30 页前先跑个大纲看 token + 图量成本。所有 key 都不硬编码 — 读自 `.env` 或 `--api-key`。
:::

完整 gallery README — 包括嵌入式 harness 视角下"宿主耦合 skill vs 宿主无关 skill"的讨论 — 在 [`examples/skills/README.md`](https://github.com/jin-bo/agentao/blob/main/examples/skills/README.md)。

## Skill 文件去哪儿了

| 路径 | 用途 |
|---|---|
| `skills/<name>/SKILL.md` | skill 本体（frontmatter + body） |
| `skills/<name>/reference/*.md` | body 引用的按需加载文档 |
| `~/.agentao/skills/<name>/` | 同样的目录结构，global 作用域（受管安装） |
| `~/.agentao/skills/registry.json` · `<cwd>/skills/registry.json` | 跟踪受管安装（source ref、版本、scope） |
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
命令语法：`/help`。Skill 列表：[`agentao/cli/ui.py:list_skills`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/ui.py)。Crystallize 逻辑：[`agentao/cli/commands_ext/crystallize.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/crystallize.py)。Skill manager：[`agentao/skills/manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/skills/manager.py)。
:::
