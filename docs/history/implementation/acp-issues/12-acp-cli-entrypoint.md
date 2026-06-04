# Add ACP CLI Entrypoint And Runtime Wiring For Stdio Mode

## Problem

There is no supported way to launch Agentao as an ACP server from the command line.

## Scope

- Add `--acp` and `--stdio` startup path
- Bypass interactive terminal UI in ACP mode

## Implementation Checklist

- [ ] Add ACP launch mode to CLI or `main.py`
- [ ] Start stdio JSON-RPC server instead of interactive CLI
- [ ] Route logs to stderr or file only
- [ ] Ensure clean process shutdown and resource cleanup

## Acceptance Criteria

- [ ] `agentao --acp --stdio` starts a valid ACP server
- [ ] Stdout contains only ACP protocol messages
- [ ] Shutdown cleans up MCP connections and session runtimes

## Dependencies

- Depends on: `01-acp-module-skeleton-and-jsonrpc-server.md`
