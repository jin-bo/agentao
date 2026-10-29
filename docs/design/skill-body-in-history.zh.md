# 激活技能正文进入持久历史（#552 本地部分）— 设计

> English: [skill-body-in-history.md](skill-body-in-history.md)

**状态：** 已实施，分支 `fix/552-local-skill-body-in-history`，2026-10-28。附录 A 记录评审经过。
**基线：** Agentao `44502d73`。对照：Codex `26ad281fd1c`、pi-mono `136b87e8a`。
**Issue：** [#552](https://github.com/jin-bo/agentao/issues/552)。#593 解决的是"模型调用 `activate_skill` 激活 MCP 技能、且历史中的结果完整"这一条路径。本文处理本地技能和内联 plugin 技能。MCP 技能保持现状。

## 摘要

- **正文进入持久历史，尾部只放状态。** 每个激活的本地技能或 plugin 技能，在历史中有一条 user `<system-reminder>`，以 `Skill loaded: "<name>"` 开头。尾部只给出 `Task:` 和一行指向提示。
- **每个技能只以最后一条状态消息为准。** 状态消息有两种：`Skill loaded` 和 `Skill no longer active`。最后一条与当前期望的消息全等，就跳过；否则追加一条。规则只依赖历史，所以是幂等的。
- **同步函数只有一个，调用点有多处。** 流程是：同步并重建请求 → 估算与压缩 → 如果历史被替换，再同步并重建 → 发送。溢出阶梯的两级也照此处理。补回的正文仍然受预算和溢出处理约束。
- **激活快照。** 正文和资源列表在激活时冻结。重新激活或 `/skills reload` 才会刷新。这是契约变化，已写进 CHANGELOG。
- **MCP 技能不在本期范围内**（§2.3）。

## 1. 实施前的状态（已用 grep 核实）

`SkillManager.get_skills_context()` 每次请求都会运行一次，由 `SystemPromptBuilder.build_volatile_tail` 调用（`prompts/builder.py:268`）。

| 激活路径 | 入口 | 正文所在位置 |
|---|---|---|
| 本地 `SKILL.md` | `tools/skill.py:75`、`cli/commands/skills.py:70`、`cli/run.py:819`（`spec.skills`）、`embedding/sessions.py::restore_agent_skills` | 每次请求的尾部。每次重新读文件，资源目录也每次重新扫描 |
| 内联 plugin skill（`full_content`，无 `path`） | 同上 | 每次请求的尾部 |
| MCP，通过模型调用 `activate_skill` 激活，且结果完整 | `tools/skill.py` | 正文在历史中，尾部只有指针（#593） |
| MCP，通过 `spec.skills` 或 `/skills activate` 激活 | 同上 | 尾部 |
| MCP，会话恢复 | `restore_agent_skills` | 不恢复，并扣留正文（D5） |

#552 实测的工作负载用 `spec.skills` 激活了一个本地技能，属于第一行。

可以复用的先例：

- 后台通知作为持久的 user `<system-reminder>` 写入 `agent.messages`（`_inject_background_notifications`）。
- `anthropic-messages` 适配器会合并连续的同角色消息。Chat Completions 接受连续两条 user 消息。
- ACP 回放会剥掉 `<system-reminder>` 块。剥掉后为空的 user 消息不会发给客户端（`acp/_transport_replay.py:194-199`）。
- 微压缩只处理 `role: "tool"` 消息。

## 2. 对外部建议的评审

### 2.1 成立的部分

| 论断 | 依据 |
|---|---|
| `spec.skills` 场景仍未解决 | `cli/run.py:819` 不产生工具结果，本地分支总是输出正文 |
| “激活期间正文不变”不是现有保证 | `get_skill_content` 每次都 `open()`。没有测试固定这种实时重读，所以它是附带行为 |
| 方案 B 会撤销 0.4.27 的不变量 | `tests/test_skills_prompt.py::test_activating_a_skill_leaves_the_system_message_byte_identical` |
| Codex：`<skill>` 片段，`role=user`，写入会话历史 | `codex-rs/ext/skills/src/fragments.rs:87-106`、`core/src/session/turn.rs:429` |
| pi-mono：`/skill:name` 展开成 `<skill>`，作为用户输入 | `packages/coding-agent/src/core/agent-session.ts:2211-2230` |

两个同类项目都没有停用通知，也都没有在压缩后重新注入。Agentao 两者都需要。

**验收口径：** 模型面对的当前状态是明确的。已停用或已替换的旧正文允许留在历史中，由后来的状态消息取代。

### 2.2 修正：激活工具结果不能直接复用

只有 MCP 技能的 `activate_skill` 结果带正文。本地技能的结果只有元数据和资源列表。这里有两种做法：(a) 把正文放进工具结果；(b) 所有入口统一由状态消息携带正文。(a) 只覆盖五个入口中的一个，还要把 #593 的微压缩保护推广出去。**选 (b)。**

### 2.3 修正：MCP 的非工具路径不并入

如果把 `<mcp-skill>` 放进 user 消息，会破坏三条不变量：

1. 来源识别按工具来源判断（`provenance.is_skill_result`）；
2. 恢复时的扣留按工具名判断（`withhold_mcp_skill_content`），恢复后服务器指令会留在上下文中，shell 门控却没有打开；
3. CLAUDE.md 要求移动 MCP 消息时必须携带来源标记。

所以这些路径维持现状，单独立项（§6）。

### 2.4 修正：不加版本字段

消息 dict 上的自定义键会发到服务端。判重按内容比较。

## 3. 设计（已实施）

### 3.1 激活快照

- `activate_skill()` 把 `_render_local_skill(name)` 的结果存为 `active_skills[name]["snapshot"]`，内容包括技能目录说明、`SKILL.md` 全文和资源列表。
- 对已激活的技能再调用一次 `activate_skill()`，会重新生成快照。
- `reload_skills()` 为仍处于激活状态、且扫描仍能找到的本地技能重新生成快照。`/crystallize` 依赖这一点。
- 宿主直接写进 `active_skills`、没有快照的条目，在第一次使用时补拍，之后保持不变（`_snapshot`）。
- 否决了按请求重读文件再比较哈希的做法：如果资源目录每次请求都变，历史会无界增长。

### 3.2 状态消息

```text
<system-reminder>
Skill loaded: "<name>"
This is the current version of this skill. It replaces any earlier instructions of this skill in this conversation.
## <name> - <title>
<snapshot>
</system-reminder>
```

```text
<system-reminder>
Skill no longer active: "<name>"
Do not follow this skill's instructions from earlier in this conversation.
</system-reminder>
```

- 第一行中的名字用 JSON 编码，任何名字都能原样读回。
- **加载消息里不放 `Task:`**，任务只出现在尾部。否则恢复时任务文本不同，比较永远不相等，正文会重复一份。
- 正文中的 `</system-reminder` 转义为 `<\/system-reminder`。不转义的话，ACP 回放的非贪婪正则会在正文中提前结束，把剩余部分发给客户端。
- 识别条件（`provenance.skill_state_name`，唯一的识别函数）：`role == "user"`，内容是字符串，以 `<system-reminder>\n` 开头，第一行是两种前缀之一，后接一个占满该行其余部分的 JSON 字符串。管理器查找“最新状态”和所有跳过这些消息的读取方都调用它，因此对同一条消息不会有两种判断。用户自己的轮次以日期提醒开头，工具结果和读出的文件不是 user 消息，所以都不会被误认。

### 3.3 判重：只看最后一条

`SkillManager.skill_state_messages(history)` 返回现在应追加的消息：

```
latest = 每个技能在 history 中最后一条状态消息
对 latest 中最后一条是 "loaded"、但当前不再激活的技能 → 停用通知
对每个当前激活的本地技能或 plugin 技能：
    want = 加载消息(name)
    latest.get(name) != want → want
```

- 如果只查“历史中是否出现过相同正文”，`A → reload B → reload A` 时会找到早期的 A 而跳过，模型最后看到的却是 B。只看最后一条就没有这个问题。
- 比较用全等。判错为“不在”只多花 token，判错为“在”会丢掉指令。
- 不维护待发队列。停用通知完全从历史推导出来：
  - 激活后、同步前就停用的技能，历史里从未加载过它，所以不发通知；
  - 恢复后已经找不到的技能，历史里最后一条是加载消息，所以会收到通知；
  - 停用后在同步前又重新激活，最后一条仍等于期望，所以什么都不发。

### 3.4 运行器：一个函数，多处调用

`ChatLoopRunner._sync_skill_state(messages_with_system, system_prompt)`：把 `skill_state_messages(agent.messages)` 返回的消息追加到 `agent.messages`，并重建 `[system] + agent.messages`。没有需要追加的消息时，原样返回。返回值不是非空字符串列表时（例如宿主替身），一律不处理。

调用点：

1. **每次迭代开始时**，在 `_volatile_tail()` 和阈值估算之前。新加载的正文计入预算，也可能触发压缩。
2. **在循环中压缩替换了历史之后**，先同步，再按 `tail_points_into_history` 决定是否重建尾部。该函数现在同时识别 MCP 和本地两种指向提示。
3. **溢出阶梯第一级（`api_overflow`）之后。**
4. **溢出阶梯第二级（`minimal_history`）之后。** 追加发生在边界修复之后，所以窗口仍然不会以 `tool` 消息开头。补回正文后如果仍然超限，就按现有规则返回 context-length 错误。

5. **413 重试的压缩（`_recover_request_too_large`）之后。** 它做一次完整的 `api_overflow` 压缩，可能移除已加载的正文。返回的 `messages_with_system` 就是实际发送的持久前缀，Tier-1 锚点按它计数。

### 3.5 尾部

```
## <name> - <title>
Task: <task>
The instructions and the file list of this skill are in its latest "Skill loaded" reminder earlier in this conversation. The skill is still active.
```

只有当历史中该技能的最后一条状态消息等于当前加载消息时，才给出指向提示。否则（例如没有传入 history，或同步没有运行）照旧给出快照。MCP 分支保持不变。

### 3.6 压缩与恢复

| 情况 | 结果 |
|---|---|
| 微压缩 | 不受影响 |
| 完整压缩、`/compact`、溢出阶梯两级 | 在下一个同步点追加一次 |
| 恢复，文件未变 | `restore_agent_skills` 重新激活，生成的加载消息与历史中的相同，不追加 |
| 恢复，文件已变 | 追加新版本 |
| 恢复，技能已不存在 | 追加停用通知 |
| 摘要器 | 状态消息留在摘要输入中，转录仍是连续后缀，但只保留第一行（`Skill loaded: "<name>"`）。压缩后正文本来就会重新追加；而如果摘要复述了正文，之后停用时历史里已没有加载消息，也就不会发停用通知，复述的规则会一直留着 |

子代理有自己的 `messages` 和运行器，同一套规则照常工作。

## 4. 被否决的方案

| 方案 | 理由 |
|---|---|
| B：正文放在 system 消息末尾 | 撤销 0.4.27 的不变量；中途每次激活都让整个历史前缀的缓存失效 |
| 永不压缩的标记 | 摘要和 `minimal_history` 会替换整个列表 |
| 按请求比较哈希 | 历史无界增长 |
| 消息上加元数据键 | 会发到服务端 |
| 判断历史中是否出现过相同正文 | `A → B → A` 会判错 |
| 只有一个刷新点 | 压缩后补回的正文会漏掉，也不计入预算 |

## 5. 测试

`tests/test_skill_body_in_history.py`：

- 正文只追加一份，尾部只有指向提示，两轮后仍只有一份，加载消息里没有任务文本；
- 压缩替换历史后补回一份；
- 首次加载的正文在阈值估算之前就进入请求；
- 历史中没有加载消息时，尾部仍然带正文；
- `A → B → A` 依次追加三份，最后一份是 A；
- 编辑文件只在重新激活后生效；
- 相同文本出现在 assistant 或 tool 消息中不计数；
- 停用发一次通知；停用后再激活不发通知；从未加载过的技能不发通知；
- 恢复时任务不同也不重复；恢复后技能已不存在时发通知；
- 正文中的 `</system-reminder>` 不会在 ACP 回放中泄漏；
- 溢出阶梯两级之后各补回一份，`outcome.messages_with_system` 等于实际发送的持久前缀。

`tests/test_run_subcommand.py::test_a_spec_skill_does_not_copy_the_prompt_into_the_tail` 改为断言：尾部不含正文，历史中恰好一份。

**反向验证：** 把运行器恢复到改动前，前三个运行器级测试会失败。

## 6. 范围之外

- MCP 技能的 `spec.skills` 和 `/skills activate` 路径：需要先让 provenance 能识别 user 角色的技能消息，并扩展恢复时的扣留逻辑。
- 在请求中撤回已停用技能的正文。
- 缓存收益实测：用 #552 的 guanlan 工作负载，前后各跑 10 次。比较未命中 token 的绝对值、总输入 token、请求次数，以及每次请求的 `volatile_tail_sections`。

## 附录 A — 评审记录

- **2026-10-28 第一轮**（外部建议）：方向为方案 A。修正：本地结果不带正文；MCP 非工具路径不并入；不加版本字段。
- **2026-10-28 第二轮**（对草案的评审），采纳了两处逻辑修正：
  1. 判重只看最后一条状态消息（`A → B → A`），加载消息声明“取代之前的指令”；
  2. 先同步再估算和压缩，历史被替换后再同步；多处调用同一个幂等函数，取代“唯一刷新点”。

  以及三处措辞修正：#593 的范围；验收口径；删去“任务文本不同就追加”。
