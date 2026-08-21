# CodexLite 0.5.1

This release adds the first native Linux x86_64 bundle while retaining the
Windows x64 package.

## Included

- Windows x64 portable bundle
- Ubuntu 22.04+ x86_64 portable bundle
- UTF-8 JSONL input and answer export
- SQLite checkpointing and interruption recovery
- Retry, timeout, concurrency, and single-question fallback controls
- An answer-only Codex build with no model-visible tools or host context
- Local request-shape verification and pinned upstream Codex source

## Linux build

The Linux executable was built natively on Ubuntu 22.04 x86_64. The patched
Codex feature tests, Python tests, binary smoke tests, and local request-shape
verification completed before packaging.

## Requirements

Users must sign in with their own Codex account and must have access to the
selected model. Authentication, entitlements, quotas, routing, and service-side
behavior are not modified.

See `README-QUICKSTART.md` inside each bundle for installation, question-bank
conversion, dry-run validation, live execution, and resume instructions.
