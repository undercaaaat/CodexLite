# Codex Batch

这是一个基于官方 `codex exec` 接口的批量问答器，适合把数千道相互独立的小题分批交给 Codex。它使用 SQLite 保存进度，支持并发、超时、重试、失败后逐题降级，以及中断后的断点续跑。

当前版本是 `0.5.0`。核心执行、恢复、审计和导出流程只使用 Python 标准库。

项目不会提取或重放 Codex 登录凭据，也不会调用未公开的内部接口。每轮使用临时会话、
空工作目录和只读沙箱。补丁版 Codex 在最终请求边界只保留当前用户消息，并清空模型
可见工具，因此项目规则、环境说明、协作说明、历史消息和工具 Schema 不会进入模型输入。

## 下载

普通使用者可以从 [GitHub Releases](https://github.com/undercaaaat/CodexLite/releases)
下载对应平台的便携包，不需要安装 Rust 或 Visual Studio：

- Windows x64：阅读 [Windows Quick Start](README-QUICKSTART.md)。
- macOS Apple Silicon/Intel：阅读 [macOS Quick Start](README-QUICKSTART-MACOS.md)。

每位使用者仍需安装官方 Codex CLI，并使用自己的账户登录。Release 不包含登录信息、
API Key、题库或生成答案。

## 环境准备

需要 Python 3.11+、已安装的 Codex CLI、可正常使用的 Codex 登录状态，以及本项目构建的
补丁版 `codex-exec`。正式问答不接受原版 Codex 包装器。Python 批处理器支持 Windows、
macOS 和 Linux；当前补丁构建脚本覆盖 Windows x64 以及 macOS Apple Silicon/Intel。

```powershell
codex --version
codex login status
python -m pip install -e .
codex-batch --version
.\experiments\codex-no-tools\build.ps1
```

macOS 在仓库根目录执行：

```bash
python3 -m pip install -e .
bash experiments/codex-no-tools/build-macos.sh
```

macOS 构建产物位于 `.tools/codex-no-tools-macos/codex-exec`。也可以在 GitHub
Actions 中手动运行 `Build macOS bundles`，分别生成 Apple Silicon 和 Intel 安装包。

安装不引入第三方 Python 依赖。

## 输入格式

输入是 UTF-8 JSONL，每行一道题。`id` 在整个文件中必须唯一，`question` 必须是非空字符串。

```jsonl
{"id":"1","question":"什么是梯度下降？"}
{"id":"2","question":"TCP 和 UDP 的主要区别是什么？"}
{"id":"3","question":"为什么 Transformer 需要位置编码？"}
```

仓库内附有可直接检查的样例：[examples/questions.jsonl](examples/questions.jsonl)。

## 运行

先做一次不调用 Codex 的输入检查：

```powershell
codex-batch run examples/questions.jsonl `
  --db answers.db `
  --output answers.jsonl `
  --dry-run
```

正式运行：

```powershell
codex-batch run questions.jsonl `
  --db answers.db `
  --output answers.jsonl `
  --codex-command .\.tools\codex-no-tools\codex-exec.exe `
  --batch-size 20 `
  --workers 2 `
  --retries 2 `
  --timeout 600 `
  --fallback-single
```

参数含义：

- `--batch-size 20`：每轮最多 20 题。
- `--workers 2`：最多同时运行 2 个 Codex 进程。
- `--retries 2`：首次失败后再重试 2 次。
- `--timeout 600`：单次 Codex 调用最多等待 600 秒。
- `--fallback-single`：整批连续失败后，把该批拆成单题再试。
- `--model MODEL`：可选；不设置时沿用 Codex 的可用默认模型。
- `--reasoning-effort low|medium|high|xhigh`：可选；小题通常从 `low` 开始。
- `--codex-command`：正式运行必填，必须指向补丁版 `codex-exec`。

正式运行固定开启一个 `features.answer_only` 开关，不能从命令行关闭。它同时清除工具
Schema 和宿主上下文。原版 `codex` 命令会在模型调用前被拒绝，不会悄悄退回高开销模式。

程序每次都会导出当前数据库状态。退出码 `0` 表示全部完成，`2` 表示仍有失败题，`130` 表示被中断。

## 断点续跑

直接重新执行同一条 `run` 命令即可。已经标记为 `done` 的题不会再次调用 Codex；`pending`、`running` 或 `failed` 的题会继续处理。

数据库会固定输入文件和执行合同的 SHA-256。题目顺序、题目文本、模型、推理强度、批量大小、结构化输出定义、提示词或 Codex CLI 版本发生变化时，程序会立即停止。要运行另一套配置，请使用新的 `--db` 路径。这样不会把不同实验条件下生成的答案悄悄混到一起。

查看进度或重新导出：

```powershell
codex-batch status --db answers.db
codex-batch export --db answers.db --output answers.jsonl
codex-batch report --db answers.db --output answers.report.json
```

输出每行包含 `id`、`question`、`answer`、`status`、`attempts` 和 `error`。写出过程使用临时文件替换，避免中断时留下半份 JSONL。首次正式运行还会在数据库旁生成 `*.manifest.json`，记录输入和运行合同、Python、SQLite 与 Codex CLI 版本。报告包含每次调用的状态、错误分类和延迟统计。

## 建议配置

短知识题可从 `batch-size=20`、`workers=2` 开始；题目变长或需要较强推理时，将批量降到 5 至 10。数学题、代码题或题目之间容易互相干扰时，使用 `batch-size=1`。并发不宜一开始就开得很高，实际吞吐仍受账户额度、模型和服务限流影响。

## 纯问答执行器

仓库提供了针对 Codex `rust-v0.144.6` 的最小源码补丁和构建脚本，见
[experiments/codex-no-tools/README.md](experiments/codex-no-tools/README.md)。补丁只有一个
`answer_only` 开关：在请求边界只保留当前用户消息并清空工具。批量问答器固定开启它，
并把运行模式和实际可执行文件的 SHA-256 写入运行合同。

这种模式尽量接近直接调用官方 Responses API：客户端请求中没有模型可见工具，也没有
Codex 宿主上下文。题目、批处理指令和结构化输出 Schema 仍然是任务本身所需的输入；
服务端协议元数据和基础行为不由本项目控制。

本机在 2026-08-20 使用 `gpt-5.6-sol`、`low` 推理强度和单字符提示词重新测量：

| 条件 | 输入 token | 相对原版减少 |
|---|---:|---:|
| 原版 Codex | 34,071 | - |
| answer-only Codex | 12 | 99.96% |

测量方法和结果说明见
[experiments/tool-schema-analysis.md](experiments/tool-schema-analysis.md)。这些数值会随
Codex 版本和服务端基础指令变化，正式实验应重新运行测量脚本。

源码编译目录可能占用二十多 GB。成功构建并确认 `codex-exec.exe` 与
`build-info.json` 已生成后，可以在 `.tools/codex-no-tools/source/codex-rs/` 中运行
`cargo clean` 回收编译缓存；这不会删除补丁源码或已经复制出的可执行文件。

## 项目结构

- `src/codex_batch/`：命令行、调度器、Codex 适配器和 SQLite 存储。
- `examples/`：最小 JSONL 输入样例。
- `experiments/codex-no-tools/`：无工具实验补丁、构建和测量脚本。
