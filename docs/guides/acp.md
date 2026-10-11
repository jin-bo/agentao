# Agent Client Protocol (ACP) Support

This guide moved. Protocol behaviour is in the developer guide: **[3.2 · Agentao as an ACP Server](https://agentao.cn/en/part-3/2-agentao-as-server)** ([中文](https://agentao.cn/zh/part-3/2-agentao-as-server)), with field-level tables in [Appendix C](https://agentao.cn/en/appendix/c-acp-messages) ([中文](https://agentao.cn/zh/appendix/c-acp-messages)). Server internals for contributors are in [docs/design/acp-server-internals.md](../design/acp-server-internals.md). The headings below are kept so that old links still land on the right section.

## Quick Start

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#launch-command) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%90%AF%E5%8A%A8%E5%91%BD%E4%BB%A4)

### Launch

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#launch-command) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%90%AF%E5%8A%A8%E5%91%BD%E4%BB%A4)

### Resume a session on startup

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#resume-a-session-on-startup) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%90%AF%E5%8A%A8%E6%97%B6%E6%81%A2%E5%A4%8D%E4%BC%9A%E8%AF%9D)

### Smoke test by hand

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#smoke-test-by-hand) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E6%89%8B%E5%8A%A8%E5%86%92%E7%83%9F%E6%B5%8B%E8%AF%95)

### From a real ACP client

Moved to [English](https://agentao.cn/en/part-3/5-zed-ide-integration#_3-5-2-registering-agentao-with-zed) · [中文](https://agentao.cn/zh/part-3/5-zed-ide-integration#_3-5-2-%E5%9C%A8-zed-%E9%87%8C%E6%B3%A8%E5%86%8C-agentao)

### Provider configuration and login (ACP Registry installs)

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#provider-configuration-and-login) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E6%8F%90%E4%BE%9B%E6%96%B9%E9%85%8D%E7%BD%AE%E4%B8%8E%E7%99%BB%E5%BD%95)

## Supported Methods

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#full-method-catalog) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%AE%8C%E6%95%B4%E6%96%B9%E6%B3%95%E6%B8%85%E5%8D%95)

### Client → server (requests Agentao handles)

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#host-%E2%86%92-agent-request-methods) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%AE%BF%E4%B8%BB-%E2%86%92-agent-%E8%AF%B7%E6%B1%82%E6%96%B9%E6%B3%95)

### Server → client (sent by Agentao)

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#agent-%E2%86%92-host-1-request-1-notification-1-extension) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#agent-%E2%86%92-%E5%AE%BF%E4%B8%BB-1-%E4%B8%AA%E8%AF%B7%E6%B1%82%E3%80%811-%E4%B8%AA%E9%80%9A%E7%9F%A5%E3%80%811-%E4%B8%AA%E6%89%A9%E5%B1%95)

### Capabilities Advertised in `initialize`

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#handshake-initialize) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E6%8F%A1%E6%89%8B-initialize)

## Scope and Limitations

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#client-capabilities-agentao-does-not-use) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#agentao-%E4%B8%8D%E4%BD%BF%E7%94%A8%E7%9A%84%E5%AE%A2%E6%88%B7%E7%AB%AF%E8%83%BD%E5%8A%9B)

### Transport

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#launch-command) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%90%AF%E5%8A%A8%E5%91%BD%E4%BB%A4)

### `session/prompt` content blocks

Moved to [English](https://agentao.cn/en/appendix/c-acp-messages#contentblock-shape) · [中文](https://agentao.cn/zh/appendix/c-acp-messages#contentblock-%E5%BD%A2%E7%8A%B6)

### Client-host capability routing

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#client-capabilities-agentao-does-not-use) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#agentao-%E4%B8%8D%E4%BD%BF%E7%94%A8%E7%9A%84%E5%AE%A2%E6%88%B7%E7%AB%AF%E8%83%BD%E5%8A%9B)

### MCP server injection (`session/new` → `mcpServers`)

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#mcp-servers-from-the-client) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%AE%A2%E6%88%B7%E7%AB%AF%E6%B3%A8%E5%85%A5%E7%9A%84-mcp-%E6%9C%8D%E5%8A%A1%E5%99%A8)

### Session management

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#sessions-and-concurrency) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E4%BC%9A%E8%AF%9D%E4%B8%8E%E5%B9%B6%E5%8F%91)

### `session/prompt` stop reasons

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#stop-reasons) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%81%9C%E6%AD%A2%E5%8E%9F%E5%9B%A0)

#### A failed model call is an error, not a stop reason

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#a-failed-model-call-is-an-error) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E6%A8%A1%E5%9E%8B%E8%B0%83%E7%94%A8%E5%A4%B1%E8%B4%A5%E8%BF%94%E5%9B%9E%E9%94%99%E8%AF%AF)

## Architecture Overview

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.md#process-layout) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.zh.md#%E8%BF%9B%E7%A8%8B%E7%BB%93%E6%9E%84)

### Stdout hygiene

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.md#stdout-hygiene) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.zh.md#stdout-%E5%8D%AB%E7%94%9F)

### Concurrent dispatch

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.md#concurrent-dispatch) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.zh.md#%E5%B9%B6%E5%8F%91%E5%88%86%E5%8F%91)

### File layout

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.md#file-layout) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.zh.md#%E6%96%87%E4%BB%B6%E5%B8%83%E5%B1%80)

## Annotated NDJSON Transcript

Moved to [English](https://agentao.cn/en/part-3/5-zed-ide-integration#_3-5-3-end-to-end-wire-trace) · [中文](https://agentao.cn/zh/part-3/5-zed-ide-integration#_3-5-3-%E7%AB%AF%E5%88%B0%E7%AB%AF%E7%BA%BF%E5%8D%8F%E8%AE%AE%E8%BD%A8%E8%BF%B9)

## Event Mapping

Moved to [English](https://agentao.cn/en/appendix/c-acp-messages#event-mapping) · [中文](https://agentao.cn/zh/appendix/c-acp-messages#%E4%BA%8B%E4%BB%B6%E6%98%A0%E5%B0%84)

#### A file edit arrives as a diff

Moved to [English](https://agentao.cn/en/appendix/c-acp-messages#a-file-edit-arrives-as-a-diff) · [中文](https://agentao.cn/zh/appendix/c-acp-messages#%E6%96%87%E4%BB%B6%E7%BC%96%E8%BE%91%E4%BB%A5-diff-%E9%80%81%E8%BE%BE)

#### Streamed tool output

Moved to [English](https://agentao.cn/en/appendix/c-acp-messages#streamed-tool-output) · [中文](https://agentao.cn/zh/appendix/c-acp-messages#%E6%B5%81%E5%BC%8F%E5%B7%A5%E5%85%B7%E8%BE%93%E5%87%BA)

### History replay (`session/load`)

Moved to [English](https://agentao.cn/en/appendix/c-acp-messages#history-replay) · [中文](https://agentao.cn/zh/appendix/c-acp-messages#%E5%8E%86%E5%8F%B2%E5%9B%9E%E6%94%BE)

## Permissions

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#tool-confirmation-session-request-permission-request) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%B7%A5%E5%85%B7%E7%A1%AE%E8%AE%A4-session-request-permission-%E8%AF%B7%E6%B1%82)

### Failure modes (all resolve to "tool rejected")

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#failures-that-reject-the-tool) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E4%BC%9A%E8%A2%AB%E8%A7%86%E4%B8%BA%E6%8B%92%E7%BB%9D%E7%9A%84%E5%A4%B1%E8%B4%A5)

## Cancellation

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#cancellation-session-cancel) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E5%8F%96%E6%B6%88-session-cancel)

## Troubleshooting

Moved to [English](https://agentao.cn/en/part-3/2-agentao-as-server#troubleshooting) · [中文](https://agentao.cn/zh/part-3/2-agentao-as-server#%E6%95%85%E9%9A%9C%E6%8E%92%E6%9F%A5)

## For Contributors

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.md#tests) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.zh.md#%E6%B5%8B%E8%AF%95)

### Tests

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.md#tests) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.zh.md#%E6%B5%8B%E8%AF%95)

### Implementation issue specs

Removed: the list of implementation issue specs was outdated. The specs are kept under `docs/history/implementation/acp-issues/`.

### Adding a new method handler

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.md#adding-a-method-handler) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/design/acp-server-internals.zh.md#%E6%96%B0%E5%A2%9E%E6%96%B9%E6%B3%95-handler)
