# 1.4 Hello Agentao in 5 min

Goal: get a minimal run through **both embedding paths**. Five minutes, no custom code.

## Prerequisites

```bash
# 1. Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install Agentao
pip install agentao      # or: uv pip install agentao
# Or from source:
git clone https://github.com/jin-bo/agentao && cd agentao && uv sync

# 3. Configure LLM credentials
export OPENAI_API_KEY="sk-..."
# Optional: switch to another OpenAI-compatible endpoint
# export OPENAI_BASE_URL="https://api.deepseek.com"
# export OPENAI_MODEL="deepseek-chat"
```

## Example A · Python SDK (~20 lines)

Save as `hello_sdk.py`:

```python
"""Minimal embedded Agentao example.
Run: OPENAI_API_KEY=sk-... python hello_sdk.py
"""
from pathlib import Path
from agentao import Agentao
from agentao.transport import SdkTransport

# 1. Optional event listener
def on_event(event):
    # event.type ∈ {TURN_START, TOOL_START, LLM_TEXT, THINKING, ...}
    if event.type.name == "LLM_TEXT":
        print(event.data.get("chunk", ""), end="", flush=True)

# 2. Tool confirmation callback (in production, wire to your approval UI)
def confirm_tool(name, description, args):
    print(f"\n[auto-approve] {name}({args})")
    return True

transport = SdkTransport(on_event=on_event, confirm_tool=confirm_tool)

# 3. Construct the agent — always pass working_directory explicitly
agent = Agentao(
    transport=transport,
    working_directory=Path.cwd(),
)

# 4. Chat
reply = agent.chat("List the 3 largest files under the current directory")
print(f"\n\n=== Final reply ===\n{reply}")

# 5. Clean up
agent.close()
```

Run:

```bash
python hello_sdk.py
```

You'll see the agent invoke tools (`run_shell_command`, `glob`, …) and stream its final answer.

### Key points

- `from agentao import Agentao` is the only entry point
- `SdkTransport` funnels all interaction through four callbacks
- **Always pass `working_directory=`** — otherwise multi-instance setups will cross-contaminate (Part 7.1)
- `agent.chat()` is blocking; see Part 2.6 for async wrappers

## Example B · ACP Protocol (any language)

Open **two terminals**:

**Terminal 1 — launch Agentao as an ACP server:**

```bash
agentao --acp --stdio
```

The process now reads JSON-RPC requests on stdin, writes responses and notifications on stdout.

**Terminal 2 — or, simply paste these lines into Terminal 1** (one NDJSON message per line):

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp"}}
{"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"<id from previous step>","prompt":[{"type":"text","text":"hello"}]}}
```

You'll see:

- `initialize` response announces capabilities (`loadSession`, `mcpCapabilities`, …)
- `session/new` returns a fresh `sessionId`
- `session/prompt` triggers a stream of `session/update` notifications (streaming text, tool events), then a final response when the turn ends

### Real host (Node pseudocode)

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
      handleUpdate(msg.params);              // streamed text, tool events, thinking
    } else if (msg.method === 'session/request_permission') {
      showPermissionDialog(msg.params);       // show your approval UI
    } else if (msg.id) {
      resolvePending(msg.id, msg);            // response
    }
  }
});

send('initialize', { protocolVersion: 1, clientCapabilities: {} });
// Later: send('session/new', { cwd: '/your/project' })
// Then:  send('session/prompt', { sessionId, prompt: [{type:'text', text:'hello'}] })
```

### Key points

- Framing is **NDJSON** (newline-delimited), not WebSocket, not raw stdout
- Handshake order: `initialize` → `session/new` → `session/prompt`
- `session/update` is a **notification** (no `id`) — do not respond
- `session/request_permission` is a **request** (has `id`) — the host must reply in reasonable time

## Next steps

With both Hello Worlds running:

- Ship fast? Go to [Part 2 · Python Embedding](/en/part-2/)
- Building an IDE plugin? Jump to [Part 3 · ACP Protocol](/en/part-3/)
- Double-check environment? Continue to [1.5 Requirements →](./5-requirements)
