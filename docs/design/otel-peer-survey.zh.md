# OpenTelemetry —— 同类 agent harness 横向调研

**状态：** 调研记录（rev 7，2026-08-15）。**零功能立项**，不构成对 roadmap P1.2 的启动决定 ——
`path-a-roadmap.zh.md` §4 的需求门纪律不变。本文只答**「有没有 OTel、放在哪一层」**，
不设计实现。

**读者：** 关注可观测性 / 企业侧接入 / 核心依赖重量的 agentao 维护者。

**配套：** 英文版 `otel-peer-survey.md` **待补**。

**相关：** `path-a-roadmap.zh.md`（P1.2 需求门所在）· `vendor-sdk-convergence-review.zh.md`
（D4 已判 table-stakes 但仍门控）· `../../developer-guide/zh/part-6/6-observability.md`
（本仓四轴叙述，其「4. 无 OpenTelemetry」由本文提供外部坐标）。

**方法：** 对 12 个 peer 克隆两轮 grep —— 先依赖清单，再源码引用；两轮**不互相替代**
（依赖命中可能是传递依赖，源码命中可能只在 test / sidecar）。命中仓人工读关键文件判类。
**判定「使用 OTel」的统一标准：自有 OTel 代码，或所用 wrapper 的依赖元数据证明其为 OTel
实现**（该标准对 lmnr / Langfuse 一视同仁 —— rev 6 曾在此双标，见勘误 #12）。
结论锚点均带 `file:line`；未命中记为「grep → no match」。

**锚点：** 见附录 A。agentao `main`@`353243d`。

**方法学警告：**

1. **claude-code-source-code 克隆陈旧**（2026-03-31），结论只对该快照成立。
2. **上游库未读源码**：Vercel AI SDK、`@effect/opentelemetry`、`lmnr`、`langfuse`。故凡经
   wrapper 出 span 的（opencode / OpenHands / hermes-agent 的 agent 面），**其最终属性本文
   未验证**，只陈述接入方式与依赖事实。
3. **gemini-cli 的 `telemetry/` 混了 ClearcutLogger（Google 一方分析）与 OTel**，只统计后者。
4. **不评估质量**：未评估任何一家的 span 划分、属性完备性、开销。
5. **「未发现」是 grep 结论且保质期短** —— hermes 曾在此列，rev 6 复核后翻转（勘误 #11）。
   两次上游复核**都改变了结论**，引用前请核对附录 A 的锚点日期。

**修订记录：** rev 1 初稿 → rev 2/4 两轮外部评审收窄 → rev 3 反向评审 → rev 5/6 openclaw 与
hermes 上游复核 → **rev 7 三轮外部评审收窄**（修正 hermes 分类的双标与「硬约束先例」的误判，
删除超出调研范围的展开）。**被推翻的结论见 §5，勿在无新证据时重提。**

---

## TL;DR

- **8/12 使用 OTel；1/12（pi-mono）用非 OTel 自建契约；3/12 未发现遥测实现。**
- **有决策意义的是分层，不是计数**（§2）：进程内 SDK 直连 / 核心外扩展 / 自建中立契约 /
  第三方 wrapper —— 四者成本与可逆性差一个数量级。
- **两个反直觉发现**（§3）：GenAI semconv **并未统一**；**「默认全关」不成立**
  （codex 的 metrics 默认开且默认发往 OpenAI 自有的 Statsig）。
- **结论只有三条**（§4）：不启动 P1.2；真做时默认关闭 + 端点显式配置 + 全部 OTel 依赖进
  extra；核心事件契约与 OTel 解耦，**JSONL 还是实时事件流待真实拓扑再定**。
- **§5 勘误表 12 条** —— 六轮修订中被推翻的结论，含两条分类性误判。

---

## 1. 结论总表

| Harness | OTel | 形态 | 关键证据 |
|---|---|---|---|
| **codex**（OpenAI） | ✅ 一等公民 | 独立 crate `codex-rs/otel`；三信号；OTLP gRPC / HTTP(proto+json) + TLS；W3C traceparent / tracestate 传播 | `codex-rs/Cargo.toml:370-374,468`；`otel/src/lib.rs:15-39`；`config/src/types.rs:549-570` |
| **gemini-cli**（Google） | ✅ 一等公民 | `packages/core/src/telemetry/`；**18 个 OTel 包**；exporter 覆盖 OTLP grpc/http、GCP、file、console；30+ 指标；用 **GenAI semconv** | `packages/core/package.json:29-52`；`telemetry/metrics.ts:30-77`；`constants.ts:13-27` |
| **Claude Code** | ✅ 一等公民 | 三信号 provider；`CLAUDE_CODE_ENABLE_TELEMETRY` 显式开关；exporter **全部动态 import**（注释：静态导入会每次启动加载 6 个变体约 1.2MB） | `src/utils/telemetry/instrumentation.ts:3-5,324-325` |
| **goose**（Block） | ✅ 一等公民，feature-gated | Cargo feature `otel`；库 crate `default = []` 不含，**goose-cli 的 `default` 含**；严格实现标准 `OTEL_*` 优先级链；span 用 `gen_ai.*` | `crates/goose/Cargo.toml:12,14-20`；`crates/goose-cli/Cargo.toml:81-92`；`otel/otlp.rs:224-249`；`agents/reply_parts.rs:312-319` |
| **opencode** | ✅ 轻量 | logs + traces；**无 endpoint 即整层 `Layer.empty`**；exporter 全动态 import；用 Proxy 包 tracer 给每个 span 打 `session.id` 再交给 AI SDK | `observability/otlp.ts:51,56`；`session/llm.ts:209-218,347` |
| **openclaw** | ✅ **在核心之外** | 扩展包 `extensions/diagnostics-otel/` 消费 plugin-SDK 的 `onDiagnosticEvent`；**核心运行时零 OTel 依赖**（根 `package.json` 唯一命中在 devDependencies）；三信号 + 5 recorder 域 + B3/Jaeger 传播；用 **GenAI semconv** | `extensions/diagnostics-otel/{package.json,api.ts:10}`；`src/service-genai-attributes.ts` |
| **OpenHands SDK** | ✅ 经 wrapper | 直接依赖 `lmnr`（PyPI 元数据：`opentelemetry-sdk` + OTLP http/grpc 双 exporter + instrumentation + semconv-ai），用 `@observe` 装饰 `agent.step` / `MCPToolExecutor.call_tool`；**无自有 OTel 代码** | `openhands-sdk/pyproject.toml:22`；`agent/agent.py:612,797`；`mcp/tool.py:70` |
| **hermes-agent** | ✅ **两个平面，两种形态** | **运维面**：自有 OTLP/HTTP exporter，导出 `gateway_health` / `gateway_diagnostic` / `cron_execution`；依赖为 optional extra `[otlp]`（注释：「never a core dependency and deliberately NOT in `[all]`」）、惰性导入、**不内置默认目的地**。**agent 面**（会话 / LLM / 工具）：经 `langfuse` wrapper（PyPI 元数据：`opentelemetry-api/sdk/exporter-otlp-proto-http`），opt-in 插件，缺 SDK 或凭证即 inert | `pyproject.toml:271-274`；`agent/monitoring/otlp_exporter.py:142-150`；`plugins/observability/langfuse/__init__.py:1052,1703` |
| **pi-mono** | ❌ 自建中立契约 | `@earendil-works/pi-telemetry` 自述 "Vendor-neutral telemetry contracts"；自有 schema + noop 实现；`@opentelemetry/api` 只在 `packages/ai/package.json`，**src 零引用** | `packages/telemetry/src/index.ts:1-72`；grep → no match |
| **qm** | ❌ | 仅 `package-lock.json` 传递依赖 | grep → 无源码引用 |
| **nanobot** / **OpenHarness** | ❌ | 0 处 | grep → no match |
| **agentao**（本仓） | ❌ | `pyproject.toml` grep → 0；`opentelemetry-api` 是 `mcp 2.0.0` 传递依赖，从未声明 | `uv.lock:1038-1046` |

**计数：8 使用 OTel**（自有代码 6：codex / gemini-cli / Claude Code / goose / opencode /
openclaw，其中 openclaw 在核心之外；经 wrapper 2：OpenHands、hermes-agent 的 agent 面）
**；1 非 OTel 契约**（pi-mono）**；3 未发现**（qm / nanobot / OpenHarness）。

---

## 2. 四种架构

「有没有 OTel」是坏问题 —— 四种形态的成本与可逆性差一个数量级。

**2.1 进程内 SDK 直连**（codex / gemini-cli / Claude Code / goose）—— 最完整也最重。三个可
量化代价：依赖重量（gemini-cli 18 个 OTel 包）；启动开销（Claude Code 专门动态 import 规避，
`instrumentation.ts:3-5` 注释算过约 1.2MB）；需要编译期/安装期开关（goose 用 Cargo feature
整块摘掉，**Python 侧对应物就是 extra**）。

**2.2 核心外扩展**（openclaw）—— 核心只发事件，OTel 完整住在可选扩展包里，14 个 OTel 包全
锁在扩展自己的 `dependencies`。**这提供最强的核心隔离边界**，但**不是唯一**兼容「核心依赖
精简 + local-first」的形态：进程内集成只要进 optional extra、延迟导入、默认关闭，同样不增加
bare install 依赖；差别是隔离**强度**与可逆性，不是可行性。注意**「核心外」是约束而非技术
路线** —— openclaw 走实时事件回调，roadmap P1.2 写的是「built on top of **P0.8 JSONL**」
（`path-a-roadmap.md:130`）；两者都保住核心零依赖，但在背压、进程边界、丢失语义上不同，
**本文证据不足以在二者间选边**。

**2.3 自建中立契约**（pi-mono）—— 不绑 OTel，自定义 span/attribute schema，默认 noop。成本是
自维护 schema 与类型工具，换来不绑供应商。**对 agentao 是参照**：本仓已有 `HostEvent` /
`AgentEvent` 这层契约，事实上已站在这条路上，只是没写 exporter。

**2.4 第三方 wrapper**（OpenHands → Laminar；hermes → Langfuse）—— 最省事：加一个依赖，源码
用装饰器标几个关键方法，无自有 OTel 代码。代价是把一家可观测性供应商引入依赖链，且属性形态
由上游决定（OpenHands 的 `llm/llm.py:2148` 已在为 `lmnr` instrumentor 细节打补丁）。
两家的**耦合强度不同**：OpenHands 把 `lmnr` 放进核心依赖并锁上下界；hermes 放进 opt-in 插件，
缺 SDK 或凭证即 inert —— **同一形态可以有很不同的可逆性。**

---

## 3. 两个反直觉发现

**3.1 GenAI semconv 并未统一。** 自身源码发 `gen_ai.*` 的有 3 家：gemini-cli
（`telemetry/constants.ts:13-27`，15 个常量）、goose（`agents/reply_parts.rs:312-319`）、
openclaw（`service-genai-attributes.ts`，8 个属性含 `cache_read`/`cache_creation` 扩展）。
**codex 明确不用**，走自有 `codex.*`（`otel/src/metrics/config.rs:17`；
`events/session_telemetry.rs:583,976,865`）。其余各家的属性由上游库铸造（AI SDK / lmnr /
langfuse），**本文未验证**，故不给采用率分数。含义：**semconv 不是既成共识，不构成
agentao 的准入门槛**；是否对齐由未来真实需求决定，本文不预判。

**3.2「默认全关」不成立。**

| Harness | 默认姿态 | 证据 |
|---|---|---|
| Claude Code | 关，需显式开关 | `instrumentation.ts:324-325`（`CLAUDE_CODE_ENABLE_TELEMETRY`） |
| opencode | 关（无 endpoint 即空实现） | `otlp.ts:51,56` |
| goose | 关（标准优先级链） | `otel/otlp.rs:224-249` |
| gemini-cli | 关（argv > env > settings） | `telemetry/config.ts:58-61` |
| hermes-agent | 关（**不内置默认目的地**） | `otlp_exporter.py:10-12` |
| **codex** | **logs/traces 关，metrics 默认开且发往 Statsig** | `config/src/types.rs:584-595`；`otel/src/config.rs:93` |

含义：「默认发指标给自己」是 vendor CLI 的姿态，不是 harness 的姿态。agentao 的 local-first
定位要求默认关 + 端点显式配置 —— **照 opencode / goose / hermes，不照 codex**。

---

## 4. 对 agentao 的含义

**不改变启动判断**（`vendor-sdk-convergence-review.zh.md` D4：re-prioritize, still gate）。
证据能支持的**三条**：

1. **当前不启动 P1.2。** 本文是外部坐标，不是需求信号。P1.2 自己的触发条件是
   「First enterprise user with concrete topology」（`path-a-roadmap.md:130`），
   「同类都有」不满足它。
2. **真做时的两条硬约束：**
   - **默认关闭、端点显式配置**（§3.2），与本仓 `no silent third-party proxy` 纪律同源。
   - **全部 OTel 依赖（API + SDK + exporter）进 extra，且凡直接 import 的都要显式声明。**
     `pyproject.toml:37` 允许 `mcp>=1.26.0,<3`，而 **mcp 1.x 不依赖 opentelemetry-api**
     （PyPI 复核：1.26.0 / 1.28.0 无，2.0.0 才有）；下游可解析到 1.x 且不受本仓 `uv.lock`
     约束。**即便锁在 2.x，靠传递依赖满足直接 import 本身就是分层违规。**
3. **核心事件契约与 OTel 解耦；消费 JSONL 还是实时事件流，等真实拓扑再定**（§2.2）。

**hermes 的双重参照价值**（rev 7 修正）：它是「optional extra + 惰性导入 + 无默认目的地」的
**首个 Python 侧先例**（`pyproject.toml:271-274`），但**不是**第 2 条的完整先例 ——
它直接 import `opentelemetry.trace` / `.metrics` / `._logs`（均属 `opentelemetry-api`），
而 `[otlp]` **只声明了 sdk + exporter**，api 仍靠传递依赖获得。
**它同时是那条分层纪律的先例与其反例**，正好演示了勘误 #4 那个陷阱有多容易踩。

---

## 5. 勘误表

六轮修订中被推翻的结论。**不要在无新证据时重提。**

| # | 被推翻的结论 | 出处 | 正确表述 | 锚点 |
|---|---|---|---|---|
| 1 | 「默认全关，无一例外」 | rev 1 | codex 的 `metrics_exporter` 默认 `Statsig`。只看字段是 `Option` 就外推默认值是错的，**必须读 `Default` 实现** | `config/src/types.rs:591` |
| 2 | 「opencode 不给 LLM 调用打 span」 | rev 1 | span 由 AI SDK 生产，opencode 用 Proxy 包 tracer。**grep 关键词选错会得出反向结论** | `session/llm.ts:209-218` |
| 3 | 「8/12 有某种形式的 OTel」 | rev 1 | 当时应为 7/12（误把标 ❌ 的 pi-mono 计入）。**勿与 rev 7 的 8/12 混淆** —— 后者分子构成完全不同 | §1 |
| 4 | 「API 面依赖增量为 0」 | rev 1 | **非 0。** mcp 1.x 不带 `opentelemetry-api`；且靠传递依赖满足直接 import 本身是分层违规。**`uv.lock` 是本仓一次解析，不是对下游的承诺** | `pyproject.toml:37` |
| 5 | 「replay 是 `subscribe()` 的在产消费者」 | rev 1 | **锚点引反。** `adapter.py:231-247` 是透传；replay 靠包装 `emit()` 记录。真消费者是 `cli/run.py:688` | `adapter.py:134-139` |
| 6 | 「核心外扩展 ＝ P1.2 的形态」 | rev 1 | 混淆两条路线。P1.2 写的是 **JSONL**，rev 1 选的是实时订阅。证据不足以选边 | `path-a-roadmap.md:130` |
| 7 | 「字段已经够了 / `LLM_CALL_COMPLETED` 已带 model」 | rev 1 | 与方法学警告冲突；该载荷**确实没有 model 名** | `runtime/llm_call.py:188-200` |
| 8 | 「无时间戳」是缺项 | rev 2 | **不成立。** replay JSONL 的 `ts` 必填，实时路线由消费者打戳。**为配合已推翻的结论收紧论证时，易多列不成立的证据** | `replay/schema.py:59,203` |
| 9 | 「子代理工具事件必然挂到父 agent，与路线正交，是第一个工作项」 | rev 3 | **全错。** 前台归属由消费者按括号事件恢复；后台子代理内部事件**根本不暴露**给父流；`child_task_id` 已在公共 host 面。**发射端字段缺失 ≠ 消费端能力缺失** | `adapter.py:535-551`；`_wrapper.py:500,506`；`host/models.py:115` |
| 10 | 「semconv 采用面 3/6」 | rev 5 | **分母是拍的**，未核对其余各家。已改为只陈述已验证的 3 家 + codex 反例，不给分数 | §3.1 |
| 11 | 「hermes-agent 无 OTel」 | rev 1–5 | **已翻转**（+7426 commits） | `agent/monitoring/otlp_exporter.py` |
| 12 | 「hermes 的 agent 面是非 OTel 通路，只有 7 家 trace agent loop」 | rev 6 | **双重标准。** 对 OpenHands 按「wrapper 内部是 OTel」计入，对 hermes/Langfuse 却排除；`langfuse` 依赖 `opentelemetry-api/sdk/exporter`。判定标准已统一写入「方法」。**另：运维面还导出 `cron_execution`，rev 6 只列两个是因为信了 docstring 而非代码**（同勘误 #2 的错误类） | `otlp_exporter.py:150`；langfuse PyPI 元数据 |
| 13 | 「hermes 是两条硬约束的首个 Python 先例」 | rev 6 | 只满足「extra + 惰性 + 无默认目的地」；它直接 import `opentelemetry.trace/.metrics/._logs` 却只声明 sdk + exporter，**恰恰违反**另一条 | `pyproject.toml:274`；`gateway_health_export.py` |

---

## 6. 需要进一步的分析

均**未做**，不应被当作已知：

1. **消费面选择**（JSONL vs 实时事件流）—— 决策所需输入（collector 部署位置、可否容忍事后
   落盘延迟、进程是否长驻）本文一个都没有。
2. **trace 树可行性** —— 只核对了字段存在性，未验证 tool span 与触发它的 LLM 调用如何关联、
   跨 `arun` 并发 turn 的 span 隔离。
3. **经 wrapper 出 span 的四家**（opencode / OpenHands / hermes agent 面 / Claude Code）
   最终属性形态未验证（方法学警告 2）。
4. **claude-code-source-code 上游现状未核**（克隆停在 2026-03-31）；**OpenHarness 的
   「未发现」锚点已超两个月**（2026-06-04）。
5. **成本侧未量化** —— 未测 exporter 在 agentao 事件频率下的开销。

---

## 附录 A：锚点

| 仓库 | HEAD | 日期 |
|---|---|---|
| hermes-agent | `bab9a85b67` | 2026-08-15 |
| openclaw（`origin/main`，工作区未切） | `ed106bdc7b1` | 2026-08-14 |
| codex | `2230d64464` | 2026-08-12 |
| goose | `e20cb8787` | 2026-08-11 |
| pi-mono | `936aff009` | 2026-08-09 |
| qm | `5eb3393` | 2026-08-03 |
| gemini-cli | `3818efbbf` | 2026-07-24 |
| nanobot | `aae259c7` | 2026-07-24 |
| software-agent-sdk（OpenHands） | `4fe56566` | 2026-07-17 |
| opencode | `34e5809059` | 2026-07-11 |
| OpenHarness | `9b2efd7` | 2026-06-04 ⚠️ |
| claude-code-source-code | `3da94d5` | 2026-03-31 ⚠️ 陈旧 |
| **agentao** | `353243d` | 2026-08-14 |

## 附录 B：可复现的 grep

```bash
# 轮 1 —— 依赖清单（含传递依赖，需人工甄别）
grep -ril "opentelemetry\|otel" --include="Cargo.toml" --include="package.json" \
  --include="pyproject.toml" --include="go.mod" . | grep -v node_modules

# 轮 2 —— 源码引用
grep -ril "opentelemetry" . | grep -v node_modules | grep -v "\.lock"

# 轮 3 —— 读命中文件判类。经 wrapper 的，查 wrapper 的依赖元数据（勘误 #12）：
curl -s "https://pypi.org/pypi/<wrapper>/json" | python3 -c \
  "import sys,json;d=json.load(sys.stdin);print([r for r in (d['info'].get('requires_dist') or []) if 'opentelemetry' in r.lower()])"

# 本仓自查：lock 查询【不足以】判断下游依赖（勘误 #4），需查约束区间内每个版本
grep -c "opentelemetry" pyproject.toml     # → 0（从未声明）
for v in 1.26.0 1.28.0 2.0.0; do
  curl -s "https://pypi.org/pypi/mcp/$v/json" | python3 -c \
    "import sys,json;d=json.load(sys.stdin);print('$v',[r for r in (d['info'].get('requires_dist') or []) if 'opentelemetry' in r.lower()] or 'NONE')"
done   # → 1.26.0 NONE / 1.28.0 NONE / 2.0.0 ['opentelemetry-api>=1.28.0']
```

**grep 关键词与证据类型的选择会决定结论**（勘误 #2、#12）：归类前必须读代码，不能只看
计数，也不能信 docstring。
