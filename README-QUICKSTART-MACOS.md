# CodexLite Quick Start for macOS

CodexLite runs independent questions through a patched Codex CLI with no
model-visible tool schema or host context. The workflow is:

1. Inspect the question-bank format.
2. Extract questions into CodexLite JSONL.
3. Validate the input, then start answering.

## Requirements

- macOS on Apple Silicon or Intel
- Python 3.11 or newer
- The Codex CLI, signed in with your own account
- A CodexLite bundle matching the Mac architecture

Check the machine architecture before downloading a bundle:

```bash
uname -m
```

Use the `arm64` bundle when the result is `arm64`. Use the `x86_64` bundle when
the result is `x86_64`.

## Install the bundle

Extract the archive, enter its directory, and install the Python package in a
virtual environment:

```bash
tar -xzf CodexLite-0.5.0-codex-0.144.6-macos-arm64.tar.gz
cd CodexLite-0.5.0-codex-0.144.6-macos-arm64

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install packages/codex_batch-0.5.0-py3-none-any.whl

chmod +x bin/codex-exec
codex-batch --version
bin/codex-exec --version
codex login status
```

Run `codex login` if the account is not signed in. CodexLite does not include
or copy login credentials.

Early research builds may be unsigned. If macOS blocks the executable, review
the downloaded source and release first, then approve the binary in **System
Settings > Privacy & Security**. Do not disable Gatekeeper globally.

## 1. Inspect the question bank

CodexLite accepts UTF-8 JSONL with one object per line. Every object must have
a unique, non-empty `id` and a non-empty `question`:

```json
{"id":"question-001","question":"What is gradient descent?"}
```

Inspect the fields and value types in the first three source records without
printing complete private questions:

```bash
source_path="/path/to/source-questions.jsonl"

python3 - "${source_path}" <<'PY'
import json
import sys

source_path = sys.argv[1]
with open(source_path, "r", encoding="utf-8") as input_file:
    for line_number in range(1, 4):
        line = input_file.readline()
        if not line:
            break
        record = json.loads(line)
        print(f"record {line_number}")
        for name, value in record.items():
            if isinstance(value, str):
                print(f"  {name}: string, {len(value)} characters")
            else:
                print(f"  {name}: {type(value).__name__}")
PY
```

Identify the stable question ID field and the complete question-text field. If
the source already uses `id` and `question`, no field conversion is needed.
CSV, Excel, SQLite, and other formats must first be exported or converted to
the same JSONL contract.

## 2. Extract a 10-question pilot

The example below reads `prompt_id` as the ID and `text` as the question. Edit
only `id_field` and `question_field` when the source uses different names. The
original question bank is never modified.

```bash
source_path="/path/to/source-questions.jsonl"
work_dir="${HOME}/codexlite-runs/pilot"
input_path="${work_dir}/questions.jsonl"

mkdir -p "${work_dir}"

python3 - "${source_path}" "${input_path}" <<'PY'
import json
import os
import sys

source_path = sys.argv[1]
output_path = sys.argv[2]
id_field = "prompt_id"
question_field = "text"

if os.path.exists(output_path):
    raise FileExistsError(f"output already exists: {output_path}")

questions = []
with open(source_path, "r", encoding="utf-8") as input_file:
    for line in input_file:
        if len(questions) == 10:
            break
        record = json.loads(line)
        question_id = str(record[id_field])
        question = record[question_field]
        if not question_id or not isinstance(question, str) or not question.strip():
            raise ValueError("an empty ID or question was found")
        questions.append({"id": question_id, "question": question})

if len(questions) != 10:
    raise ValueError(f"expected 10 questions, found {len(questions)}")
if len({item["id"] for item in questions}) != len(questions):
    raise ValueError("duplicate question IDs found")

with open(output_path, "w", encoding="utf-8", newline="\n") as output_file:
    for question in questions:
        output_file.write(json.dumps(question, ensure_ascii=False) + "\n")

for question in questions:
    print(question["id"], len(question["question"]))
PY
```

The final output should list 10 unique IDs with non-zero question lengths.
Remove the `len(questions) == 10` limit only after reviewing the pilot results.

## 3. Validate without calling Codex

```bash
db_path="${work_dir}/answers.db"
answers_path="${work_dir}/answers.jsonl"

codex-batch run "${input_path}" \
    --db "${db_path}" \
    --output "${answers_path}" \
    --dry-run
```

Confirm that `Total` is `10`, `Unfinished` is `10`, and the command reports
that Codex was not called.

## 4. Start answering

Run one independent question at a time for the first pilot:

```bash
codex_command="${PWD}/bin/codex-exec"

codex-batch run "${input_path}" \
    --db "${db_path}" \
    --output "${answers_path}" \
    --codex-command "${codex_command}" \
    --model "gpt-5.6-sol" \
    --reasoning-effort medium \
    --batch-size 1 \
    --workers 1 \
    --retries 2 \
    --timeout 900 \
    --fallback-single
```

Use a model available to the signed-in account. For code, mathematics, and
independent evaluation, keep `--batch-size 1`. Increase concurrency only after
the pilot completes successfully.

## Results and resume

```bash
codex-batch status --db "${db_path}"
```

Answers are exported to `answers.jsonl`; checkpoints and attempt history remain
in `answers.db`. If the process stops, activate the same virtual environment
and execute the same live command again. Do not delete the database. Use a new
working directory when the input, model, reasoning effort, batch size, or
fallback setting changes.

## Appendix: Start answering parameters

| Argument | Required | Default | Description |
|---|---:|---:|---|
| `INPUT` | Yes | None | UTF-8 JSONL file containing `id` and `question`. |
| `--db PATH` | Yes | None | SQLite checkpoint database. |
| `--output PATH` | Yes | None | Exported answer and status JSONL. |
| `--codex-command PATH` | Live runs | None | Patched `codex-exec` executable. |
| `--model MODEL` | No | Codex default | Model available to the signed-in account. |
| `--reasoning-effort LEVEL` | No | Codex default | `low`, `medium`, `high`, or `xhigh`. |
| `--batch-size N` | No | `20` | Questions sent in one Codex turn. |
| `--workers N` | No | `2` | Concurrent Codex processes. |
| `--retries N` | No | `2` | Retries after the first failed call. |
| `--timeout SECONDS` | No | `600` | Per-call timeout. |
| `--retry-delay SECONDS` | No | `10` | Initial delay before a retry. |
| `--fallback-single` | No | Enabled | Retry a failed multi-question batch one question at a time. |
| `--no-fallback-single` | No | Disabled | Disable the single-question fallback. |
| `--dry-run` | No | Disabled | Validate input without calling Codex. |
