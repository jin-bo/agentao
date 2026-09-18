# 多线路协议支持：`anthropic-messages`、`openai-responses` 与 `gemini-api`

**状态：** **阶段 0 已实施，随 0.4.26 发布。阶段 1 已于 2026-09-18 实施（已随 0.5.0 发布），
验证对象是脚本化的 socket，随后是真实端点（见下文「真实端点实测」）；阶段 3 里「切换 provider」那一块同日跟进。
阶段 2–3 的其余部分仍是提案、未授权。rev 15（2026-09-18）。** §2.3 的阶段 0a 与 0b 已在 `main`；阶段 1 在 `LLMClient` 之下加了适配器
接缝，以及第二条线路 `anthropic-messages`，启动时选定、或由切换 provider 改变。§12.1 —— 阶段 0 是否让适配器变得
不必要 —— **没有**先靠实测回答：维护者在阶段 0 的账单对比仍欠着的情况下授权了阶段 1，这笔
欠账顺延（见「阶段 1 落了什么」末段）。除此之外，本文记录接缝在哪、放接缝的三个选项、推荐
方案、agentao 自身历史格式里已核实的翻译陷阱，以及分阶段计划。

**阶段 0 落了什么**（`prompts/builder.py`、`agent.py`、`runtime/chat_loop/_runner.py`、
`runtime/llm_call.py`、`context_manager.py`、`llm/_cache_control.py`、`llm/client.py`、
`embedding/factory.py`；`tests/test_volatile_tail_request.py`、
`tests/test_prompt_cache_breakpoints.py`）：

- **0a** —— 技能清单、激活技能全文、todos、`<memory-context>` 与 plan 提示从 system 消息
  移出，改为一条仅属于请求的 `<system-reminder>` 尾消息，在**唯一一处**收口追加
  （`_call_llm_with_overflow_recovery` 里的 `_send`，全包唯一调用 `agent._llm_call` 的地方），
  因此七个装配点照旧只拼持久前缀，谁也不可能丢掉或重复一条它们根本不持有的尾消息。锚点
  修正同批落地：`record_api_usage(prompt_tokens, len(persistent), tail_tokens=est(T))`，
  §11 的漂移门做成了一条跑 12 轮、刻意变动尾消息大小的测试。
- **0b** —— opt-in 的显式 `cache_control`（`LLM_PROMPT_CACHE=anthropic`、`prompt_cache=`），
  最多 3 个断点、保留第 4 槽、copy-on-mark，在 `_build_request_kwargs` 里施加（位于 replay
  之下），且按调用 opt-in（所以摘要调用不打标记）。默认关闭：端点是否认这个键仍未核实，
  而这正是本设计说过不能假设的那一件事。

**阶段 0 自己的验收门还缺什么：** 在真实端点上实测缓存命中率与成本 —— 0a 对比改造前、0b
对比 0a。目前本机的测量是结构性的、不是账单：system 消息 2,350 tokens 且已逐轮逐字节相同，
对应一条约 1.8k tokens 的重发尾消息，其中 1,779 是可用技能清单。这个比例是仓库相关的，也是
最该先核的数字 —— 见 §13 的「停在阶段 0」。

**后续，0.4.27 —— 技能清单搬回了前缀。** 它是尾消息里最大的一项，而它本不必易变：它之所以
易变，只是因为它只列**未激活**的技能，每次激活都会改写它。现在它列出每一个已启用的技能、
不论是否激活，位于 system 消息里、`<memory-stable>` 之前；它只随启用集合变化，而那类事件
本来就会改写工具块里 `activate_skill` 的枚举。pi-mono（`system-prompt.ts`）和 gemini-cli
（`promptProvider.ts`）也是这样列的，都不会在技能用过之后把它移出清单；codex 只在清单变化时
追加一条 `developer` 更新。在本机（盘上 14 个技能）重测：system 消息约 5.2k tokens，尾消息
为空。尾消息里剩下的大项是**已激活技能的正文** —— 一个技能约 4.1k tokens／请求 —— 而这一项
不是无代价的搬动：放进前缀，每次激活要付一次整段历史的缓存失效，所以取决于技能通常在会话的
什么时点被激活，这一点尚无数据。做账单实测时，请记录每次激活时的历史长度。

**阶段 1 落了什么**（`llm/_api_format.py`、`llm/_openai_completions.py`、
`llm/_anthropic_messages.py`、`llm/client.py`、`llm/_stream_response.py`、
`runtime/chat_loop/_serialize.py`、`runtime/chat_loop/_runner.py`、`runtime/model.py`、
`context_manager.py`、`agent.py`、`embedding/factory.py`、`agents/tools/_wrapper.py`、
`cli/commands/provider.py`；`tests/test_llm_api_extraction_noop.py`、
`tests/test_anthropic_messages_adapter.py`、`tests/test_anthropic_messages_runtime.py`、
`tests/support/anthropic_wire.py`）：

- **接缝。** `LLMClient` 保留重试/退避循环、日志和 token 累计；适配器拥有请求形状、线路调用、
  流循环、一次性请求修复和重试分类。Chat Completions 路径逐句搬进了
  `OpenAICompletionsAdapter`，它构造的请求与 `main@a2c8c6d`（抽取之前）抓取的样本保持
  **逐字节相同**（§10 要的回归证据，同一个 PR）。当前工作接口是 `create_client /
  build_request / log_view / send / new_accumulator / consume_stream / repair_request /
  classify_retry`（外加 `reset_latches`）。§5.1 草图里的 `describe_error` 和 `purge_keys`
  **没有**做成方法：溢出检测和上限解析本来就是基于字符串的、且已经匹配 Anthropic 的文本；清洗
  则是 `runtime/model.py` 里的一张列表。两者仍留给第一个后续适配器去定型。
- **§12 的待决问题，这里的决定。**（2）SDK 是**核心依赖**（`anthropic>=1.6.0`，与
  `openai` 并列），这是维护者的决定 —— 初稿曾把它做成 extra。它是惰性导入的，默认线路从不加载
  它；进核心依赖也正是 CI 会真的跑适配器测试、而不是跳过的原因。
  （3）子代理继承 `api_format`，理由同 `extra_body`：同一个端点。（4）**记日志，不弹提示。**切换
  provider 时协议随之切换（rev 13）：清洗照常运行并把数量写进 `agentao.log`，与每次切换模型
  时一样 —— 那条路径本来就会不可恢复地丢掉同样的签名块，也没有提示。线路变化多出来的只有一点：
  `extra_body` 是为另一种协议写的，CLI 会列出它的键。
- **配置。** `{PROVIDER}_API_FORMAT` 与仅关键字的 `Agentao(api_format=)` /
  `LLMClient(api_format=)`；未知值和尚未实现的值一律 fail closed，并列出有效值（§9）。

**实现与下文正文不一致之处，以及原因。** 每一条都是跑真实的 `anthropic` SDK（1.6.0）跑出来
的，不是读协议读出来的：

1. **`chat()` 和 `chat_stream()` 都走流式传输。** SDK 会拒绝 `max_tokens` 意味着超过十分钟的
   非流式请求（`_base_client.py::_calculate_nonstreaming_timeout`：约 21,333 以上）。agentao
   的默认值是 65,536，而摘要器调用 `chat()` 时根本不带上限，所以真正的非流式路径会在第一次
   压缩时抛 `ValueError`。`chat()` 就是不带回调地消费这条流；§10 的「流式/非流式一致」因此是
   构造使然，但仍有测试。
2. **不发送 `temperature`，§6.8 因此不再适用。** 这个 SDK 的 `messages.create` 没有
   `temperature` / `top_p` / `top_k` 参数 —— 传一个就是 `TypeError`，请求根本到不了网络。
   `LLM_TEMPERATURE` 和 `/temperature` 在这条线路上不起作用；确实接受它的网关通过
   `extra_body` 传。有一条测试钉住这个 `TypeError`，所以哪天某个版本把参数加回来，测试会说。
3. **块载体只写在六处中的两处，§5.1 的元组没有变长。** 工具调用消息和最终回复记录的是模型
   **自己**的输出，带 `anthropic_thinking_blocks`。四条合成的收尾消息（max-iterations、长度
   中止、hook stop、doom loop）是用同一个响应拼出来的**第二条** assistant 消息，那个响应的块
   已经在第一条上了；在那里再放一遍带签名的块，等于把一次模型输出记了两遍。它们照旧只带截断
   的展示副本。
4. **thinking 排在所属轮次的最前；与 text 的交错不保留。** OpenAI 形状的 dict 只有一个
   `content` 字符串和一个 `tool_calls` 列表，`[thinking, text, thinking, tool_use]` 没有表示
   方式。发出时先按序放块，再放 text，再放调用 —— 这正是 API 自己的规则点名的位置（开着
   thinking 时，工具循环里的 assistant 轮次必须以 thinking 块开头）。这就是 §4 说过的那个
   上限，碰到了。
5. **流内部的 `error` 事件按 body 分类。** 它是随 HTTP 200 到达的，SDK 抛出的是
   `status_code == 200` 的裸 `APIStatusError`，只看状态码的分类表会把它判成永久错误。过载是
   最常见的一种，而且通常早于任何内容。`overloaded_error` / `rate_limit_error` /
   `api_error` / `timeout_error` 在尚未向宿主展示任何内容时会重试。
6. **三处较小的增补。** 一次性修复会采纳模型自己说出的输出上限（`max_tokens: N > M`）——
   默认的 65,536 可能超过某个模型的上限，而这条报错的文本格式已在真实端点上核实（见「真实端点实测」）；来自
   别家 provider 会话的 id（`functions.read_file:0`）只在出站副本上改写，并且经过一张
   **覆盖整个请求的一对一映射** —— 单纯改写是有损的（`call.1` 和 `call:1` 会撞，第 64 个
   字符之后才不同的 id 也会撞），而重复的 `tool_use` id 是 400，且因为 id 在历史里，此后每个
   请求都 400；Anthropic 的第二种溢出报错（`input length and max_tokens exceed context
   limit: A + B > C`）加进了检测表和上限表，因为在这条线路上它才是**最先**遇到的溢出。

7. **第一轮评审改掉的两处。** §6.6 的「data URL 之外一律显式报错」改为对 `http(s)` 图片 URL
   透传（`source: {type: "url"}`）：这个 part 是持久化的，所以报错是永久性的 —— 此后每个请求
   都会报错，只有 `/clear` 能恢复。其它形态仍然报错。另外，`extra_body` 的遮蔽告警
   （`host-llm-extra-params.zh.md` §3.3）改为从适配器读取键集合：在这条线路上 `system` 是
   结构性字段 —— 写进 `extra_body` 会悄悄替换掉整个 system prompt —— 而 `temperature` 和
   `thinking` 不是，因为在这里 `extra_body` 正是宿主传它们的方式。

**阶段 1 的验收门还缺什么。** 上面所有内容都是用真实 SDK 在脚本化 socket 上验证的：请求体是
SDK 序列化出来的 JSON，事件和异常都是 SDK 自己的。当时**没有任何一项对真实端点跑过**，所以有四件
事是依据文档和同行实现断言的、而不是观察到的 —— thinking 在前的顺序会被接受；assistant 开头
的历史前面补的那条合成 user 消息会被接受；输出上限报错的措辞；以及提升到 `tool_result` 上的
断点会生效。§10 验收门里的**缓存收益检查**当时同样没做，需要在同一个端点上做三组：只有
阶段 0a、Chat Completions 上的 0b、以及原生线路。这两笔欠账都在下文结清。

**真实端点实测（2026-09-18，`api.anthropic.com`，`claude-sonnet-5`，经由 `LLMClient` 与本
适配器，约十来个小请求）。** 上面四条断言现在都是观测结果：（1）报错原文是 `max_tokens:
1000000 > 128000, which is the maximum allowed number of output tokens for
claude-sonnet-5`，修复采用了 128000 并重发成功 —— 而默认的 65,536 **低于**这个模型的上限，所以
这条修复在它上面不会触发；（2）合成的 user 消息被接受；（3）工具循环内、排在 turn 开头回传的签名块
被接受，对照组把签名改坏则是 400（`Invalid signature in thinking block`），说明 API 确实在
校验；（4）`tool_result` 上的断点第一个请求写入 13,007 个缓存 token，第二个请求读到 13,007。
另有三件没人断言过的事：**这个模型拒绝 `thinking.type.enabled`**，要的是 `{"thinking":
{"type": "adaptive"}, "output_config": {"effort": ...}}` —— 此前所有示例写的都是旧写法，现已
订正；它返回的 **thinking 文本是空的**（0 个字符，旁边是 504 字符的签名），所以显示副本也是空的；
以及在签名完好的前提下**改动** thinking 文本被接受了，所以「签名覆盖文本」只是说法、不是事实 ——
清洗豁免的依据改为协议的「原样回传」规则。thinking 落在 **turn 中间**（`[text, thinking, text,
tool_use]` 后接 `tool_result`，即同角色合并可能产生的形状）同样被接受，所以合并不需要加防护。

**三组缓存对比（同一天，同一端点与模型）。** 同一段脚本化会话 —— 7 个用户轮次、13 个请求、
对三个预置文件做真实工具调用，prompt 从约 9.6k 增长到约 17.9k tokens —— 跑三遍，每组在第一个
工具定义里放一个随机标记，与其他组的缓存隔离。

| 组 | 线路 | 断点 | Prompt tokens | 缓存写入 | 缓存读取 | 输入成本（以未缓存 token 为单位） |
|---|---|---|---|---|---|---|
| A —— 只有 0a | Chat Completions（`/v1/`） | 无 | 176,166 | 未报告 | 未报告 | 176,166 |
| B —— 0a + 0b | Chat Completions（`/v1/`） | 3 个 `cache_control` | 178,920 | 未报告 | 未报告 | 178,920 |
| C —— 原生 | `anthropic-messages` | 3 个，原生 | 177,771 | 17,902 | 159,843 | **38,388**（−78%） |

成本单位是 `未缓存 + 1.25 × 写入 + 0.1 × 读取`。原生线路上，第一个请求之后的每个请求都把上一次
的整个前缀读了回来（第 13 个请求：17,904 里读到 17,807），整段会话按全价计费的只有 26 个 token。
**A、B 两组从这里分不出高下**：Anthropic 的 OpenAI 兼容端点接受了 `cache_control` 标记、没有
报错，但它的 `usage` 里完全没有缓存字段（`prompt_tokens_details` 为 null），所以它是否真的按
标记缓存，从响应里看不出来 —— 只能看账单。这些数字能定下来的是 §12.1 在这个端点上的答案：
**在 Anthropic 自己的 API 上，缓存收益只有走原生线路才拿得到、也才量得出。** 0b 的价值在于那些
既认标记又上报缓存的第三方网关，这一点仍未实测，也是它保持默认关闭的原因。

**复现方式，以及还欠什么。** 上面那次对比是手工跑的，脚本没有留下。现在留下了：
`scripts/measure_prompt_cache.py` 跑同样的三组（或其中几组、对任意端点），每组用第一个工具
定义里的 nonce 隔离缓存，逐请求的缓存计数读自 `LLM_CALL_COMPLETED` —— 该事件自 0.5.1 起带
这两个字段。不加 `--yes` 它不发任何请求；端点不上报缓存字段时，它显示「未上报 —— 请看账单」，
绝不显示成 0。还剩三个问题，重跑上面那张表一个也回答不了：

1. **Anthropic 兼容端点上 A 与 B 之分**只在账单里，别处看不到。只有账号持有人能查。
2. **0b 在「既认标记又上报」的网关上的收益**：`--arms a,b --base-url-compat <网关>/v1`。
   尚未跑 —— 需要这样一个网关。
3. **激活技能的正文该不该放进前缀。** `--activate-skill NAME --at-turn K` 会记录激活那一刻的
   历史规模，这正是回答它需要的输入。取舍如下（来自价格模型，不是来自实测）：放尾部，正文 `S`
   在此后 `N` 个请求里每次都按未缓存发送；放前缀，它只写入一次、之后读取，但激活那一次要把
   系统提示 `P` 和其后的历史 `H` 重写而不是读取。按写 1.25、读 0.1 计，前缀更划算的条件是
   `N > (1.15 × (P + H) / S + 1.15) / 0.9` —— 以本仓库的 `P` ≈ 5.2k、`S` ≈ 4.1k 计，
   `H` = 10k 时约需再发 **6** 个请求，`H` = 50k 时约 **19** 个。这是一个待检验的界，不是结论：
   它没算 5 分钟过期，也没算中途的压缩，这两者都对尾部有利。

**rev 15 改了什么：** 适配器采纳 provider 的 Models API（`GET /v1/models/{id}`）：
`max_tokens` 在任何拒绝发生之前就写入输出上限闩锁，`max_input_tokens` 成为有效上下文窗口的
第三项、且只收窄不放宽，`capabilities` 供 `/thinking` 使用 —— 它在这条线路上现在写的是
`output_config.effort`（档位取自 `capabilities.effort`，否则用 API 接受的那五个；实测只写
effort 就会打开 adaptive thinking）。查询发生在发送路径上、请求构造之前，所以日志里记的就是
发出去的值；明确答复对该模型即为定论，瞬时失败再试一次，切换后重查；
端点没有这条路由时完全不起作用 —— 两种情况都实测过（`api.anthropic.com` 采纳 128,000 /
1,000,000；兼容网关回 404，一切照旧）。这是设计里「不做模型目录」（§9）唯一松动的地方，而且
只松到这里：是 **provider** 在陈述用户已经指名的那个模型的上限；agentao 仍然不带任何表格，
也不从名字推断任何东西。

**rev 14 改了什么：** 上面的「真实端点实测」，以及 thinking 示例的订正。

**rev 13 改了什么：** 第二条线路的第一次实际使用就是 `/provider` 切到配置了它的块，而阶段 1
拒绝了这个操作。现在阶段 3 里「切换 provider」这一块已实施，该阶段其余部分没有：
`LLMClient.reconfigure`、`Agentao.set_provider` 与 `runtime/model.py::set_provider` 接受
`api_format=`（`None` 保持当前线路），`/provider` 传目标块的 `{PROVIDER}_API_FORMAT`，ACP 传
resolver 返回值里可选的 `api_format` —— 省略即默认线路，与变量未设置同义，绝不是「会话当前的
线路」，否则只给 Anthropic provider 标了协议的 resolver 就切不回来。`MODEL_CHANGED` 带
`api_format_changed`。线路变化会换掉适配器 —— 是新建的，所以它的闩锁一并消失
—— 并且单凭自身就归入 §8 的「切换即清除」家族，哪怕模型名和 URL 都没变：清洗、token 锚点、
观测到的上限、能力闩锁、显式缓存断点。`extra_body` 保留，与每次切换一致，并按新适配器重新检查
结构键重叠（`system` 在一条线路上无害，在另一条上就是整个系统提示）。取值在任何状态被改动之前
解析，client 构造失败时 `reconfigure` 整体回滚，所以被拒绝的切换会让会话停在原来的 provider 上。`/model` 与 ACP `session/set_model`
仍在同一线路内，按模型覆盖（§9）没有做。两个方向都从 socket 上读取验证
（`tests/test_anthropic_messages_runtime.py`）。

**rev 12 改了什么：** 阶段 1 已实施；上面两块记录了落了什么、与正文哪里不一致及原因、以及
它的验收门还缺什么。§12.2 与 §12.3 已定（核心依赖；继承）。下文的设计正文相对 rev 10 其余未改。

**rev 11 改了什么：** 阶段 0 已实施；上面的状态块记录了落了什么、落在哪、以及它的验收门
还缺什么。下文的设计正文相对 rev 10 未改 —— 它是这次实施所依据的记录，也是将来评判阶段 1
的那份记录。

**rev 10 改了什么：** 核实第二份佐证实现 gemini-cli（`9450ade79`，`@google/genai` 1.30.0）：
同一个 GenerateContent 协议服务三种接入（API key、`vertexai: true`、Code Assist
`v1internal:streamGenerateContent`），佐证 §3 的 `api`/`provider` 分轴，Code Assist 明确
不在范围内。两仓 grep `interactions.create` 均零命中，附录 C 仍无对照实现。附录 B.3 增加
两条实测规则：**签名缺失本身会 400**（gemini-cli 用占位签名兜底，只补每条消息的第一个
functionCall；而 agentao 的压缩 / `/resume` / minimal-history 三条路径必然产出这种历史），
以及**签名不能跨端点**（Genai → Vertex 会失败），后者同时写进 §8 并成为 §9 排除 Vertex 的
技术理由。B.5 的告警改为一般化：两份实现在同一字段上写法相反，抄之前要确认目的字段的语义。

**rev 9 改了什么：** 核实本地 pi-mono（`5a3a03a7f`）的两个 Google 适配器都走
GenerateContent 原生流式（`generateContentStream`，`@google/genai` 2.21.0），因此它们对照
**附录 B**，而其十值 `KnownApi` 里没有 Interactions —— 附录 C 无对照实现，此不对称记入 §3。
§2.2 的成本参照改为按文件实测（原「约 900 行」不准）。附录 B.3 增加两条对照实现细则
（签名 ≠ thinking；流式签名可能只在块首 delta），B.5 增加「不要照抄 pi-mono 那一行」——
它的 `input` 是未缓存输入，照抄会少报 Tier-1 锚点。

**rev 8 改了什么：** 三处收口。附录 C.5 点名终止事件（`interaction.completed` / `error` /
`done`，`step.stop` 不是收尾信号）；§9 写明 `gemini-api` 在 §3 定线路前不进入已发布值域；
§2.2 与附录 C 开头的沉没成本告诫改为对称覆盖附录 B 与 C，并写明 §12.1 可同时取消两者。

**rev 7 改了什么：** 新增附录 C，记录 Interactions 无状态 steps 回放、工具配对、独立
thought 签名、首版原生流式和 usage 验收。附录 B/C 成为 §3 的两份候选方案；仍先过
§12.1 再做线路比较，不新增配置值，也不授权同时实现两条 Google 线路。

**缘起：** 2026-09-17 的 pi-mono pull 评审（会话记录 —— pi-mono
`400d6905c..5a3a03a7f`；未落成文档），其头条结论是 agentao 只投资了 prompt 缓存的一半、
跳过了另外几半。那份评审里三条补救中的两条，只有换一种 agentao 不会说的线路协议才够得着。
那些结论的证据在本文 §1、§2.3 原样重述，而非引用，以便本文自足。

**读者：** 决定是否拓宽 LLM 边界的 agentao 维护者；实施 PR 的评审人。

**对照件：**
- `docs/design/llm-api-adapters.md` —— 英文孪生。
- `docs/design/host-llm-extra-params.md` —— 同源的姊妹原语（`extra_body`）；同一个
  「请求 kwargs 是闭集」观察，那次解决的是 body，这次是协议。
- `docs/design/embedded-host-contract.md` —— 为什么 `agent.messages` 不能改形状。
- `docs/design/compaction-orchestration-plan.md` —— 约束任何翻译层的压缩形状规则。
- `docs/design/tool-search.md` —— 一条已记录的决定，本设计会移除它写明的阻塞理由（§2.2）。

**锚点 —— agentao（核实于 `main@592f028`）：**
- `agentao/llm/client.py` —— `_build_request_kwargs`（`389-424`）、非流式发送点
  （`455`）、流式发送点（`717`）、`_is_gemini`（`515-527`）、
  `reset_capability_latches`（`375-387`）、client 构造（`219-223`、`366-370`）。
- `agentao/llm/_stream_response.py` —— 鸭子类型契约，模块 docstring（`1-16`）。
- `agentao/runtime/llm_call.py` —— 可观测性包装（整文件）。
- `agentao/runtime/chat_loop/_runner.py` —— 请求装配（`342-346`）。
- `agentao/runtime/chat_loop/_serialize.py` —— `_serialize_tool_call`（`38-73`）。
- `agentao/runtime/tool_result_formatter.py` —— tool 结果消息形状（`232-237`）。
- `agentao/context_manager.py` —— 历史中段的摘要消息（`1071-1078`）、
  minimal-history 头部修复（`1185-1195`）、`role: "tool"` 切分规则（`799-820`）。
- `agentao/tools/base.py` —— `to_openai_format`（`170-179`、`365-...`）。
- `agentao/embedding/factory.py` —— `LLM_PROVIDER` 解析（`68-85`）。

**锚点 —— pi-mono（`5a3a03a7f`，作为佐证实现引用，不是规范引用）：**
- `packages/ai/src/types.ts:17-27` —— 十值 `KnownApi` 联合类型。
- `packages/ai/src/compat.ts:180-191,244-266` —— 注册表 + 按 `model.api` 分发。
- `packages/ai/src/api/anthropic-messages.ts:1226-1241,1275-1308,1385-1394,1081-1104` ——
  消息转换、待决 system 消息队列、tool 结果分组、缓存标记。
- `packages/ai/src/api/openai-responses-shared.ts:328-350,480-515,533-548` ——
  `function_call_output`、复合 tool-call id、reasoning 持久化。
- `packages/ai/src/api/openai-responses.ts:318,322,353` —— `store: false`、
  `max_output_tokens` 下限、`include: ["reasoning.encrypted_content"]`。
- `packages/ai/src/api/openai-completions.ts:1081-1135,1632` —— **在 Chat Completions
  线路上**打 Anthropic 式 `cache_control`。见 §2.3，这是那条便宜路径。
- `packages/ai/src/api/google-generative-ai.ts:100`、`google-vertex.ts:109` —— 两个 Google
  适配器都调 `client.models.generateContentStream(params)`，官方 SDK `@google/genai`
  **2.21.0**。**它们对照的是附录 B，不是附录 C**：`types.ts:17-27` 的十值里
  `google-generative-ai` 与 `google-vertex` 都在，没有 Interactions 适配器。
- `packages/ai/src/api/google-shared.ts:112-145,235-273` —— 签名语义（`isThinkingPart`、
  `retainThoughtSignature`）与流式保留；两个适配器共用这 515 行。
- `packages/ai/src/api/google-generative-ai.ts:231-240` —— usage 映射。注意它的 `input`
  是**未缓存**那部分（`promptTokenCount - cachedContentTokenCount`），不是 agentao 的
  `prompt_tokens`；见附录 B.5。

**锚点 —— gemini-cli（`9450ade79`，第二份佐证实现，官方 SDK `@google/genai` 1.30.0）：**
- `packages/core/src/core/contentGenerator.ts:285-312`、`:392` —— 按登录方式分流；Vertex
  是同一个 SDK 加 `vertexai: true`，不是另一种线路协议。
- `packages/core/src/code_assist/server.ts:73-74,93` —— Google 账号登录走 Code Assist
  的 `https://cloudcode-pa.googleapis.com` + `v1internal`，即
  `v1internal:streamGenerateContent`。**内部接口，明确不在 agentao 范围内。**
- `packages/core/src/core/geminiChat.ts:110,1259-1310,1850-1864` ——
  `SYNTHETIC_THOUGHT_SIGNATURE = 'skip_thought_signature_validator'` 与
  `ensureActiveLoopHasThoughtSignatures`；见附录 B.3。
- `packages/core/src/config/config.ts:1580-1589`、`geminiChat.ts:1241-1257` ——
  换 auth 时 `stripThoughtsFromHistory()`：Genai 的签名发给 Vertex 会失败。
- `packages/core/src/agent/event-translator.ts:468-470` —— `inputTokens:
  promptTokenCount`（**不**减缓存），与 pi-mono 相反；见附录 B.5。
- 全仓 `grep interactions.create|previous_interaction_id` 零命中。

---

## 1. agentao 今天说什么，以及接缝其实已经在哪

agentao 只说一种线路协议：**OpenAI Chat Completions**。两个发送点，
`client.py:455`（非流式，`with_raw_response.create`）与 `client.py:717`
（流式，`create(stream=True)`），共用一个闭集 kwargs 构造器
`_build_request_kwargs`（`client.py:389-424`），产出
`{model, messages, stream?, stream_options?, temperature?, tools?, tool_choice?,
max_tokens|max_completion_tokens?, extra_body?}`。

`agentao/` 下有 32 个文件引用 OpenAI 消息形状（`tool_calls` / `"function"` /
`finish_reason` / `chat.completions`）。它们分三组，而**这个区分就是整个设计**：

| 组 | 文件（举例） | 真正依赖的东西 |
|---|---|---|
| **线路** | `llm/client.py`、`llm/_logging.py` | HTTP 请求/响应字节 |
| **响应形状** | `runtime/llm_call.py`、`runtime/chat_loop/_runner.py`、`runtime/sanitize.py` | `response.choices[0].message.{content,tool_calls,reasoning_content}`、`response.usage`、`response.model` |
| **历史形状** | `context_manager.py`、`compaction/`、`runtime/tool_*`、`session.py`、`replay/`、`acp/`、`cli/` | `agent.messages` 是一串 OpenAI 形状的 dict |

**关键观察：响应形状早就是一个接口，不是一个类型。** `llm/_stream_response.py`
是手写的鸭子类型，从 SSE delta 重建出 `ChatCompletion`，其模块 docstring（`:1-16`）
逐条列出了下游真正触碰的属性面。`llm/` 之外没有任何代码需要一个 OpenAI SDK 对象，
只需要一个带那些属性的对象。所以**只要新适配器返回同一个鸭子类型**，第二种线路协议
可以复用整个 runtime。

历史形状则相反：它到处都是承重的，会被持久化进 session 文件和 replay 文件，会穿过
ACP 边界，而且 `docs/reference/host-api.md:202` 明确告诉宿主直接读 `agent.messages`。
它是契约，不是实现细节。

## 2. 这事值不值得做 —— 三个诚实的子问题

### 2.1 新协议到底买到什么

| 想要的能力 | 今天的 Chat Completions | 需要 `anthropic-messages` | 需要 `openai-responses` | `gemini-api` |
|---|---|---|---|---|
| 显式 prompt 缓存断点（`cache_control`、1h 保留） | Anthropic 的 OpenAI 兼容端点不暴露 | 是 —— 原生 | 不适用 | 不适用 |
| 签名 thinking 块完整往返 | agentao 把 `reasoning_content` 截到 500 字符（`_serialize.py:20`），切换时清洗 | 是 | 不适用 | 不适用 |
| 跨 turn **保留** reasoning，且不依赖服务端状态（`reasoning.encrypted_content`） | 无对应物 —— 直接丢弃 | 不适用 | 是 | 不适用 |
| thinking 预算/力度作为一等参数 | 只能经 `extra_body` 的 `reasoning_effort`，且无自动恢复 | 是（`thinking.budget_tokens` / effort） | 是（`reasoning.effort`） | 待 §3 线路选择确认 |
| `prompt_cache_key`、服务端会话状态 | `extra_body` 能设 key；没有状态 | 不适用 | 是 | 不适用 |
| Gemini 签名保真 + 逐字输出 | `_is_gemini` 命中的全部模型绕过流式（`client.py:515-527,559-561`），无逐字输出 | 不适用 | 不适用 | 首版原生流式 + parts 签名往返；线路选择须通过 §3 门槛 |

`gemini-api` 另提供 Gemini 原生 parts / thought signatures 与原生流式的适配边界
（附录 B），并为 `cachedContent` 资源式缓存留出独立接入点。现有兼容路径为了保留
`thought_signature` 会绕过流式（`client.py:559-561`）；原生适配器须同时满足签名保真
与增量输出。显式缓存资源的创建、失效与成本另行验收，不是 0b 的 `cache_control` 标记。

**这会重开一条已记录的决定。** `docs/design/tool-search.md` 当初否掉 pi-mono 的
「transcript 携带式工具激活」，理由是它「需要一个 agentao 的 chat-completions 路径所缺少的
provider 原生加载点 —— 所以**并不降低 agentao 的成本**」。Anthropic Messages 与 OpenAI Responses 恰好提供了这样一个
加载点。这本身不足以让那个提案复活（它的驱动力当时被判定为 provider 主导、而非工具清单膨胀），
但写明的那条阻塞理由将不再成立。

### 2.2 代价

完整适配器负责请求/响应翻译、usage 与 stop-reason 映射、重试错误分类、上下文溢出解析
（`parse_observed_context_limit`）、能力 latch，以及自己的流式事件状态机和累加器。
对照实现按文件实测（pi-mono `5a3a03a7f`，测试之外）：`anthropic-messages.ts` 1,520 行；
`openai-responses.ts` 397 行 + 多个 Responses 适配器共用的 `openai-responses-shared.ts`
793 行；Gemini 线路是 `google-generative-ai.ts` 470 行 + 两个 Google 适配器共用的
`google-shared.ts` 515 行（`google-vertex.ts` 另 553 行，复用同一份共用翻译）。
#283 之后 agentao 整个 `llm/` 是 **1,527 行**。这是完整实现的成本参照，不是阶段 1 的预算；
共用文件服务多个适配器，不能整份算到某一个头上。

阶段 1 的交付要求包含新协议的原生流式出口：收到文本增量即通过 `on_text_chunk` 交付，
流结束后返回完整鸭子类型响应。缓存收益验证本身不依赖流式，但首版使用体验要求逐字输出，
因此该协议自己的事件状态机、累加器、取消与异常处理成本均纳入阶段 1。
`client.py:559-561` 的 Gemini 非流式绕行仍是既有 provider 特例。

Gemini 还须计入 Legacy 线路的后续迁移成本，不能把已写出的附录 B/C 当作沉没成本 ——
既不默认继续旧线路，也不因为两份候选都已写满规则就默认要在其中挑一个实现。§3 的路线
比较是阶段 2 的前置门槛，而它本身以 §12.1 为前置。

### 2.3 拿到大部分缓存收益的便宜路径 —— 先验证这条

`packages/ai/src/api/openai-completions.ts:1632` 在
`provider === "openrouter" && model.id.startsWith("anthropic/")` 时把
`cacheControlFormat` 设为 `"anthropic"`，随后 `applyAnthropicCacheControl`
（`:1081-1089`）在**三个**断点注入 `cache_control: {type: "ephemeral", ttl?: "1h"}` ——
system prompt、最后一个 tool 定义、最后一条会话消息 —— **走的是普通的 Chat
Completions 线路。**

阶段 0 分两步，先做 provider 中立的前缀稳定化，再验证一个端点的显式标记。

**0a：把 volatile 块移到 history 之下。** `prompts/builder.py:95-107` 把 skills、todos、
dynamic_recall、plan 放在 system 内部；system 又是 `messages[0]`。这些块一变，整段
history 的缓存前缀就失效，影响显式断点，也影响 OpenAI/DeepSeek 的隐式前缀缓存。
`context_manager.py:320-327` 已记录 system 每轮携带 volatile 内容重建，只是此前将其
视为 token 估算的有界偏差。此修正无需新旋钮或 Anthropic 端点，但必须同时修正下述 Tier-1 锚点计算；实际命中与成本改善仍需测量。

**选择请求装配处追加临时 user 尾消息**，以 `<system-reminder>` 包裹当前 volatile 内容。
稳定 system + history + 临时尾消息，每次请求只装配一份。不要照搬 `_runner.py:342`
将日期时间拼入 user 消息并持久化的方式：todos 快照不能逐轮堆进 transcript。
临时尾消息只在新建的 `messages_with_system` 中存在，绝不写入 `agent.messages`；工具
循环、压缩和溢出重试重建请求时也要重新装配，不能丢失、重复或拆开 tool-call/result 配对。

**七处重建都要接通。** `_runner.py` 的 `messages_with_system = [` 出现在
`:344、551、717、876、1078、1154、1209`；`:393` 调用的后台通知注入会走 `:1154`
这一处，不能另算第八处。实施时集中到同一个请求装配辅助函数，让七处都构建
`persistent = [S] + H`，然后仅在请求中追加当前尾消息 T。覆盖工具循环、压缩、溢出重试
和后台通知，不能漏装或重复装配。

日期时间（`:342`）和后台通知（`:1146-1156`）两个 `<system-reminder>` 先例均写入历史；
后者会 append 一条 user 消息。0a 是这七条装配路径中首次引入不入历史的消息，属于**新的
生命周期不变量**：尾消息只在请求中，后台通知仍持久化，二者不可混同。

**锚点按持久前缀记，不能直接记请求长度。** 当前阈值估算（`:386`）和记锚点
（`:427-428`）用同一个列表。若发送 `[S]+H+[T]` 后仍记整表长度，下一轮切片会漏掉
第一条新增历史 Δ[0]，并重新计入新尾消息。忽略本地编码误差、假设至少新增一条历史：

```text
truth - estimate ≈ tokens(S') - tokens(S) + tokens(Δ[0]) - tokens(T)
```

当旧尾部大于首条新增历史时，估算系统性偏高，压缩提前触发；反之可低估。它有界且不
逐轮累积，但每轮重现，量级可能是一整个 volatile 尾部，远大于旧方案的两轮 system 差。
选择以下计算，`est(T)` 包含尾消息封装开销，并在发送时固定下来：

```python
record_api_usage(prompt_tokens - est(T), len(persistent))
next_estimate = anchor + est(new_persistent_messages) + est(T_next)
```

估算入口也必须分开持久前缀和当前尾部；不能只改 `message_count`。压缩重写持久前缀后
仍使锚点失效。代价是 Tier-1 从纯真实值变成**真实值减本地估算**：尾部估错会污染锚点，
剩余误差包含尾部估算误差的轮间变化。连续 N 轮（N ≥ 10）改变尾部大小、固定历史增长，
记录 `estimate - actual_prompt_tokens`；验收应无随尾部大小系统性增长的偏差，同时报告
本地估算误差范围。仅验证新增消息不被跳过不足以通过。

**0b：显式 `cache_control`，opt-in，锁定一个端点。** 这条路无需适配器。
**SDK 透传已核实：** 本机 openai 2.24.0 的
`maybe_transform(body, CompletionCreateParamsNonStreaming)` 原样保留消息 dict、content
part、tool dict 上未知的 `cache_control` 键，无需 SDK 逃生口。
**端点是否识别仍未核实：** pi-mono 的一个 provider/模型前缀 gate 不是 agentao 的端点
验收；开启前须明确并验证一个端点，不盲发字段。会话断点应落在稳定 history 尾部，
不要落在 0a 的 volatile 临时尾消息上。

**copy-on-mark 是硬约束。** `_runner.py:344-346` 只是浅拼接，`chat()` 和
`_build_request_kwargs` 不复制；原地标记会污染 `agent.messages`，进入 session、replay、
ACP `session/load` 和压缩引擎，旧断点还会逐轮累积。只浅拷贝被标记的消息/tool dict；
若标记 content part，则沿该路径复制所属消息、content 列表与目标 part，其他对象保持共享
且只读。副本只用于本次请求，绝不回流历史或规范 tool schema。
Anthropic [官方缓存文档](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
允许最多 4 个断点；本方案预算固定为**最多 3 个显式断点 + 为自动缓存保留 1 槽**
（2026-09-18 核实）。原文明确：若启用自动缓存时已经存在四个显式块级断点，
API 返回 400，因为自动缓存没有剩余槽位。 这不是说单独使用四个显式断点非法，也不表示本方案
默认开启自动缓存。无 tool 或 history 时少放断点；每次重建、总数不累积，调用方已有标记
也计入预算。§5.3 的原生块携带键必须持久化，与缓存标记生命周期相反。

## 3. 术语：`api` 不是 `provider`

pi-mono 维持两条正交的轴 —— `api`（十种线路协议）与 `provider`（约 40 个凭据/端点
命名空间），多对一映射。agentao 已经有 `LLM_PROVIDER`（`embedding/factory.py:68-85`），
但它**只**表示凭据命名空间：用来选 `{PROVIDER}_API_KEY` / `_BASE_URL` / `_MODEL`。

**不要把协议选择重载到 `LLM_PROVIDER` 上。** 一个用户设 `LLM_PROVIDER=ANTHROPIC`
但指向一个 OpenAI 兼容网关，这是合法且当前可用的配置；让这个名字隐含线路协议会静默
破坏它。新旋钮是另一条轴 —— 见 §9。

**`gemini-api` 保留为原生适配器标识，但不冻结首版线路。** Google 的 OpenAI 兼容接口
仍归 `openai-completions`。2026-09-18 直接打开 thought-signatures 与 caching guide，
页面标题均带 **Gemini Generate Content API (Legacy)**；API 参考页未带该标记。
[Interactions 官方概览](https://ai.google.dev/gemini-api/docs/interactions-overview)
明确其于 2026 年 6 月 GA、推荐新项目使用、Python SDK `google-genai` ≥ 2.3.0 已支持；
同时称 GenerateContent 仍受完整支持，并列出 Interactions 尚缺显式缓存。因此不能拿
“Interactions 未稳定/SDK 未覆盖”为旧线路辩护，也不能把 Legacy 等同于已公布下线日期。

**无显式缓存不等于无缓存。** 同一概览的 Limitations 提到 `previous_interaction_id`
可利用服务端隐式缓存，而 Best practices 明确隐式缓存同时支持**有状态与无状态**模式；
会话链有助于命中，并非唯一入口。按附录 A.3 的同一原则，初始比较使用 `store=false`、
完整历史回传、不串联服务端 id；因此 Interactions 的无状态隐式缓存仍是候选。
若要用会话链增益，必须统一重开附录 A.3，解决压缩、`/clear`、replay 与服务端历史的一致性，
不能仅为 Gemini 破例。是否持有服务端会话状态是架构约束，但不能单凭它判定缓存不可用；
显式资源与无状态隐式缓存的实际收益仍须测量。

**这条轴的划法有两份佐证。** gemini-cli（`9450ade79`）用**同一个** GenerateContent 协议
服务三种接入：API key 走 `models.generateContent(Stream)`；Vertex 是同一个 SDK 加
`vertexai: true`（`contentGenerator.ts:392`）；Google 账号登录走 Code Assist 的
`v1internal:streamGenerateContent`（`server.ts:73-74,93`）。一种线路协议、三套凭据/端点
安排 —— 正是本节要求分开的那两条轴。其中 Code Assist 是内部接口，不在 agentao 范围内；
Vertex 因此也不是「另一种协议」，而是另一个 provider 块（§9 仍把它排除在首版之外，并另有
一条技术理由 —— 见附录 B.3 的跨端点签名不可移植）。

**两个候选的实施风险不对称 —— 这与线路寿命是两件事。** 两份本地对照实现都走
GenerateContent，**没有一份用 Interactions**：pi-mono（`5a3a03a7f`）的两个 Google 适配器
用 `generateContentStream`（`@google/genai` 2.21.0），gemini-cli（`9450ade79`）三条接入
路径全部是 GenerateContent 系列（`@google/genai` 1.30.0），两仓 grep `interactions.create`
均零命中。所以附录 B 的原生流式与逐 part 签名回放**有两份可读的对照实现**，附录 C 至今
只有一手文档。
这不减少 Legacy 线路的迁移成本，也不构成选择理由（§2.2 的沉没成本告诫同样适用），但它是
比较里必须记上的一项：两条候选的未知量不一样多，验证成本也就不一样。

**撤回无条件排除 Interactions 的决定。** 附录 B 的 `generateContent` /
`streamGenerateContent` 暂留作候选，因为它能满足可单独计量的显式缓存资源需求；若实际
只要签名保真与逐字输出，这个理由不成立，应优先评估官方推荐的 Interactions。
仅在 §12.1 确认仍有值得实现的收益后，Gemini 开工前才比较两条线路的无状态完整历史回放、签名、工具、原生流式、缓存成本及
后续迁移成本；用实际目标模型/SDK fixture 定案，更新选定的附录 B/C 后再授权实现。尚无这些数据，
不能宣称旧线路更便宜。参考[官方迁移指南](https://ai.google.dev/gemini-api/docs/migrate-to-interactions)。
Vertex AI 凭据/部署配置与 Live API 仍不在首版范围；不能静默将两种线路混入同一个已发布
`api_format` 的契约。

## 4. 接缝位置的三个选项

| | **A —— `LLMClient` 之下的适配器** | **B —— 中立的内部消息模型** | **C —— 宿主注入整个 client** |
|---|---|---|---|
| 规范历史格式 | 仍是 OpenAI dict | 新的中立类型 | 仍是 OpenAI dict |
| 改动文件数 | `llm/` + 配置 + `/model` 面 | 约 32 个文件，外加 session / replay / ACP 格式 | 约 0 |
| 宿主契约（`agent.messages`） | 不变 | **破坏** | 不变 |
| session / replay 文件 | 不变 | 需要迁移 | 不变 |
| 谁来写适配器 | agentao | agentao | 每个宿主各写一遍 |
| 保真度上限 | 受限于 OpenAI dict 能携带什么（§5.3） | 最高 | 因宿主而异 |
| pi-mono 对应 | —— | pi-mono 走的就是这条 | —— |

**推荐 A。** 抽象地看 B 是更好的架构，也是 pi-mono 的选择 —— 但 pi-mono 是在
*还没有*在 OpenAI 形状之上累积出压缩引擎、replay 格式、ACP session-load 路径和一份
成文宿主契约之前就做的选择。agentao 不是，而迁移成本现在恰好集中在最难测的那几个子系统上。
C 不是设计，是「什么都不发布」的决定。

**A 的上限是真实的，应该开门见山写出来：** 交换格式是 OpenAI 形状的 dict，所以目标协议
需要而 OpenAI dict 表达不了的东西，都得作为额外键挂在那个 dict 上。agentao 已经这么干过
两次（`reasoning_content`、`thought_signature` —— `_serialize.py:23-36`，以及
`_serialize_tool_call` 里 `model_dump()` 的注释），所以这个模式是既有的、不是这里发明的。
但每加一个这样的键，就必须在切换时被清洗（`runtime/model.py::purge_thinking_artifacts`）
并在出站 sanitize 中豁免，而这张清单会随适配器增长。

## 5. 选项 A 细节

### 5.1 契约（草案，不要提前冻结）

只有一个实现时，判断不出这些方法里哪些是契约、哪些只是第一个协议的偶然形状。把下面
这个形状当作阶段 1 的工作草案，等首个后续适配器落地时再定（§10 阶段 2）。

四个操作加 `api` 即可；SDK client 由适配器自己构造并持有，`LLMClient` 不传 SDK client。

```python
class LLMApiAdapter(Protocol):
    api: str  # openai-completions | anthropic-messages | openai-responses | gemini-api

    def chat(self, *, model, messages, tools, max_tokens,
             temperature, extra_body) -> Any: ...  # duck response
    def stream(self, *, model, messages, tools, max_tokens, temperature,
               extra_body, on_text_chunk, cancellation_token) -> Any: ...  # duck response
    def describe_error(self, exc) -> ErrorDescription: ...
    def purge_keys(self) -> tuple[str, ...]: ...
```

`describe_error` 合并重试分类和上下文上限解析，保留重试判断、状态码/原因、可选的
provider 断言上限；`ErrorDescription` 的具体表示暂不冻结。`stream` 是必需操作，由
`LLMClient.chat_stream` 委派调用；执行期间回调文本增量，结束时返回与 `chat` 一致的
完整鸭子类型响应，并传递取消信号。SDK 事件解析与累加器由适配器内部负责。
`build_request`、`send`、`to_duck_response` 都是适配器内部步骤，不进契约。

`LLMClient` 保留公开面：`chat`、`chat_stream`、`model`、`temperature`、`max_tokens`、
`extra_body`、`reconfigure`、`reset_capability_latches`，以及 `llm/` 外部读取的 `.logger`
（`context_manager.py` 13 处）、`.total_prompt_tokens` / `.total_completion_tokens`
（`agent.py:1262-1263`、`:1415-1416`）、`.api_key` / `.base_url`
（`agent.py:834-835`，子 agent 配置据此构造）。协议部分委派出去；另写整个 client 类
必须复制这些兼容面，这也是选项 A 的理由。

**但 `llm/` 之上有一处必须改。** 适配器只能*返回*更丰富的响应，它决定不了什么被写进历史。
runner 在**六处**手工构造 assistant dict（`_runner.py:573、623、735、820、839、943`），
只复制 `content` / `tool_calls` / `reasoning_content`，此外一概不取；而且六处随后都调用
`_attach_reasoning`（`:576、630、738、823、842、944`），它会**截断到 500 字符**
（`_serialize.py:20`）。适配器交回的 `signature` 或 `reasoning_item` 在那里被静默丢弃，
而一个被截断的签名块会在下一次请求时被 provider 拒绝。所以 **reasoning 保真是在历史写入处
决定的**，不在适配器；在那处写入改掉之前，§5.3 的携带键是死的。

`_attach_reasoning` 需要增加第二个、不截断的携带物：一个**按序排列的原生块列表**，而不是
「一个字符串加一个签名」—— Anthropic 一轮会返回多个 `thinking` / `redacted_thinking` 块，
摊平会同时丢掉顺序和逐块签名。展示副本维持原样。

**保存逻辑仍集中在那一个函数里，但值必须被送到它手上 —— 六处调用点不会自动继承。**
`_attach_reasoning` 今天收到的是一个*字符串*（`_serialize.py:23`），所以块列表要像
`reasoning_content` 一样接通到每一处：

- **三个起点**从响应上读取 —— `:568-569`、`:614`、`:898` —— 每处都要再读一次块；
- **一处中间契约**：`:614` 的值作为三元组的第三个元素返回（`:640`，arity 记在 `:603`），
  在 `:684`、`:787` 两处解包，喂给六处写入中的三处（`:738`、`:823`、`:842`）。这个元组要
  多一个元素，两处解包也随之改动。

这些都不需要新增抽象，但也不是「只改辅助函数即可」。

### 5.2 一动不动的部分

- `agent.messages` —— 仍是 OpenAI dict。session 文件、replay 文件、ACP `session/load`、
  `cli/display.py`、压缩引擎：**形状**不变，而 §5.1/§5.3 那个加性携带键会像
  `reasoning_content` 今天那样，一并落进这些持久化文件。**不能发生的是换一套消息模型**；
  assistant dict 上多一个键不算换。
- `to_openai_format()`（`tools/base.py:170-179`）仍是规范的 tool schema。需要别的形状的
  适配器（Responses 会把嵌套的 `function` 对象摊平）从它**翻译过去**。不要新增第二个
  schema 产出点。
- `runtime/llm_call.py` 的事件载荷。`tools_hash` 读的是 `t["function"]["name"]`
  （`llm_call.py:52-57`）—— 它必须继续对**规范** schema 求哈希，而不是翻译后的，
  否则同一套工具在不同 api 下哈希不同，replay 比对就断了。
- 日志脱敏 formatter。它是挂在 handler 上的 `Formatter`（`llm/client.py`，见
  CLAUDE.md § Logging），所以新适配器天然被覆盖。

### 5.3 「携带键」问题，点名

这些原生块携带键必须持久化；§2.3 的 `cache_control` 仅存在于请求副本，两者不能共用生命周期。

| 协议需要 | 挂在 OpenAI dict 上的形式 | 还必须同步加入 |
|---|---|---|
| Anthropic thinking 块 + `signature` | 独立的按序原生块列表，保留逐块签名（§5.1）；`reasoning_content` 仍作展示副本 | `purge_thinking_artifacts`、sanitize 白名单 |
| Responses `reasoning` item + `encrypted_content` | assistant 消息上的 `reasoning_item` 键 | 同上 |
| Responses 的双 id（`call_id` **和** item `id`） | 见附录 A.2 —— **不要发明第二个 id 字段** | —— |

Gemini 同样需要按序原生 parts 携带物，逐 part 保存 `thoughtSignature`（附录 B）。
现有 `_serialize_tool_call` 保留工具调用额外字段，但不能覆盖非工具 part 的签名；
因此不能仅沿用工具调用上的 `thought_signature` 就宣称保真。持久化表示须 JSON 可序列化，
SDK 的 bytes 签名要无损编码，回放时还原；同样纳入 sanitize 与切换清洗。

若选择 Interactions，则携带按序模型 steps（附录 C.3/C.4），而非上述 GenerateContent
parts；同一持久化和清洗契约适用，原生载荷须区分线路，不能相互回放。

## 6. 翻译规则 —— `anthropic-messages`

下面每条都点名了逼出这条规则的 agentao 代码。pi-mono 的行号是「这条规则真实存在」的
佐证，不是规范引用。

1. **把 system 消息提上去。** agentao 把系统提示词作为 `messages[0]` 发送
   （`_runner.py:342-346`）；Anthropic 收的是顶层 `system` 参数。机械映射。

2. **历史中段的 `role: "system"` 消息无处安放 —— 而 agentao 恰好会发一条。**
   `context_manager.py:1071-1078` 把压缩摘要作为 `role: "system"` 消息注入到
   *历史中段*。Anthropic 没有会话中系统消息。pi-mono 撞到同一堵墙，解法是一个延迟队列：
   `anthropic-messages.ts:1237-1241` 说明夹在 `tool_use` 和它的 `tool_result` 之间的
   system 消息会被**拒绝**，所以更新先存进 `pendingSystemMessages`，在下一个用户消息
   边界冲刷出去。agentao 必须做同样的事，或者把摘要折进紧随其后的用户消息。
   **这是幼稚实现里最可能出现的静默 400**，因为它只在第一次压缩之后才出现。

3. **agentao 不应依赖首条 assistant 消息被接受。**
   `_minimal_history_start`（`context_manager.py:1185-1195`）会刻意回退到开启这轮工具
   调用的那条 assistant 消息，所以在最后一级溢出阶梯之后，`messages[0]` 可能是 assistant
   消息。适配器必须检出这种情况并补一条合成 user turn（或拒绝在那里切）。那一级已有的
   测试要扩展，不能直接信任。这是未对目标端点做首条 assistant 实测前的保守兼容规则，
   不宣称协议普遍要求首条必须为 user。

4. **合并 tool 结果。** agentao 每条结果追加一条
   `{"role": "tool", "tool_call_id", "name", "content"}`（`tool_result_formatter.py:232-237`）。
   Anthropic 要求一个 assistant turn 的所有结果作为 `tool_result` 块放进**单条** `user`
   消息里（`anthropic-messages.ts:1385-1394`）。把连续的 `role: "tool"` 段分组，保持顺序。

   后台通知已经会在 tool 结果后持久化一条 user 消息（`_runner.py:1146-1156`），不是
   0a 新增的问题。选择在出站副本中把紧随结果组的连续 user 内容并入同一 user turn：
   所有 `tool_result` 在前，通知及临时尾部文本按原序在后，不回写历史。
   Anthropic [Messages 参考](https://platform.claude.com/docs/en/api/messages/create)
   明确接受连续同角色 turn 并合并；主动归一化可固定块顺序。fixture 覆盖并行结果 +
   后台通知 + 临时尾部，以及 §6.2 的中段摘要，验证调用/结果不被拆开。

5. **`tool_call_id` 逐字节往返。** 这已经是 agentao 的不变量（CLAUDE.md § unicode tags：
   id 豁免于剥离）。Anthropic 的 `tool_use_id` 一一对应 —— 不需要复合 id，和 Responses
   不同（附录 A.2）。

6. **图像。** agentao 发
   `{"type": "image_url", "image_url": {"url": "data:<mime>;base64,<data>"}}`
   （`_runner.py:326-330`）→ Anthropic 的
   `{"type":"image","source":{"type":"base64","media_type","data"}}`。解析 data URL。
   **agentao 自己的路径不会产生别的东西**：`chat(images=...)` 要求 `data` + `mimeType`，
   否则直接抛错（`_runner.py:318-330`），所以远程 URL 只可能由宿主直接写 `agent.messages`
   带进来。Anthropic 本身接受远程图像（`source: {type: "url", url: ...}`，见 Anthropic
   vision 文档的 *URL-based image example*），需要支持这种情况时直接透传即可。v1 只处理
   data URL 因此是一条明确的**范围**限制，而非协议属性：对其它形态显式报错，不要静默丢弃。

7. **Thinking 块。** Anthropic 返回带签名的 `thinking` 与 `redacted_thinking` 块，
   下次请求必须原样送回。agentao 为历史把 `reasoning_content` 截到 500 字符
   （`_serialize.py:20`）—— **那个截断与签名往返不兼容**，本适配器必须绕过它，把未截断
   文本与展示副本分开携带。一个被截断的签名块比没有块更糟：provider 会直接拒绝。

8. **`temperature` 与扩展思考不兼容**（佐证见 `anthropic-messages.ts:1104`）。
   agentao 已有 `omit_temperature` latch（`client.py:375-387`）—— 复用它，别再加一个
   平行开关。

9. **缓存断点。** 与 §2.3 相同的三个位置：system、最后一个 tool、稳定 history 的最后
   一条消息（不含 0a 临时尾消息），遵守 §2.3 copy-on-mark。
   用配置开关 gate 住；在量出来之前默认关。

10. **`stop_reason` → `finish_reason`。** `end_turn`→`stop`、`max_tokens`→`length`、
    `tool_use`→`tool_calls`、`stop_sequence`→`stop`。注意
    `_StreamAccumulator.finish_reason_reported`（`_stream_response.py:50-61`）存在的
    意义正是区分「provider 说了 stop」和「agentao 的兜底」—— 要如实设置。

11. **Usage。** Anthropic 的 `input_tokens` 只计**未缓存**那部分，所以映射必须把缓存
    字段**折进去**：

    ```text
    prompt_tokens = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
    ```

    两个缓存字段仍要加性保留，用于成本报告 —— 但它们是额外信息，不是替代品。写错这条
    不是报表瑕疵：`record_api_usage(prompt_tokens)`（`context_manager.py:289-299`）
    喂的是 **Tier-1 锚点**，`_threshold_token_estimate`（`:312-328`）拿它当「已发送前缀」
    的真实大小、只本地估算此后追加的消息。若只映射 `prompt_tokens = input_tokens`，
    前缀就会正好少报掉被缓存的那一块 —— 缓存工作良好时那是前缀的*大部分* —— 于是压缩
    推迟甚至不触发，这一轮直接撞上 provider 的上下文上限。**故障程度与 §2.3/§6.9 的缓存
    效果成正比**，正好与一个缓存特性该有的表现相反。（那段 docstring 自己的告诫「不要靠
    更信任锚点来『修』它」说的是一个有界、自愈的偏差；这个偏差无界。）

    与附录 B.5 的 Gemini 规则方向相反：Anthropic 要加回缓存，Gemini 已含缓存不能再加。
    golden fixture 必须走到 `record_api_usage` 并断言最终锚点（按 §2.3 减去固定尾部估算），
    不能只比较映射后的 usage 字段。

## 7. 后续协议（移至附录）

`openai-responses` 与 `gemini-api` 均列入后续适配器范围。Responses 翻译规则与复合 id
约束在附录 A；GenerateContent 规则在附录 B，Interactions 规则在附录 C。
按 §3 比较 B/C 后再选定 Gemini 线路。

## 8. 会长出 `api` 维度的横切面

| 面 | 改动 |
|---|---|
| `runtime/model.py::purge_thinking_artifacts` | 必须清洗所有适配器携带键的并集（§5.3），且仍在**每次**切换时运行。Gemini 的逐 part 签名携带物是实测证据：签名不能跨端点，gemini-cli 换 auth 时就得 `stripThoughtsFromHistory()`（附录 B.3），所以清洗必须按模型**或**端点触发，不能只看模型名 |
| `runtime/model.py::set_model` / `set_provider` | api 变更就是一次切换：清 latch、编码、token 锚点、observed limit —— 同一族 |
| `context_manager.parse_observed_context_limit` | 溢出错误形状因协议而异；它明确是「provider 断言、不确定就不采纳」，每个适配器都要保持这个姿态 |
| `llm/_retry.py::_classify_retry` | 状态码（`RETRYABLE_STATUS_CODES`，`:26`）**外加** #283 起 429 分支上的一次精确字符串匹配：`_is_quota_exhausted`（`:111-123`）拿 `exc.code` / `exc.type` 去比 `QUOTA_EXHAUSTED_CODES`（`:40-46`）—— 那是 **OpenAI 的错误码**。两半都与 api 绑定：Anthropic SDK 抛自己的异常类型，那套码会永远静默匹配不上，于是配额耗尽的 429 又退回重试四次。分类归适配器所有，不能共用一张表。另：`RETRYABLE_STATUS_CODES` 至今仍无 520/524（pi-mono `e5d18382a`）—— 与本设计独立 |
| `/model`、`/provider`、ACP `session/set_model` | 必须能指名 api；api 切换要发 `MODEL_CHANGED` |
| `llm/client.py::_is_gemini`（`515-527`） | **留在原地。** 精神上它是本设计的祖先，但这里的 Gemini 说的是走 OpenAI 兼容端点的 Chat Completions —— 这是既有线路上的 *provider* 怪癖（pi-mono 叫它 `compat`），不是一种线路协议。把它提升成适配器，恰好会把 §3 特意分开的 `api`/`provider` 两条轴重新搅在一起 |

`gemini-api` 按显式 `api_format` 分发，必须在进入 Chat Completions 的 `_is_gemini`
绕行逻辑之前选定；它不继承该路径的非流式限制。

## 9. 配置面

采用 **`{PROVIDER}_API_FORMAT`**：协议随凭据/端点块切换，不是全局请求偏好
（`embedding/factory.py:103-115`、`.env.example:3-5`）。同一 base URL 可服务多种协议，
所以显式配置、不从 URL 或 provider 名推断；按模型覆盖留到阶段 3。

```bash
LLM_PROVIDER=ANTHROPIC
ANTHROPIC_API_FORMAT=anthropic-messages   # 不设 → openai-completions
```

Gemini 原生配置示例（先过 §3 线路门槛，实现后启用；兼容端点仍选 `openai-completions`）：

```bash
LLM_PROVIDER=GEMINI
GEMINI_API_KEY=...
GEMINI_API_FORMAT=gemini-api
```

- 值域：`openai-completions|anthropic-messages|openai-responses|gemini-api`，随实现阶段开放；
  未知或尚未实现的值 fail closed 并列出当前合法值。默认保留现有 Chat Completions 行为。
  **`gemini-api` 在 §3 定下线路之前不进入已发布值域** —— 一个取值不能先后代表两种线路，
  届时按选定线路确认拼写。
- 构造参数 `Agentao(..., api_format=...)` 必须放在 `*` 之后，避免移位遗留位置参数。
- `_API_FORMAT` 指全局协议名，前缀只标所属 provider 块。
  `ANTHROPIC_API_FORMAT=openai-completions` 合法（`.env.example:18`）；凭据擦除目前是
  精确名匹配（`capabilities/process.py:125`），不会误删此键。
- 不加 `settings.json` provider 层：一个旋钮不足以引入新层级，env 加构造参数即可。
- **不做模型目录**：由用户指定 api；不引入 models.dev 和随模型命名变化的前缀启发式。

## 10. 分阶段计划

**主线是阶段 0 → 一个适配器。** 阶段 0 任一步已补足收益即可终止；若仍需原生缓存或
thinking，阶段 1 才做 `anthropic-messages`。Responses 与 Gemini 规则分别留在附录 A/B/C；后续适配器按需求逐个加入，
动态切换与按模型覆盖后置。

| 阶段 | 内容 | 门槛 |
|---|---|---|
| **0a** | provider 中立的 volatile 次序修复；稳定 system + history + 请求专用临时 user 尾消息（§2.3），无需新旋钮 | 对照现状测隐式前缀缓存命中与实际成本；验证尾消息不落历史、重建请求不丢失/重复，以及连续 N ≥ 10 轮 estimate − 真实 prompt_tokens 不随尾部大小系统性漂移。收益已足够则停在这里 |
| **0b** | opt-in 显式 `cache_control`，锁定一个支持的端点；SDK 透传已核实。copy-on-mark，只标稳定前缀，每次重建且不回流历史 | 与 0a 对照命中率和实际成本；多轮验证断点不累积、输入不变且最多 3 个显式 + 1 个自动缓存预留槽。**若阶段 0 补上缺口，就到此为止，下面都不做** |
| **1** | 抽取现有路径并加入一个 `anthropic-messages` 适配器，**仅启动时选择，首版同时支持流式与非流式**。官方 SDK；历史写入（§5.1）、usage 映射（§6.11）；协议自己的事件状态机与累加器，文本增量经回调交付 | 抽取 no-op 证据；双向 golden fixture；§6.2 / §6.3 / §6.7 测试；签名 thinking 完整往返；流式/非流式响应一致，工具参数与 usage 完整，取消与异常路径可用；验证缓存收益 |
| **2** | 按需求逐个加入 `gemini-api`（候选附录 B/C）与 `openai-responses`（附录 A）；首个后续适配器落地时再固化共享接口。每个新协议首版均支持原生流式 | 双向 fixture、流式一致性、取消与异常测试；先过 §12.1 剩余收益门槛，再做 Gemini 的 §3 线路/寿命比较；阶段 0 已补足收益则不做比较；协议专项门槛见附录 A/B/C |
| **3** | 动态切换：`/model` / `/provider` / ACP `session/set_model` 带 api；按模型覆盖（§9）；横切清洗（§8） | 切换确实清掉 §8 所列状态 |
| —— | 文档孪生、`CHANGELOG.md`、`docs/reference/configuration.md` | 随产生行为变更的阶段一起发 |

**抽取动作需要回归证据，独立 PR 只是取得证据最便宜的一种方式。** 把 `LLMClient` 重构成
适配器、同时加进第一个适配器，会让「抽取有没有改变今天的行为」无法靠阅读回答 —— 但一个
对照改动前输出的 `_build_request_kwargs` 字节相等测试同样能回答，而且可以放在同一个 PR 里。
两者取其便宜的；但不能两者都不要。

## 11. 测试计划

- **阶段 0 验收**：0a 对照当前行为，0b 对照 0a，分别报告命中率与实际成本；验证临时尾消息
  不持久化、工具/压缩/重试重建正确、记录连续 N ≥ 10 轮 `estimate - actual_prompt_tokens`，
  改变尾部大小并断言误差无系统性漂移；同时报告本地尾部估算误差。0b 连续多轮验证原输入未变、
  标记只在请求副本中、断点数不累积，最多 3 个显式断点且预留 1 个自动缓存槽；
  保留 SDK 透传探针与独立端点验收。
- **阶段 1 同时验收流式与非流式**：验证文本增量在流结束前经回调交付，结束后的鸭子响应
  与非流式一致；覆盖分片工具参数、thinking 签名、usage 的完整累加，以及取消、中途异常
  和资源关闭。部分响应不能被当作完整成功响应，也不能在重试时重复已交付文本。

- **每个适配器、双向 golden fixture**：一份固定的 `agent.messages`（须包含：历史中段的
  压缩摘要、一批三条 tool 结果、一个图像 part、一个来自 minimal-history 阶梯的
  assistant 开头历史）→ 精确的请求 body；以及一份录制的 provider 响应 → 精确的鸭子类型。
- **usage 验收到锚点**：fixture 经过响应映射、尾部估算扣除直到 `record_api_usage`，断言
  锚点 token 值及持久前缀长度。协议专项夹具放在 §6 和附录 A/B/C，不在这里逐协议扩列。
- **用真实 SDK 模型构造输入，绝不用 `MagicMock`。** 这是复发教训而非预防：`MagicMock`
  对任何名字都答 `hasattr`，因此满足一切能力探测，恰好把适配器层存在的意义 —— 抓协议
  断裂 —— 全部掩盖（见 mcp 2.x 兼容那次）。
- **一套一致性套件，对当阶段已实现的适配器都跑**，断言 `_stream_response.py:1-16` 枚举的鸭子类型
  属性面。过不了它的适配器就没做完。
- **阶段 1 抽取部分的 no-op 测试**：直接测试 openai-completions 适配器自己的
  `_build_request_kwargs`，同样输入 → 与抽取前逐字节相同的请求。

## 12. 待维护者决定的问题

1. **§2.3 是否让 §6/附录 A/B/C 变得不必要？** 如果便宜路径拿下了缓存收益，仍须单独评估签名保真与 Gemini 逐字输出的
   剩余价值；缓存命中不能修复当前流式绕行。这应该在授权阶段 1 之前量出来。
2. **厂商 SDK 怎么打包？** §10 已经定了**用官方 SDK** —— 对 REST 端点手写 `httpx`
   买来一个维护面，还丢掉 SDK 自己做的兼容工作，这笔账 mcp 1.x/2.x 那次已经付过。
   尚未定的是打包方式：新增一个可选 extra（与 `[cli]` / 纯库的拆分一致），还是进核心依赖。
   extra 能让裸 `pip install agentao` 保持原样，代价是多一条安装路径要写文档、要测。
3. **子 agent 继承 api 吗？** 它们今天继承 `extra_body`（`agents/tools/_wrapper.py`）。
   大概率同样处理，但必须写明。
4. **会话中途切换 api，历史怎么办？** 清洗（§8）会移除携带键，但 Anthropic 的签名
   thinking 块和 Responses 的 reasoning item 一旦丢弃都是**不可恢复**的。切换可以静默
   降级 transcript 吗，还是应该警告？

## 13. 什么会推翻这些结论

- **重选 Gemini 线路**：GenerateContent 若公布弃用/下线计划、目标模型只在 Interactions
  提供，或实测不需要旧线路独有的缓存能力，就停止以旧线路为首版目标，按 §3 重新决策。
  当前 Legacy + 迁移指南已经足以触发开工前比较，不能等到下线才算迁移成本。

- **选项 A → B**：如果任一适配器的原生语义无法通过有界携带键表达成 OpenAI dict，
  或无法通过 sanitize、压缩、session/replay 的保真测试。增加 `gemini-api` 是一次检验，
  不是仅凭协议数量就自动迁移历史模型。
- **停在阶段 0**：如果实测缓存命中率补上了缺口（§12.1）。
- **重开模型目录的决定（§9）**：只有在有证据表明用户选错 api 的频率高到值得处理时 ——
  而不是因为同行有一个。

## 附录 A. 翻译规则 —— `openai-responses`

1. **`messages` → `input` item。** 角色能映射，但工具调用与结果变成顶层 item
   （`function_call`、`function_call_output`），不再是消息字段
   （`openai-responses-shared.ts:328-350`）。

2. **双 id 问题，以及由它推出的规则。** Responses 同时带 `call_id`（用于关联输出）和
   item `id`。agentao 的历史只有一个 id 槽，而那个 id 必须逐字节往返。pi-mono 的答案是
   复合：``id = `${item.call_id}|${item.id}` ``（`openai-responses-shared.ts:488`），
   出站时再拆开（`:334`，`const [callId] = msg.toolCallId.split("|")`）。
   **采用复合 id；不要往历史 dict 里加第二个 id 字段** —— 新键得活过 sanitize、压缩、
   replay 和 session load，而压缩的配对规则正是以 `tool_call_id` 匹配为键的
   （`context_manager.py:1219-1221`）。若采用复合 id，要加一个测试覆盖 provider 自己的
   `call_id` 里就含 `|` 的情况（按**最后**一个分隔符切，或者转义）。

3. **v1 保持无状态：`store: false`**（`openai-responses.ts:318`）加
   `include: ["reasoning.encrypted_content"]`（`:353`）。**不要**采用
   `previous_response_id`。agentao 的历史是唯一真相 —— 压缩会重写它，`/clear` 会抹掉它，
   replay 会重放它。服务端会话 id 会静默偏离这三者，而偏离的表现形式是：模型记得用户已经
   清掉的东西。注意无状态**不**等于少发：`store: false` 下，加密的 reasoning item 必须在
   下一次请求的 input 里**原样送回**，这正是 §5.3 那个携带键的用途。省掉的是 provider 端
   的会话，不是线上的字节。

4. **tool schema 是摊平的** —— Responses 把 `name`/`parameters` 放在 item 层，不嵌在
   `function` 下。从 `to_openai_format()` 翻译（§5.2）；不要分叉 schema 产出点。

5. **`max_output_tokens` 有下限**（16，据 `openai-responses.ts:32`）。agentao 今天把
   `max_tokens` 直通；适配器里要夹紧。

6. **reasoning item 必须回放。** `openai-responses-shared.ts:533-548` 记录了 Azure 可能
   在逐 item 事件里省略 `encrypted_content`、只在终态响应里给 —— 所以适配器必须从终态
   载荷回填，不能假设 item 事件带了它。这正是单 provider 测试永远抓不到的那类 bug。

7. **流式是事件定型的流**（`response.output_item.added`、
   `response.function_call_arguments.delta` …），不是 chat delta。
   `_stream_response.py` 里的累加器是 Chat-Completions 形状的
   （`tool_call_key()` 围绕 `index` 字段推理，`:71-96`），Responses 适配器需要**自己的**
   累加器来产出同一个鸭子类型，**而不是**去改那一个。`_StreamAccumulator` 原样保留 ——
   它对无 index provider 的处理是拿真实 bug 换来的（goose #10023）。

**协议专项验收：** 复合 id（包括含 `|` 的原生 id）、终态 reasoning 回填、无状态
完整历史回放，以及事件流终态与非流式响应一致性；共用验收仍见 §11。

## 附录 B. 翻译规则 —— `gemini-api` GenerateContent 候选

本附录有对照实现可读：pi-mono `5a3a03a7f` 的 `google-generative-ai.ts` /
`google-vertex.ts` 走同一条线路的原生流式（见页首锚点）。对照实现只佐证规则真实存在，
不是规范，也不改变 §3 的线路决定。以下仅是待 §3 线路决策确认的 GenerateContent 候选规则，
不能直接当作 Interactions 规范。范围为 Gemini Developer API 的 `generateContent`
与 SSE `streamGenerateContent`，
使用官方 Google Gen AI SDK（`google-genai`）。以下是设计要求，不代表适配器已实现或端点
集成测试已通过。

1. **消息与工具。** 历史翻译为 `contents`，使用 `user` / `model` 角色及按序 `parts`；
   初始 system 提升到 `systemInstruction`。规范 tool schema 翻译成
   `functionDeclarations`，调用与结果分别为 `functionCall` / `functionResponse`。
   保留原生调用 id；缺少 id 时生成稳定本地 id 并保留无歧义配对映射，同名并行调用不能
   仅靠名称配对。工具仍由 agentao 执行，不另开 SDK 自动工具执行循环。
   [GenerateContent 参考](https://ai.google.dev/api/generate-content)。

2. **历史修复。** 对历史中段摘要与溢出后的 assistant 开头历史（§6.2/§6.3），分别验证
   Gemini 的翻译。摘要保持时间位置，折入合适的 user content；不提升到稳定 system
   前缀，不拆开工具调用/结果组。出站副本把后续通知/临时尾部 user parts 接在全部 function-response parts 后，
   保持顺序，并对选定端点测试连续 user 输入。通知仍持久化，0a 尾部仍只在请求中。

3. **签名。** 保留带签名的 parts 及其顺序，包括只携带签名的空文本 part；不能合并或
   截断带签名的 part。SDK 自动签名处理以保留完整原生响应为前提，agentao 的 dict 历史
   需要 §5.3 的显式携带物和持久化测试。展示文本与原生回放表示分开处理。

   两条来自对照实现的细则。其一，**签名不等于 thinking**：`thought: true` 是唯一的
   thinking 标记，签名可以出现在**任意** part 上（文本、`functionCall` 皆可），
   pi-mono 的 `isThinkingPart` 因此只读 `thought`（`google-shared.ts:112-131`）—— 把带
   签名的 part 一律当成 thinking，会把普通文本送进 reasoning 的展示与截断路径。其二，
   **流式里签名可能只出现在同一块的第一个 delta**，后续 delta 会省略：累加器要在块内保留
   最后一个非空签名，并且**绝不跨 part 搬移或合并**（`retainThoughtSignature`，
   `google-shared.ts:133-145`）。第二条与附录 A.6 的 Azure 漏 `encrypted_content` 同类，
   单 provider 测试抓不到。

   **签名缺失本身会 400，而 agentao 的历史正会缺。** gemini-cli 的注释直说：为了让请求
   通过校验，活动循环内每个 model turn 的**第一个** functionCall 必须带
   `thoughtSignature`，否则 API 返回 400；缺失时它补一个占位签名
   `SYNTHETIC_THOUGHT_SIGNATURE = 'skip_thought_signature_validator'`，且只补每条消息的
   第一个 functionCall（`geminiChat.ts:110,1259-1310`）。agentao 有三条路径必然产出这种
   历史：压缩重写历史、`/resume` 从更早的 session 文件恢复（那时还没有这个携带物）、
   minimal-history 切进一段工具调用。**这是 Gemini 线路上最可能的静默 400**，与 §6.2 同类，
   适配器必须在出站副本里补占位或显式失败，不能默默发出去。

   **签名不能跨端点。** gemini-cli 在换 auth 时调用 `stripThoughtsFromHistory()`，理由写在
   `config.ts:1580-1589`：Genai 与 Vertex 的加密不兼容，带着 Genai 签名的历史发给 Vertex
   会失败。所以这个携带物必须进 §8 的清洗并集，而且 agentao 的清洗触发条件（模型**或**
   端点变化）本来就是对的形状 —— 这条是它必须覆盖新携带物的证据，也是 §9 把 Vertex 排除在
   首版之外的技术理由：同一协议、换个端点，携带的签名并不通用。
   [Thought signature 规则](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)。

4. **流式与错误。** 首版即实现原生累加器，增量交付可见文本，同时收齐调用、签名与
   usage。显式映射终止/阻断原因；无 candidate 或被阻断的响应不能伪装成成功的空答案。
   验证取消及中途失败。`describe_error` 分类 Google SDK 异常，包括配额和有明确断言的
   上下文上限，不套用 OpenAI 错误码表。

5. **Usage。** `prompt_tokens = promptTokenCount`，它已包含缓存 token，不能再加一次。
   `cachedContentTokenCount` 单独保留；completion 映射为
   `candidatesTokenCount + thoughtsTokenCount`（可选计数缺失按零处理），保留 provider
   总数及明细用于核对。流式重复报告的累计 usage 不能逐 chunk 求和。
   与 §6.11 相反，缓存不再加回。fixture 须断言 `record_api_usage` 的最终锚点
   （含 §2.3 的尾部扣除），不只断言字段映射。
   **两份对照实现在这里写法相反，所以哪一行都不能照抄。** pi-mono 的 `input` 意思是
   「未缓存输入」，于是写 `promptTokenCount - cachedContentTokenCount`
   （`google-generative-ai.ts:231-240`）；gemini-cli 的 `inputTokens` 意思是「总输入」，
   于是直接写 `promptTokenCount`，另出 `cachedTokens`（`event-translator.ts:468-470`）。
   两者各自自洽 —— 同一个字段名在两处含义不同，这也从第二、第三个独立来源印证
   `promptTokenCount` 是含缓存的总数。agentao 的 `prompt_tokens` 要的是**总数**，所以
   照抄 pi-mono 那一行会让 Tier-1 锚点少报被缓存的前缀：与 §6.11 警告的失败方向一致，
   且缓存命中越多越严重。抄之前先确认目的字段的语义，而不是字段名。
   [UsageMetadata](https://ai.google.dev/api/generate-content#UsageMetadata)。

6. **缓存。** 显式缓存创建资源，再由 `cachedContent` 引用，不是 Anthropic 的逐块
   `cache_control`。原生适配器首版可先不管理显式缓存资源；启用前另行决定创建、TTL、
   复用、压缩或切换模型后的失效策略，并实测存储及请求成本。资源句柄属于适配器运行时
   状态，不写进规范历史；session 重载不能依赖旧句柄。隐式缓存仍可配合阶段 0a。
   [Gemini 上下文缓存](https://ai.google.dev/gemini-api/docs/generate-content/caching)。

- **Gemini 验收**：同名并行工具调用配对、历史中段摘要、压缩后的历史起点、仅含签名的
  空文本 part、签名经过 session/replay 的无损往返、流式终态 usage；验证
  `promptTokenCount` 已含缓存而不重复相加。每个适配器只跑自己的协议专项 fixture。


## 附录 C. 翻译规则 —— Google Interactions API

**没有对照实现。** 两份本地实现都不用 Interactions：pi-mono（`5a3a03a7f`）的十值
`KnownApi` 里没有 Interactions 适配器，两个 Google 适配器都走 GenerateContent；
gemini-cli（`9450ade79`）三条接入路径同样全是 GenerateContent 系列。所以以下规则全部
来自一手文档，尚无任何可读的工作实现佐证 —— 这项不对称记在 §3。

这是与附录 B GenerateContent 并列的 `gemini-api` 线路候选，为 §3 比较提供设计，
不表示授权同时实现两条线路，也不新增配置值。先过 §12.1，再在发布适配器前选定线路：
若 §12.1 显示阶段 0 已补上缺口，附录 B 与 C 都不实施。
使用官方 `google-genai` SDK；首版覆盖模型文本/图像输入、客户端执行工具和原生流式。
托管 agent、provider 托管工具与后台执行不在首版范围。以下来源核实于 2026-09-18，
目标端点 fixture 尚待执行。

1. **明确无状态。** 每次调用，包括工具续接与重试，都显式设置 `store=false`，不传
   `previous_interaction_id`。从当前规范历史重建 `input`，每次重传 system 指令、工具
   和生成设置；压缩、`/clear`、replay 继续决定上下文。测量无状态隐式缓存收益；该候选
   暂不提供显式缓存。引入服务端会话链必须重开附录 A.3，不能静默改变适配器行为。
   [Interactions 概览](https://ai.google.dev/gemini-api/docs/interactions-overview)。

2. **翻译 steps，不套用 GenerateContent parts。** user 消息映射为 `user_input`，
   assistant 文本为 `model_output`，初始 system 为 `system_instruction`。中段摘要保持
   时间位置，临时尾部仍只在请求中（§2.3）。按序遍历响应 `steps`；单读 `.output_text`
   会漏掉被非文本 step 隔开的较早文本。构造既有鸭子响应，同时保留原生序列用于回放。
   不支持的内容显式报错，不能静默丢失。
   [迁移指南](https://ai.google.dev/gemini-api/docs/migrate-to-interactions)。

3. **工具与历史归属。** 规范 schema 翻译为 Interactions function 声明。
   `function_call.id` 映射为规范 tool-call id，结果映射为 `function_result.call_id`，
   携带 `name` 和带类型的 `result` 内容。id 逐字节保留，同名并行调用不能混配。
   无状态续接需包含 user 输入、全部模型返回 steps 和工具结果。通过 §5.1 持久化按序
   模型 step 携带物；存在该携带物时只发一次，不再从展示字段重复构造文本与调用。
   检出导致携带物失效的历史修改，不能让旧携带物覆盖压缩结果。工具由 agentao 执行。
   [无状态工具调用](https://ai.google.dev/gemini-api/docs/function-calling#stateless-function-calling)。

4. **Thinking 是独立 step。** 完整保留 `thought` 的 `signature`，以及可能缺失或为空的
   `summary`；不摊平到 function call，也不当成 GenerateContent 的 `thoughtSignature`
   part。原生携带物须可 JSON 序列化、保持顺序且不截断，展示摘要仍可限长。接通 §5.1
   全部写入点、sanitize 豁免、session/replay 和 `purge_keys()`。未知原生 step 在支持其
   回放语义前必须显式失败。
   [Interactions thinking](https://ai.google.dev/gemini-api/docs/thinking)。

5. **首版原生流式。** 调用 `interactions.create(..., stream=True)`，按 step index
   累加 `step.start`、`step.delta`、`step.stop`。可见文本经 `on_text_chunk` 输出，参数和
   签名增量分别保留。某个 step 结束不等于整个 interaction 结束：终止只由
   `interaction.completed`、`error` 或 `done` 判定，`step.stop` 不是收尾信号（同一个流
   另有 `interaction.created` 与 `interaction.status_update`，共八种事件，2026-09-18
   核实）。核对终态状态及 usage 后才返回鸭子响应，不能再追加一次终态文本。测试取消、流中断及已输出文本后的异常，
   重试不能盲目重复已交付文本。
   [流式 Interactions](https://ai.google.dev/gemini-api/docs/streaming)。

6. **状态、错误与 usage。** 带受支持 function calls 的 `requires_action` 映射为
   `tool_calls`；completed 响应根据实际终止信息映射。失败或未完成不能变成成功的空答案。
   `describe_error` 负责 SDK 异常分类和有明确断言的上下文上限提取。保留
   `total_input_tokens`、`total_cached_tokens`、`total_output_tokens`、
   `total_thought_tokens`、`total_tool_use_tokens`、`total_tokens`。候选映射为
   input → prompt，output + thought → completion，cached/tool-use 保留为明细。
   这不是已实测的端点映射：必须用缓存命中/未命中与 thinking fixture 确认包含关系再定案，
   不能从附录 B 字段名推断，也不能重复累加流式累计 usage。断言 §2.3 扣除尾部后的最终
   锚点；usage 缺失时不能制造零值锚点。
   [Interactions API 参考](https://ai.google.dev/api/interactions-api)。

**协议专项验收：** 无状态多轮工具执行，模型 steps 完整且仅回传一次；同名并行调用；
只有签名的 thought；非文本 step 前后均有文本；摘要/通知/临时尾部顺序；压缩、`/clear`、
session 重载均不依赖 interaction id；流式/非流式一致；取消与中途失败；缓存命中/未命中
usage 及最终锚点。固定测试的 SDK/API schema 与目标模型，记录不支持的情况，与附录 B
结果一起用于 §3 线路比较。
