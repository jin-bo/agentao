# 把需求拆成原子卡 · Survey → Classify → Prescribe → Verify

> 本 playbook 是 SKILL.md Step 1 的展开，对应 `AGENTAO.md` 的 Code Review 思路（Survey → Classify → Prescribe → Verify）。

## 工作面规则

**动 CLI 之前**，先在对话中给用户一张表，等用户确认/调整再落卡。**不要**没确认就一口气 `card add` 5 张：

| # | title | goal | priority | depends_on | acceptance(关键 1-3 条) |
|---|---|---|---|---|---|

≥3 张时直接套 `playbooks/cards.template.yaml` 模板，让用户在 YAML 里直接改，最后一条命令落卡：

```bash
uv run python $SKILL_DIR/scripts/kanban_cards_from_yaml.py cards.yaml
```

## 拆卡四原则

### 1. 原子性

一张卡 = 一个**可独立完成**、**可独立验证**的产出。

| ❌ 不是卡 | ✅ 是卡 |
|---|---|
| "重构 X 模块" | "把 X.foo 拆成两个纯函数并迁移调用点" |
| "改善 ingest 性能" | "给 ingest.read_batch 加 LRU cache，benchmark 提升 ≥30%" |
| "整理文档" | "把 README §快速开始 拆出独立 docs/quickstart.md，并更新交叉引用" |

启发式：如果你写不出至少 1 条**人/CI 能直接判定**的 acceptance，这张卡还不够原子。

### 2. 依赖显式化

只有**真依赖**（B 必须等 A 的产出落地）才用 `--depends`。同主题不等于依赖。

- ✅ "B 用到了 A 新增的接口" → `B --depends A`
- ✅ "重构必须等迁移脚本跑完" → `重构 --depends 迁移`
- ❌ "都属于 ingest 主题" → 不写 depends；如果用户想看在一起，靠 priority 或 board view 而不是依赖
- ❌ "顺序无所谓但希望先做 A" → 用 priority 表达

依赖图越稀疏，并发度越高。**默认无依赖**，只在必须等时加。

### 3. acceptance 是验收凭据

每张卡至少 **1 条** `acceptance`，写成可被人/CI 直接判定通过/失败的语句：

| ❌ 弱 acceptance | ✅ 强 acceptance |
|---|---|
| "代码质量提升" | "`uv run ruff check kanban/ingest.py` 0 错" |
| "有测试覆盖" | "`uv run pytest tests/ingest -k reader` 至少 8 个 case 全绿" |
| "文档更新" | "`docs/ingest.md` 存在且包含 §API、§错误码 两章" |
| "性能更好" | "`benchmarks/ingest_bench.py` p50 < 50ms（基线 80ms）" |

worker / verifier 真跑时会按 acceptance 自检；写得越具体，自动判定越靠谱。

### 4. 优先级保守用

- **MEDIUM**（默认）— 绝大多数卡。
- **HIGH** — 这周必须出，或挡了下游卡。
- **CRITICAL** — 阻断其它卡 / 线上故障类。**别滥用**，否则信号失效。
- **LOW** — 想做但不急；通常该考虑直接砍掉。

## Survey → Classify → Prescribe → Verify 套路

走需求 → 卡片的四步：

### Survey（看清需求边界）

- 用户真正想要的产出是什么？拿在手里是个 PR、一份文档、一段数据，还是一个长期 dashboard？
- 已有什么？哪些子目标已经在仓库里完成了？（先 `grep` / 看 `git log`，避免重复造轮子。）
- 边界在哪？哪些**不在**这次范围内（写下来，免得被卷进卡里）。

### Classify（分类拆解）

把需求拆成离散类别。常见分类：

| 类别 | 典型标题 |
|---|---|
| **代码改动** | "把 X 拆成 Y/Z" / "给 W 加 cache" |
| **测试** | "给 X 写表格驱动单测" / "加端到端冒烟测试" |
| **文档** | "把 §快速开始 拆到 docs/quickstart.md" |
| **数据 / 一次性脚本** | "跑 schema 迁移脚本并落地结果到 workspace/data/" |
| **复盘 / 报告** | "整理本次迭代的复盘到 workspace/reports/" |

每类至少 1 张卡，避免一张卡跨类别（"改代码 + 写测试 + 改文档"是 3 张）。

### Prescribe（开方）

为每张卡写：

- **title**：动词开头，≤60 字；目标对象具体到文件/模块。
- **goal**：1-2 句，**为什么**做这件事 + 完成后能解锁什么。
- **acceptance**：1-3 条，强 acceptance（见原则 3）。
- **context**：把人/agent 看这张卡时**必须读**的文件/行号一并附上（`--kind required`）。可选参考用 `--kind optional`。
- **depends**：仅真依赖。

### Verify（落卡前自检）

- [ ] 每张卡能不能拎出来单独做？
- [ ] acceptance 是不是 CI/人能机械判定？
- [ ] 依赖图有没有环（CLI 会拒，但你也别浪费时间）？
- [ ] 是否有"重写整个模块"这种伪卡？拆细。
- [ ] 要不要给某些卡加 `context_refs` 让 worker 不用满仓库找？

## 反模式

- **"重构 X"** 单卡：永远拆细，不然 verifier 没有抓手。
- **`CRITICAL` 满天飞**：信号被稀释，真有线上故障时反而看不到。
- **`--depends` 把同主题串起来**：让并发度归零；该用 priority 或同 sprint 标签（外部追踪）。
- **acceptance 只有"完成"**：等于没写。
- **没确认就批量落卡**：不可逆（要么挨个 `block` + 删除 frontmatter，要么 `--force` 修板）。先表格 → 用户点头 → 再 `card add`。
