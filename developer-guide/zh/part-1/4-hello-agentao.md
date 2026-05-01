# 1.4 5 分钟 Hello Agentao

目标：从零跑通**两条路径**各一个最小示例。全程 5 分钟，不写任何自定义代码。

## 前置条件

```bash
# 1. 安装 uv（Python 包管理器）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 装 Agentao 并加上 CLI extras（示例 B 走 `agentao` 命令行）
pip install 'agentao[cli]'      # 或：uv pip install 'agentao[cli]'
# 嵌入路径（示例 A）裸装 `pip install agentao` 即可。
# 或从源码：
git clone https://github.com/jin-bo/agentao && cd agentao && uv sync

# 3. 配置 LLM 凭据
export OPENAI_API_KEY="sk-..."
# 可选：切换到其他兼容端点
# export OPENAI_BASE_URL="https://api.deepseek.com"
# export OPENAI_MODEL="deepseek-chat"
```

## 示例 A · Python SDK（约 20 行）

把下面的代码保存为 `hello_sdk.py`：

```python
"""最小可运行的 Agentao 嵌入示例。
运行：OPENAI_API_KEY=sk-... python hello_sdk.py
"""
from pathlib import Path
from agentao import Agentao
from agentao.transport import SdkTransport

# 1. 定义一个可选的事件监听器（可省略）
def on_event(event):
    # event.type ∈ {TURN_START, TOOL_START, LLM_TEXT, THINKING, ...}
    if event.type.name == "LLM_TEXT":
        print(event.data.get("chunk", ""), end="", flush=True)

# 2. 工具确认回调（生产环境应接入你的 UI 做审批）
def confirm_tool(name, description, args):
    print(f"\n[auto-approve] {name}({args})")
    return True

transport = SdkTransport(on_event=on_event, confirm_tool=confirm_tool)

# 3. 构造 Agent —— working_directory 务必显式指定
agent = Agentao(
    transport=transport,
    working_directory=Path.cwd(),
)

# 4. 开始对话
reply = agent.chat("帮我列出当前目录下 3 个最大的文件")
print(f"\n\n=== Final reply ===\n{reply}")

# 5. 优雅清理
agent.close()
```

运行：

```bash
python hello_sdk.py
```

你会看到 Agent 通过工具调用（`run_shell_command`、`glob` 等）完成任务，流式输出最终答复。

### 关键点

- `from agentao import Agentao` 是唯一入口
- `SdkTransport` 把所有交互收敛为 4 类回调
- `working_directory=` **必须显式传入**，否则多实例场景会串扰（第 7.1 节）
- `agent.chat()` 是阻塞调用；异步封装见第 2.6 节

## 示例 B · ACP 协议（任意语言）

打开**两个终端**：

**终端 1 —— 启动 Agentao 作为 ACP Server：**

```bash
agentao --acp --stdio
```

此时进程通过 stdin 读入 JSON-RPC 请求、通过 stdout 写出响应和通知。

**终端 2 —— 手工喂几条协议消息（演示用）：**

你也可以直接往终端 1 粘贴下面这几行（每行一条 NDJSON）：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp"}}
{"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"<上一步返回的 id>","prompt":[{"type":"text","text":"你好"}]}}
```

你会看到：
- `initialize` 响应中 Agent 宣告支持的能力（`loadSession`, `mcpCapabilities` 等）
- `session/new` 返回新建的 `sessionId`
- `session/prompt` 触发一系列 `session/update` 通知（流式文本、工具调用），最后返回一个响应表示轮次结束

### 真实宿主的做法（Node 示例伪代码）

```javascript
import { spawn } from 'node:child_process';

const proc = spawn('agentao', ['--acp', '--stdio']);
let nextId = 1;

function send(method, params) {
  const msg = { jsonrpc: '2.0', id: nextId++, method, params };
  proc.stdin.write(JSON.stringify(msg) + '\n');
}

proc.stdout.on('data', (buf) => {
  for (const line of buf.toString().split('\n').filter(Boolean)) {
    const msg = JSON.parse(line);
    if (msg.method === 'session/update') {
      // 流式文本、工具事件、思考过程 ...
      handleUpdate(msg.params);
    } else if (msg.method === 'session/request_permission') {
      // 把工具确认弹窗给用户
      showPermissionDialog(msg.params);
    } else if (msg.id) {
      // 响应
      resolvePending(msg.id, msg);
    }
  }
});

send('initialize', { protocolVersion: 1, clientCapabilities: {} });
// 稍后：send('session/new', { cwd: '/your/project' })
// 再后：send('session/prompt', { sessionId, prompt: [{type:'text', text:'你好'}] })
```

### 关键点

- 协议帧是 **NDJSON**（换行分隔的 JSON），不是 WebSocket 也不是纯 stdout
- 先 `initialize` 握手，再 `session/new`，再 `session/prompt`
- `session/update` 是**通知**（不带 `id`），不需要响应
- `session/request_permission` 是**请求**（带 `id`），宿主必须在合理时间内回复

## 下一步

两个 Hello World 都跑通后，你可以：

- 想快速上线：跳去 [第 2 部分 · Python 嵌入](/zh/part-2/)
- 想做 IDE 插件：跳去 [第 3 部分 · ACP 协议](/zh/part-3/)
- 先确认环境：继续看 [1.5 运行环境要求 →](./5-requirements)
