import unittest
from unittest.mock import patch
import forge

from forge import check_file_coverage, check_success, build_continuation_message,  build_evidence_summary



class TestForgeEvidence(unittest.TestCase):

    def setUp(self):
        self.evidence = {
            "search results": [
                {
                    "result": [
                        "hello.py:1:print(1)",
                        "hello2.py:1:print(2)"
                    ]
                }
            ],
            "relevant file contents": [
                {
                    "arguments": {"filename": "hello.py"},
                    "result": "print(1)"
                }
            ],
            "command execution result": [],
            "test execution result": [],
            "workspace file listing": []
        }

        self.success_definition = {
            "goal": "find and inspect hello files",
            "required_evidence": [
                "search results",
                "relevant file contents"
            ]
        }

    def test_detects_missing_inspection(self):
        coverage = check_file_coverage(self.evidence)

        self.assertEqual(
            coverage["missing_inspections"],
            ["hello2.py"]
        )

    def test_rejects_incomplete_coverage(self):
        completion = check_success(
            self.success_definition,
            self.evidence
        )

        self.assertFalse(completion["success"])
        self.assertIn(
            "uninspected files: hello2.py",
            completion["missing"]
        )

    def test_accepts_complete_coverage(self):
        self.evidence["relevant file contents"].append({
            "arguments": {"filename": "hello2.py"},
            "result": "print(2)"
        })

        completion = check_success(
            self.success_definition,
            self.evidence
        )

        self.assertTrue(completion["success"])
        self.assertEqual(completion["missing"], [])

    def test_execution_not_required_for_inspection(self):
        self.evidence["relevant file contents"].append({
            "arguments": {"filename": "hello2.py"},
            "result": "print(2)"
        })

        completion = check_success(
            self.success_definition,
            self.evidence
        )

        self.assertTrue(completion["success"])
        self.assertEqual(
            self.evidence["command execution result"],
            []
        )

    def test_continuation_names_missing_files(self):
        completion = {
            "success": False,
            "missing": [
                "uninspected files: hello.py, hello2.py"
            ]
            }

        message = build_continuation_message(completion)

        self.assertIn("- hello.py", message)
        self.assertIn("- hello2.py", message)
        self.assertIn("read_file", message)

    def test_continuation_handles_generic_evidence(self):
         completion = {
            "success": False,
             "missing": ["test execution result"]
            }

         message = build_continuation_message(completion)

         self.assertIn("test execution result", message)
         self.assertIn("Continue working", message)

    def test_evidence_summary_preserves_execution_output(self):
        self.evidence["command execution result"].append({
             "tool": "run_command",
             "arguments": {"command": "python broken3.py"},
             "result": {
                "exit_code": 0,
                  "stdout": "",
                  "stderr": ""
             }
        })

        summary = build_evidence_summary(self.evidence)

        self.assertIn("EXECUTED COMMAND: python broken3.py", summary)
        self.assertIn("Exit code: 0", summary)
        self.assertIn("Stdout: ''", summary)
        self.assertIn(
            "does not by itself prove that tests passed",
            summary
         )

    def test_grounded_answer_uses_temporary_context(self):
        original_messages = list(forge.messages)

        completion = {
            "success": True,
            "missing": []
        }

        try:
            with patch.object(
            forge,
                "ask_model",
                return_value="Verified explanation"
            ) as mock_model:

              answer = forge.generate_grounded_answer(
                    "Explain hello.py",
                    self.evidence,
                    completion
                    )

            self.assertEqual(
                    answer,
                    "Verified explanation"
                )

            mock_model.assert_called_once()

            self.assertEqual(
                    forge.messages,
                    original_messages
                    )

        finally:
            forge.messages[:] = original_messages

class TestForgeIntegration(unittest.TestCase):
    def test_productive_continuation_exceeds_three_turns(self):
        from unittest.mock import patch
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import contextlib
        import io

        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            for filename in ("one.py", "two.py", "three.py"):
                (workspace / filename).write_text("print('hello')\n")
            responses = iter([
                '{"tool":"search_files","arguments":{"query":"hello"}}',
                "I found three files.",
                '{"tool":"read_file","arguments":{"filename":"one.py"}}',
                "Read one.",
                '{"tool":"read_file","arguments":{"filename":"two.py"}}',
                "Read two.",
                '{"tool":"read_file","arguments":{"filename":"three.py"}}',
                "Read three.",
                "Verified: three files contain hello.",
            ])
            original_messages = list(forge.messages)
            try:
                with patch.object(forge, "WORKSPACE", workspace), patch.object(
                    forge, "ask_model", side_effect=lambda: next(responses)
                ), contextlib.redirect_stdout(io.StringIO()):
                    result = forge.run_task(
                        "find where hello is used, inspect the relevant files", max_steps=20
                    )
                self.assertTrue(result["completion"]["success"])
                self.assertEqual(result["steps"], 4)
                self.assertIn("Verified: three files", result["answer"])
            finally:
                forge.messages[:] = original_messages

    def test_test_evidence_requires_test_command(self):
        evidence = {k: [] for k in (
            "search results", "relevant file contents", "test execution result",
            "command execution result", "workspace file listing"
        )}
        forge.record_evidence(evidence, "run_command", {"command": "python hello.py"},
                              {"exit_code": 0, "stdout": "all tests passed", "stderr": ""})
        self.assertFalse(evidence["test execution result"])
        forge.record_evidence(evidence, "run_command", {"command": "python -m unittest"},
                              {"exit_code": 0, "stdout": "OK", "stderr": ""})
        self.assertEqual(len(evidence["test execution result"]), 1)

    def test_unknown_request_is_not_auto_verified(self):
        evidence = {k: [] for k in (
            "search results", "relevant file contents", "test execution result",
            "command execution result", "workspace file listing"
        )}
        result = forge.check_success(forge.define_success("build an application"), evidence)
        self.assertFalse(result["success"])

if __name__ == "__main__":
    unittest.main()
