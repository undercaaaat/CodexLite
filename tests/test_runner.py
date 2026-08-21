from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_batch.runner import (
    HOST_CONTEXT_ENVIRONMENT_VARIABLES,
    CodexRunner,
    CodexSettings,
    validate_answers,
)
from codex_batch.storage import Question


class CodexRunnerTests(unittest.TestCase):
    def make_runner(
        self,
        executable: str = "codex-exec",
    ) -> CodexRunner:
        settings = CodexSettings(
            command="answer-only-codex",
            model="gpt-test",
            reasoning_effort="medium",
        )
        with (
            patch(
                "codex_batch.runner.shutil.which",
                return_value=executable,
            ),
            patch("codex_batch.runner.sha256_file", return_value="binary-hash"),
            patch.object(CodexRunner, "_read_cli_version", return_value="codex-test"),
        ):
            return CodexRunner(settings)

    def test_accepts_native_macos_executable_name(self) -> None:
        runner = self.make_runner("/opt/codexlite/bin/codex-exec")
        self.addCleanup(runner.close)

        self.assertEqual(runner.executable, "/opt/codexlite/bin/codex-exec")

    def test_command_uses_one_answer_only_switch(self) -> None:
        runner = self.make_runner()
        self.addCleanup(runner.close)

        command = runner._build_command(Path("result.json"))

        self.assertEqual(command[0], "codex-exec")
        self.assertEqual(command[-1], "-")
        self.assertIn("features.answer_only=true", command)
        self.assertNotIn("features.no_tools=true", command)
        self.assertNotIn("features.minimal_context=true", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--ephemeral", command)

    def test_host_task_environment_is_removed(self) -> None:
        with patch.dict(
            os.environ,
            {name: "secret-host-value" for name in HOST_CONTEXT_ENVIRONMENT_VARIABLES},
        ):
            runner = self.make_runner()
        self.addCleanup(runner.close)

        for name in HOST_CONTEXT_ENVIRONMENT_VARIABLES:
            self.assertNotIn(name, runner.subprocess_environment)

    def test_run_contract_records_answer_only_mode(self) -> None:
        runner = self.make_runner()
        self.addCleanup(runner.close)

        contract = runner.run_contract(batch_size=10, fallback_single=True)

        self.assertEqual(contract["adapter"], "codex_exec_answer_only")
        self.assertIs(contract["answer_only"], True)
        self.assertNotIn("no_tools", contract)
        self.assertNotIn("minimal_context", contract)


class ValidateAnswersTests(unittest.TestCase):
    def test_accepts_one_answer_per_question(self) -> None:
        questions = [Question(id="a", question="A?"), Question(id="b", question="B?")]
        result = {
            "answers": [
                {"id": "a", "answer": "A"},
                {"id": "b", "answer": "B"},
            ]
        }

        self.assertEqual(validate_answers(result, questions), {"a": "A", "b": "B"})

    def test_rejects_missing_answers(self) -> None:
        questions = [Question(id="a", question="A?"), Question(id="b", question="B?")]

        with self.assertRaisesRegex(RuntimeError, "missing IDs: b"):
            validate_answers({"answers": [{"id": "a", "answer": "A"}]}, questions)


if __name__ == "__main__":
    unittest.main()
