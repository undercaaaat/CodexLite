from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Protocol

from codex_batch import __version__
from codex_batch.storage import Database, Question, sanitize_error_text, sha256_text


BASE_INSTRUCTIONS = (
    "Answer the supplied independent questions and follow the output schema."
)

BATCH_INSTRUCTIONS = """Answer each question independently.
Preserve every ID and return one concise, non-empty answer per ID.
Return only the object required by the JSON schema.

Payload:
"""

HOST_CONTEXT_ENVIRONMENT_VARIABLES = (
    "CODEX_CI",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "CODEX_PERMISSION_PROFILE",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
)


class BatchRunner(Protocol):
    async def ask_batch(self, questions: Sequence[Question]) -> dict[str, str]: ...


@dataclass(frozen=True, slots=True)
class CodexSettings:
    command: str
    model: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: int = 600


class CodexRunner:
    def __init__(self, settings: CodexSettings) -> None:
        executable = shutil.which(settings.command)
        if executable is None:
            raise FileNotFoundError(
                f"Codex command not found: {settings.command!r}. "
                "Build the patched codex-exec and pass --codex-command."
            )
        self.executable = executable
        if Path(executable).stem.lower() != "codex-exec":
            raise ValueError(
                "codex-batch requires the patched codex-exec executable; "
                "the stock Codex wrapper retains model-visible host context"
            )
        self.executable_sha256 = sha256_file(Path(executable))
        self.settings = settings
        self.subprocess_environment = os.environ.copy()
        for name in HOST_CONTEXT_ENVIRONMENT_VARIABLES:
            self.subprocess_environment.pop(name, None)
        self.empty_workspace = tempfile.TemporaryDirectory(
            prefix="codex_batch_workspace_"
        )
        self.schema_path = Path(
            str(files("codex_batch").joinpath("answer_schema.json"))
        )
        self.cli_version = self._read_cli_version()

    def close(self) -> None:
        self.empty_workspace.cleanup()

    def run_contract(
        self,
        *,
        batch_size: int,
        fallback_single: bool,
    ) -> dict[str, object]:
        schema_sha256 = sha256_text(self.schema_path.read_text(encoding="utf-8"))
        prompt_sha256 = sha256_text(BASE_INSTRUCTIONS + "\n" + BATCH_INSTRUCTIONS)
        return {
            "adapter": "codex_exec_answer_only",
            "adapter_source_sha256": sha256_text(
                Path(__file__).read_text(encoding="utf-8")
            ),
            "adapter_version": __version__,
            "batch_size": batch_size,
            "codex_cli_version": self.cli_version,
            "codex_executable_sha256": self.executable_sha256,
            "fallback_single": fallback_single,
            "model": self.settings.model,
            "answer_only": True,
            "output_schema_sha256": schema_sha256,
            "prompt_sha256": prompt_sha256,
            "reasoning_effort": self.settings.reasoning_effort,
            "sanitized_environment_variables": list(
                HOST_CONTEXT_ENVIRONMENT_VARIABLES
            ),
        }

    def _read_cli_version(self) -> str:
        result = subprocess.run(
            [self.executable, "--version"],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        value = (result.stdout or result.stderr).strip()
        if result.returncode != 0 or not value:
            raise RuntimeError("unable to read Codex CLI version")
        return value[:200]

    async def ask_batch(self, questions: Sequence[Question]) -> dict[str, str]:
        if not questions:
            raise ValueError("Cannot submit an empty batch")

        payload = {
            "questions": [
                {"id": question.id, "question": question.question}
                for question in questions
            ]
        }
        prompt = BATCH_INSTRUCTIONS + json.dumps(payload, ensure_ascii=False)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="codex_batch_output_",
            delete=False,
        ) as output_file:
            output_path = Path(output_file.name)

        command = self._build_command(output_path)
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.empty_workspace.name,
                env=self.subprocess_environment,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")),
                    timeout=self.settings.timeout_seconds,
                )
            except asyncio.TimeoutError as error:
                await terminate_process(process)
                raise RuntimeError(
                    f"Codex timed out after {self.settings.timeout_seconds} seconds"
                ) from error

            if process.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                stdout_text = stdout.decode("utf-8", errors="replace").strip()
                diagnostic = stderr_text or stdout_text or "no diagnostic output"
                raise RuntimeError(
                    f"Codex exited with code {process.returncode}: {diagnostic[:4000]}"
                )

            raw_result = output_path.read_text(encoding="utf-8").strip()
            if not raw_result:
                raise RuntimeError("Codex returned an empty final response")
            try:
                result = json.loads(raw_result)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"Codex returned invalid JSON: {raw_result[:2000]}"
                ) from error
            return validate_answers(result, questions)
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                await terminate_process(process)
            raise
        finally:
            output_path.unlink(missing_ok=True)

    def _build_command(self, output_path: Path) -> list[str]:
        command = [
            self.executable,
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--output-schema",
            str(self.schema_path),
            "--output-last-message",
            str(output_path),
            "-c",
            "features.answer_only=true",
            "-c",
            "suppress_unstable_features_warning=true",
            "-c",
            "instructions=" + json.dumps(BASE_INSTRUCTIONS),
        ]
        if self.settings.model:
            command.extend(("--model", self.settings.model))
        if self.settings.reasoning_effort:
            command.extend(
                (
                    "-c",
                    "model_reasoning_effort="
                    + json.dumps(self.settings.reasoning_effort),
                )
            )
        command.append("-")
        return command


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        terminator = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await terminator.wait()
    else:
        process.kill()
    await process.wait()


def validate_answers(
    result: object,
    questions: Sequence[Question],
) -> dict[str, str]:
    if not isinstance(result, dict) or not isinstance(result.get("answers"), list):
        raise RuntimeError("Codex response must contain an answers array")

    expected_ids = {question.id for question in questions}
    answers: dict[str, str] = {}
    for index, item in enumerate(result["answers"]):
        if not isinstance(item, dict):
            raise RuntimeError(f"Answer at index {index} is not an object")
        question_id = item.get("id")
        answer = item.get("answer")
        if not isinstance(question_id, str) or not question_id:
            raise RuntimeError(f"Answer at index {index} has an invalid ID")
        if question_id in answers:
            raise RuntimeError(f"Codex returned duplicate ID: {question_id}")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError(f"Codex returned an empty answer for ID: {question_id}")
        answers[question_id] = answer

    returned_ids = set(answers)
    missing_ids = sorted(expected_ids - returned_ids)
    extra_ids = sorted(returned_ids - expected_ids)
    if missing_ids or extra_ids:
        details = []
        if missing_ids:
            details.append("missing IDs: " + ", ".join(missing_ids))
        if extra_ids:
            details.append("extra IDs: " + ", ".join(extra_ids))
        raise RuntimeError("Invalid Codex response; " + "; ".join(details))
    return answers


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ScheduleSettings:
    workers: int
    retries: int
    retry_delay_seconds: float
    fallback_single: bool


async def process_questions(
    database: Database,
    runner: BatchRunner,
    batches: Sequence[Sequence[Question]],
    settings: ScheduleSettings,
) -> None:
    semaphore = asyncio.Semaphore(settings.workers)
    total_batches = len(batches)

    async def process_batch(
        batch: Sequence[Question],
        batch_index: int,
    ) -> None:
        async with semaphore:
            error = await _attempt_batch(
                database=database,
                runner=runner,
                batch=batch,
                retries=settings.retries,
                retry_delay_seconds=settings.retry_delay_seconds,
                label=f"{batch_index}/{total_batches}",
            )
            if error is None:
                return
            if settings.fallback_single and len(batch) > 1:
                print(
                    f"[{batch_index}/{total_batches}] batch failed; "
                    f"retrying {len(batch)} questions individually"
                )
                for single_index, question in enumerate(batch, start=1):
                    await _attempt_batch(
                        database=database,
                        runner=runner,
                        batch=[question],
                        retries=settings.retries,
                        retry_delay_seconds=settings.retry_delay_seconds,
                        label=(
                            f"{batch_index}/{total_batches} "
                            f"single {single_index}/{len(batch)}"
                        ),
                    )

    tasks = [
        asyncio.create_task(process_batch(batch, batch_index))
        for batch_index, batch in enumerate(batches, start=1)
    ]
    await asyncio.gather(*tasks)


async def _attempt_batch(
    database: Database,
    runner: BatchRunner,
    batch: Sequence[Question],
    retries: int,
    retry_delay_seconds: float,
    label: str,
) -> Exception | None:
    question_ids = [question.id for question in batch]
    total_attempts = retries + 1
    last_error: Exception | None = None

    for attempt in range(1, total_attempts + 1):
        attempt_id = database.start_attempt(question_ids, batch_label=label)
        started_at = time.monotonic()
        try:
            answers = await runner.ask_batch(batch)
            latency_ms = (time.monotonic() - started_at) * 1000.0
            database.complete_attempt_success(attempt_id, answers, latency_ms)
            print(f"[{label}] OK ({len(batch)} questions)")
            return None
        except asyncio.CancelledError:
            database.interrupt_attempt(attempt_id)
            raise
        except Exception as error:
            last_error = error
            safe_error = sanitize_error_text(str(error))
            latency_ms = (time.monotonic() - started_at) * 1000.0
            database.complete_attempt_failure(
                attempt_id,
                safe_error,
                classify_error(error),
                latency_ms,
                terminal=attempt == total_attempts,
            )
            print(
                f"[{label}] attempt {attempt}/{total_attempts} failed: "
                f"{safe_error[:500]}"
            )
            if attempt < total_attempts:
                delay = min(retry_delay_seconds * (2 ** (attempt - 1)), 120.0)
                await asyncio.sleep(delay)

    assert last_error is not None
    return last_error


def classify_error(error: Exception) -> str:
    message = str(error).casefold()
    if isinstance(error, asyncio.TimeoutError) or "timed out" in message:
        return "timeout"
    if "429" in message or "rate limit" in message or "usage limit" in message:
        return "rate_or_usage_limit"
    if "quota" in message or "credit" in message:
        return "quota_or_credit"
    if any(
        marker in message
        for marker in (
            "invalid json",
            "invalid codex response",
            "duplicate id",
            "empty answer",
            "empty final response",
        )
    ):
        return "invalid_response"
    if "exited with code" in message:
        return "codex_exit"
    return "unexpected_error"


def make_batches(
    questions: Sequence[Question],
    batch_size: int,
) -> list[list[Question]]:
    return [
        list(questions[start : start + batch_size])
        for start in range(0, len(questions), batch_size)
    ]
