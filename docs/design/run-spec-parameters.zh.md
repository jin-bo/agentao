# RunSpec 参数与指令：补齐 Recipe 缺口

**状态：** 已落地 2026-05-25。实现说明见文末。
**读者：** 关注 `agentao run` 易用性、以及 goose-recipes 对比的 agentao 维护者。
**配套：** `run-spec-parameters.md`。

## 问题

goose 提供 "recipe" 概念（`crates/goose/src/recipe/`）：一个 YAML 文件声明
`prompt / instructions / 类型化 parameters / settings / extensions` 以及可选
的 `response.json_schema`。Recipe 通过 MiniJinja 渲染，是 goose 主要的"可分
享、可参数化的工作流产物"。

直接照搬会在 `RunSpec` 旁边引入一个平行的 "Recipes" 概念——也就是给"如何启
动一次非交互运行"引入第二个真理来源。**但对 agentao 不合适**，因为
`RunSpec`（`agentao/cli/run_models.py:111`）已经覆盖了 ~75% 的 recipe 语义。
缺口窄、且是纯增量。

本设计在不引入第二个概念的前提下补齐缺口。

## 现有覆盖

goose recipe 字段 → `RunSpec` 字段，对照 `agentao/cli/run_models.py:111-131`：

| Recipe 字段              | RunSpec 字段                            | 状态 |
|--------------------------|----------------------------------------|------|
| `prompt`                 | `prompt`                                | ✅ |
| `settings.provider/model`| `model`, `base_url`                     | ✅ |
| `settings.max_turns`     | `max_iterations`                        | ✅ |
| `extensions.builtin`     | `skills`                                | ✅ |
| `extensions.mcp`         | `.agentao/mcp.json`（独立配置，引用即可）| ✅ |
| `permissions`            | `permissions` + `permission_mode`       | ✅ 更整洁 |
| (replay)                 | `replay`                                | ✅ 领先 goose |
| `instructions`           | —                                       | ❌ 缺口 |
| `parameters`（类型化）   | —                                       | ❌ 缺口 |
| `activities`（UI 快捷键）| —                                       | 跳过（无 UI） |
| `response.json_schema`   | —                                       | 跳过（独立设计） |
| `title`, `description`   | —                                       | 跳过（暂无消费方） |

两个真实缺口。`title` / `description` 延后到真有 `--list` 命令需要时再加——
现在加是"为假想的未来需求设计"，违反项目的「不要过度设计」规则。

## 提案

给 `RunSpec` 加两个字段、一道 Jinja 渲染流程。不引入新文件格式、不引入新
CLI 子命令、不另建 Recipes 模块。

### Pydantic 模型增量（`agentao/cli/run_models.py`）

```python
import re

from pydantic import (
    BaseModel, ConfigDict, Field, field_validator, model_validator,
)


class RunParameter(BaseModel):
    """Spec 级 Jinja 替换用的 string 类型参数槽。"""

    name: str
    required: bool = False
    default: Optional[str] = None
    choices: Optional[List[str]] = None       # 枚举式校验

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _name_must_be_identifier(cls, v: str) -> str:
        # ASCII identifier 规则——否则 Jinja `{{ name }}` 会把
        # 'pr-number' 解析成减法、对 'foo bar' 直接抛错。
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(
                f"parameter name {v!r} must be an ASCII identifier "
                "(matching [A-Za-z_][A-Za-z0-9_]*)"
            )
        return v

    @model_validator(mode="after")
    def _required_and_default_are_exclusive(self):
        if self.required and self.default is not None:
            raise ValueError(
                f"parameter '{self.name}' cannot be both required and defaulted"
            )
        return self

    @model_validator(mode="after")
    def _default_must_be_in_choices(self):
        if (
            self.default is not None
            and self.choices is not None
            and self.default not in self.choices
        ):
            raise ValueError(
                f"parameter '{self.name}' default {self.default!r} is "
                f"not in choices {self.choices}"
            )
        return self


class RunSpec(BaseModel):
    # ... 现有字段 ...
    instructions: Optional[str] = None        # 追加到系统提示
    parameters: Optional[List[RunParameter]] = None

    @model_validator(mode="after")
    def _no_duplicate_parameter_names(self):
        if self.parameters:
            seen: set[str] = set()
            for p in self.parameters:
                if p.name in seen:
                    raise ValueError(f"duplicate parameter '{p.name}'")
                seen.add(p.name)
        return self
```

**同时更新 `run_models.py:194` 的 `__all__`**，将 `RunParameter` 加入导
出列表——该模块的本地约定是严格按字母序导出。

这些 validator 都在 spec-parse 阶段（即 `_parse_spec_text` 内）触发，呈
现为 `invalid_spec` / exit 2。这是结构性校验，应当落在 model 上，而非
renderer。

`required: true` + `default: ...` 直接拒绝，**不做"哪个胜出"的隐式裁
决**。一个带默认值的参数按定义就不是必填；两个同时存在只会引发实现漂移。

**v1 只支持 `string` 类型参数。** `number` 和 `boolean` 延后：两者都需要
明确的 coercion 规则（`true/false/1/0/yes/no` 是否都接受？`42.0` 是 int 还
是 float？choices 比较是 coercion 前还是后？），在没有真实消费方驱动之前
不值得设计。string-only 的 v1 覆盖 CLI 参数模式的主要场景；等真有人需要再
加类型。

两个新字段都是可选；`extra="forbid"` 依然拒绝拼写错误。**已有 spec 不需要
任何修改即可继续 validate**——纯增量。

### 管线位置

**`--param` 的值不是 RunSpec 字段。** 它们**不能**走 `_apply_cli_overrides`，
因为 `extra="forbid"` 会拒绝；即便不拒绝，把"参数声明"和"参数取值"混进同
一个 Pydantic 模型也是把两个概念搅在一起。

`agentao/cli/run.py` 新增两个辅助函数：

```python
def _parse_cli_params(items: Optional[list[str]]) -> dict[str, str]:
    """把重复的 --param KEY=VALUE 解析为 dict。

    格式错误或重复 key 抛 _UsageError。
    """
```

`_execute_with_args`（`agentao/cli/run.py:315`）按以下顺序调用：

1. `_load_spec(args)` —— 第 323 行（不变）。
2. `_apply_cli_overrides(spec, args)` —— 第 337 行（不变）。
3. **新增**——必须用 try/except 包起来，路由到既有的
   `_emit_invalid_usage`：
   ```python
   try:
       params = _parse_cli_params(args.params)
       spec = render_spec(spec, params)
   except (_UsageError, RunTemplateError) as exc:
       return _emit_invalid_usage(str(exc), output_format)
   ```
   `_parse_cli_params` 抛 `_UsageError`（已在 `run.py` 定义）。
   `render_spec` 抛一个小的 `RunTemplateError(ValueError)`，定义在
   `run_template.py`——异常面显式、except 子句能明确写出捕获什么、避免误
   吞 Pydantic 自带的无关 `ValueError`。
4. `if not spec.prompt: ...` —— 第 346 行（不变；现在校验的是**渲染后**的
   prompt）。
5. `_run_pipeline(spec, ...)` —— 第 359 行（不变）。

渲染必须**在 `_apply_cli_overrides` 之后**（这样 `--prompt` 覆盖值也可以
是模板，前提是 spec 声明了 parameters）、**在 `prompt is required` 校验
之前**（模板渲染为空串依然能正确报错）。

### Renderer 触发规则（三种情况）

| `spec.parameters` | CLI `params` | 行为 |
|-------------------|--------------|------|
| 空 / None         | 空           | no-op。Jinja2 不被调用。spec 里的字面 `{{ }}` 原样透传。 |
| 空 / None         | 非空         | exit 2 `invalid_spec`——第一个传入的 key 报 `unknown parameter`。 |
| 非空              | 任意         | 跑 renderer（校验参数、渲染 `prompt` + `instructions`）。 |

理由：用户给一个没声明 parameters 的 spec 传 `--param` 是误用。静默 no-op
会让真实的 typo 不可见。"spec 无 parameters" 这条路径，**专给完全不用模
板的 spec**。

### Renderer 细节

`agentao/cli/run_template.py::render_spec(spec, params) -> RunSpec`：

1. 用 `spec.parameters` 校验传入参数（required、choices、unknown）。
2. 用 `undefined=StrictUndefined` 的沙箱 Jinja2 `Environment` 渲染
   `spec.prompt` 和 `spec.instructions`（仅此两个字段）。
3. 返回一个渲染后的新 `RunSpec`。

失败模式（全部 exit 2，`invalid_spec`）：

- 必填参数缺失 → `agentao run: parameter '{name}' is required`
- 未知参数 → `agentao run: unknown parameter '{name}'`
- choices 违反 → `agentao run: parameter '{name}' must be one of {choices}`
- Jinja 未定义变量 → `agentao run: template uses undefined variable
  '{name}' (declare it in spec.parameters)`
- Jinja 语法错误 → `agentao run: template syntax error in spec.{field}: {msg}`

`StrictUndefined` 不能让步——静默替换为空串会让模板里的拼写错误悄悄上线。

### 依赖

**Jinja2 作为普通（非可选）依赖。** "可选依赖"方案引入了模板语法探测分支、
惰性 import、额外测试——对一个 ~1MB、无 native 传递依赖的包来说不值得。
直接加到 `pyproject.toml` 基础依赖，`run_template.py` 顶部 import，结束。

### CLI 命令行旗标（`agentao/cli/run.py`）

`add_run_subparser` 上新增一个 flag：

```python
parser.add_argument(
    "--param", dest="params", action="append", default=None,
    metavar="KEY=VALUE",
    help="设置一个 spec 参数。可多次。示例：--param depth=deep",
)
```

`_parse_cli_params` 规则（所有错误均为 `_UsageError` → exit 2，
`invalid_spec`）：

- **缺 `=`**：`--param foo` → `agentao run: malformed --param 'foo'
  (expected KEY=VALUE)`。
- **key 为空**：`--param =1` → 同样的 malformed 报错。
- **value 内多个 `=`**：`--param expr=a=b` → key=`expr`, value=`a=b`
  （只在**首个** `=` 拆分；value 原样保留）。
- **重复 key**：`--param x=1 --param x=2` → `agentao run: --param 'x'
  supplied multiple times`。**直接报错**而非 last-wins——后者更易引发意外。
- **非 identifier key**：`--param foo-bar=v` 或 `--param 1foo=v` →
  `agentao run: --param '{key}' is not a valid identifier (must match
  [A-Za-z_][A-Za-z0-9_]*)`。与 spec validator 同一条 regex；在此处报错
  比让它走到下游变成 "unknown parameter" 更清晰。

### Agent 接入（具体）

`agentao/cli/run.py:404` 当前的 `factory_kwargs` 构造：

```python
factory_kwargs: Dict[str, Any] = dict(
    working_directory=cwd,
    transport=transport,
    replay_config=replay_config,
)
if spec.model is not None:
    factory_kwargs["model"] = spec.model
if spec.base_url is not None:
    factory_kwargs["base_url"] = spec.base_url
```

在同一块新增一行：

```python
if spec.instructions is not None:
    factory_kwargs["project_instructions"] = spec.instructions
```

这条 kwarg 路径已经在 `agentao/agent.py:68` 存在，它会短路 AGENTAO.md
的磁盘读取（`agent.py:349-352`）。**优先级隐式生效**：`spec.instructions`
非空时 AGENTAO.md 跳过；为空时 AGENTAO.md 正常读取。**不发警告、不做磁
盘存在性探测**。

## 完整示例

```yaml
# .agentao/runs/review-pr.yaml
parameters:
  - name: pr_number
    required: true
  - name: depth
    default: shallow
    choices: [shallow, deep]
skills: [code-review]
instructions: |
  你正在 review PR #{{ pr_number }}。
  使用 {{ depth }} 模式：shallow = 只看表面问题；
  deep = 跨文件追踪数据流。
prompt: |
  Review PR #{{ pr_number }} on this repo. 关注正确性 bug。
permission_mode: workspace-write
max_iterations: 30
```

调用：

```bash
agentao run --spec .agentao/runs/review-pr.yaml \
            --param pr_number=142 --param depth=deep
```

## 测试计划

在 `tests/cli/test_run_parameters.py` 下新增：

1. **正常渲染**：一个 required 参数，prompt 模板使用它 → `Agentao` 收到
   渲染后的 prompt。
2. **必填缺失**：参数标记 required、未提供 `--param` → exit 2，
   `invalid_spec`，消息中包含参数名。
3. **应用默认值**：可选参数带 `default`、未提供 `--param` → 用默认值渲染。
4. **choices 校验**：参数 `choices=[shallow,deep]`、`--param x=other` →
   exit 2，`invalid_spec`，消息列出允许值。
5. **未知参数（已声明 parameters）**：spec 声明 `[{name: a}]`、传
   `--param b=1` → exit 2。
6. **未知参数（无 parameters 块）**：spec 无 `parameters`、传 `--param
   x=1` → exit 2（覆盖触发规则第二行）。
7. **无 parameters + 无 CLI params**：spec 无 `parameters`，prompt 含字
   面 `{{ literal }}` → 原样透传（覆盖第一行）。
8. **StrictUndefined**：prompt 引用了 `{{ missing }}` 但 `parameters` 未
   声明 → exit 2，错误中带变量名。
9. **spec 重复参数名**：spec 有两个 `parameters[*].name: depth` →
   `_parse_spec_text` 抛错，exit 2，消息含重复名。
10. **`--param` 格式错误**：`--param foo`（无 `=`） → exit 2，消息含
    "expected KEY=VALUE"。
11. **`--param` 重复 key**：`--param x=1 --param x=2` → exit 2，消息含
    "supplied multiple times"。
12. **`instructions` 流向 `project_instructions`**：断言传给
    `build_from_environment` 的 `factory_kwargs` 含
    `project_instructions=<rendered string>`。
13. **`required` + `default` 互斥**：spec 参数同时设置两者 → spec-parse
    阶段 exit 2，消息含参数名。
14. **`default` 不在 `choices` 中**：spec 参数 `choices: [a, b]` +
    `default: c` → exit 2，消息同时列出两者。
15. **非 identifier 参数名**：参数化覆盖 `""`、`" x "`、`"pr-number"`、
    `"1foo"`、`"foo bar"`——均为 spec 侧，均在 spec-parse 时 exit 2。一
    个子用例额外验证 `--param foo-bar=v` 在 CLI 解析阶段就报 identifier
    规则错误。

十五个测试。**无需 mock Jinja2 import、无需 sys.modules 快照**。

## 明确不做（Out of scope）

- **`title` / `description` 字段**：在 `--list` 命令设计之前是投机性
  schema。需要时一起加。
- **`number` / `boolean` 参数类型**：等有真实消费方驱动 coercion 规则
  再加。
- **`response.json_schema` / FinalOutputTool 校验**：等真实消费方出现后
  另立设计。与 recipes 解耦。
- **Recipe `activities` UI 快捷键**：agentao 无桌面 UI。
- **Recipe `sub_recipes`**：等 subagents 落地后再说
  （[[project_async_tool_landed]] 给了 dispatch 原语，但还没有 agent-
  spawning 接口）。
- **AGENTAO.md "两者同时存在" 警告**：不值得多一次磁盘探测。
- **除 `prompt` 和 `instructions` 之外字段的模板化**：给 `permissions`
  或 `skills` 加模板易出 footgun。
- **`{% include %}` / 模板内文件 include**：文件系统解析作用域有歧义，
  延后。
- **把模板能力推进交互式 CLI**：无需求。

## 迁移 / 兼容性

纯增量。已有 spec 不受影响——两个新字段都是 optional。`extra="forbid"`
保留。

无 deprecation。无 callback 签名变化。无 host-API 影响——
`Agentao(project_instructions=...)` 已存在，本次不动。

## 风险与对策

| 风险 | 对策 |
|------|------|
| 变量拼写错误被静默吞掉 | `StrictUndefined` 抛错；错误消息中显式给出变量名。 |
| 参数值含 Jinja 语法 | 参数值作为 **render context** 传入，**不会**被二次渲染。 |
| 不受信任来源的参数值 | `choices` 是唯一硬门控；其余情况是调用方自己的数据流入 prompt，与今天 `--prompt` 的信任模型一致。 |
| 不声明 parameters 的 spec 含字面 `{{ }}` | renderer 在 spec 和 CLI 参数都为空时整体 no-op，字面量原样透传。 |
| `--param` 拼写错误静默改变行为 | 重复 key、未知 key、缺 `=` 全部直接报错。无静默回退。 |

## 待决问题

1. 是否支持 `--param @file.json` 批量加载？CI 矩阵场景有用。合理的
   follow-up，不阻塞 v1。
2. `number` / `boolean` 何时加？建议：「消费方提 issue 给出明确 coercion
   规则偏好后再加」。

## 工作量估算

- Pydantic 模型 + 4 个 validator + Jinja renderer + `--param` 解析 +
  接入行：~110 行
- 测试（上述 15 个用例）：~220 行
- Docstring + 更新 `docs/CONFIGURATION.md` 一节：~30 行
- 合计：~360 行，**不动 core agent**。

一个 PR，门控就是上面的测试计划。无 host-API 接口变更 → 安全在 patch
release 中落地。

## 实现说明（已落地 2026-05-25）

落地 PR 与设计一致，并在评审过程中（`/code-review` + 5 次
`/codex:review` 迭代）补齐了 5 项加固。下方每一项都指出对应文件位置与
触发它的具体失败场景。

### 沙箱化的 Jinja 环境

`run_template.py::_build_environment` 使用 `jinja2.sandbox.SandboxedEnvironment`，
而非草案伪代码中的 `Environment`。若不沙箱化，共享 / 不可信的 recipe 可通过
`cycler` 等 Jinja 全局对象访问 Python 内部，在 `permission_mode` 与工具权限
生效之前就执行任意代码。`SecurityError` 被包装为 `RunTemplateError`，使沙箱
拒绝时仍返回 exit 2 / `invalid_spec` 的诊断信息，而非崩溃 CLI。回归测试：
`tests/cli/test_run_parameters.py::test_sandbox_blocks_attribute_escape`。

### 渲染时异常统一回收

`template.render()` 可传播模板内部产生的任意 Python 异常（`{{ 1/0 }}` →
`ZeroDivisionError`、`{{ "x" + 1 }}` → `TypeError`、缺 loader 的
`{% include %}` → `TemplateNotFound`）。渲染器用最终的 `except Exception`
（**不**扩展到 `BaseException`，让 `KeyboardInterrupt` / `SystemExit` 保留
原语义）将其包成 `RunTemplateError`。否则这些输入会带着完整 Python
traceback 崩出 CLI，而非返回设计中的 exit 2 envelope。回归测试：
`test_runtime_template_errors_map_to_invalid_spec`（参数化覆盖三种失败）。

### 在 spec 校验阶段拒绝 Jinja 保留名

`RunParameter` 的 name 校验在 ASCII 标识符正则之外，新增对 Jinja 常量
（`true`/`True`/`false`/`False`/`none`/`None`）与关键字
（`for`/`if`/`in`/`set`/...）的黑名单。两类名字都能通过
`[A-Za-z_][A-Za-z0-9_]*` 但会破坏模板：常量会静默胜过上下文变量
（`{{ true }}` 解析为 Jinja 的 `True` 而非用户传入的值），关键字在
`{{ }}` 中引用时会触发模板语法错误。黑名单同样包含 `self` 与
`parent`——它们由 Jinja runtime 无条件注入，接受这两个名字会
静默丢弃用户提供的值。回归测试：
`test_jinja_reserved_parameter_name_rejected`（参数化覆盖常量 + 关键字 +
runtime 注入名）。

### 渲染上下文用 positional 形式传入

`render_spec` 调用 `template.render(context)`（以位置参数方式传入 dict），
而非 `template.render(**context)`。kwargs 形式会和 `Template.render` 作为
绑定方法的 `self` 形参冲突（`got multiple values for argument 'self'`），
当 spec 声明参数名为 `self` 时触发。上面的黑名单已经拒掉了这个名字，
但 positional 形式作为防御深度防止将来再忘记加入名单。

### 仅有空白字符的 instructions 仍走 AGENTAO.md

`run.py` 用 `if spec.instructions and spec.instructions.strip():` 守住
`project_instructions` 接入路径，不是单纯的真值判断。YAML block scalar
`instructions: |\n  {{ extra }}\n` 在 `extra` 为空时渲染结果为 `"\n"`
（`keep_trailing_newline=True` 保留末尾换行）——这个独立换行符 truthy，
若不加 `.strip()` 守卫，仍会把它当成有效指令并静默覆盖 AGENTAO.md。
回归测试：原有的
`test_empty_rendered_instructions_does_not_override_agentao_md` 加上
`test_whitespace_only_rendered_instructions_does_not_override_agentao_md`。

### 诊断信息打磨

评审过程中还顺手修了几个相邻的 UX 问题：

- 渲染后 prompt 变成空字符串时，错误信息改成 "prompt template rendered
  to empty; check --param values"，而非通用的 "prompt is required" ——
  后者会让调用方误判根因。`_execute_with_args` 在渲染前快照 prompt 的
  truthy 状态，以便区分两种失败。
- 多个未知的 `--param` 现在一次性聚合在同一条错误里，而不是一次只报一个。
  保留用户在命令行上的输入顺序（不是 `sorted()`），消息按用户键入顺序
  指出问题键。
- `_render_field` 用正则（`'([^']+)'`）从 `UndefinedError` 的消息中抽取
  出错变量名，而非
  `split(" ", 1)[0].strip(...)`。正则可干净处理形如
  `'dict object' has no attribute 'missing'` 的复合错误，并在 Jinja
  消息格式变化时回退到原始消息。

### 涉及文件

- `agentao/cli/run_models.py` — `RunParameter`、`_JINJA_RESERVED_NAMES`、
  validator（标识符 + 保留名 + required-XOR-default + default∈choices +
  名字不重复）。
- `agentao/cli/run_template.py` — `SandboxedEnvironment`、
  `RunTemplateError`、`render_spec`、`_validate_params`、`_render_field`。
- `agentao/cli/run.py` — `--param` flag、`_parse_cli_params`、
  `render_spec` 调用点、`project_instructions` 接入（含 strip 守卫）。
- `tests/cli/test_run_parameters.py` — 30 个测试用例（原测试计划 15 个 +
  评审过程中追加 15 个）。
- `pyproject.toml` — 将 `jinja2>=3.0.0` 加入基础依赖。

最终测试数：`tests/` 由 2666 → 2684 全部通过（+18 新增；0 回归）。
