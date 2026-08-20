# Codex answer-only 请求分析

## 结论

原版 Codex 即使在提示词中要求“不使用工具”，仍会把工具 Schema 和宿主消息发送给模型。
要真正移除这些内容，修改点必须位于最终请求构造边界。

本项目的补丁只增加一个 `features.answer_only` 开关。开启时：

- `input` 只保留当前用户消息；
- `tools` 为空；
- `tool_choice` 为 `none`；
- `parallel_tool_calls` 为 `false`；
- Responses Lite 不插入 `AdditionalTools` 消息。

开发者指令和 JSON 输出 Schema 会保留，因为它们分别定义任务行为和返回格式，不属于
工具 Schema 或 Codex 宿主上下文。

## 修改位置

补丁基于官方 Codex 标签 `rust-v0.144.6`，提交
`5d1fbf26c43abc65a203928b2e31561cb039e06d`，只修改三个文件：

1. `codex-rs/features/src/lib.rs`：注册 `answer_only`。
2. `codex-rs/core/src/session/turn.rs`：保留当前用户消息并清空工具。
3. `codex-rs/core/src/client.rs`：工具为空时使用 `tool_choice: none`，并省略
   `AdditionalTools`。

Python 批处理器只传入一个功能开关，不再维护一长串容易随 Codex 版本变化的禁用项。
它仍使用临时会话、空工作目录、只读沙箱、忽略用户配置和规则，并清理 Codex Desktop
的任务标识环境变量。

## 请求验证

`experiments/codex-no-tools/verify_request.py` 启动一个本地无鉴权 HTTP 端点，让补丁版
Codex 向它发送一次请求。验证器直接检查最终 JSON：

- 工具缺失或为空；
- `tool_choice` 和 `parallel_tool_calls` 正确；
- 没有 `AdditionalTools`；
- `input` 中没有非消息项；
- 只存在指定的开发者内容和当前用户内容。

验证过程不连接外部 API，不读取或记录登录凭据。2026-08-20 的重新构建已通过该检查。

## Token 测量

同日使用 `gpt-5.6-sol`、`low` 推理强度和单字符提示词测得：

| 条件 | 输入 tokens | 相对原版减少 |
|---|---:|---:|
| 原版 `codex-cli 0.144.6` | 34,071 | - |
| answer-only `codex-exec 0.144.6` | 12 | 99.96% |

answer-only 的 12 tokens 与单字符用户输入和单字符开发者指令相符。原版数值会随 Codex
功能、应用注入和服务端基础指令变化，因此重点不是 34,071 这个绝对值，而是补丁版请求
已由本地请求体检查确认只剩任务内容。

## 与官方 API 的差别

模型可见的主要输入已经接近无工具的 Responses API 调用：开发者指令、用户输入和可选的
结构化输出约束。Codex 仍会携带其协议所需的客户端元数据、流式设置、缓存键以及登录路径
使用的服务端字段。本项目不修改这些字段，也不绕过鉴权、配额、路由或服务端控制。

如果研究只需要直接评估模型能力，官方 Responses API 或 Batch API 仍是最容易说明的
基线。这个补丁适合必须经过 Codex CLI 登录路径，同时又要排除工具和宿主提示影响的实验。
