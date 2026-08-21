# Codex answer-only 构建

这套构建用于单轮批量问答。它基于官方 Codex `rust-v0.144.6`，固定提交
`5d1fbf26c43abc65a203928b2e31561cb039e06d`。

补丁只增加一个默认关闭的开关：

```toml
features.answer_only = true
```

开关在请求构造边界完成两件事：

1. 只保留当前用户消息。
2. 清空模型可见工具。

当工具为空时，请求使用 `tool_choice: "none"` 和
`parallel_tool_calls: false`。Responses Lite 不再插入 `AdditionalTools` 消息。
批量问答所需的开发者指令和 JSON 输出 Schema 会保留；项目规则、环境说明、协作说明、
历史消息和工具 Schema 不会进入模型输入。

这不会修改登录、鉴权、模型路由、配额或服务端行为。该模式只适合无历史依赖的单轮
问答，不能作为普通 Codex 编码模式使用。

## 构建

Windows 需要 Git、Rust stable MSVC 工具链，以及 Visual Studio 的
“Desktop development with C++”工作负载。

```powershell
.\experiments\codex-no-tools\build.ps1
```

macOS 需要 Git、Python 3、Rust，以及 Xcode Command Line Tools。构建脚本会自动识别
Apple Silicon 或 Intel，并生成对应架构的原生执行器：

```bash
bash experiments/codex-no-tools/build-macos.sh
```

Linux 需要 Git、Python 3、Rust、C/C++ 构建工具、`pkg-config`、OpenSSL 开发包和
`libcap` 开发包。脚本支持 x86_64 和 aarch64，并在当前机器上原生构建：

```bash
bash experiments/codex-no-tools/build-linux.sh
```

构建脚本会：

1. 检出并核对固定的 Codex 源码提交。
2. 应用补丁并运行 `codex-features` 测试。
3. 只构建非交互的 `codex-exec`（Windows 上为 `codex-exec.exe`）。
4. 使用本地无鉴权端点检查最终请求体。

验证器要求请求中没有工具、没有 `AdditionalTools`、没有非消息上下文，并且只包含指定的
开发者消息和当前用户消息。它不会连接任何外部 API，也不会读取 Codex 登录凭据。

构建产物位于：

```text
.tools\codex-no-tools\codex-exec.exe
.tools\codex-no-tools\build-info.json
.tools/codex-no-tools-linux/codex-exec
.tools/codex-no-tools-linux/build-info.json
.tools/codex-no-tools-macos/codex-exec
.tools/codex-no-tools-macos/build-info.json
```

无法在 Windows 上完成可验证的 macOS 原生链接。仓库中的 `Build macOS bundles`
GitHub Actions 工作流会分别在 Apple Silicon 和 Intel macOS 运行器上构建、测试、验证并
打包。首批产物不包含 Apple Developer 签名或公证。

## 使用

```powershell
codex-batch run questions.jsonl `
  --db answers.db `
  --output answers.jsonl `
  --codex-command .\.tools\codex-no-tools\codex-exec.exe `
  --model gpt-5.6-sol `
  --reasoning-effort medium `
  --batch-size 10 `
  --workers 2
```

`codex-batch` 固定启用 `answer_only`，调用者不需要再维护一长串功能开关。

## 独立检查

构建后可以随时重新检查请求形状：

```powershell
python .\experiments\codex-no-tools\verify_request.py `
  --codex-command .\.tools\codex-no-tools\codex-exec.exe `
  --model gpt-5.6-sol
```

测量原版与 answer-only 版本的固定输入开销：

```powershell
.\experiments\codex-no-tools\measure.ps1
```

源码构建缓存较大。确认二进制和 `build-info.json` 已复制后，可以清理 Codex 源码目录内的
Cargo 缓存；不会影响已经复制出的执行器。
