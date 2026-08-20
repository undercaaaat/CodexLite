# Modifications to OpenAI Codex

CodexLite includes an optional patched build of OpenAI Codex CLI. The patch is
based on Codex `rust-v0.144.6` at commit
`5d1fbf26c43abc65a203928b2e31561cb039e06d`.

The patch adds an `answer_only` feature that:

- keeps only the current user message when building a turn;
- removes model-visible tools;
- sets `tool_choice` to `none` when no tools are present;
- prevents Responses Lite from inserting an `AdditionalTools` item.

The feature is disabled by default in the patched Codex source and is enabled
explicitly by CodexLite. Authentication, entitlements, quotas, routing, and
service-side behavior are not changed.

The complete source modification is provided in
`experiments/codex-no-tools/codex-rust-v0.144.6-no-tools.patch`.
