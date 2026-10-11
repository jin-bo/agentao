# Embedding Agentao

This guide moved into the developer guide: **[Part 2 · Embedding Agentao](https://agentao.cn/en/part-2/)** ([中文](https://agentao.cn/zh/part-2/)), chiefly [2.2 · Constructor Reference](https://agentao.cn/en/part-2/2-constructor-reference) and [4.7 · Host Contract](https://agentao.cn/en/part-4/7-host-contract). The 0.2.15 → 0.3.3 upgrade notes are in [docs/migration/0.2.15-to-0.3.3.md](../migration/0.2.15-to-0.3.3.md). Coding agents embedding Agentao into another project start with [embed-for-agents.md](embed-for-agents.md). The headings below are kept so that old links still land on the right section.

## TL;DR

Moved to [English](https://agentao.cn/en/part-2/2-constructor-reference#two-construction-paths) · [中文](https://agentao.cn/zh/part-2/2-constructor-reference#%E4%B8%A4%E7%A7%8D%E6%9E%84%E9%80%A0%E8%B7%AF%E5%BE%84)

## When to use which form

Moved to [English](https://agentao.cn/en/part-2/2-constructor-reference#two-construction-paths) · [中文](https://agentao.cn/zh/part-2/2-constructor-reference#%E4%B8%A4%E7%A7%8D%E6%9E%84%E9%80%A0%E8%B7%AF%E5%BE%84)

## 1. Minimal embedded construction (factory path)

Moved to [English](https://agentao.cn/en/part-2/2-constructor-reference#factory-path-build-from-environment) · [中文](https://agentao.cn/zh/part-2/2-constructor-reference#%E5%B7%A5%E5%8E%82%E8%B7%AF%E5%BE%84-build-from-environment)

## 2. Pure-injection construction

Moved to [English](https://agentao.cn/en/part-2/2-constructor-reference#tier-1-%C2%B7-the-smallest-call-that-works) · [中文](https://agentao.cn/zh/part-2/2-constructor-reference#%E7%AC%AC-1-%E6%A1%A3-%C2%B7-%E8%83%BD%E8%B7%91%E8%B5%B7%E6%9D%A5%E7%9A%84%E6%9C%80%E5%B0%8F%E8%B0%83%E7%94%A8)

### What you have to pass explicitly

Moved to [English](https://agentao.cn/en/part-2/2-constructor-reference#tier-2-%C2%B7-common-production-params) · [中文](https://agentao.cn/zh/part-2/2-constructor-reference#%E7%AC%AC-2-%E6%A1%A3-%C2%B7-%E7%94%9F%E4%BA%A7%E5%B8%B8%E7%94%A8)

### Permissions and the transport

Moved to [English](https://agentao.cn/en/part-5/4-permissions#permissionmode-four-presets) · [中文](https://agentao.cn/zh/part-5/4-permissions#permissionmode-%E5%9B%9B%E7%A7%8D%E9%A2%84%E8%AE%BE)

### Optional: provider-specific request params (`extra_body`)

Moved to [English](https://agentao.cn/en/part-2/2-constructor-reference#tier-3-%C2%B7-advanced-injections) · [中文](https://agentao.cn/zh/part-2/2-constructor-reference#%E7%AC%AC-3-%E6%A1%A3-%C2%B7-%E9%AB%98%E7%BA%A7%E6%B3%A8%E5%85%A5)

### Optional: silencing or redirecting `agentao.log`

Moved to [English](https://agentao.cn/en/part-6/6-observability#take-over-agentao-s-logger) · [中文](https://agentao.cn/zh/part-6/6-observability#%E6%8E%A5%E7%AE%A1-agentao-%E7%9A%84-logger)

## 3. Capability injection

Moved to [English](https://agentao.cn/en/part-2/2-constructor-reference#tier-3-%C2%B7-advanced-injections) · [中文](https://agentao.cn/zh/part-2/2-constructor-reference#%E7%AC%AC-3-%E6%A1%A3-%C2%B7-%E9%AB%98%E7%BA%A7%E6%B3%A8%E5%85%A5)

## 4. Async usage

Moved to [English](https://agentao.cn/en/part-2/3-lifecycle#async-hosts-arun-and-the-turn-pool) · [中文](https://agentao.cn/zh/part-2/3-lifecycle#%E5%BC%82%E6%AD%A5%E5%AE%BF%E4%B8%BB-arun-%E4%B8%8E%E8%BD%AE%E6%AC%A1%E7%BA%BF%E7%A8%8B%E6%B1%A0)

### Streaming text (`astream()`)

Moved to [English](https://agentao.cn/en/part-4/7-host-contract#streaming-text-agent-astream) · [中文](https://agentao.cn/zh/part-4/7-host-contract#streaming-text-agent-astream)

### Multimodal input (`images=`)

Moved to [English](https://agentao.cn/en/appendix/a-api-reference#image-input-and-vision-degradation) · [中文](https://agentao.cn/zh/appendix/a-api-reference#%E5%9B%BE%E7%89%87%E8%BE%93%E5%85%A5%E4%B8%8E%E8%A7%86%E8%A7%89%E9%80%80%E5%8C%96)

## 5. Replay, Sandbox, BgStore (opt-in subsystems)

Moved to [English](https://agentao.cn/en/part-2/2-constructor-reference#tier-3-%C2%B7-advanced-injections) · [中文](https://agentao.cn/zh/part-2/2-constructor-reference#%E7%AC%AC-3-%E6%A1%A3-%C2%B7-%E9%AB%98%E7%BA%A7%E6%B3%A8%E5%85%A5)

## 6. Built-in sub-agents

Moved to [English](https://agentao.cn/en/part-2/2-constructor-reference#tier-3-%C2%B7-advanced-injections) · [中文](https://agentao.cn/zh/part-2/2-constructor-reference#%E7%AC%AC-3-%E6%A1%A3-%C2%B7-%E9%AB%98%E7%BA%A7%E6%B3%A8%E5%85%A5)

## 7. Host-facing harness contract

Moved to [English](https://agentao.cn/en/part-4/7-host-contract#which-names-you-need) · [中文](https://agentao.cn/zh/part-4/7-host-contract#%E4%BD%A0%E9%9C%80%E8%A6%81%E5%93%AA%E4%BA%9B%E5%90%8D%E5%AD%97)

### `agent.active_permissions()`

Moved to [English](https://agentao.cn/en/part-4/7-host-contract#_4-7-6-agent-active-permissions-%E2%80%94-policy-snapshots) · [中文](https://agentao.cn/zh/part-4/7-host-contract#_4-7-6-agent-active-permissions-%E2%80%94%E2%80%94-%E7%AD%96%E7%95%A5%E5%BF%AB%E7%85%A7)

### `agent.events(session_id=None)` — async iterator

Moved to [English](https://agentao.cn/en/part-4/7-host-contract#_4-7-5-end-to-end-tenant-scoped-audit-pipeline) · [中文](https://agentao.cn/zh/part-4/7-host-contract#_4-7-5-%E7%AB%AF%E5%88%B0%E7%AB%AF-%E7%A7%9F%E6%88%B7%E7%BA%A7%E5%AE%A1%E8%AE%A1%E6%B5%81%E6%B0%B4%E7%BA%BF)

### What is *not* on the harness surface

Moved to [English](https://agentao.cn/en/part-4/7-host-contract#_4-7-8-what-s-not-in-the-contract) · [中文](https://agentao.cn/zh/part-4/7-host-contract#_4-7-8-%E4%B8%8D%E5%9C%A8%E5%90%88%E7%BA%A6%E9%87%8C%E7%9A%84%E4%B8%9C%E8%A5%BF)

### Schema snapshots

Moved to [English](https://agentao.cn/en/part-4/7-host-contract#_4-7-7-forward-compatibility-guarantees) · [中文](https://agentao.cn/zh/part-4/7-host-contract#_4-7-7-%E5%89%8D%E5%90%91%E5%85%BC%E5%AE%B9%E6%89%BF%E8%AF%BA)

## 8. Migration guide: 0.2.15 → 0.2.16 → 0.3.0 → 0.3.1 → 0.3.3

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md)

### From 0.2.15

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#from-0215) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#from-0215)

### From 0.2.16

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#from-0216) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#from-0216)

### Other 0.3.0 BREAKING signals to watch for

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#other-030-breaking-changes) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#other-030-breaking-changes)

### From 0.3.0

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#from-030) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#from-030)

### From 0.3.1

Moved to [English](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#from-031) · [中文](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.2.15-to-0.3.3.md#from-031)

## Reference

Removed: each row now appears as a source link in the developer-guide pages above.
