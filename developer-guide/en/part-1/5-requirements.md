# 1.5 Requirements

Verify your environment before embedding Agentao.

## Python version

- **Minimum: Python 3.10**
- Recommended: 3.11 or 3.12 (better tracebacks and perf)
- 3.13 works but is not exhaustively verified

## Package management

Agentao's own repo uses **`uv`**. When embedding, **pip is fine** — `agentao` is a standard PyPI package.

```bash
# Pip
pip install agentao

# uv (recommended)
uv add agentao
```

## LLM credentials

Agentao calls LLMs through an OpenAI-compatible interface. Configure at least one credential set:

| Env var | Purpose | Example |
|--------|---------|---------|
| `OPENAI_API_KEY` | API key (required) | `sk-...` |
| `OPENAI_BASE_URL` | Custom endpoint (optional) | `https://api.deepseek.com` |
| `OPENAI_MODEL` | Model id (optional) | `gpt-4o-mini` |
| `LLM_TEMPERATURE` | Sampling temp (optional, default 0.2) | `0.3` |
| `LLM_PROVIDER` | Vendor tag (optional, default OPENAI) | `ANTHROPIC` |

### Verified compatible endpoints

| Vendor | base_url | Default model |
|--------|----------|---------------|
| OpenAI | (default) | gpt-4o / gpt-5 family |
| Anthropic | `https://api.anthropic.com/v1` | claude-sonnet-4-6 |
| Gemini | via OpenAI-compatible gateway | gemini-flash-latest |
| DeepSeek | `https://api.deepseek.com` | deepseek-chat |
| Self-hosted vLLM | `http://your-host:8000/v1` | depends on your deployment |

## OS support matrix

| OS | Core runtime | Shell sandbox | MCP | Notes |
|----|-------------|---------------|-----|-------|
| macOS 13+ | ✅ Full | ✅ `sandbox-exec` | ✅ | Recommended for dev |
| Linux | ✅ Full | ❌ No sandbox (recommend container / user namespace) | ✅ | Preferred for production |
| Windows | ⚠️ Basic | ❌ No sandbox | ⚠️ Some MCP servers are Unix-only | Prefer WSL2 |

> The shell sandbox is an **optional extra layer** (Part 6.2). Without it, `run_shell_command` is still gated by the **permission engine** and **tool confirmation**.

## Network

- **Outbound**: LLM calls hit your configured `base_url`; some tools (`web_fetch`, `google_web_search`) hit the public internet
- **Inbound**: **none**. Agentao does not listen on a port; ACP mode uses stdio
- **MCP SSE servers**: if you use SSE-based MCP servers, the host must reach their URLs

## Disk layout

When embedded, Agentao reads/writes these paths:

| Path | Purpose | Can disable? |
|------|---------|--------------|
| `<working_directory>/.agentao/` | Project-level config (MCP, permissions, memory) | Constrained by permission rules |
| `<working_directory>/AGENTAO.md` | Project instructions (you write this) | Skip the file and nothing loads |
| `<working_directory>/agentao.log` | Runtime log | Disable via custom logger |
| `~/.agentao/` | User-level config, memory | Relocate or skip |

In production, set `<working_directory>` to a **per-tenant / per-session temp dir** so sessions stay isolated (Part 7.1 covers this).

## Optional dependencies

Advanced tools are extras:

```bash
pip install 'agentao[pdf]'       # PDF reading
pip install 'agentao[excel]'     # Excel read/write
pip install 'agentao[image]'     # Image processing
pip install 'agentao[tokenizer]' # Precise token accounting
pip install 'agentao[full]'      # Everything
```

Install only what you need to keep the dependency surface minimal.

## Version compatibility

- Agentao is currently in **0.x (Beta)**. Breaking changes can land between minor versions — pin exact versions:
  ```
  agentao==0.2.10     # or >=0.2.10,<0.3
  ```
- This guide targets **v0.2.10 GA**. Version-specific notes will be flagged inline.

## Checklist

```bash
# All of these should succeed
python --version                        # >= 3.10
pip show agentao | grep Version          # your pinned version
echo $OPENAI_API_KEY | head -c 10       # key prefix visible
agentao --help                          # CLI reachable (optional)
python -c "from agentao import Agentao; print('OK')"
python -c "from agentao.transport import SdkTransport; print('OK')"
```

Environment green — move on to Part 2 for actual integration work.

→ [Part 2 · Python In-Process Embedding](/en/part-2/)
