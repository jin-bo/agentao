# 08 Test Fixtures And Coverage

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

建立 ACP client / project-local servers 功能的测试夹具和回归覆盖。

## Scope

- 假 ACP server fixture
- 握手成功场景
- 非法 JSON 场景
- 崩溃场景
- 慢响应 / cancel 场景
- CLI 集成回归

## Deliverables

- 新测试夹具
- `tests/test_acp_client_*.py`
- `tests/test_cli_*.py` 中相关补充

## Dependencies

- 01
- 02
- 03
- 04
- 05
- 06
- 07

## Design Notes

- 尽量复用现有 ACP server 测试思路
- 夹具应覆盖：
  - 正常握手
  - 连续多条 `session/update`
  - 错误响应
  - stdout 非法 JSON
  - 进程退出
- 需要验证 CLI flush 不破坏主循环

## Tests

- 单测与集成测试见 scope

## Acceptance Criteria

1. 主流程有端到端回归保护
2. 失败路径有明确测试
3. 不影响现有 ACP server 测试集

## Out Of Scope

- 性能基准测试
- 大规模并发压力测试
