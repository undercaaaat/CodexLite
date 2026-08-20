from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
_SECRET_SHAPE_RE = re.compile(
    r"(?<![A-Za-z0-9])sk-(?:or-v1-)?[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[^\s,;]+")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sanitize_error_text(value: str) -> str:
    value = _BEARER_RE.sub(r"\1[REDACTED]", value)
    return _SECRET_SHAPE_RE.sub("[REDACTED_API_KEY]", value)


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    question: str


class DatabaseLockError(RuntimeError):
    """Another writer already owns this database."""


class _ProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            self.handle.close()
            self.handle = None
            raise DatabaseLockError(
                f"another codex-batch process is using {self.path.stem}"
            ) from error

    def close(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class Database:
    def __init__(self, path: Path, *, acquire_lock: bool = True) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = _ProcessLock(path.with_name(path.name + ".lock")) if acquire_lock else None
        if self.lock is not None:
            self.lock.acquire()
        try:
            self.connection = sqlite3.connect(path, timeout=30.0)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA busy_timeout = 30000")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = FULL")
            self._initialize_schema()
        except Exception:
            if hasattr(self, "connection"):
                self.connection.close()
            if self.lock is not None:
                self.lock.close()
            raise

    def _initialize_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS questions (
                id TEXT PRIMARY KEY,
                source_order INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_label TEXT NOT NULL,
                question_ids_json TEXT NOT NULL,
                batch_size INTEGER NOT NULL,
                state TEXT NOT NULL
                    CHECK (state IN ('running', 'succeeded', 'failed', 'interrupted')),
                error_category TEXT,
                error TEXT,
                latency_ms REAL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS attempts_state_idx
            ON attempts(state, attempt_id);
            """
        )
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self.connection.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif int(row["value"]) != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported database schema version {row['value']}; "
                f"expected {SCHEMA_VERSION}"
            )
        self.connection.commit()

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            if self.lock is not None:
                self.lock.close()

    def _meta(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def _set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def sync_questions(self, questions: Sequence[Question]) -> str:
        input_by_id = {question.id: question for question in questions}
        if len(input_by_id) != len(questions):
            raise ValueError("Input contains duplicate question IDs")

        input_value = [
            {"id": question.id, "question": question.question}
            for question in questions
        ]
        input_sha256 = sha256_text(canonical_json(input_value))
        stored_input_sha256 = self._meta("input_sha256")
        if stored_input_sha256 is not None and stored_input_sha256 != input_sha256:
            raise ValueError(
                "the input dataset differs from this database's immutable identity; "
                "use a new --db path"
            )

        existing_rows = self.connection.execute(
            "SELECT id, question FROM questions"
        ).fetchall()
        existing_by_id = {row["id"]: row["question"] for row in existing_rows}
        removed_ids = sorted(set(existing_by_id) - set(input_by_id))
        if removed_ids:
            preview = ", ".join(removed_ids[:10])
            raise ValueError(
                "The input no longer contains IDs already stored in the database: "
                f"{preview}. Use a new database for a different dataset."
            )
        changed_ids = sorted(
            question_id
            for question_id, stored_text in existing_by_id.items()
            if input_by_id[question_id].question != stored_text
        )
        if changed_ids:
            preview = ", ".join(changed_ids[:10])
            raise ValueError(
                "Question text changed for existing IDs: "
                f"{preview}. Use a new database or restore the original text."
            )

        with self.connection:
            for source_order, question in enumerate(questions):
                self.connection.execute(
                    """
                    INSERT INTO questions (id, source_order, question)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_order = excluded.source_order,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (question.id, source_order, question.question),
                )
            self._set_meta("input_sha256", input_sha256)
            self._set_meta("question_count", str(len(questions)))
        return input_sha256

    def configure_run(self, contract: dict[str, Any]) -> str:
        contract_json = canonical_json(contract)
        fingerprint = sha256_text(contract_json)
        stored_fingerprint = self._meta("run_contract_sha256")
        if stored_fingerprint is None:
            completed = self.connection.execute(
                "SELECT COUNT(*) FROM questions WHERE status = 'done'"
            ).fetchone()[0]
            if completed:
                raise ValueError(
                    "this legacy database contains answers but no immutable run contract; "
                    "use a new --db path"
                )
        if stored_fingerprint is not None and stored_fingerprint != fingerprint:
            raise ValueError(
                "the model, prompt, schema, CLI version, or batching contract changed; "
                "use a new --db path for a different run contract"
            )
        with self.connection:
            self._set_meta("run_contract_json", contract_json)
            self._set_meta("run_contract_sha256", fingerprint)
        return fingerprint

    def run_identity(self) -> dict[str, Any]:
        contract_json = self._meta("run_contract_json")
        return {
            "input_sha256": self._meta("input_sha256"),
            "question_count": int(self._meta("question_count") or 0),
            "run_contract": None if contract_json is None else json.loads(contract_json),
            "run_contract_sha256": self._meta("run_contract_sha256"),
            "schema_version": SCHEMA_VERSION,
        }

    def recover_interrupted(self) -> int:
        rows = self.connection.execute(
            "SELECT attempt_id FROM attempts WHERE state = 'running'"
        ).fetchall()
        if not rows:
            return 0
        with self.connection:
            self.connection.execute(
                """
                UPDATE attempts
                SET state = 'interrupted',
                    error_category = 'process_interrupted',
                    error = 'process ended before the attempt completed',
                    finished_at = CURRENT_TIMESTAMP
                WHERE state = 'running'
                """
            )
            self.connection.execute(
                """
                UPDATE questions
                SET status = 'pending',
                    error = 'previous process ended during this question',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                """
            )
        return len(rows)

    def unfinished_questions(self) -> list[Question]:
        rows = self.connection.execute(
            """
            SELECT id, question
            FROM questions
            WHERE status != 'done'
            ORDER BY source_order
            """
        ).fetchall()
        return [Question(id=row["id"], question=row["question"]) for row in rows]

    def start_attempt(self, question_ids: Iterable[str], batch_label: str) -> int:
        ids = list(question_ids)
        if not ids:
            raise ValueError("cannot start an empty attempt")
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO attempts (
                    batch_label, question_ids_json, batch_size, state
                ) VALUES (?, ?, ?, 'running')
                """,
                (batch_label, canonical_json(ids), len(ids)),
            )
            self.connection.executemany(
                """
                UPDATE questions
                SET status = 'running',
                    attempts = attempts + 1,
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status != 'done'
                """,
                ((question_id,) for question_id in ids),
            )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)

    def complete_attempt_success(
        self,
        attempt_id: int,
        answers: dict[str, str],
        latency_ms: float,
    ) -> None:
        expected_ids = self._attempt_question_ids(attempt_id, expected_state="running")
        if set(answers) != set(expected_ids):
            raise ValueError("attempt answers do not match the reserved question IDs")
        self._validate_latency(latency_ms)
        with self.connection:
            self.connection.executemany(
                """
                UPDATE questions
                SET answer = ?,
                    status = 'done',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ((answer, question_id) for question_id, answer in answers.items()),
            )
            self.connection.execute(
                """
                UPDATE attempts
                SET state = 'succeeded',
                    latency_ms = ?,
                    finished_at = CURRENT_TIMESTAMP
                WHERE attempt_id = ? AND state = 'running'
                """,
                (latency_ms, attempt_id),
            )

    def complete_attempt_failure(
        self,
        attempt_id: int,
        error: str,
        error_category: str,
        latency_ms: float,
        *,
        terminal: bool,
    ) -> None:
        question_ids = self._attempt_question_ids(attempt_id, expected_state="running")
        self._validate_latency(latency_ms)
        bounded_error = sanitize_error_text(error)[:4000]
        next_status = "failed" if terminal else "pending"
        with self.connection:
            self.connection.execute(
                """
                UPDATE attempts
                SET state = 'failed',
                    error_category = ?,
                    error = ?,
                    latency_ms = ?,
                    finished_at = CURRENT_TIMESTAMP
                WHERE attempt_id = ? AND state = 'running'
                """,
                (error_category, bounded_error, latency_ms, attempt_id),
            )
            self.connection.executemany(
                """
                UPDATE questions
                SET status = ?,
                    error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status != 'done'
                """,
                (
                    (next_status, bounded_error, question_id)
                    for question_id in question_ids
                ),
            )

    def interrupt_attempt(self, attempt_id: int) -> None:
        question_ids = self._attempt_question_ids(attempt_id, expected_state="running")
        with self.connection:
            self.connection.execute(
                """
                UPDATE attempts
                SET state = 'interrupted',
                    error_category = 'process_interrupted',
                    error = 'attempt cancelled before completion',
                    finished_at = CURRENT_TIMESTAMP
                WHERE attempt_id = ? AND state = 'running'
                """,
                (attempt_id,),
            )
            self.connection.executemany(
                """
                UPDATE questions
                SET status = 'pending',
                    error = 'attempt cancelled before completion',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status != 'done'
                """,
                ((question_id,) for question_id in question_ids),
            )

    def _attempt_question_ids(self, attempt_id: int, *, expected_state: str) -> list[str]:
        row = self.connection.execute(
            """
            SELECT question_ids_json, state
            FROM attempts
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown attempt ID: {attempt_id}")
        if row["state"] != expected_state:
            raise ValueError(
                f"attempt {attempt_id} is {row['state']}, expected {expected_state}"
            )
        value = json.loads(row["question_ids_json"])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"attempt {attempt_id} has invalid question IDs")
        return value

    @staticmethod
    def _validate_latency(latency_ms: float) -> None:
        if not math.isfinite(latency_ms) or latency_ms < 0:
            raise ValueError("attempt latency must be finite and nonnegative")

    def counts(self) -> dict[str, int]:
        counts = {status: 0 for status in ("pending", "running", "done", "failed")}
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM questions GROUP BY status"
        ).fetchall()
        for row in rows:
            counts[row["status"]] = row["count"]
        counts["total"] = sum(counts.values())
        return counts

    def attempt_summary(self) -> dict[str, Any]:
        state_rows = self.connection.execute(
            "SELECT state, COUNT(*) AS count FROM attempts GROUP BY state"
        ).fetchall()
        category_rows = self.connection.execute(
            """
            SELECT error_category, COUNT(*) AS count
            FROM attempts
            WHERE error_category IS NOT NULL
            GROUP BY error_category
            ORDER BY error_category
            """
        ).fetchall()
        latency_row = self.connection.execute(
            """
            SELECT AVG(latency_ms) AS average_ms, MAX(latency_ms) AS maximum_ms
            FROM attempts
            WHERE latency_ms IS NOT NULL
            """
        ).fetchone()
        return {
            "error_categories": {
                row["error_category"]: row["count"] for row in category_rows
            },
            "latency_ms": {
                "average": (
                    None
                    if latency_row["average_ms"] is None
                    else round(float(latency_row["average_ms"]), 3)
                ),
                "maximum": (
                    None
                    if latency_row["maximum_ms"] is None
                    else round(float(latency_row["maximum_ms"]), 3)
                ),
            },
            "states": {row["state"]: row["count"] for row in state_rows},
            "total": sum(row["count"] for row in state_rows),
        }

    def verify_integrity(self) -> None:
        quick_check = self.connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise ValueError(f"SQLite integrity check failed: {quick_check}")
        invalid_status = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM questions
            WHERE (status = 'done' AND answer IS NULL)
               OR (status != 'done' AND answer IS NOT NULL)
            """
        ).fetchone()[0]
        if invalid_status:
            raise ValueError("question status and answer fields are inconsistent")
        running_attempts = self.connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE state = 'running'"
        ).fetchone()[0]
        running_questions = self.connection.execute(
            "SELECT COUNT(*) FROM questions WHERE status = 'running'"
        ).fetchone()[0]
        if bool(running_attempts) != bool(running_questions):
            raise ValueError("running attempt and question states are inconsistent")

    def report(self) -> dict[str, Any]:
        self.verify_integrity()
        return {
            "attempts": self.attempt_summary(),
            "database": str(self.path.resolve()),
            "integrity": "ok",
            "run_identity": self.run_identity(),
            "schema_version": 1,
            "tasks": self.counts(),
        }

    def export_jsonl(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_name(output_path.name + ".tmp")
        rows = self.connection.execute(
            """
            SELECT id, question, answer, status, attempts, error
            FROM questions
            ORDER BY source_order
            """
        ).fetchall()
        with temporary_path.open("w", encoding="utf-8", newline="\n") as output_file:
            for row in rows:
                item = {
                    "id": row["id"],
                    "question": row["question"],
                    "answer": row["answer"],
                    "status": row["status"],
                    "attempts": row["attempts"],
                    "error": row["error"],
                }
                output_file.write(json.dumps(item, ensure_ascii=False) + "\n")
        temporary_path.replace(output_path)
