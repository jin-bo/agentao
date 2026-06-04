# 运行时工具注入：`add_tool` / `remove_tool`（推迟设计）

**状态：** **v1 已落地** —— 按 §6 实现了 `Agentao.add_tool` / `remove_tool` + `ToolRegistry.unregister`。跟踪于 [issue #65](https://github.com/jin-bo/agentao/issues/65)。本文档保留为设计记录;§8 的 demand-gate 说明它在此前为何推迟。
**读者：** 在构造期工具注入(PR #64)之上,考虑给 host 一个运行时(构造后)增/删工具入口的 agentao 维护者。
**配套:**
- `docs/design/runtime-tool-injection.md` — 英文版
- `docs/design/host-tool-injection.zh.md` — 构造期注入(`extra_tools` / `disable_tools`),本设计的前置
- `docs/design/embedded-host-contract.md` — host 契约稳定边界(本设计的归属处)
- `agentao/tools/base.py` — `ToolRegistry`(改造主战场:新增 `unregister`)
- `agentao/tooling/registry.py` — `_bind_and_register`(运行时注入复用它)

---

## 1. 问题:构造期注入有了,运行时没有

PR #64 落地了**构造期**工具注入——`Agentao(extra_tools=..., disable_tools=...)`(见 `host-tool-injection.zh.md`)。这两个 kwarg 在 `_wire_tooling` 里被消费一次,之后**不再读**。

本设计跟踪它的**运行时**对偶:host 想在构造**之后**、两次 `chat()` 调用**之间**增/替/删一个工具。真实场景:

- ACP 会话中途接入一个 connector(对偶于 `extra_mcp_servers=` 在 `session/new` 的会话级注入,Issue 11)。
- host 依据运行时状态切换某项能力(如登录后才暴露某工具)。
- 长会话嵌入里按用户操作动态裁剪工具面。

## 2. 现状(已在 `main` 上 grep 核实)

| 事实 | 锚点 | 含义 |
|---|---|---|
| **schema 每次 `chat()` / `arun()` 调用重建一次** | `runtime/chat_loop/_runner.py:201`(`to_openai_format()` 在进入内部 LLM 迭代循环**之前**取一次快照) | 两次 `chat()`/`arun()` 调用**之间**改注册表,下一次调用即生效;一次调用**之内**(含后续 LLM iteration、stop-hook re-entry)工具列表冻结 |
| **动态增/替技术上能跑** | `tools/base.py:209` `ToolRegistry.register(tool, replace=)` | 但这是捅运行时内部,不在 `agentao.host` 契约面 |
| **但它绕过两道保障** | `tooling/registry.py:67` `_bind_and_register` | (a) capability 绑定(`working_directory`/`filesystem`/`shell`)丢失 → 工具变"裸"(ACP cwd 隔离 + host FS/shell 重定向失效);(b) 构造期校验(`mcp_` 前缀禁令、空名/类型 guard、override 审计日志)全不生效 |
| **动态移除完全没 API** | `tools/base.py:198-263`(只有 `register`/`get`/`list_tools`/`to_openai_format`) | 无 `unregister`/`remove`;`disable_tools` 只在构造期过滤 |

一句话:**运行时增/替能做但不安全(裸工具 + 无校验),运行时删根本没接口。** 两者都不在契约面里。（变更的可见性 = 下一次 `chat()`/`arun()` 调用,见 §5。）

## 3. 范围:`add_tool` + `remove_tool`,且 demand-gated

**仅当 §8 触发后**,给 `agentao.host` 契约加两个方法,**复用现有基础设施而非特判**:

```python
agent.add_tool(tool, *, replace=False)   # 复用 _bind_and_register + extra_tools 的校验
agent.remove_tool(name) -> bool          # 复用新增的 ToolRegistry.unregister(name)
```

放在 `Agentao` 公共方法簇里,与 `events()`(`agent.py:681`)、`active_permissions()`(`agent.py:699`)、`add_host_event_observer()`(`agent.py:658`)并列——它们已是契约面方法。

**明确不做:**
- 不做 mid-turn(单次 `chat()` 内)变更可见——snapshot 已天然约束,见 §5。
- 不做跨 task 并发修改注册表——v1 只支持在两次 `chat()`/`arun()` 调用之间调用,见 §7。
- 不做 plan 工具(`_PLAN_ONLY_TOOLS`)的增/删/替换——保留名,`add_tool` / `remove_tool` 均直接 `ValueError`,见 §5。
- 不做 `tool_options` / settings.json(`host-tool-injection` §10 已推迟)。
- 不做 MCP 工具的增删——那归 MCP 生命周期(`mcp_manager=` / `extra_mcp_servers=`),见 §5。

## 4. 接口

### 4.1 `Agentao.add_tool`

```python
def add_tool(self, tool: "RegistrableTool", *, replace: bool = False) -> None:
    """构造后注册一个工具。下一次 ``chat()`` / ``arun()`` 调用对模型可见(见 §5)。

    与 ``extra_tools=`` 走同一条校验 + capability 绑定路径:
    - 拒绝**保留名**——``mcp_`` 前缀(MCP 命名空间保留)、``_PLAN_ONLY_TOOLS``
      (``plan_save`` / ``plan_finalize``,与 plan 状态机绑定)——以及空/非字符串名;
    - 绑定 ``working_directory`` / ``filesystem`` / ``shell``;
    - ``replace=False`` 且重名 → ``ValueError``(显式要求 ``replace=True``,
      比 ``register`` 的 warn-and-overwrite 更严,适合有意的 host 调用);
    - ``replace=True`` 覆盖内置 / agent / 其它 extra 工具,静默 + INFO 审计行。
    """
```

### 4.2 `Agentao.remove_tool`

```python
def remove_tool(self, name: str) -> bool:
    """构造后注销一个工具。返回是否真的移除了(不存在 → False,非异常)。

    - ``mcp_`` 前缀 / plan 工具(``plan_save`` / ``plan_finalize``,即
      ``_PLAN_ONLY_TOOLS``)→ ``ValueError``:前者走 MCP 生命周期,后者与
      plan 状态机绑定,均不归这里删。
    - 内置 / extra / agent 工具可删。
    - 下一次 ``chat()`` / ``arun()`` 调用对模型不可见。
    """
```

### 4.3 `ToolRegistry.unregister`(新增底座)

```python
def unregister(self, name: str) -> bool:
    """从注册表移除 ``name``。返回是否存在过。纯 dict 操作,无副作用。"""
    return self.tools.pop(name, None) is not None
```

## 5. 语义与优先级

1. **可见性 = "下一次 `chat()` / `arun()` 调用"**。`to_openai_format()` 只在每次 `chat()` 进入内部 LLM 迭代循环**之前**取一次快照(`_runner.py:201`),所以构造后的增/删在**下一次 prompt/chat 调用**才生效:**同一次 `chat()` 之内**(后续 LLM iteration、stop-hook re-entry)**模型可见的 schema** 不变。这是契约,不是缺陷——它让"单次调用内 schema 一致"成为不变量(与 plan-mode 的 `plan_*` 过滤同源)。精确界定范围:冻结的是**模型看到的 schema**;工具**执行**按名字在 live 注册表里解析(`tool_planning.py:270` 每次调用 `self._tools.get(name)`),故 turn 中途 `remove_tool` *会*把已发出的调用变成查找失败。该窗口仅在 host 于 turn 中途改动(并发 task 或工具自身 `execute()` 内)时才打开——§7 已把它划出 v1 范围,"调用之间"的用法永不触发。
2. **`add_tool` 与 `extra_tools` 同校验同绑定**。把 PR #64 里 `_validate_tool_injection`(`agent.py`)的**单工具**校验抽成可复用函数(如 `_validate_one_extra_tool(tool)`),`add_tool` 与构造期循环共用——运行时注入不可能产出裸工具,也不可能用保留名(`mcp_` 前缀 ∪ `_PLAN_ONLY_TOOLS`)替换 MCP / plan 工具。注:把保留名集扩到含 `_PLAN_ONLY_TOOLS` 后,构造期 `extra_tools` 也一并受益——顺手堵上「extra 命名为 `plan_save` 被 CLI 后注册覆盖」的旧灰区,是一致的收紧。
3. **覆盖范围:内置 + agent + extra,不含 MCP**。`mcp_` 前缀在 `add_tool`/`remove_tool` 都被拒——MCP 工具名恒以 `mcp_` 开头(`mcp/tool.py:19-21` `make_mcp_tool_name`),故运行时注入构造上就碰不到 MCP。MCP 的运行时增删走 `mcp_manager=` / `extra_mcp_servers=`,边界清楚。
4. **plan 工具不可增/删/替换(保留名)**。`plan_save`/`plan_finalize` 由 CLI 在构造后注册(`cli/app.py:91-92`),只在 plan 模式进 schema(`_PLAN_ONLY_TOOLS`),且与 plan 状态机绑定。`add_tool`(含 `replace=True`)**和** `remove_tool` 对 `_PLAN_ONLY_TOOLS` 名**都直接 `ValueError`**——只禁 remove 会留下 `add_tool(name="plan_save", replace=True)` 这个绕口子,故两端一起禁,不留公共 API 灰区。

## 6. 实现草图(改动面)

| 改动 | 位置 |
|---|---|
| `ToolRegistry.unregister(name) -> bool` | `tools/base.py`,紧挨 `register`(:209) |
| 抽出单工具校验 `_validate_one_extra_tool(tool)` | `agent.py`,从现有 `_validate_tool_injection` 提取;构造期循环与 `add_tool` 共用。**保留名集 = `mcp_` 前缀 ∪ `_PLAN_ONLY_TOOLS`**,加空/非字符串名 guard |
| `Agentao.add_tool` / `remove_tool` | `agent.py`,与 `events()`/`active_permissions()` 同簇(:681/:699 附近)。两者都先按保留名集校验(`add_tool` 走 `_validate_one_extra_tool`;`remove_tool` 同样拒 `mcp_` + `_PLAN_ONLY_TOOLS`),再绑定/`unregister` |
| `add_tool` 复用 `_bind_and_register` | 已在 `tooling/registry.py:67`,无需改 |
| 文档 + 契约面 | `docs/reference/host-api.md` 公共方法表加两行;`host/__init__.py` 无需动(是 `Agentao` 方法,非包级符号) |

路线简明,与 PR #64 同形:**grep 验证 → 设计(本文档)→ patch + 测试**。

## 7. 调用时机与并发(写窄)

v1 **只支持在两次 `chat()` / `arun()` 调用之间**调用 `add_tool` / `remove_tool`。**不承诺**:跨 task 并发修改注册表、在一次 `chat()` 进行中(从另一 task 或从工具自身 `execute()` 内)修改注册表。

理由:`to_openai_format()` 对 `self.tools` 做 `sorted(self.tools.values(), ...)` 迭代取快照,与并发的 `dict` 增删组合**不是**安全的(可能撞上"迭代中改变 size")。把语义写窄到"调用之间"后,这种组合天然不会发生——于是 v1 **无需加锁**,也**无需**依赖任何单操作 GIL 原子性的论证。

若将来出现"会话进行中需并发改工具面"的真实场景,再评估给 `ToolRegistry` 加锁或快照加版本号——那是另一项设计,不是本文档的承诺。

## 8. 实现触发条件(demand gate)

满足**其一**才启动实现(gap≠need):

1. 真实嵌入场景需要会话**中途**增/删工具,且构造期 `extra_tools`/`disable_tools` + 重建 `Agentao` 无法满足(如长 ACP 会话不能重建)。
2. ACP `session/update` 类需求出现,要求会话级动态工具面。
3. 有 host 反馈"只能捅 `agent.tools.register(...)`,且踩了裸工具(FS/shell 未绑定)的坑"。

在此之前本文档是"束之高阁的规格",确保触发时实现以日为单位。

## 9. 待定问题(v1 范围外,记录备查)

**v1 收敛为三步,不扩张:** `ToolRegistry.unregister()` + `Agentao.add_tool()`(复用 `_bind_and_register`)+ `Agentao.remove_tool()`(名字校验后 `unregister`)。下列均**先不做**:

- **`add_tool(replace=False)` 重名该 raise 还是 warn?**(实现时定;倾向 raise,显式 host 调用严于 `register` 的 warn——届时在文档里与 `register` 语义区分清楚。)
- **并发加锁 / 快照版本号**——见 §7,等真实信号。
- **`has_tool(name)` / `list_tool_names()` 只读查询面**——`list_tools()` 已存在(`base.py:240`)但返回实例;按需再加。
- **`remove_tool` 删 agent 工具后的 `AgentManager` 联动**——v1 假设注册表是唯一暴露面、sub-agent 路径无悬挂引用,实现时确认即可,不预设联动机制。

## 10. 本文档不是什么

- **不是 mid-turn 变更**。单次 `chat()` / `arun()` 内工具面冻结是有意的不变量。
- **不是 MCP 运行时管理**。MCP 增删走 `mcp_manager=` / `extra_mcp_servers=`。
- **不是 `tool_options`**。配置内置仍按 `host-tool-injection` §10 推迟。
- **不是绕过 permission engine**。`remove_tool` 减的是 schema 暴露,不是安全边界;安全/越权仍归 permission engine。

## 11. 引用

- **前置设计:** `host-tool-injection.zh.md`(构造期注入,PR #64)、其 §9(落地前置)。
- **Agentao 触及面(2026-06-01 验证):**
  - `agentao/tools/base.py:198-263` — `ToolRegistry`(无 `unregister`)。
  - `agentao/runtime/chat_loop/_runner.py:201` — `chat()`/`arun()` 进入 LLM 迭代循环前的 `to_openai_format` 快照(唯一的运行时工具面构建点;`agent.py` 里 `get_conversation_summary()` 那处 `to_openai_format` 是 token 估算,不是运行时工具面)。
  - `agentao/tooling/registry.py:67` — `_bind_and_register`(capability 绑定,运行时复用)。
  - `agentao/tooling/registry.py:138` — `register_extra_tools`(构造期最后 pass,同形参照)。
  - `agentao/mcp/tool.py:19-21` — `make_mcp_tool_name`(`mcp_` 前缀来源)。
  - `agentao/cli/app.py:91-92` — plan 工具构造后注册(正交路径)。
  - `agentao/agent.py:658/681/699` — `add_host_event_observer` / `events` / `active_permissions`(公共方法簇,新方法的放置处)。
- **跟踪:** [issue #65](https://github.com/jin-bo/agentao/issues/65)。
