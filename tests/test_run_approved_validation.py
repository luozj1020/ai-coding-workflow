import importlib.util
import io
import json
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-approved-validation.py"
SPEC = importlib.util.spec_from_file_location("run_approved_validation", SCRIPT)
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


class ApprovedValidationTests(unittest.TestCase):
    def test_audit_extracts_commands_without_returning_bodies(self):
        commands, summary = validation.extract_commands(
            "```validation\npython -m unittest tests.test_one\ngit diff --check\n```\n"
        )
        self.assertEqual(len(commands), 2)
        self.assertEqual(summary["accepted"], 2)
        self.assertEqual(summary["first_launcher"], "python")
        self.assertNotIn("commands", summary)

    def test_unsafe_and_oversized_commands_fail_closed(self):
        commands, summary = validation.extract_commands(
            "```validation\necho ok; echo unsafe\n" + "x" * 501 + "\ntrue\n```\n"
        )
        self.assertEqual(commands, ["true"])
        self.assertEqual(summary["unsafe"], 1)
        self.assertEqual(summary["oversized"], 1)

    def test_run_executes_only_frozen_shell_free_command(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = root / "CLAUDE_TASK_CARD.md"
            command = shlex.join([sys.executable, "--version"])
            card.write_text(f"```validation\n{command}\n```\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                result = validation.main(["run", "--task-card", str(card)])
            self.assertEqual(result, 0)
            self.assertIn("validation_index=1 status=finished exit_code=0", output.getvalue())

    def test_run_refuses_card_when_any_command_is_unsafe(self):
        with tempfile.TemporaryDirectory() as raw:
            card = Path(raw) / "CLAUDE_TASK_CARD.md"
            card.write_text("```validation\ntrue\necho bad; false\n```\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                result = validation.main(["run", "--task-card", str(card)])
            self.assertEqual(result, 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
