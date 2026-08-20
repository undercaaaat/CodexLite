# CodexLite 0.5.0

Initial research preview for resumable, independent batch question answering
through Codex CLI.

## Included

- Windows x64 portable bundle
- macOS Apple Silicon portable bundle
- macOS Intel portable bundle
- UTF-8 JSONL input and answer export
- SQLite checkpointing and interruption recovery
- Retry, timeout, concurrency, and single-question fallback controls
- An answer-only Codex build with no model-visible tools or host context
- Local request-shape verification and pinned upstream Codex source

## Requirements

Users must sign in with their own Codex account and must have access to the
selected model. Authentication, entitlements, quotas, routing, and service-side
behavior are not modified.

The macOS artifacts are unsigned research previews. Review the source and
release before approving them in macOS Privacy & Security. Do not disable
Gatekeeper globally.

See `README-QUICKSTART.md` inside each bundle for installation, question-bank
conversion, dry-run validation, live execution, and resume instructions.
