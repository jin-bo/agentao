# 01 Config Models And Loader

Parent doc: [ACP Client And Project-Local Servers](../../ACP_CLIENT_PROJECT_SERVERS.md)

Issue index: [ACP Client Project-Local Servers Issues](README.md)

## Goal

定义 project-local ACP 配置模型和加载器，作为后续 runtime、CLI 和测试的统一入口。

## Scope

- `cwd/.agentao/acp.json` 定位规则
- 配置数据模型
- schema 校验
- 默认值填充
- server `cwd` 路径解析

## Deliverables

- `agentao/acp_client/models.py`
- `agentao/acp_client/config.py`
- 配置加载与校验单测

## Dependencies

- 无

## Design Notes

- v1 仅支持项目级配置，不向上查找父目录
- `servers` 顶层键为 server 名称到配置对象的映射
- 必填字段：
  - `command`
  - `args`
  - `env`
  - `cwd`
- 可选字段：
  - `autoStart`
  - `startupTimeoutMs`
  - `requestTimeoutMs`
  - `capabilities`
  - `description`
- 相对 `cwd` 相对于项目根目录解析为绝对路径
- `capabilities` 是展示和未来 routing hint 的元数据，不绑定自动行为

## Tests

- 配置文件不存在时返回空配置或稳定结果
- 非法 JSON 返回稳定错误
- 缺失必填字段时报错
- 相对 `cwd` 被正确解析为绝对路径
- 默认值填充正确

## Acceptance Criteria

1. 后续模块不直接读原始 JSON，而是依赖统一模型
2. 配置错误能稳定定位到具体 server 和字段
3. 项目根目录切换时解析结果正确

## Out Of Scope

- 启动子进程
- JSON-RPC 握手
- CLI 命令
