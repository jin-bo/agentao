# 工具搜索：按需加载的工具发现机制

**状态：** 设计草案。决策时间 2026-05-24。实现推迟，直到触发条件（见下文）满足。
**读者：** 关注 MCP / 插件工具数膨胀带来的工具列表预算压力的 agentao 维护者。
**配套：** `tool-search.md`（英文版）。

## 问题

`ToolRegistry.to_openai_format()`（`agentao/tools/base.py:244-263`）每轮都把所有
注册工具的 `{name, description, parameters}` 整体输出，按字母序排序以保证缓存
前缀稳定。所有原生 `Tool`、所有 `AsyncToolBase`、以及 MCP 桥接进来的工具
（`agentao/mcp/tool.py:71-81`）都走这同一份列表。

当前规模下（约 15 个原生工具 + 轻量 MCP）这工作得很好。下面这些场景下就不行了：

- 同时接入多个 MCP server（filesystem + github + slack + 自研 = 轻松 60+ 个工具）。
- 插件生态形成，每个插件常态贡献 5–15 个工具。
- 长会话嵌入场景，用户会话中途动态接入新的 connector。

随工具数线性增长的开销：

1. **初始 prompt 的 token**：每个工具的 name + description + JSON Schema 都在
   缓存前缀里，但仍占用模型的上下文窗口。
2. **选择准确率**：扁平的大工具列表会损害模型的工具选择准确率，业界通常引用的
   实践拐点在 50 个工具左右。
3. **schema 注入成本**：MCP server 经常输出冗长的 schema——每个数百 token——
   而模型很少会真的去看。

agentao 当前**没有任何机制**支持「工具注册可调用，但初始 schema 不暴露给模型」。

## Codex 的设计（参考）

Codex 的方案由四部分组成（2026-05-24 在 codex 仓库 grep 验证）：

1. **`ToolExposure` 枚举**（`codex-rs/tools/src/tool_executor.rs:8-27`）：
   - `Direct`——初始列表里就有，模型可见。
   - `Deferred`——注册到 runtime，初始列表里没有，由 `tool_search` 发现。
   - `DirectModelOnly`——初始列表里有，但 code-mode 嵌套表格里没有（与 agentao
     无关；agentao 没有 code-mode）。
   - `Hidden`——注册到 runtime，模型完全不可见。

2. **`tool_search` 工具**（`codex-rs/core/src/tools/handlers/tool_search.rs`）——
   一个 `Direct` 工具，handler 用 **BM25**（Rust `bm25` crate）对 deferred 工具的
   搜索文本排序，返回匹配的 `LoadableToolSpec` 给下一轮模型调用。

3. **决策规则**（`codex-rs/core/src/mcp_tool_exposure.rs:17-48`）：
   `should_defer = search_tool_enabled && (always_defer_flag || tool_count >= 100)`。
   Multi-agent v2 的 5 个工具家族（`SpawnAgent`、`SendInput`、`ResumeAgent`、
   `WaitAgent`、`CloseAgent`）在 search + namespace 都开时硬编码为 `Deferred`。
   原生工具（shell、apply_patch 等）永远 `Direct`。

4. **空集时不注册**：`append_tool_search_executor`
   （`codex-rs/core/src/tools/spec_plan.rs:780`）在没有任何 deferred 工具时跳过
   `tool_search` 的注册——低规模零成本。

配套的 `request_plugin_install` 工具处理「插件尚未安装」流程，依赖 codex 的插件
市场，本设计不涉及。

## 决策

agentao **采纳设计，但推迟实现**，直到出现真实触发信号。触发条件是二值的：

> 某个具体的嵌入场景报告了可度量的工具列表膨胀问题——token 预算压力、有 eval
> 证据的选择准确率退化、或用户可见的延迟——并且原因来自 MCP / 插件工具数。

在此之前，本文档是「束之高阁的规格」。过早实现会引入一个没有度量级用户的
工具暴露维度，让今后每次工具改动都更复杂，换来的是没人要求过的收益。

这个立场与 [Codex 反向评审](codex-reverse-review.md) 一致：codex 的设计是被
他们的 connector 市场 + multi-agent v2 驱动的；这两样 agentao 都没有。

## Schema（实现时）

### 1. `Tool` 上增加 exposure 维度

在 `_BaseTool` 上加一个 property，让 `Tool` 和 `AsyncToolBase` 都继承：

```python
class ToolExposure(str, Enum):
    DIRECT = "direct"     # 默认，保持当前行为
    DEFERRED = "deferred" # 注册可调用，但初始 schema 不暴露
    HIDDEN = "hidden"     # 注册可调用，永远模型不可见

class _BaseTool:
    @property
    def exposure(self) -> ToolExposure:
        return ToolExposure.DIRECT
```

跳过 `DirectModelOnly`——agentao 无 code-mode。

### 2. `ToolRegistry.to_openai_format()` 过滤

在现有列表推导（`agentao/tools/base.py:259-263`）里加一行条件：

```python
return [
    tool.to_openai_format()
    for tool in sorted(self.tools.values(), key=lambda t: t.name)
    if (plan_mode or tool.name not in self._PLAN_ONLY_TOOLS)
    and tool.exposure is ToolExposure.DIRECT
]
```

字母序排序的缓存前缀不变性保持不变。

### 3. `tool_search` 工具本身

一个 agentao 原生 `Tool`（不是 MCP，不是插件），ToolRegistry 在「至少有一个
注册工具的 `exposure == DEFERRED`」时自动注入：

```python
class ToolSearchTool(Tool):
    name = "tool_search"
    description = (
        "Search deferred tools by name/description. "
        "Returns matching tool specs; they become callable on the next turn."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 8},
        },
        "required": ["query"],
    }
```

**后端**：起步用 `rank-bm25`（PyPI，纯 Python，约 200 行），对
`{name} {description} {tags}` 建索引。原因：与 codex 同一算法、不引入 Rust 工具链、
未来质量不够可替换为 tfidf 或向量检索。每轮重建索引——百级工具规模是微秒级。

### 4. 激活模型

两个候选：

- **(a) 无状态**：`tool_search` 在返回结果里携带匹配的工具规格。模型下一轮直接
  按名调用，dispatcher 本来就能跑（工具一直注册着）。历史里上一条 `tool` 消息
  自然带着规格。
- **(b) 有状态**：`tool_search` 在 session 上标记「已提升」的工具；
  `to_openai_format` 在后续轮里把它们包含进来直到 session 结束。

**推荐 (a) 无状态**。更简单。没有 session 状态分歧。不需要设计 replay /
compaction 的交互。与 codex 行为一致——codex 也是通过搜索结果重新注入，不是
持久状态。

### 5. MCP 默认决策规则

照搬 codex 模式，阈值取适合 agentao 的值：

```python
DEFAULT_MCP_DEFER_THRESHOLD = 50  # codex 用 100；起点更低
```

原生工具默认 `Direct`。插件工具默认 `Direct`。MCP 工具总数过阈值时自动 defer，
**或者**宿主显式配置 defer。宿主可以通过 registry 逐个覆盖。

### 6. 宿主选项

加入嵌入式 harness 契约：

```python
agent = Agentao(
    ...,
    tool_exposure_policy=ToolExposurePolicy(
        auto_defer_mcp_threshold=50,
        always_defer_plugins=False,
    ),
)
```

默认策略：不到阈值不 defer。现有嵌入场景行为不变。

## 本文档不是什么

- **不是 code mode**。Code mode 是 codex 的另一个设计（一个 freeform `exec` 工具
  + 嵌入 V8）。agentao 走 chat-completions function-calling 路径，不支持 freeform
  工具，重构代价大；V8 嵌入也不在 roadmap 上。
- **不是插件市场**。Codex 的 `request_plugin_install` 流程假设有可安装插件市场。
  agentao 的插件模型是本地文件型。
- **不是向量检索系统**。BM25 在「这事真值得做」的规模（几十到低百量级工具）下
  足够了。
- **不是逃避工具预算讨论的借口**。如果某个嵌入场景真的工具列表炸了，第一反应
  通常是「砍 MCP server」或「把嵌入拆成专注的小 agent」，而不是「加
  `tool_search`」。

## 实现的触发条件

实现启动需要满足**其中一条**：

1. 真实 agentao 嵌入场景报告 >30 个 MCP 工具同时接入并出现可度量的 token 预算
   压力。
2. 经验观察到选择准确率回归（eval 套件证据：错误工具选择率随工具数上升）。
3. agentao 引入一等公民的插件工具注册机制，且插件常态贡献 5+ 工具。

在此之前本文档记录设计，确保触发时实现以日为单位、不是周。

## 待定问题

- **阈值默认值**。Codex 用 100。agentao 用户群偏向更小嵌入；50 可能仍偏高。
  等真实信号到了再定。
- **按 server 粒度**。宿主能否「defer 这个 MCP server，但 direct 那个」？
  大概率要支持；需要 registry 侧的 UX。
- **工具搜索结果的形态**。返回完整 schema（大但一次往返）还是「name + 简短描述」
  （模型还得追问拿 schema）？Codex 返回完整 `LoadableToolSpec`。默认完整；
  如果成本大于收益再回退。
- **索引生命周期**。每轮重建（便宜简单）vs 缓存 + registry 变更时失效
  （更快、更多代码）。起步用每轮重建。
- **与未来 `Hidden` 维度的交互**。`Hidden` 是 exposure 的一个值，还是正交标志？
  倾向正交：`exposure=DIRECT, hidden=True` 与 `exposure=DEFERRED` 语义不同。
  与 Hidden 提案（来自 codex 反向评审的 Worth-Considering 项）一起定。

## 引用

- **Codex 实现（2026-05-24 验证）：**
  - `codex-rs/tools/src/tool_executor.rs:8-27`——`ToolExposure` 枚举。
  - `codex-rs/core/src/tools/handlers/tool_search.rs`——BM25 handler。
  - `codex-rs/core/src/tools/handlers/tool_search_spec.rs`——工具 schema +
    模型可见描述。
  - `codex-rs/core/src/mcp_tool_exposure.rs:17-48`——MCP defer 决策。
  - `codex-rs/core/src/tools/spec_plan.rs:762-781`——`append_tool_search_executor`。
  - `codex-rs/core/templates/search_tool/tool_description.md`——模型可见 prompt。
- **Agentao 触及面：**
  - `agentao/tools/base.py:198-263`——`ToolRegistry.to_openai_format`。
  - `agentao/mcp/tool.py:71-81`——`McpTool` 的 schema 桥接。
- **相关 agentao 设计：** [codex-reverse-review.md](codex-reverse-review.md)。
- **BM25 实现：** `rank-bm25`（PyPI，MIT 许可，纯 Python）。
