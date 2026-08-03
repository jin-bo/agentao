# Lint 门禁

**状态：** 已落地。CI job 名为 `lint-gate`，配置在 `pyproject.toml :: [tool.ruff.lint]`。

英文对照件：[lint-gate.md](lint-gate.md)。

## 是什么

`ruff check .`，只开六组规则：

| 规则 | 抓什么 | 范围 |
|---|---|---|
| `E9` | 语法 / IO 错误 —— 代码根本跑不起来 | 全仓 |
| `F402` | import 被循环变量遮蔽 —— 潜伏的 `UnboundLocalError` | 全仓 |
| `F811` | 重复定义未使用的名字 —— 两者之一是死的 | 全仓 |
| `F821` | 未定义名字 —— 运行时必然 `NameError` | 全仓，**星号导入模块除外** |
| `F405` | 星号导入模块**内**的未定义名字 —— F821 的盲区 | 那 8 个星号导入模块 |
| `F401` | 未使用的 import | 除 `agentao/` 外全仓 —— 见下文 |

没了。不管风格、不管格式化、不管 import 排序、不管语法现代化。

### 为什么要带上 `F405`

它不是第二个目标，而是**让 F821 真正生效的前提**。pyflakes 会把任何含
`from x import *` 的模块里的未定义名字全部降级成 `F405`（"可能未定义，或来自星号
导入"）。只选 F821 的话，这条旗舰规则在最不该失效的那批模块里恰好静默失效：

```
agentao/harness/__init__.py            agentao/harness/projection.py
agentao/harness/events.py              agentao/harness/replay_projection.py
agentao/harness/models.py              agentao/harness/schema.py
agentao/harness/protocols.py           agentao/tool_runner.py
```

`agentao/harness/` 是 `agentao.host` 的**公开**弃用别名，而且这些文件里每一个都
带有真实的导入后逻辑（`HarnessEvent = _HostEvent`、`__all__ = list(_host_all) +
[...]`、`__getattr__` / `__dir__`），不只是一行星号导入。

实测验证过：星号导入模块里放一个未定义名字，`F821` 报 `All checks passed`，
`F405` 才报出来。不堵这个洞的话，0.5.0 删除别名时改到这些文件引入的一个拼写错误
会一路绿灯发布，然后在仍走弃用路径的下游 embedder 那里 `import agentao.harness`
直接 `NameError`。

`F405` 在全仓实测为 **0**，所以今天加它零成本。

### 范围是 `.`，不是目录清单

门禁扫整个仓库（ruff 自己遵守 `.gitignore` 和 `[tool.ruff] exclude`）。早期版本传的是
`agentao/ tests/ examples/`，静默漏掉了 13 个受版本控制的文件：`main.py`、CI 自己会
执行的两个 `scripts/write_*_schema.py`，以及 `skills/skill-creator/` 下的 10 个文件
—— 那是 `agentao/` 之外唯一被 **force-include 进 wheel**
（`pyproject.toml :: force-include`）、并且在技能激活时由 agent 实际执行的 Python。

这个遗漏不是理论问题：`skills/skill-creator/scripts/quick_validate.py` 里就躺着一个
门禁永远看不见的未使用 `import os`。

## 为什么这么窄

规则选择来自实测，不是口味。用锁定的 ruff 0.16.1 对 merge-base 树（`7eb762e`）
重新测量，加 `--isolated` 以保证测的是规则原始命中数、而非配置后的门禁：

| 规则集 | 命中数 | 找到的真实 bug |
|---|---|---|
| `UP` (pyupgrade) | 2743 | —— 不是缺陷类别 |
| ruff 自带默认 (`E4,E7,E9,F`) | 344 | 1（见下） |
| `F401`（未使用 import） | 253 = 68 `agentao/` + 169 `tests/` + 16 `examples/` | 0 |
| `F841`（未使用变量） | 25 | 1 —— 已修，见下 |
| `E9,F402,F811,F821` | 4 | **0** |
| `F405` | 0 | 0 |

那 4 条门禁命中在修之前都被逐条读过，没有一条是活 bug。所以这个门禁诚实的说法
**不是**"它找到了 bug"，而是"它花几秒 CI，钉住一个已经咬过本仓库一次的类别"。

那一次是 `eef5b70`（PR #141），标题就是 *"declare plugin types for F821"*。门禁落地时
F821 实测为 0 —— 门禁的作用是让它保持为 0。

有主张的规则集是凭证据否掉的。`UP` 的 2743 条会把全树能正常工作的代码重写一遍，
把真正的改动淹没在 review 里。本仓库已经用同样理由否过一次可比的 mypy ratchet ——
见 `docs/design/refactor-audit-2026-07.md`，89 条 mypy 命中里有 27 条是 mixin 误报。

### `select` 是替换 ruff 默认值 —— 包括丢掉 F841

`select` 是替换而非追加：一旦配置，ruff 自带的 `E4,E7,E9,F` 就被**关掉**了。实际后果是
`F841`（未使用变量，25 条命中）没有被启用，尽管 ruff 开箱即用是开着它的。

这是有意的取舍，不是疏漏，而且它不免费。25 条里有一条是已发布代码里的真实缺陷，
**现已修复**：`agentao/skills/drafts.py:262` 在注释 *"Drop any extra trailing newline
beyond what old_fm had, keep body intact"* 下面绑定了 `old_fm = m.group(0)`，然后返回一个
硬编码的 `new_fm`，`old_fm` 从头到尾没被用过。

`replace_skill_name` 改写的是用户自己写的文件，所以契约是「除 `name:` 的值以外逐字节
不变」。用字面量 `f"---\n{block}\n---\n"` 重建 frontmatter 破坏了这个契约 —— 实测 7 种
排版里坏了 **6 种**，其中包括本仓库每个技能都在用的那一种：

| 输入形状 | 旧输出 |
|---|---|
| `---\n…\n---\n\n# Body`（正文前有空行） | 空行被删 |
| `---\n…\n---`（文件在围栏处结束） | 多加了换行 |
| `\n---\n…`（开头有空行） | 前导空白被丢 |
| `---  \n…`（围栏行尾有空格） | 空格被丢 |
| CRLF 文档 | 被改写成 LF |
| `name: a\n\ndesc:`（frontmatter 内有空行） | 空行被删 |

修法是按 frontmatter 块自身的 span 把新值**拼接**回原字符串，且在块内只替换 `name:` 的
**值**那一段 —— 替换整个 match 会把行尾空白一起带走，因为 `_NAME_LINE_RE` 以 `\s*$`
结尾，而 `\s` 会吞掉行尾的 `\r`。现在有 16 个测试比对完整字符串；原有那个测试之所以
在 buggy 版本上也能通过，是因为 `"# Python Testing" in out` 看不见被删掉的空行。

F841 仍然暂不启用：剩下的命中需要逐条读（循环变量、有意的 `_` 式绑定都是常见误报
形状），而且那和"把这个门禁立起来"是两件事。它依然是下一条最该加的规则。

## 为什么 `agentao/` 关掉了 F401

`F401` 在门禁走到的每个地方都强制执行 —— `tests/`、`examples/`、`skills/`、`scripts/`、
`main.py` —— 唯独通过 `per-file-ignores` **整包豁免 `agentao/`**。豁免的是整个包而不是一份模块清单，这一点
才是有意思的地方。

最初的方案是只豁免"再导出模块"。结果那个集合**不可知**。三个独立检查互相矛盾：

**1. 实证 —— 删掉就构建失败。** 对 `agentao/` + `tests/` 跑
`ruff check --select F401 --fix` 应用了 212 处修复，然后 **9 个测试模块在收集阶段挂掉**：

```
E ImportError: cannot import name 'AcpInteractionRequiredError' from 'agentao.acp_client.client'
E ImportError: cannot import name '_parse_retry_after' from 'agentao.llm.client'
E ImportError: cannot import name 'acp_client' from 'agentao'
```

失败全部在 `agentao/`，`tests/` 一个都没有。

**2. 静态分类与之矛盾。** 一个"这个名字在仓库别处有没有从这个模块被 import"的扫描
把 `AcpInteractionRequiredError` 和 `_parse_retry_after` 归类为*死代码* —— 正是检查 1
刚刚证明是载荷中的那两个名字。多行括号 import 骗过了 grep。

**3. 同一次扫描把 `HostEvent` 也判成死的。** 而 `HostEvent` 在
`agentao.host.__all__` 里 —— 那是本包对 embedder 公开宣传的稳定边界。

根因不是工具质量问题。**agentao 是一个发布出去的库。** 为下游 embedder 再导出的名字
在本仓库内无人 import —— 而那恰恰就是公开 API 在单文件 linter 眼里、在全仓 grep 眼里、
在测试套件眼里的样子。可用的三个信号没有一个能把"公开接口"和"死代码"分开，按它们的
建议删除，就是给 embedder 制造一次静默的破坏性变更。

`tests/` 和 `examples/` 没有这种歧义：没有任何东西从测试模块 import，所以那里的未使用
import 明确是死的。那一半已清理（91 个文件共 185 条 —— `tests/` 184 条，其余在
`examples/`），现已纳入门禁。

要让 `agentao/` 将来变得可判定，就给每个再导出枢纽加显式 `__all__` —— ruff 把 `__all__`
成员算作已使用，届时这条规则可以逐模块打开，歧义是被真正解决掉的，而不是被假设掉的。

### 豁免范围明知比证据宽

这一点值得挑明，因为上面的论证并不能覆盖它的全部：`agentao/` 那 68 条 F401 命中里，
25 条在 `__init__.py`（再导出本就是这类模块的全部职责），42 条散在 22 个叶子模块里。
实证上真正弄坏构建的那两个名字 —— `_parse_retry_after` 和
`AcpInteractionRequiredError` —— 恰好就落在其中两个叶子模块里，且都是有文档记录的
兼容枢纽。

也就是说，整包豁免为了保住 2 条而静默了约 40 条，其中至少一条确实是死的：
`agentao/sandbox/policy.py:32` 有一个未使用的 `import platform` —— 就在本门禁落地 PR
改过的那个文件里。

更窄的配置 —— 豁免 `agentao/**/__init__.py` 加上那两个具名枢纽 —— 可以在**大致**同等
安全度下让规则对其余约 260 个模块保持生效。这里没有采用，是因为"大致"两个字在那句话里
承担了真实分量：那两个枢纽是**靠弄坏构建**找出来的，不是靠一个可推广的方法找出来的，
没有任何理由相信这份枚举是完整的。宁可走上面的 `__all__` 路线，也不要猜这份清单。

## 门禁落地时修掉的四条

| 位置 | 命中 | 修法 |
|---|---|---|
| `agentao/sandbox/policy.py:213` | `for field, ...` 遮蔽了 `dataclasses.field` | 改名 `field_name`，与同文件的 `_absolutize_path_fields` 一致。该函数从未调用 `dataclasses.field`，所以这是潜伏陷阱而非活 bug —— 但日后加一次这样的调用就会以一个令人困惑的 `UnboundLocalError` 失败。 |
| `agentao/cli/app.py:272` | `PlanController` 被 import 两次，第一份没用 | 从第一条 import 里去掉。 |
| `agentao/cli/_utils.py:154` | `console` 形参遮蔽了模块级 import | 改名 `console_`，与 `replay_render/_turn.py`、`_views.py`、`_banners.py` 里同样问题的同一修法一致。 |
| `tests/test_host_typing.py:227` | 函数内 `import sys`，而模块级已经 import 过 | 删掉函数内那次重复 import。 |

注意第三条**没有**用 `# noqa` 糊过去 —— 加抑制等于把这条规则要抓的那个混淆原样保留。
它也没有用"删掉形参"来修，第一版就是那么改的，而那样错在另一个点上：本仓库捕获和
重定向 CLI 输出的方式，是替换**处理器模块**上的 `console` 属性
（`tests/test_acp_client_cli.py` 里的 `patch.object(acp_mod, "console", ...)`），而渲染
函数住在另一个模块、有自己的那份 import。删掉形参会让 `/memory user` 把外围文字打进
被捕获的 sink、把条目本身打到真实 stdout 上。**当被遮蔽的东西是一个注入点时，改名，
不要删。**

## 怎么抑制误报

`F401` 在 `tests/` 里有一种已知合法的抑制形状：为 pytest 的按名注入而 import 的共享
fixture，并非为了直接使用。`tests/support/acp_server.py` 明确鼓励这种写法（"需要按
`tmp_path` 参数化时把它们包成 `@pytest.fixture`"），而 ruff 看不见这类引用 —— 更糟的是
它给出的自动修复会删掉那行 import，把文件里每个测试变成收集期的
`fixture ... not found`。

写法：

```python
from tests.support.acp_server import mock_server_fixture  # noqa: F401 — pytest fixture injection
```

**永远在规则码后面写理由。** 光秃秃一个 `# noqa` 是不可 review 的。

## 本地怎么跑

```bash
uv run ruff check .
```

这和 CI 里的命令逐字符相同。规则（`[tool.ruff.lint]`）和范围（ruff 的仓库遍历加
`[tool.ruff]` 排除项）都在 `pyproject.toml` 里，所以没有需要在两处同步的目录清单 ——
早期版本曾把目录清单手抄到 workflow 和本文档的两个位置，而它已经和实际生效的范围
产生了偏差。

## 以后怎么抬高标准

一次加一条规则，且必须先测量它会命中什么、并抽样读过那些命中。本文档确立的先例是：
**一个规则集靠找到缺陷来赢得位置，而不是靠它是个公认标准。**

下一批候选，按顺序：**`F841`** —— 它已经靠揪出 `drafts.py` 的 frontmatter 缺陷证明了自己
（见上）；剩下的工作是把它其余的命中逐条读完。之后
`B`（flake8-bugbear）值得测一测；`UP`、`SIM`、`I` 明确排除在外，除非有人能指出它们在
这里本可以抓到的一个缺陷。
