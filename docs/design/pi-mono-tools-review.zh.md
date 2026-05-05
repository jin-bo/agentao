# pi-mono 工具实现层评审

**状态:** 决策记录。2026-05-04 起草,作为 `pi-mono-borrow-review.md` 的补充。
**范围:** agentao (`agentao/tools/`) 与 pi-mono coding-agent (`packages/coding-agent/src/core/tools/`) 中**同名工具**的实现差异。前一份评审覆盖协议/特性层借鉴(`shouldStopAfterTurn` / `terminate` / executionMode 等);本份只看工具层(read / write / edit / ls / glob / grep / shell)。
**对应英文版:** `pi-mono-tools-review.md`。
**方法:** 7 对同功能工具逐一对比 → 5 个借鉴候选 → 按 agentao 嵌入式 harness 定位反向评审 → 用 codex 交叉验证幸存项 → 最终落槌。

## TL;DR

5 个初选候选,反向评审后**砍掉 4 个**,只剩 1 个。codex 交叉检查后,这 1 个的实现形态被进一步修正。

| 候选 | 初评 | 反向评审 | codex 检查 |
|---|---|---|---|
| Edit Unicode 归一化 | ⭐⭐⭐ | 改为 capability 选项 | **默认开,作为金字塔末档** |
| Grep `-A/-B/-C` 上下文行 | ⭐⭐⭐ | 先量再说,大概率推迟 | 不变 |
| `withFileMutationQueue` 文件互斥锁 | ⭐⭐⭐ | **撤回** —— 层错了,且无并发证据 | 不变 |
| 结构化 `TruncationResult` | ⭐⭐ | **撤回** —— pi-mono 是给自己渲染层用的,agentao 没消费方 | 不变 |
| 多 edit `edits[]` 批处理 + overlap 检测 | ⭐⭐ | **撤回** —— 与嵌入式 harness 审计粒度冲突 | 不变 |

诚实的元结论:工具层面"pi-mono 哪里好"再次被 CLI 打磨向偏置。agentao 的嵌入式 harness 定位(每次调用粒度的权限闸门 / capability 注入 / 审计事件)在好几处反转了 pi-mono 的取舍——多 edit 批处理与结构化截断在这一层尤其属于反特性。

## 背景 —— 比的是什么

agentao 工具层(Python,`Tool` / `AsyncToolBase` 子类):
- `agentao/tools/shell.py` —— `ShellTool` / `run_shell_command`
- `agentao/tools/file_ops.py` —— `ReadFileTool` / `WriteFileTool` / `EditTool` / `ReadFolderTool`
- `agentao/tools/search.py` —— `FindFilesTool`(glob)/ `SearchTextTool`(grep)

pi-mono 工具层(TypeScript,工厂函数返回 `ToolDefinition`):
- `packages/coding-agent/src/core/tools/bash.ts`
- `packages/coding-agent/src/core/tools/{read,write,edit,ls,find,grep}.ts`
- `packages/coding-agent/src/core/tools/edit-diff.ts`(edit 的姐妹文件,匹配器与 diff 渲染都在这)

### 架构层差异(一张表)

| 维度 | agentao | pi-mono |
|---|---|---|
| 抽象 | OO 子类 `Tool(ABC)` + `@property` 元数据 (`tools/base.py:140`) | 工厂闭包 `createXTool(cwd, options)` 返回 `ToolDefinition` (`index.ts:117`) |
| 能力注入 | `_BaseTool` 上的 `self.filesystem` / `self.shell` 懒加载 (`base.py:43-55`) | `options.operations: BashOperations / ReadOperations …` 接口参数 |
| 执行模型 | 同步 `execute() -> str`;可选 `AsyncToolBase.async_execute()` | 全异步 `Promise<Result>`,`AbortSignal`,`onUpdate` 流式回调 |
| cwd 绑定 | 注册时绑定 `working_directory: Path`,每次调用 `_resolve_path()` (`base.py:61-92`) | 工厂闭包捕获,每次调用 `resolveToCwd()` |
| schema 声明 | OpenAI function 格式由 `to_openai_format()` 生成 (`base.py:128`) | 独立 `createXToolDefinition()` 返回 Typebox schema + `promptSnippet` + `renderCall/renderResult` |
| 渲染层 | 无 —— 工具返回纯字符串 | 内置 TUI 渲染(语法高亮缓存、unified diff、增量刷新) |
| 截断 | 字符总额拼接 | 结构化 `TruncationResult { truncatedBy, outputLines, totalLines, firstLineExceedsLimit }` |

含义:pi-mono 工具的许多特性(结构化截断、多 edit、render 钩子)是给它的 TUI/流式层喂数据用的。agentao 在工具这一层没有对应消费者——消费者是 `EventStream` / `ToolLifecycleEvent` / 权限引擎。这个错位是下面"撤回"判定的主因。

## 同名工具差异(精简版)

### Shell —— `shell.py` ↔ `bash.ts`

agentao 自带 macOS sandbox 包装 (`shell.py:71-113`)、`is_background` 模式返回 PGID/PID、ANSI/CR 折叠、按"无活动"判超时。pi-mono 多了 `BashSpawnHook`(宿主可改 env/cmd)、`onUpdate` 流式输出、超长输出落临时文件。

**结论:** agentao 偏防御(sandbox / 白名单),pi-mono 偏可观察(流式 / hook)。pi-mono 的 `BashSpawnHook` 与 agentao 的 `ShellExecutor` capability 重复。

### Read —— `ReadFileTool` ↔ `read.ts`

agentao 用 `cat -n` 格式 + `File: …` 头 (`file_ops.py:101`),行长上限 2000 字符,`\x00` 检测二进制。pi-mono 多了**图像支持**(MIME 检测、自动缩放到 2000×2000、base64 编码,`read.ts:209`);文本路径无行号。

**结论:** 唯一实质差距是图像支持。多模态进路线图前先放着。

### Write —— `WriteFileTool` ↔ `write.ts`

agentao 有 `append` 参数;`requires_confirmation=True` 接到主循环。pi-mono 总是覆盖、显式 `mkdir -p`、用 `withFileMutationQueue` 防并发写、写入时做增量语法高亮缓存。

**结论:** 安全相关的差距只有 mutation queue 这一项;反向评审认为这把锁的正确位置在 `FileSystem` capability,不在工具装饰器。详见下文。

### Edit —— `EditTool` ↔ `edit.ts` + `edit-diff.ts`

agentao:单次 `old_text/new_text/replace_all`,失败回退到"strip 行尾空格"的灵活匹配 (`file_ops.py:219-241`);找不到时用 difflib 给最相似片段提示 (`file_ops.py:243-268`)。

pi-mono:`edits[]` 数组(一次多处编辑)、Unicode 归一化(智能引号/破折号/NBSP/em-spaces → ASCII,`edit-diff.ts:34-55`)、显式重叠检测 (`edit-diff.ts:239-243`)、BOM/CRLF 保留、unified diff 预览。

**结论:** Unicode 归一化是真正的改进,也是反向评审唯一幸存的候选。多 edit 因为另一个原因(审计粒度)被单独砍掉。

### List —— `ReadFolderTool` ↔ `ls.ts`

agentao 支持 `recursive`,显示 `[DIR]/[FILE] name (N bytes)`,无条目上限。pi-mono 仅平铺,默认 500 条目上限 + 字节截断,无 size 列。

**结论:** 平局。默认值不同,各有道理。

### Glob —— `FindFilesTool` ↔ `find.ts`

agentao:Python `pathlib.glob`,**24 小时内修改的文件按 mtime 排序到前面**,内置 `DEFAULT_SKIP_DIRS` 黑名单(`.git / node_modules / .venv / …`)+ "显式引用即放行" (`search.py:46-60`)。pi-mono:走 `fd` 子进程,严格遵循 `.gitignore`,无 mtime 排序,1000 条上限。

**结论:** 设计中心不同(Python 回退保可移植性 vs 子进程求快)。agentao 的最近修改优先排序确实有用,pi-mono 没有。

### Grep —— `SearchTextTool` ↔ `grep.ts`

agentao:三级降级 `git grep → ripgrep → Python 回退` (`search.py:348-360`),100 条硬上限,无上下文行。pi-mono:仅 ripgrep(强制依赖),**支持 `-A/-B/-C` 上下文行**(`path-N- ctx` 格式)、行级 + 字节级双截断、`fileCache` 缓存上下文。

**结论:** 上下文行确实是 ergonomic 缺口,但收益要量化(模型 grep 完真的常去 read_file 吗?如果不常,加 context 只是把每次 grep 的 token 量翻倍)。

## 初选 5 个候选(反向评审之前)

逐工具差异得出:

1. **Edit Unicode 归一化 + BOM/CRLF 保留** —— 抄 `edit-diff.ts:34-55, 11-25, 137-139`。
2. **Grep `-A/-B/-C` 上下文行** —— 透传到现有后端。
3. **每文件互斥锁** —— 把 `file-mutation-queue.ts` 翻译成 `dict[Path, asyncio.Lock]`。
4. **结构化 `TruncationResult`** —— 把字符串 footer 换成可解析结构。
5. **多 edit `edits[]` + overlap 检测** —— 抄 `edit.ts:42-50` 与 `edit-diff.ts:193-260`。

## 反向评审

### #1 Edit Unicode 归一化 —— 初定 capability 选项,经 codex 检查后改主意

**反向评审提出的担忧:** 静默语义偷换。如果用户文件**真的**有智能引号(中文 markdown 常见),用户 prompt 里写了 ASCII 引号,fuzzy 匹配可能命中错误位置——嵌入式 harness 的审计日志会出现"模型本意改 X,工具改了 Y"。

**初定结论:** 值得做,但应该挂到 `FileSystem` capability,宿主显式选 strict / fuzzy。

**这一结论被 codex 检查推翻,见下文。**

### #2 Grep 上下文行 —— 量化后再说

**担忧:** "模型 grep 后会再 read_file"是假设而非数据。无条件加 context 把每次 grep 的 token 量翻倍。真实 ROI 取决于实际比例;没数据就是瞎优化。

**结论:** 翻一周 `agentao.log`,数 grep → read_file 的跟随比例;>40 % 就抄,否则推迟。

### #3 文件互斥锁 —— 撤回

**三个担忧,任意一个就够否决:**

- **没有真实并发证据。** `AsyncToolBase` landed(memory: `project_async_tool_landed.md`)、嵌入式 harness landed(memory: `project_embedded_harness_landed.md`),但这不等于宿主真在并行 fan-out 编辑。Subagent 生命周期事件 ≠ 并行调度。没有真实并发写工作负载,这把锁就是死代码。
- **层错了。** 真要并发,正确位置是 `FileSystem` capability(Docker FS / 虚拟 FS / 审计代理实现可以自己决定并发语义——锁、事务、CoW),不是工具装饰器。
- **进程内锁泛化不了。** 多个 agentao 实例嵌入同一宿主(多进程)无法共享进程内锁;那是另一个问题(OS 级 flock)。

pi-mono 加这个是因为它**没有** `FileSystem` 抽象,只能在工具上糊。agentao 已经有更干净的位置。

**结论:** 撤回。除非 (a) 真实工作负载暴露进程内并发写、且 (b) 该工作负载在 capability 层放不下,才重启。

### #4 结构化 TruncationResult —— 撤回

**担忧:** 这个结构在 pi-mono 是给 `renderResult` 渲染层用的。agentao 在工具这一层没有对应消费者——消费者是模型(读字符串)。把结构塞进字符串输出要花 token,模型还要 prompt 教它怎么解析(再花 token)。agentao 现在的 `Showing lines A–B` + `... (truncated)` 已经够模型算下个 offset 了。

**结论:** 撤回。这是渲染基础设施被误读为"给模型看的结构"。

### #5 多 edit 批处理 —— 撤回

**担忧(定位冲突):** 嵌入式 harness 契约的核心是**每次调用粒度的权限闸门**。`PermissionDecisionEvent` / `ToolLifecycleEvent`(memory: `project_embedded_harness_landed.md`)都假设**一次工具调用 = 一个原子的、可批准/可拒绝、可审计的操作**。把 7 处编辑塞一个 call 里,宿主只能要么全批要么全否,审计时也得自己拆 N 个变更。这是用 harness 可控性换 CLI 吞吐——方向是反的。

**结论:** 撤回。如果"多轮往返成本"以后真成痛点(且能量化),应该在 loop 层解决(并行工具调用机制现在就支持),不是放宽工具语义。

## codex 交叉验证(精修 #1)

反向评审之后,去 `../codex` 找有没有可比的 Unicode-fuzzy 匹配实现,在 `codex-rs/apply-patch/src/seek_sequence.rs:76-107` 找到了。要点:

1. **同思路,codepoint 表更全。** codex 归一化的是同样四类(破折号 / 单引号 / 双引号 / 各种空格),但比 pi-mono 多覆盖了几个 codepoint(整段 em/en-space U+2002–200A、U+205F medium math space、U+3000 全角空格)。
2. **注释明确这是业内常规做法。** codex 注释写 `mirrors the fuzzy behaviour of git apply` (`seek_sequence.rs:72-73`)。这不是 pi-mono 独创,是 `git apply` 风格的通行约定。
3. **金字塔位置才是关键设计选择。** codex 把归一化放在**严格度递减金字塔的第 4 档**:

   ```
   1. 精确匹配
   2. trim_end(rstrip)
   3. trim(双侧)
   4. normalize-then-trim         ← Unicode fuzzy
   ```

   前三档先跑。用户文件里的字节和模型给的字节完全相同的情况,**根本走不到第 4 档**。归一化只在前面所有严格档全 fail 的情况下触发。

**对原结论的修正:** "静默语义偷换"的担忧在 codex 这种金字塔形态下自然消解。如果用户文件有 `"`、prompt 里也有 `"`,第 1 档 exact 就赢了——归一化根本不会跑。归一化只救**本来就要失败的**调用。这意味着 capability 闸门没必要,**默认开是对的**——前提是实现取金字塔形态,而不是 pi-mono 那种"进 fuzzy 路径就归一"的形态。

**agentao 移植的最终形态:**

- 在 `file_ops.py:219-241` 现有灵活匹配后面再加一档归一化。
- 直接照抄 codex 的 codepoint 表(更全的那张)。
- 把 `mirrors the fuzzy behaviour of git apply` 这条注释一并搬过来,后人能看出血统。
- 默认开。无 capability 开关。
- 估算:~30 行 Python + 一个回归测试 fixture(智能引号源文件、ASCII 引号 prompt,期望成功匹配)。

## 落槌表

| 候选 | 初评 | 反向评审 | codex 检查 | 最终 |
|---|---|---|---|---|
| Edit Unicode 归一化 | ⭐⭐⭐ keep | capability 选项 | 金字塔末档,默认开 | **DO** —— 扩展 `file_ops.py:219` |
| Grep 上下文行 | ⭐⭐⭐ keep | 先量化 | — | **MEASURE** —— 扫日志再决定 |
| 文件互斥锁 | ⭐⭐⭐ keep | 层错 + 无证据 | — | **DROP** |
| 结构化 TruncationResult | ⭐⭐ keep | 工具层无消费者 | — | **DROP** |
| 多 edit `edits[]` | ⭐⭐ keep | 与审计粒度冲突 | — | **DROP** |

## 实现草图 —— Edit Unicode 归一化

```python
# 在 agentao/tools/file_ops.py 中扩展现有的灵活匹配块。
#
# Mirrors the fuzzy behaviour of `git apply` —— 作为现有 exact / rstrip / trim
# 三档的最末一档跑;字节相同的情况完全不受影响。codepoint 表来自
# codex-rs/apply-patch/src/seek_sequence.rs:79-92。

_DASHES = "‐‑‒–—―−"
_SQUOTES = "‘’‚‛"
_DQUOTES = "“”„‟"
_SPACES = "            　"

_NORMALIZE_TABLE = str.maketrans({
    **{c: "-" for c in _DASHES},
    **{c: "'" for c in _SQUOTES},
    **{c: '"' for c in _DQUOTES},
    **{c: " " for c in _SPACES},
})

def _normalize_for_match(s: str) -> str:
    return s.translate(_NORMALIZE_TABLE)
```

接到现有灵活匹配(strip 空格)的后面,作为第 4 档。测试 fixture:源文件含 `def greet(name="world")`(智能引号包裹 `world`);`old_text` 写的是 ASCII 引号版本;期望匹配成功且重写正确。

## 经验教训

1. **CLI 打磨 ≠ harness 改进。** pi-mono 工具层的多数特性(多 edit、结构化截断、mutation queue)都是给它的 TUI/流式层喂数据用的。agentao 的工具消费者是事件和权限,不是渲染器;同样的特性搬过来代价大于收益。
2. **单源借鉴要做交叉验证。** 只看 pi-mono 的 edit 实现就抄,会顺带继承它的具体取舍(pi-mono 在 fuzzy 路径里就归一;codex 只在末档归一)。codex 一查,改的不只是结论,而是实现形态。
3. **反向评审模式重复出现。** 跟协议层评审一样,工具层的初选名单也偏向"看着聪明的"。和 agentao 真实分层(`FileSystem` capability、`EventStream`、`PermissionDecisionEvent`)对齐后,大部分候选直接坍缩。
4. **记下血统出处。** codex 的 `seek_sequence` 注释写明了这是 `git apply` 行为。后人维护时省事;搬过去时把注释一起搬。
