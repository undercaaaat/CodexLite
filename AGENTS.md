# Codex Batch Project Guide

## Purpose

This repository implements a resumable batch question-answering runner on top
of the supported `codex exec` interface. It deliberately does not extract,
replay, or proxy private Codex authentication credentials or internal backend
requests.

## Architecture

- `src/codex_batch/cli.py`: command-line interface and input loading.
- `src/codex_batch/runner.py`: isolated, non-interactive Codex subprocesses and
  batch scheduling.
- `src/codex_batch/storage.py`: SQLite checkpointing and JSONL export.
- `src/codex_batch/answer_schema.json`: structured-output contract sent to
  Codex.

Each Codex turn is ephemeral, receives a small batch of independent questions,
and must return one answer for every input ID. A batch is committed atomically
only after its returned IDs have been validated exactly. Failed work remains in
SQLite and can be resumed by rerunning the same command.

The run database also owns an immutable input hash and execution contract,
per-attempt audit rows, interruption recovery, integrity checks, and an adjacent
manifest. Keep these guarantees implemented locally; do not introduce runtime
dependencies on provider-specific experiment packages.

## Commands

```powershell
python -m pip install -e .
codex-batch run examples/questions.jsonl --db answers.db --output answers.jsonl `
  --codex-command .\.tools\codex-no-tools\codex-exec.exe
codex-batch status --db answers.db
codex-batch export --db answers.db --output answers.jsonl
codex-batch report --db answers.db --output answers.report.json
```

## Development Rules

- Support Python 3.11 or newer and use only the standard library unless a new
  dependency is explicitly requested.
- Follow PEP 8, use 4-space indentation, and add type hints to public functions.
- Keep input and output as UTF-8 JSONL. Treat IDs as opaque strings.
- Preserve fail-fast validation for malformed input, duplicate IDs, changed
  questions, missing answers, extra answers, and duplicate returned IDs.
- Keep database writes transactional. Never mark a partial batch as complete.
- Preserve the database process lock, immutable run identity, attempt audit
  trail, and fail-fast integrity checks.
- Keep Codex isolated: ephemeral session, ignored user config/rules, read-only
  sandbox, empty working directory, no model-visible tools, and no host context.
- Never print tokens, credentials, complete private prompts, or full stderr that
  may contain sensitive data. Persist bounded error messages only.
- Do not add code that bypasses authentication, entitlement, quotas, routing,
  attestation, or other service-side controls.
