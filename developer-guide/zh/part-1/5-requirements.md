# 1.5 运行环境要求

> **本节你会学到**
> - 嵌入 Agentao 之前对 Python / OS / 网络 / 磁盘的要求
> - 按你需要的功能选哪些 extras
> - 7 行命令的环境就绪自检清单

把 Agentao 嵌入你的产品前，先核对环境。

## Python 版本

- **最低要求：Python 3.10**
- 推荐：3.11 或 3.12（更好的错误追踪与性能）
- 3.13 可用但未做广泛验证

## 包管理

Agentao 源码库使用 **`uv`** 做包管理与运行。嵌入你的应用时，**你可以继续用 pip**——`agentao` 是标准 PyPI 包。

0.4.0 起 `pip install agentao` 只装嵌入用的最小核心，按使用场景选安装行：

```bash
# 嵌入宿主（`from agentao import Agentao`）—— 闭包最小
pip install agentao

# 用 web_fetch / web_search 工具 —— 加 beautifulsoup4
pip install 'agentao[web]'

# 用中文记忆召回 —— 加 jieba
pip install 'agentao[i18n]'

# CLI 用户（`agentao` 命令行）—— 加 rich/prompt-toolkit/readchar/pygments
pip install 'agentao[cli]'

# uv 用户写法相同
uv add 'agentao[cli]'
```

## LLM 凭据

Agentao 通过 OpenAI 兼容接口调用 LLM。你至少需要**一组凭据**：

| 环境变量 | 说明 | 示例 |
|---------|------|------|
| `OPENAI_API_KEY` | API Key（**必填**） | `sk-...` |
| `OPENAI_BASE_URL` | API 端点（**必填**） | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 模型名（**必填**） | `gpt-5.4` |
| `LLM_TEMPERATURE` | 采样温度（可选，默认 0.2） | `0.3` |
| `LLM_PROVIDER` | 厂商标签（可选，默认 OPENAI） | `ANTHROPIC` |

> **`{PROVIDER}_API_KEY`、`{PROVIDER}_BASE_URL` 和 `{PROVIDER}_MODEL` 三者均为必填。** 若任一缺失且未通过构造器参数传入，`LLMClient.__init__` 在启动时立即抛出 `ValueError`。以编程方式嵌入时，构造器参数 `api_key=`、`base_url=` 和 `model=` 可代替环境变量。

### 已验证的兼容端点

| 厂商 | base_url | 默认模型 |
|------|----------|---------|
| OpenAI | （默认） | gpt-5.4 / gpt-5 系列 |
| Anthropic | `https://api.anthropic.com/v1` | claude-sonnet-4-6 |
| Gemini | 通过 OpenAI 兼容网关 | gemini-flash-latest |
| DeepSeek | `https://api.deepseek.com` | deepseek-chat |
| 自建 vLLM | `http://your-host:8000/v1` | 按你部署的模型 |

## 操作系统支持矩阵

| OS | 核心运行时 | Shell 沙箱 | MCP | 备注 |
|----|----------|----------|-----|------|
| macOS 13+ | ✅ 完整 | ✅ `sandbox-exec` | ✅ | 推荐开发环境 |
| Linux | ✅ 完整 | ❌ 无沙箱（运行于容器/用户命名空间建议） | ✅ | 生产首选 |
| Windows | ⚠️ 基础可用 | ❌ 无沙箱 | ⚠️ 部分 MCP 服务器可能仅 Unix | 建议通过 WSL2 |

> Shell 沙箱是**可选**的额外一层防御（第 6.2 节）。没有沙箱时，`run_shell_command` 仍受**权限引擎**和**工具确认**约束。

## 网络要求

- **出站**：Agent 的 LLM 调用需要访问你配置的 `base_url`；部分工具（`web_fetch` / `web_search`）需要访问外网
- **入站**：**不需要**。Agentao 不监听端口；ACP 模式走 stdio，不占网络端口
- **MCP SSE 服务器**：如果你接入基于 SSE 的 MCP 服务器，需要访问它们的 URL

## 磁盘布局

嵌入后 Agentao 会在以下位置读写文件：

| 路径 | 用途 | 是否可关闭 |
|------|------|----------|
| `<working_directory>/.agentao/` | 项目级配置（MCP、权限、记忆） | 通过权限规则可约束 |
| `<working_directory>/AGENTAO.md` | 项目说明（你写的） | 不写就不加载 |
| `<working_directory>/agentao.log` | 运行日志 | 可通过自定义 logger 关闭 |
| `~/.agentao/` | 用户级配置、记忆 | 可改路径或跳过 |

生产环境通常把 `<working_directory>` 设为**租户/会话专属的临时目录**，隔离彼此（第 7.1 节详述）。

## 可选依赖

0.4.0 起能力按 extras 切分；多 extras 用逗号合写（`agentao[cli,web]`）：

```bash
# CLI / 交互 UI（P0.9 把这些从默认核心里拆出来）
pip install 'agentao[cli]'       # rich + prompt-toolkit + readchar + pygments
pip install 'agentao[web]'       # beautifulsoup4 —— web_fetch / web_search 必需
pip install 'agentao[i18n]'      # jieba —— 中文记忆召回

# 重型文件处理工具
pip install 'agentao[pdf]'       # PDF 读取（pymupdf、pdfplumber）
pip install 'agentao[excel]'     # Excel 读写（pandas、openpyxl）
pip install 'agentao[image]'     # 图像处理（Pillow）
pip install 'agentao[crypto]'    # pycryptodome
pip install 'agentao[google]'    # google-genai
pip install 'agentao[crawl4ai]'  # crawl4ai
pip install 'agentao[tokenizer]' # tiktoken —— 精确 token 计数

# 元 extras
pip install 'agentao[full]'      # 全部（与 0.3.x 闭包等价）
```

> 不装 `[web]` 时注册阶段就会**跳过** `web_fetch` / `web_search`——LLM 在工具
> 列表里看不到它们，避免 model 调一个会失败的工具。不装 `[i18n]` 时 CJK 记忆
> 召回会一次性 warning 并降级（Latin 查询完全跳过 jieba，零成本）。`[cli]` 是
> 跑 `agentao` 命令行的必需 extras——裸装运行 `agentao` 会输出友好提示
> `pip install agentao[cli]` 并 exit 2。

完整 0.3.x → 0.4.0 迁移矩阵见
[`docs/migration/0.3.x-to-0.4.0.md`](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.3.x-to-0.4.0.md)。

## 版本兼容性

- Agentao **目前为 0.x**（Beta）。次版本间可能有破坏性变化，请锁定版本号：
  ```
  agentao>=0.4.0,<0.5
  ```
- 本指南基于 **v0.4.0 GA**。版本差异会在章节开头标注。
- 0.4.0 唯一的 break 是依赖拆分（P0.9）。0.3.x 用户要零行为变更可用
  `pip install 'agentao[full]'`。

## 检查清单

```bash
# 跑这几条命令应全部通过
python --version                        # >= 3.10
pip show agentao | grep Version          # 你锁定的版本
echo $OPENAI_API_KEY | head -c 10       # 应有 key 前缀
echo $OPENAI_BASE_URL                   # 必须非空
echo $OPENAI_MODEL                      # 必须非空
agentao --help                          # CLI 可用性（可选）
python -c "from agentao import Agentao; print('OK')"
python -c "from agentao.transport import SdkTransport; print('OK')"
```

环境就绪，进入第 2 部分真正开始集成。

## TL;DR

- **Python ≥ 3.10** 必需，推荐 3.11 / 3.12。
- **3 个环境变量必填**：`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`（也可走构造器参数）。
- **默认装包只含嵌入核心**，按需加 extras：`[web]` / `[i18n]` / `[cli]` / `[pdf]` / `[excel]` / `[image]` / `[full]`（全装）。
- **不监听任何入站端口**——Agentao 要么是库、要么是 stdio 子进程；出站只会去你的 LLM 端点和工具 URL。
- **生产环境务必锁定版本范围**：`agentao>=0.4.0,<0.5`。

→ [第 2 部分 · Python 进程内嵌入](/zh/part-2/)
