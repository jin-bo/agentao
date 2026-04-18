# 1.5 运行环境要求

把 Agentao 嵌入你的产品前，先核对环境。

## Python 版本

- **最低要求：Python 3.10**
- 推荐：3.11 或 3.12（更好的错误追踪与性能）
- 3.13 可用但未做广泛验证

## 包管理

Agentao 源码库使用 **`uv`** 做包管理与运行。嵌入你的应用时，**你可以继续用 pip**——`agentao` 是标准 PyPI 包。

```bash
# 你的应用（pip 方式）
pip install agentao

# 或 uv 方式（推荐）
uv add agentao
```

## LLM 凭据

Agentao 通过 OpenAI 兼容接口调用 LLM。你至少需要**一组凭据**：

| 环境变量 | 说明 | 示例 |
|---------|------|------|
| `OPENAI_API_KEY` | API Key（必填） | `sk-...` |
| `OPENAI_BASE_URL` | 自定义端点（可选） | `https://api.deepseek.com` |
| `OPENAI_MODEL` | 模型名（可选） | `gpt-4o-mini` |
| `LLM_TEMPERATURE` | 采样温度（可选，默认 0.2） | `0.3` |
| `LLM_PROVIDER` | 厂商标签（可选，默认 OPENAI） | `ANTHROPIC` |

### 已验证的兼容端点

| 厂商 | base_url | 默认模型 |
|------|----------|---------|
| OpenAI | （默认） | gpt-4o / gpt-5 系列 |
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

- **出站**：Agent 的 LLM 调用需要访问你配置的 `base_url`；部分工具（`web_fetch` / `google_web_search`）需要访问外网
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

某些高级工具需要 extras：

```bash
pip install 'agentao[pdf]'      # PDF 读取
pip install 'agentao[excel]'    # Excel 读写
pip install 'agentao[image]'    # 图像处理
pip install 'agentao[tokenizer]' # 精确 token 计数
pip install 'agentao[full]'     # 全部
```

只安装你用得到的，保持依赖面最小。

## 版本兼容性

- Agentao **目前为 0.x**（Beta）。次版本间可能有破坏性变化，请锁定版本号：
  ```
  agentao==0.2.10     # 或 >=0.2.10,<0.3
  ```
- 本指南基于 **v0.2.10 GA**。版本差异会在章节开头标注。

## 检查清单

```bash
# 跑这几条命令应全部通过
python --version                        # >= 3.10
pip show agentao | grep Version          # 你锁定的版本
echo $OPENAI_API_KEY | head -c 10       # 应有 key 前缀
agentao --help                          # CLI 可用性（可选）
python -c "from agentao import Agentao; print('OK')"
python -c "from agentao.transport import SdkTransport; print('OK')"
```

环境就绪，进入第 2 部分真正开始集成。

→ [第 2 部分 · Python 进程内嵌入](/zh/part-2/)
