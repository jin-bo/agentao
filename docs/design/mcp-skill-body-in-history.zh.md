# MCP 技能正文写入持久历史（#552：MCP 非工具路径，及压缩后的回退）— 设计

> English: [mcp-skill-body-in-history.md](mcp-skill-body-in-history.md)

**状态：** 设计评审通过（附录 A 第 1–2 轮），2026-10-28。§6 选定方案 (a)。尚未实施。
**基线：** Agentao `96cca5b7`（#594 + #595）。
**Issue：** [#552](https://github.com/jin-bo/agentao/issues/552)。本文承接 [skill-body-in-history.zh.md](skill-body-in-history.zh.md)：那份文档把本地和插件技能的正文移进了历史，留下了 MCP 路径（该文 §2.3、§6）。

## 摘要

- **范围：**
  1. 由 `spec.skills`（`agentao run`）或 `/skills activate` 激活的 MCP 技能。这两条路径不产生 `activate_skill` 结果，#593 的指向提示用不上，正文每次请求都在 tail 里，不进缓存。
  2. 模型激活的 MCP 技能，其 `activate_skill` 结果已被压缩删掉或摘要掉。目前正文会回到 tail。
- **方案：** 沿用本地技能的 `Skill loaded` 状态消息。正文用 `_render_mcp_skill` 渲染的块，末尾加一行完整的来源标记。压缩、会话保存和子代理各路径本来就读这个标记作为来源。名称前缀 `mcp:` 是第二个信号，不依赖标记。
- **有效状态由当前激活集合决定。** 与当前持有字节一致的完整 `activate_skill` 结果就是那一份，它取代更早的状态消息。这个结果没了以后，再以状态消息补入当前版本（§2.2）。
- **要补的缺口：** `withhold_mcp_skill_content` 目前只覆盖工具结果。它还必须在恢复时扣留 MCP 状态消息的正文，同时保留消息首行。
- **来源取实际提取到的并集。** 一条消息什么都提取不到时，才回退到未知来源 `*`（§2.4）。

## 1. 现状（在 `96cca5b7` 上 grep 核实）

| 路径 | 正文在哪里 | 历史中的来源记录 |
|---|---|---|
| 模型 `activate_skill`，结果完整 | 工具结果，tail 指向它（#593） | 工具结果（`provenance.is_skill_result`） |
| 模型 `activate_skill`，结果被摘要掉或被扣留 | 每次请求的 tail | 摘要标记或占位符标记 |
| `/skills activate` | 每次请求的 tail | `cli/commands/skills.py:20::_record_mcp_activation` 追加一条带 `result_marker([label])` 的 user `<system-reminder>` |
| `spec.skills`（`cli/run.py:818`） | 每次请求的 tail | 无。来源只在保存时取自 `_mcp_session_origins`（持有条目）和 `name_origin(active_skills)` |
| 恢复 | 不重新激活（D5，`embedding/sessions.py:583`） | 工具结果被扣留（`withhold_mcp_skill_content`），来源被标记为需门控（`_gate_restored_origins`） |

本设计依赖的现有事实：

1. **user 消息上的整行标记是可信来源。** `provenance.marker_origins` 读取除 assistant 消息和非子代理工具结果以外所有消息上的标记。`_record_mcp_activation` 已经依赖这一点。
2. **所有来源读取方都读标记：**
   - `skill_origins`，用于压缩摘要标记（`context_manager.py:1317`）、会话文件的 `mcp_skill_origins`（`sessions.py:307`）和子代理结果（`_wrapper.py:1188`）。
   - crystallizer，它跳过带标记的 user 消息（`context_manager.py:1301`）。
3. **门控只看持有条目，不看激活集合，也不看历史。** 停用的技能，其持有条目一直保留到 `/clear`，所以停用后留在历史里的正文仍然受门控。
4. **#594 的读取方都已跳过状态消息**：crystallizer、`/crystallize`、摘要器（只看首行）、子代理的父上下文、召回提示。
5. **本地和插件技能不能用 `mcp:` 前缀**（CLAUDE.md，MCP Skills）。名称以 `mcp:` 开头的状态消息就是 MCP 内容。
6. **文本中没有 `<mcp-skill server=…>` 包装时，`provenance.content_origins` 返回 `{"*"}`。** 这一点影响 §2.4。

## 2. 设计

### 2.1 loaded 消息

```text
<system-reminder>
Skill loaded: "mcp:<label>:<uri>"
This is the current version of this skill. It replaces any earlier instructions of this skill in this conversation.
## mcp:<label>:<uri>
<_render_mcp_skill(held)>
[This result includes content from MCP skills served by: <label>]
</system-reminder>
```

- **正文是 `_render_mcp_skill(held)`**，与 tail 和工具结果的字节相同。持有的字节在激活时已经固定，所以激活快照语义不需要新代码。
- **标记独占一行**，由 `result_marker([label])` 写出。它在 `<mcp-skill>` 包装之外，恢复时的块替换不会删掉它。
- **转义：**
  - `_render_mcp_skill` 已转义 `</mcp-skill`。
  - 消息还要像本地消息一样，用 `_CLOSING_REMINDER_RE` 转义 `</system-reminder`。
- **没有 `Task:` 行。** 任务只在 tail 里。
- `_skill_loaded_message` 增加 MCP 分支，按 held 对象和 title 缓存。

### 2.2 有效状态

**规则：** 当前激活集合决定模型应当有什么。读历史只为找出该技能最新的*记录*，并且只回答一个问题：模型是否已经有了这一份？

一个技能在历史中的记录有三种：
- 它的 `Skill loaded` 消息（任意版本）；
- 它的 `Skill no longer active` 通知；
- 对 MCP 技能而言，`provenance.mcp_activation_name()` 认定为一次加载的 `activate_skill` 结果。该函数只匹配首行为 `Skill Activated: mcp:…` 的结果。被拒绝、被否决、被取消或执行失败的调用返回的是别的文字，不算加载，也不能取代之前的停用通知。恢复时被扣留的激活结果保留了这一首行，所以仍算一次加载，而该技能此时未激活，下一次同步会发停用通知。

*最新记录*指下标最大的那一条。

```
pointed = mcp_pointer_targets(history)        # 与当前持有字节一致的完整结果（#593）
对每个激活技能：
    本地                     → want = loaded(name)；最新状态消息 != want 时追加
    MCP，held 为 None        → 不需要
    MCP，name 在 pointed 中  → 不需要：完整结果取代所有更早的状态消息
    MCP，其他情况            → want = loaded(name)；最新状态消息 != want 时追加
对每个未激活、且最新记录是一次加载（loaded 消息或 activate_skill 结果）的技能
                             → 一条 "no longer active" 通知
```

由此得到以下结果，每一行都有测试（§5 第 5 项）：

| 序列 | 结果 |
|---|---|
| loaded A → 通知 → 模型重新激活，结果完整 | 激活且被指向：不追加。旧通知被取代，不会误判为已停用。tail 指向该结果 |
| loaded A → 模型以 B 重新激活（重新授权），结果完整 | 指向 B：不追加。tail 指向 B，不会指向 A |
| 同上，之后压缩删掉了 B 结果 | 不再被指向。最新状态消息是 A 或通知，不等于 loaded(B)，所以追加一次 loaded(B)。再同步一次不再追加 |
| 模型激活，结果完整，之后停用 | 最新记录是一次加载（工具结果）：追加一条通知。**这是模型路径的新行为**，现在不发通知 |
| 停用后在同步前重新激活 | 处于激活状态，且最新记录仍然一致：不追加 |
| 通知 → 模型重新激活失败（被拒绝或出错）→ 同步 | 失败的结果不算加载，技能也未激活：通知仍是最新记录，不追加，也不再发新通知 |

- 通知循环必须使用完整的激活集合。它现在只用*本地*激活技能，所以一个最新消息为 "loaded" 的激活中 MCP 技能会被误发通知。
- `get_skills_context` 的 MCP 分支：
  - `name in pointed` 时指向工具结果；
  - 否则，最新状态消息等于 `want` 时指向状态消息；
  - 否则发送技能块。
  - 本地的提示语（`LOCAL_SKILL_IN_HISTORY_NOTE`）已经写的是“最新的 'Skill loaded' 提醒”，可以原样复用。

### 2.3 运行器

不需要新的调用点。`_sync_skill_state` 已经在以下位置运行：
- 估算之前，
- 循环内压缩之后，
- 两级溢出处理之后，
- 413 重试之后。

微压缩只处理工具消息，所以不会裁剪状态消息；被指向的完整结果仍照旧整条保留。

### 2.4 来源

- **新增：** `provenance.is_mcp_skill_state_message(m)`，即 `skill_state_name(m)` 以 `mcp:` 开头。它基于 #595 的唯一识别函数。
- **这类消息的 `skill_origins`** 取实际提取到的 label 的并集。辅助函数给出的*回退值* `*` 不算提取到的 label：
  - 各包装上的 `server=` 属性，直接用 `_SERVER_ATTR` 读取。**不用** `content_origins`，后者在没有包装时返回 `*`。
  - 消息上的标记（`marker_origins`）。标记中**明确**写出的 `*` 算提取到的 label，予以保留：它表示“服务器未知”，保持其门控含义。
  - `name_origin(name)`，仅在它解析出 label 时采用。对无法解析的 `mcp:` 名称它返回 `UNKNOWN_ORIGIN`，这个回退值丢弃。
  - 什么都没提取到时，才返回 `{UNKNOWN_ORIGIN}`。
  - 扣留后的占位消息和停用通知都没有包装，但名称和标记仍能给出 label，所以不会被扩大为 `*`。
- **删除 `_record_mcp_activation`（`/skills activate`）。** 它记录的内容由状态消息替代，而它的文字也会变得不对。从命令执行到第一次同步之间，来源仍可从持有条目（`_mcp_session_origins`）和保存时的 `active_skills` 读到。有一个测试专门覆盖这段时间（§5 第 6 项）。

### 2.5 恢复：扣留

`withhold_mcp_skill_content` 在检查工具角色之前加一种情况：

```
if is_mcp_skill_state_message(message) 且它是 loaded 消息:
    head = 它的前两行（开头标签 + "Skill loaded: …"）
    内容替换为 head + withheld(§2.4 的来源) + "\n</system-reminder>"
```

- 保留首行，消息就仍是状态消息：
  - 所有“用户原话”的读取方仍然跳过它；
  - 它的最新记录是一个未激活技能的加载，所以下一次同步会追加 `Skill no longer active` 通知。
- 扣留后的文字带有标记，再保存、再恢复一次，来源仍然相同。
- 通知消息不含正文，保持不变。
- `_gate_restored_origins` 中没有 Skills 会话的分支：它把所有带标记的消息替换成 `withheld(origins)`。对状态消息，它必须使用同一个保留首行的辅助函数。
- 替换的是列表元素，不就地修改，所以 ACP 给用户回放的历史不受影响。

### 2.6 子代理和 `agentao run`

- 子代理的 `active_skills` 一开始为空，`_build_parent_context` 跳过状态消息，所以 MCP 正文不会经这两处到达子代理。
- 用 `spec.skills` 的 `agentao run`：正文在历史中出现一次，不再出现在每次请求的 tail 里。

## 3. 不改动的部分

- 授权规则：模型激活仍需 `require_approval`；`/skills activate` 和 `spec.skills` 本身即授权（`mcp/skills.py:804`）。
- 门控（持有条目）、`/clear`（新的会话代）、D5（恢复时不重新激活）。
- 0.4.27 的系统前缀不变量和技能目录。
- 结果完整时的工具结果路径，只多出 §2.2 的停用通知。

## 4. 风险

| 风险 | 缓解 |
|---|---|
| user 角色消息中的 MCP 文本被当成用户原话 | #594 的所有读取方都跳过状态消息，标记同样会让 crystallizer 跳过。`_render_mcp_skill` 的说明文字写明内容来自服务器 |
| 恢复后正文未经授权仍在上下文里 | §2.5 扣留正文。测试覆盖有 Skills 会话和没有 Skills 会话两个分支 |
| 宿主伪造一条形似状态消息的消息 | 结果是它被扣留，且增加门控，两者都偏安全 |
| 标记损坏 | `name_origin(name)` 和 `server=` 仍能给出 label |
| 没有包装的消息把来源扩大为 `*` | §2.4 只合并实际提取到的来源。往返测试（§5 第 4 项）断言不出现 `*` |
| 摘要丢失“这是 MCP 技能”的信息 | `skill_origins(prep.to_summarize)` 读的是消息本身，不是摘要器看到的首行 |

## 5. 测试（验收）

使用真实的 `mcp.types` 模型和 `tests/support/skills_mcp_server.py`，不用 `MagicMock` 替身（CLAUDE.md）。

1. **激活：** 分别用 `spec.skills` 和 `/skills activate`，正文在历史中出现一次，tail 只有指向提示，第二轮不再追加。
2. **停用：** 追加一条 `Skill no longer active` 通知。同步前先停用再激活，不追加任何消息。
3. **压缩（按条件）：** 对完整压缩、两级溢出处理和 413 重试：
   - 压缩删掉了 loaded 消息或完整结果时，追加一份；
   - 压缩保留了它时，不追加。
   - 只要有 MCP 内容被摘要，压缩摘要就带有来源标记。
4. **恢复后、重新授权前：**
   - 正文被扣留，首行保留，下一次同步追加停用通知。
   - 有 Skills 会话时，门控对 shell 发出询问；没有时，内容被扣留。
   - 再次 `/skills activate` 后，追加一条新的 loaded 消息。
   - **恢复 → 保存 → 再恢复：** 会话文件的 `mcp_skill_origins` 每次都恰好是原来的 label，没有多出 `*`。
   - **来源边界情况：**
     - 名称无法解析、标记有效：只得到标记中的 label；
     - 标记明确写出 `*`：保留 `*`；
     - 什么都没提取到：得到 `{*}`。
5. **状态转换（§2.2 的表）：** 每一行一个测试，包括“停用 → 重新激活失败 → 同步”。断言不会误发通知、不会指向旧正文、不会重复追加。
6. **第一次同步前保存：** 在 `/skills activate` 和 `spec.skills` 之后、任何一轮之前保存，`mcp_skill_origins` 记录了该 label。
7. **模型路径：** `activate_skill` 结果完整时不追加状态消息，tail 指向该结果。
8. **转义：** 服务器 SKILL.md 中的 `</system-reminder` 和 `</mcp-skill` 既不在 ACP 回放中泄漏，也不会提前关闭包装。
9. **读取方：** crystallizer、`/crystallize`、父上下文和召回提示都跳过该消息（一个参数化测试）。
10. **变异检查：** 回退 §2.5 后，第 4 项失败；把 §2.4 的并集换回 `content_origins` 后，往返断言失败。

## 6. 决定：压缩后的模型激活技能

**选定 (a)。** 完整结果没了以后，正文像非工具路径一样进入状态消息。所有 MCP 技能用一条规则，每次压缩后 tail 每次请求都带一份正文的情况也随之消失。

不采用 (b) 保留 tail：缓存未命中仍在，两条 MCP 路径的生命周期也不一致。

结果完整时，模型调用 `activate_skill` 的路径不变。新增的只有停用通知（§2.2）和结果没了以后的回退。

## 7. 不在范围内

- 从请求中删除已停用技能的正文。
- 测量 MCP 场景。[#552 评论](https://github.com/jin-bo/agentao/issues/552#issuecomment-6489784417)中的本地测量支持这一机制，但不能说明 MCP 场景的收益量级，本设计也不对此作出声称。

## 附录 A — 评审记录

- **2026-10-28，第 1 轮。** 选定 (a)。已做的修改：
  1. **来源：** 取实际提取到的并集，什么都提取不到时才用 `*`。新增“恢复 → 保存 → 再恢复”测试。只用 `content_origins` 的话，没有包装的占位消息或通知会被扩大为 `*`。
  2. **状态转换：** 当前激活集合决定有效状态。与持有字节一致的完整结果取代更早的状态消息；它没了以后补入当前版本。新增转换表和对应测试。
  3. **同意删除 `_record_mcp_activation`。** 新增“第一次同步前保存”测试。
  4. **压缩验收改为按条件：** 只有那一份确实被删掉时才追加。
  5. **收窄**关于 MCP 缓存收益的表述。
  6. **标题和范围**补充了压缩后的回退。
- **2026-10-28，第 2 轮。** 设计评审通过，文档 PR 在 CI 通过后可以合并。补充的澄清：
  1. **§2.4：** 对无法解析的名称，`name_origin()` 返回的 `UNKNOWN_ORIGIN` 是回退值，不是提取到的 label，从并集中丢弃。标记中明确写出的 `*` 予以保留。新增边界情况测试。
  2. **§2.2：** 加载记录指 `mcp_activation_name()` 能读出的结果。被拒绝、被取消或执行失败的 `activate_skill` 结果不算，不能取代停用通知。新增“停用 → 重新激活失败 → 同步”用例。
  - 本轮只评审了文档及其引用的现有代码，尚未验证实现。
