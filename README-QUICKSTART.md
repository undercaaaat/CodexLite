# CodexLite Quick Start

CodexLite runs independent questions through a patched Codex CLI with no
model-visible tool schema or host context. The normal workflow is:

1. Inspect the question-bank format.
2. Extract questions into CodexLite JSONL.
3. Validate the input, then run the answers.

This guide uses Windows PowerShell and a JSONL source file as an example.

## Requirements

- Windows x64
- Python 3.11 or newer
- `codex-batch` installed
- The patched `codex-exec` binary
- A Codex account with access to the selected model

For a release bundle, create an isolated environment and install the included
Python wheel:

```powershell
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install ".\packages\codex_batch-0.5.1-py3-none-any.whl"
```

Then check the installation and sign in with your own account:

```powershell
codex-batch --version
codex login status
```

Never distribute API keys, Codex login files, private question banks, or
generated answer databases with the application.

## 1. Inspect the question bank

CodexLite accepts UTF-8 JSONL with one object per line. Every object must have
a unique, non-empty `id` and a non-empty `question`:

```json
{"id":"question-001","question":"What is gradient descent?"}
```

Before converting a source file, inspect a few records without modifying it:

```powershell
$source_path = "C:\path\to\source-questions.jsonl"

$sample = @(
    Get-Content -LiteralPath $source_path -Encoding UTF8 -TotalCount 3 |
        ForEach-Object { $_ | ConvertFrom-Json }
)

$sample | Format-List *
```

Identify:

- the field containing the stable question ID;
- the field containing the complete question text;
- whether either field is empty or duplicated.

If the source already uses `id` and `question`, no field conversion is needed.
CSV, Excel, SQLite, and other formats must first be exported or converted to
the same JSONL contract.

## 2. Extract a small pilot set

Do not edit the original question bank. Create a separate working file first.
The example below takes the first 10 records from a source whose fields are
named `prompt_id` and `text`. Change only those two field names when your source
uses different names.

```powershell
$source_path = "C:\path\to\source-questions.jsonl"
$work_dir = "C:\path\to\codexlite-run"
$input_path = Join-Path $work_dir "questions.jsonl"

New-Item -ItemType Directory -Force -Path $work_dir | Out-Null

if (Test-Path -LiteralPath $input_path) {
    throw "The output file already exists: $input_path"
}

$questions = @(
    Get-Content -LiteralPath $source_path -Encoding UTF8 -TotalCount 10 |
        ForEach-Object {
            $record = $_ | ConvertFrom-Json
            [PSCustomObject][ordered]@{
                id = [string]$record.prompt_id
                question = [string]$record.text
            }
        }
)

if ($questions.Count -ne 10) {
    throw "Expected 10 questions, found $($questions.Count)"
}

if (@($questions.id | Sort-Object -Unique).Count -ne $questions.Count) {
    throw "Duplicate question IDs found"
}

if ($questions | Where-Object {
    [string]::IsNullOrWhiteSpace($_.id) -or
    [string]::IsNullOrWhiteSpace($_.question)
}) {
    throw "An empty ID or question was found"
}

$json_lines = @(
    $questions | ForEach-Object { $_ | ConvertTo-Json -Compress }
)

[System.IO.File]::WriteAllLines(
    $input_path,
    $json_lines,
    [System.Text.UTF8Encoding]::new($false)
)

Get-Content -LiteralPath $input_path -Encoding UTF8 |
    ForEach-Object { $_ | ConvertFrom-Json } |
    Select-Object id, @{Name="QuestionLength"; Expression={$_.question.Length}} |
    Format-Table -AutoSize
```

The table should contain 10 unique IDs and non-zero question lengths. Remove
`-TotalCount 10` only after the pilot run has been reviewed.

## 3. Validate without calling the model

A dry run checks the JSONL file and creates the resumable database without
calling Codex:

```powershell
codex-batch run "C:\path\to\codexlite-run\questions.jsonl" `
    --db "C:\path\to\codexlite-run\answers.db" `
    --output "C:\path\to\codexlite-run\answers.jsonl" `
    --dry-run
```

Confirm that `Total` is the expected number and `Unfinished` is `10`.

## 4. Start answering

Set the executable path for your installation. A release bundle uses
`bin\codex-exec.exe`; a source build uses `.tools\codex-no-tools\codex-exec.exe`.

Start conservatively with one question per call and one worker:

```powershell
$codex_command = (Resolve-Path ".\bin\codex-exec.exe").Path

codex-batch run "C:\path\to\codexlite-run\questions.jsonl" `
    --db "C:\path\to\codexlite-run\answers.db" `
    --output "C:\path\to\codexlite-run\answers.jsonl" `
    --codex-command $codex_command `
    --model "gpt-5.6-sol" `
    --reasoning-effort medium `
    --batch-size 1 `
    --workers 1 `
    --retries 2 `
    --timeout 900 `
    --fallback-single
```

Use a model available to your account. For code, mathematics, and independent
evaluation, `--batch-size 1` avoids mixing multiple questions in one model
turn. Test higher concurrency only after the pilot succeeds.

## Results and resume

Check progress:

```powershell
codex-batch status --db "C:\path\to\codexlite-run\answers.db"
```

Answers are written to `answers.jsonl`. Attempt history and checkpoint state
remain in `answers.db`.

If a run stops, execute the same live command again. Completed questions are
not called again. Keep the database, input file, model, reasoning effort, and
batch size unchanged while resuming. Use a new working directory and database
when any of those settings change.

## Appendix: Start answering parameters

The `codex-batch run` command accepts the following arguments:

| Argument | Required | Default | Description |
|---|---:|---:|---|
| `INPUT` | Yes | None | Path to the UTF-8 JSONL file containing `id` and `question`. This is the positional argument after `run`. |
| `--db PATH` | Yes | None | SQLite checkpoint database. Keep this file to resume an interrupted run. |
| `--output PATH` | Yes | None | JSONL file receiving the latest exported answers and statuses. |
| `--codex-command PATH` | Live runs | None | Path to the patched `codex-exec` binary. The unmodified Codex executable is rejected. |
| `--model MODEL` | No | Codex default | Model available to the signed-in account, for example `gpt-5.6-sol`. |
| `--reasoning-effort LEVEL` | No | Codex default | Reasoning level: `low`, `medium`, `high`, or `xhigh`. |
| `--batch-size N` | No | `20` | Number of questions sent in one Codex turn. Use `1` for independent code, mathematics, or evaluation tasks. |
| `--workers N` | No | `2` | Maximum number of concurrent Codex processes. Start with `1` when testing a new account or dataset. |
| `--retries N` | No | `2` | Retry attempts after the first failed call. `0` disables retries. |
| `--timeout SECONDS` | No | `600` | Maximum time allowed for each Codex call. Must be greater than zero. |
| `--retry-delay SECONDS` | No | `10` | Initial delay before retrying a failed call. Must be zero or greater. |
| `--fallback-single` | No | Enabled | After a multi-question batch fails, retry its questions one at a time. |
| `--no-fallback-single` | No | Disabled | Disable the single-question fallback behavior. |
| `--dry-run` | No | Disabled | Validate and checkpoint the input without calling Codex. `--codex-command` is not required in this mode. |

Use the same input file, database, model, reasoning effort, batch size, and
fallback setting when resuming a run. Use a new database when changing the
execution contract.
