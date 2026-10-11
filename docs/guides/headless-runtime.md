# Headless Runtime

This guide moved into the developer guide: the headless contract in **[3.4 · Reverse: Calling External ACP Agents](https://agentao.cn/en/part-3/4-reverse-acp-call#_3-4-11-headless-contract)** ([中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#_3-4-11-headless-%E5%90%88%E7%BA%A6)), with error codes in Appendix D and migrations in Appendix E. The source is `developer-guide/en/part-3/4-reverse-acp-call.md` and its `zh` twin. The headings below are kept so that old links still land on the right section.

## 1. Public runtime surface

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#entry-points-and-support-levels) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E5%85%A5%E5%8F%A3%E4%B8%8E%E6%94%AF%E6%8C%81%E7%BA%A7%E5%88%AB)

## 2. Concurrency contract

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#one-active-turn-per-server) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E6%AF%8F%E4%B8%AA-server-%E5%8F%AA%E6%9C%89%E4%B8%80%E4%B8%AA%E6%B4%BB%E8%B7%83-turn)

## 3. Status snapshot (v1 + v2)

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#status-fields) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E7%8A%B6%E6%80%81%E5%AD%97%E6%AE%B5)

### 3.1 Week 1 field semantics (frozen)

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#status-fields) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E7%8A%B6%E6%80%81%E5%AD%97%E6%AE%B5)

### 3.2 Week 2 field semantics

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#status-fields) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E7%8A%B6%E6%80%81%E5%AD%97%E6%AE%B5)

### 3.3 Readiness classifier (v2)

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#readiness-classes) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E5%B0%B1%E7%BB%AA%E5%88%86%E7%BA%A7)

### 3.4 Migration from the pre-Week-1 dict shape

Moved to [English](https://agentao.cn/en/appendix/e-migration#e-11-acp-client-%E2%80%94-get-status-returns-serverstatus) · [中文](https://agentao.cn/zh/appendix/e-migration#e-11-acp-%E5%AE%A2%E6%88%B7%E7%AB%AF%E2%80%94%E2%80%94get-status-%E8%BF%94%E5%9B%9E-serverstatus)

## 4. State-vs-error contract (v2)

Moved to [English](https://agentao.cn/en/appendix/d-error-codes#d-5-state-vs-error-contract-headless) · [中文](https://agentao.cn/zh/appendix/d-error-codes#d-5-%E7%8A%B6%E6%80%81%E4%B8%8E%E9%94%99%E8%AF%AF%E5%90%88%E7%BA%A6-headless)

### 4.1 What does **not** go into `last_error`

Moved to [English](https://agentao.cn/en/appendix/d-error-codes#d-5-state-vs-error-contract-headless) · [中文](https://agentao.cn/zh/appendix/d-error-codes#d-5-%E7%8A%B6%E6%80%81%E4%B8%8E%E9%94%99%E8%AF%AF%E5%90%88%E7%BA%A6-headless)

## 5. Error classification

Moved to [English](https://agentao.cn/en/appendix/d-error-codes#d-1-code-table) · [中文](https://agentao.cn/zh/appendix/d-error-codes#d-1-%E9%94%99%E8%AF%AF%E7%A0%81%E8%A1%A8)

## 6. Interaction policy (Week 3)

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#non-interactive-turns) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E9%9D%9E%E4%BA%A4%E4%BA%92-turn)

### 6.1 `InteractionPolicy`

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#non-interactive-turns) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E9%9D%9E%E4%BA%A4%E4%BA%92-turn)

### 6.2 Precedence

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#per-call-policy-override) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E5%8D%95%E6%AC%A1%E8%B0%83%E7%94%A8%E7%AD%96%E7%95%A5%E8%A6%86%E7%9B%96)

### 6.3 Signatures

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#per-call-policy-override) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E5%8D%95%E6%AC%A1%E8%B0%83%E7%94%A8%E7%AD%96%E7%95%A5%E8%A6%86%E7%9B%96)

### 6.4 Config shape (`.agentao/acp.json`)

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#_3-4-5-config-format-%E2%80%94-agentao-acp-json) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#_3-4-5-%E9%85%8D%E7%BD%AE%E6%A0%BC%E5%BC%8F-%E2%80%94-agentao-acp-json)

### 6.5 `config_warnings`

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#status-fields) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E7%8A%B6%E6%80%81%E5%AD%97%E6%AE%B5)

## 7. Lifecycle & recovery (Week 4)

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#_3-4-7-lifecycle-recovery) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#_3-4-7-%E7%94%9F%E5%91%BD%E5%91%A8%E6%9C%9F%E4%B8%8E%E6%81%A2%E5%A4%8D)

### 7.1 Deterministic cleanup after cancel / timeout (Issue 15)

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#cleanup-order-after-a-cancel-or-timeout) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E5%8F%96%E6%B6%88%E6%88%96%E8%B6%85%E6%97%B6%E5%90%8E%E7%9A%84%E6%B8%85%E7%90%86%E9%A1%BA%E5%BA%8F)

### 7.2 Client / process death classification (Issue 16)

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#process-death-classification) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E8%BF%9B%E7%A8%8B%E6%AD%BB%E4%BA%A1%E5%88%86%E7%B1%BB)

### 7.3 Config

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#tuning-maxrecoverablerestarts) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#%E8%B0%83%E6%95%B4-maxrecoverablerestarts)

### 7.4 Regression suite

Removed: the test-suite pointer. The regression tests live in `tests/test_headless_runtime.py`.

## 8. What's **not** in scope

Moved to [English](https://agentao.cn/en/part-3/4-reverse-acp-call#_3-4-11-headless-contract) · [中文](https://agentao.cn/zh/part-3/4-reverse-acp-call#_3-4-11-headless-%E5%90%88%E7%BA%A6)
