# 1.4 5 分钟 Hello Agentao

目标：跑通最小的 Python 嵌入示例，**不用写任何自定义代码**。

> 想先尝鲜非 Python 路径？跳到 [3.1 ACP Quick Try](/zh/part-3/1-acp-tour#60-秒快速尝鲜)。

::: tip ⚡ 端到端可跑（约 3 分钟）
**产出** —— Agent 思考、调用 `glob` + `run_shell_command`，打印 cwd 下最大的 3 个文件。
**技术栈** —— `pip install 'agentao>=0.4.0'` + 3 个环境变量 + 6 行 Python。
**运行** —— 粘贴 Step 3 的代码后跑 `python hello.py`。
:::

## 第 1 步 · 安装（1 分钟）

```bash
pip install 'agentao>=0.4.0'
```

`pip install agentao` 只装嵌入核心。`[web]` / `[cli]` / `[i18n]` 等 extras 按需后加，见 [1.5 运行环境](./5-requirements)。

## 第 2 步 · 配置凭据（1 分钟）

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"   # 或任意 OpenAI 兼容端点
export OPENAI_MODEL="gpt-5.4"
```

三个变量都必需。DeepSeek / Gemini / vLLM 用法一致，把 `OPENAI_BASE_URL` 和 `OPENAI_MODEL` 指向对应端点即可。

## 第 3 步 · 运行（1 分钟）

保存为 `hello.py`：

```python
from pathlib import Path
from agentao import Agentao

agent = Agentao(working_directory=Path.cwd())
print(agent.chat("列出当前目录下 3 个最大的文件。"))
agent.close()
```

```bash
python hello.py
```

你会看到 Agentao 思考、调用 `run_shell_command` / `glob`，最后打印类似：

```text
当前目录下最大的 3 个文件：
1. ./node_modules/.cache/...   (12 MB)
2. ./dist/bundle.js            (4.1 MB)
3. ./README.md                 (38 KB)
```

## 刚刚发生了什么

- `Agentao(...)` 创建了**一个有状态的会话** —— 历史、工具、记忆都绑定到这个实例
- `chat()` 跑完了完整的 LLM 循环：思考 → 调工具 → 观察结果 → 再思考 → 回答
- `working_directory` 把文件/Shell 工具锚定到当前目录。**生产环境务必显式传 `Path`**，多实例并发时不能共享 `Path.cwd()`
- `close()` 释放 MCP 子进程和 DB 句柄。真实代码里要放在 `try/finally` 中

## 加上流式输出（再 5 行）

```python
from pathlib import Path
from agentao import Agentao
from agentao.transport import SdkTransport

def stream(ev):
    if ev.type.name == "LLM_TEXT":
        print(ev.data["chunk"], end="", flush=True)

agent = Agentao(
    working_directory=Path.cwd(),
    transport=SdkTransport(on_event=stream),
)
agent.chat("列出当前目录下 3 个最大的文件。")
agent.close()
```

整个集成模式就这两步：`Agentao(...)` + `chat(...)`。工具确认、自定义工具、权限、记忆等所有其他能力都是从这两个调用扩展出来的。

## 常见问题

| 现象 | 原因 |
|------|------|
| `ImportError: cannot import name 'Agentao'` | 没装包，或从 `agentao.agent`（非公开路径）导入 |
| `ValueError: OPENAI_API_KEY is not set` | `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` 三者都必需 |
| Agent 一直回复 `Tool execution cancelled by user` | 默认权限拒绝了写操作，见 [5.4 权限引擎](/zh/part-5/4-permissions) |
| `chat()` 永不返回 | 多半是工具死循环或缺 `ask_user` 回调，见 [附录 F.2](/zh/appendix/f-faq#f-2-runtime-behavior) |

完整排错：[附录 F](/zh/appendix/f-faq)。

## 下一步去哪

| 你想做… | 推荐章节 |
|--------|---------|
| 把自己的业务 API 包成工具 | [5.1 自定义工具](/zh/part-5/1-custom-tools) |
| 在 FastAPI / Flask 里做 SSE 流式输出 | [2.7 FastAPI / Flask 嵌入](/zh/part-2/7-fastapi-flask-embed) |
| 用 Node / Go / Rust / IDE 驱动 Agentao | [第 3 部分 · ACP](/zh/part-3/) |
| 先确认环境是否满足 | [1.5 运行环境要求](./5-requirements) |
| 理解核心名词（Agent / Tool / Skill / …） | [1.2 核心概念](./2-core-concepts) |
