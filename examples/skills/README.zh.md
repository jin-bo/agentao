# Skills 画廊

> English: [README.md](./README.md)

宿主无关、即拷即用的 **skill 能力包**——可以让 Agentao agent 在运行时激活的「能力插件」。和父目录 [`examples/`](../README.md) 下的宿主集成示例（演示*如何把 Agentao 嵌入应用*）正好互补：那边是「壳」，这里是「料」。

一个 skill 就是一个目录，里面放一份 `SKILL.md`（YAML frontmatter + 说明文档），可选附带 `scripts/`、`reference/` 等辅助文件。Agentao 启动时由 `SkillManager` 按以下优先级扫描三个位置（[`agentao/skills/manager.py`](../../agentao/skills/manager.py)）：

1. `~/.agentao/skills/` — 全局（所有项目都可见）
2. `<项目目录>/.agentao/skills/` — 项目级配置
3. `<项目目录>/skills/` — 仓库内，优先级最高

## 当前画廊中的 skills

| 目录 | 用途 | 触发场景 | 依赖 |
|------|------|---------|------|
| [`zootopia-ppt/`](./zootopia-ppt/) | 把演讲稿转成「**拟人化动物 · 3D 动画电影**」风格的整套 PPT 配图。三步走：理解讲稿 → 逐页生成图像提示词 → 调用 `scripts/image_gen_ppt*.py` 批量出图。 | "用疯狂动物城 / 3D 动画电影风格做这份演示" | `TENSORLAB_API_KEY`（默认后端）；或 `GEMINI_API_KEY` / `QWEN_API_KEY` / OpenRouter key 走备选后端 |
| [`pro-ppt/`](./pro-ppt/) | 同一套流程的「**高端商务编辑**」风变体（极浅灰主色 + 金色点缀 + 深蓝灰背景，麦肯锡 / Apple Keynote 调性）。**复用** `zootopia-ppt/scripts/image_gen_ppt.py`，请一起安装。 | "做一份高级感 / 咨询风 / 编辑风的 PPT" | 同 `zootopia-ppt` |
| [`ocr/`](./ocr/) | 用 Qwen-VL 做一次性图片 OCR（`scripts/ocr.py`）。 | "把这张截图里的文字提出来 / OCR 这张图" | `.env` 里配 `QWEN_API_KEY` + `QWEN_BASE_URL` |

## 安装

挑上面三个发现位置中的**任意一个**，把 skill 目录拷贝或软链过去：

```bash
# 最省事：装到全局，任何项目都能用
cp -R examples/skills/ocr ~/.agentao/skills/

# 或者只对单个项目生效
cp -R examples/skills/zootopia-ppt /path/to/your/project/.agentao/skills/
cp -R examples/skills/pro-ppt      /path/to/your/project/.agentao/skills/
```

重启 Agentao（或在 CLI 中执行 `/skills`），新 skill 应出现在「可用列表」里。通过 `activate_skill` 工具或直接让 agent 使用它来激活。完整生命周期见 [docs/features/skills.md](../../docs/features/skills.md)。

## 关于图像生成额度

`zootopia-ppt` 和 `pro-ppt` 会调用付费的图像生成 API（TensorsLab / Gemini / DashScope-Wan / OpenRouter）。所有脚本一律从 `.env` 或 `--api-key` 读取密钥，**不会硬编码**。建议先跑一个短大纲估算成本，再批量出 30 页的整份演示。

## 安装（含依赖）

每个 skill 都自带 `requirements.txt`：

```bash
# 在你的项目 venv 里
pip install -r examples/skills/zootopia-ppt/requirements.txt
pip install -r examples/skills/ocr/requirements.txt
# pro-ppt 的 requirements.txt 通过 `-r ../zootopia-ppt/requirements.txt` 复用
```

`zootopia-ppt` 里的图像生成后端（`google-genai`、`dashscope` 等）是**互为替代**的——只装你要用的那一个，其他用 `#` 注释掉。

## 嵌入式 harness 视角 —— 看活的

Skills 不是和「嵌入式 harness」并列的另一套东西，而是它的一部分。宿主应用嵌入 Agentao 时，往往希望同时打包自己的领域 skill、自己的工具、自己的 `AGENTAO.md`。这个画廊是「宿主无关」的那一半；「**co-located**（就近放置）」的另一半，仓库里有三个宿主蓝图已经活生生地演示了：

| 宿主蓝图 | 就近放置的 skill | 为什么没法搬进画廊 |
|---------|---------------|------------------|
| [`data-workbench/`](../data-workbench/.agentao/skills/) | `duckdb-analyst`、`matplotlib-charts` | 紧耦合该蓝图的 `[CHART] <path>` 解析协议和 parquet workspace 布局 |
| [`ticket-automation/`](../ticket-automation/.agentao/skills/) | `support-triage` | 引用了该蓝图的 escalation matrix 和 `ConfidenceGatedEngine` 置信度阈值，离开就没意义 |
| [`batch-scheduler/`](../batch-scheduler/.agentao/skills/) | `daily-digest` | 绑死蓝图的 `RESULT: {...}` stdout 协议，cron 调度器靠这个解析 |

**判断准则**：可复用的 → 放进这个画廊；只在某个宿主里有意义的（依赖宿主的 tool / 输出协议 / 阈值） → 就近放在 `<host>/.agentao/skills/` 里。

## 贡献

实战里被验证过、靠得住的 skill，欢迎提 PR。一个 skill 适合放进这个画廊的标准：

- **宿主无关**——SaaS 机器人、Jupyter kernel、CLI 会话都能受益
- `SKILL.md` 有清晰的**触发描述**（agent 什么时候该想到它？）
- 辅助脚本只从 env / `.env` / CLI 参数读密钥，不要硬编码
- 篇幅可控，一遍读得懂

那种和具体宿主深度耦合的 skill（比如只在 `examples/slack-bot/` 里有意义的 thread-summarizer），应该就近放进对应的宿主示例目录，而不是放到这里。
