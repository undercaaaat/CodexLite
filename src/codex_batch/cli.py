from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import sqlite3
import sys
from pathlib import Path
from typing import NoReturn

from codex_batch import __version__
from codex_batch.runner import (
    CodexRunner,
    CodexSettings,
    ScheduleSettings,
    make_batches,
    process_questions,
)
from codex_batch.storage import Database, Question


def load_questions(input_path: Path) -> list[Question]:
    questions: list[Question] = []
    seen_ids: set[str] = set()
    with input_path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            stripped_line = line.strip()
            if not stripped_line:
                continue
            try:
                item = json.loads(stripped_line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{input_path}:{line_number}: invalid JSON: {error.msg}"
                ) from error
            if not isinstance(item, dict):
                raise ValueError(
                    f"{input_path}:{line_number}: each line must be a JSON object"
                )
            if "id" not in item or item["id"] is None:
                raise ValueError(f"{input_path}:{line_number}: missing id")
            question_id = str(item["id"])
            if not question_id:
                raise ValueError(f"{input_path}:{line_number}: id cannot be empty")
            if question_id in seen_ids:
                raise ValueError(
                    f"{input_path}:{line_number}: duplicate id {question_id!r}"
                )
            question_text = item.get("question")
            if not isinstance(question_text, str) or not question_text.strip():
                raise ValueError(
                    f"{input_path}:{line_number}: question must be a non-empty string"
                )
            seen_ids.add(question_id)
            questions.append(Question(id=question_id, question=question_text))

    if not questions:
        raise ValueError(f"{input_path}: no questions found")
    return questions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-batch",
        description="Resumable batch question answering through Codex CLI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    run_parser = subparsers.add_parser("run", help="Run or resume a question set")
    run_parser.add_argument("input", type=Path, help="UTF-8 questions JSONL")
    run_parser.add_argument("--db", type=Path, required=True, help="SQLite state file")
    run_parser.add_argument(
        "--output", type=Path, required=True, help="Exported answers JSONL"
    )
    run_parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=20,
        help="Questions per Codex turn (default: 20)",
    )
    run_parser.add_argument(
        "--workers",
        type=_positive_int,
        default=2,
        help="Concurrent Codex processes (default: 2)",
    )
    run_parser.add_argument(
        "--retries",
        type=_non_negative_int,
        default=2,
        help="Retries after the first attempt (default: 2)",
    )
    run_parser.add_argument(
        "--timeout",
        type=_positive_int,
        default=600,
        help="Per-call timeout in seconds (default: 600)",
    )
    run_parser.add_argument(
        "--retry-delay",
        type=_non_negative_float,
        default=10.0,
        help="Initial retry delay in seconds (default: 10)",
    )
    run_parser.add_argument("--model", default=None)
    run_parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default=None,
    )
    run_parser.add_argument(
        "--codex-command",
        default=None,
        help="Path to the patched codex-exec executable (required for live runs)",
    )
    run_parser.add_argument(
        "--fallback-single",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Retry failed multi-question batches one question at a time (default: true)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and checkpoint input without calling Codex",
    )

    status_parser = subparsers.add_parser("status", help="Show database progress")
    status_parser.add_argument("--db", type=Path, required=True)

    export_parser = subparsers.add_parser("export", help="Export database to JSONL")
    export_parser.add_argument("--db", type=Path, required=True)
    export_parser.add_argument("--output", type=Path, required=True)

    report_parser = subparsers.add_parser(
        "report", help="Write an integrity and attempt report"
    )
    report_parser.add_argument("--db", type=Path, required=True)
    report_parser.add_argument("--output", type=Path, required=True)
    return parser


def run_command(args: argparse.Namespace) -> int:
    questions = load_questions(args.input)
    database = Database(args.db)
    runner: CodexRunner | None = None
    exit_code = 0
    try:
        database.sync_questions(questions)
        recovered = database.recover_interrupted()
        if recovered:
            print(f"Recovered:  {recovered} interrupted attempts")
        unfinished = database.unfinished_questions()
        print(f"Total:      {len(questions)}")
        print(f"Unfinished: {len(unfinished)}")
        print(f"Batch size: {args.batch_size}")
        print(f"Workers:    {args.workers}")

        if args.dry_run:
            print("Dry run complete; Codex was not called.")
            return 0
        if args.codex_command is None:
            raise ValueError(
                "--codex-command is required for live runs and must point to "
                "the patched codex-exec executable"
            )

        runner = CodexRunner(
            CodexSettings(
                command=args.codex_command,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                timeout_seconds=args.timeout,
            )
        )
        contract = runner.run_contract(
            batch_size=args.batch_size,
            fallback_single=args.fallback_single,
        )
        contract_sha256 = database.configure_run(contract)
        write_run_manifest(
            database=database,
            contract_sha256=contract_sha256,
            cli_version=runner.cli_version,
        )
        if not unfinished:
            print("All questions are already complete.")
            return 0
        batches = make_batches(unfinished, args.batch_size)
        schedule_settings = ScheduleSettings(
            workers=args.workers,
            retries=args.retries,
            retry_delay_seconds=args.retry_delay,
            fallback_single=args.fallback_single,
        )
        try:
            asyncio.run(
                process_questions(
                    database=database,
                    runner=runner,
                    batches=batches,
                    settings=schedule_settings,
                )
            )
        except KeyboardInterrupt:
            print("Interrupted; saved progress will be exported.")
            exit_code = 130
    finally:
        try:
            if runner is not None:
                runner.close()
            database.verify_integrity()
            database.export_jsonl(args.output)
            counts = database.counts()
        finally:
            database.close()
        print(f"Exported:   {args.output}")
        print(
            "Status:     "
            f"done={counts['done']} failed={counts['failed']} "
            f"pending={counts['pending']} running={counts['running']}"
        )

    if exit_code == 0 and counts["done"] != counts["total"]:
        return 2
    return exit_code


def status_command(args: argparse.Namespace) -> int:
    if not args.db.exists():
        raise ValueError(f"Database does not exist: {args.db}")
    database = Database(args.db, acquire_lock=False)
    try:
        database.verify_integrity()
        counts = database.counts()
        attempts = database.attempt_summary()
    finally:
        database.close()
    print(f"Database: {args.db}")
    print(f"Total:    {counts['total']}")
    print(f"Done:     {counts['done']}")
    print(f"Failed:   {counts['failed']}")
    print(f"Pending:  {counts['pending']}")
    print(f"Running:  {counts['running']}")
    print(f"Attempts: {attempts['total']}")
    return 0 if counts["done"] == counts["total"] else 2


def export_command(args: argparse.Namespace) -> int:
    if not args.db.exists():
        raise ValueError(f"Database does not exist: {args.db}")
    database = Database(args.db, acquire_lock=False)
    try:
        database.verify_integrity()
        database.export_jsonl(args.output)
        counts = database.counts()
    finally:
        database.close()
    print(f"Exported {counts['total']} rows to {args.output}")
    return 0


def report_command(args: argparse.Namespace) -> int:
    if not args.db.exists():
        raise ValueError(f"Database does not exist: {args.db}")
    database = Database(args.db, acquire_lock=False)
    try:
        report = database.report()
    finally:
        database.close()
    _write_json(args.output, report)
    print(f"Report written to {args.output}")
    return 0


def write_run_manifest(
    *,
    database: Database,
    contract_sha256: str,
    cli_version: str,
) -> Path:
    path = database.path.with_name(database.path.name + ".manifest.json")
    manifest = {
        "environment": {
            "codex_cli": cli_version,
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
        },
        "run_identity": database.run_identity(),
        "schema_version": 1,
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_sha256 = existing.get("run_identity", {}).get(
            "run_contract_sha256"
        )
        if existing_sha256 != contract_sha256:
            raise ValueError("run manifest does not match the database contract")
        return path
    _write_json(path, manifest)
    return path


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _positive_int(value: str) -> int:
    parsed_value = int(value)
    if parsed_value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed_value


def _non_negative_int(value: str) -> int:
    parsed_value = int(value)
    if parsed_value < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed_value


def _non_negative_float(value: str) -> float:
    parsed_value = float(value)
    if not math.isfinite(parsed_value) or parsed_value < 0:
        raise argparse.ArgumentTypeError("must be a finite number zero or greater")
    return parsed_value


def fail(message: str) -> NoReturn:
    raise SystemExit(f"error: {message}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.subcommand == "run":
            exit_code = run_command(args)
        elif args.subcommand == "status":
            exit_code = status_command(args)
        elif args.subcommand == "export":
            exit_code = export_command(args)
        else:
            exit_code = report_command(args)
    except (OSError, RuntimeError, ValueError) as error:
        fail(str(error))
    raise SystemExit(exit_code)
