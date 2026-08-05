import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-review-packet.py"
SPARK_SUMMARY_SCRIPT = ROOT / "scripts" / "build-spark-summary-capsule.py"
VERIFY_CAPSULE_SCRIPT = ROOT / "scripts" / "verify-evidence-capsule.py"


class ReviewToolCapsuleTests(unittest.TestCase):
    def make_run(
        self, root: Path, hunks: int = 1, payload_bytes: int = 0,
    ) -> tuple[Path, Path]:
        run_dir = root / "run"
        run_dir.mkdir()
        task = run_dir / "task-card-test.md"
        task.write_text(
            "# Task\n\n## Handoff Contract\n\n- Must do: update worker\n",
            encoding="utf-8",
        )
        diff = run_dir / "builder.diff"
        parts = []
        for index in range(hunks):
            parts.extend([
                "diff --git a/src/file-{0}.py b/src/file-{0}.py".format(index),
                "--- a/src/file-{0}.py".format(index),
                "+++ b/src/file-{0}.py".format(index),
                "@@ -1 +1 @@",
                "-old = {0}".format(index),
                "+new = {0}  # {1}".format(index, "x" * payload_bytes),
            ])
        diff.write_text("\n".join(parts) + "\n", encoding="utf-8")
        return run_dir, task

    def test_compact_receipt_references_full_artifacts_and_recommends_spark_for_complex_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, task = self.make_run(root, hunks=12, payload_bytes=900)
            packet = root / "review-packet.json"
            prompt = root / "review-prompt.md"
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT), str(run_dir),
                    "--task-card", str(task),
                    "--diff-file", str(run_dir / "builder.diff"),
                    "--output", str(packet),
                    "--prompt-output", str(prompt),
                ],
                text=True, encoding="utf-8", capture_output=True, check=True,
            )

            receipt = json.loads(result.stdout)
            full = json.loads(packet.read_text(encoding="utf-8"))
            self.assertEqual(receipt["kind"], "aiwf-review-packet-capsule")
            self.assertEqual(receipt["review_packet_path"], str(packet.resolve()))
            self.assertEqual(receipt["review_prompt_path"], str(prompt.resolve()))
            self.assertEqual(receipt["diff_hunk_count"], len(full["diff_hunks"]))
            self.assertNotIn("diff_hunks", receipt)
            self.assertNotIn("task_summary", receipt)
            self.assertTrue(receipt["compression_route"]["spark_recommended"])
            self.assertEqual(receipt["compression_route"]["spark_mode"], "postflight-bundle")
            self.assertIn("many-diff-hunks", receipt["compression_route"]["reason_codes"])
            self.assertFalse(receipt["compression_route"]["spark_can_authorize_acceptance"])
            tool_request = receipt["compression_route"]["tool_request"]
            self.assertEqual(tool_request["input_artifact"]["path"], str(packet.resolve()))
            self.assertEqual(
                tool_request["input_artifact"]["sha256"], receipt["evidence"]["sha256"]
            )
            self.assertEqual(tool_request["result_contract"], "advisory-summary-only")
            self.assertEqual(
                tool_request["argv"],
                [
                    "bash", "ai/run-codex-spark.sh", str(task.resolve()),
                    "--mode", "postflight-bundle",
                    "--artifact", str(packet.resolve()),
                    "--result-mode", "direct",
                    "--execution-env", "host",
                ],
            )
            self.assertLessEqual(len(result.stdout.encode("utf-8")), 4097)
            self.assertTrue(prompt.is_file())
            self.assertLess(prompt.stat().st_size, 2048)
            self.assertNotIn("+new =", prompt.read_text(encoding="utf-8"))
            self.assertTrue(receipt["transfer_metrics"]["within_limit"])

    def test_legacy_stdout_remains_explicitly_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, task = self.make_run(root)
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT), str(run_dir),
                    "--task-card", str(task),
                    "--diff-file", str(run_dir / "builder.diff"),
                    "--stdout-mode", "legacy",
                ],
                text=True, encoding="utf-8", capture_output=True, check=True,
            )
            self.assertIn("Review packet:", result.stdout)
            self.assertIn("Prompt bytes:", result.stdout)

    def test_stdout_off_writes_packet_without_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, task = self.make_run(root)
            packet = root / "packet.json"
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT), str(run_dir),
                    "--task-card", str(task),
                    "--diff-file", str(run_dir / "builder.diff"),
                    "--output", str(packet),
                    "--stdout-mode", "off",
                ],
                text=True, encoding="utf-8", capture_output=True, check=True,
            )
            self.assertEqual(result.stdout, "")
            self.assertTrue(packet.is_file())

    def test_small_multi_hunk_packet_stays_local_when_savings_are_low(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, task = self.make_run(root, hunks=12)
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT), str(run_dir),
                    "--task-card", str(task),
                    "--diff-file", str(run_dir / "builder.diff"),
                ],
                text=True, encoding="utf-8", capture_output=True, check=True,
            )
            receipt = json.loads(result.stdout)
            self.assertFalse(receipt["compression_route"]["spark_recommended"])
            self.assertIn("many-diff-hunks", receipt["compression_route"]["reason_codes"])
            self.assertLess(
                receipt["compression_route"]["estimated_codex_bytes_saved"],
                receipt["compression_route"]["minimum_savings_for_spark"],
            )

    def test_spark_postflight_output_is_reduced_to_bounded_advisory_capsule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spark_stdout = root / "spark.stdout"
            capsule_path = root / "spark-capsule.json"
            sections = []
            for heading in (
                "Decision Summary", "Risk Flags", "Scope and Boundaries",
                "Acceptance Matrix", "Evidence Conflicts",
                "Required Codex Decisions", "Recommended Next Action",
            ):
                sections.extend([f"## {heading}", "detail " * 1000])
            spark_stdout.write_text(
                "\n".join([
                    "spark_protocol=aiwf-spark-stdout-v1",
                    "spark_status=started",
                    *sections,
                    "spark_output_truncated=no",
                    "spark_model_response_received=yes",
                    "spark_failure_class=none",
                    "spark_status=success",
                    "spark_protocol_end=aiwf-spark-stdout-v1",
                ]),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable, str(SPARK_SUMMARY_SCRIPT), str(spark_stdout),
                    "--output", str(capsule_path),
                ],
                text=True, encoding="utf-8", capture_output=True, check=True,
            )
            # The file is the durable tool contract.  Some Windows runners do
            # not preserve captured stdout for this nested subprocess even
            # though the command and artifact write complete successfully.
            capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
            self.assertTrue(capsule["envelope"]["complete"])
            self.assertEqual(capsule["envelope"]["terminal_status"], "success")
            self.assertEqual(capsule["section_count"], 7)
            self.assertTrue(capsule["advisory_only"])
            self.assertFalse(capsule["spark_can_authorize_acceptance"])
            self.assertLessEqual(capsule_path.stat().st_size, 4097)
            self.assertLess(capsule_path.stat().st_size, spark_stdout.stat().st_size)
            self.assertEqual(capsule["source"]["path"], str(spark_stdout.resolve()))

    def test_capsule_verifier_fails_closed_after_diff_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, task = self.make_run(root)
            capsule_path = root / "capsule.json"
            subprocess.run(
                [
                    sys.executable, str(SCRIPT), str(run_dir),
                    "--task-card", str(task),
                    "--diff-file", str(run_dir / "builder.diff"),
                    "--capsule-output", str(capsule_path),
                    "--stdout-mode", "off",
                ],
                text=True, encoding="utf-8", capture_output=True, check=True,
            )
            valid = subprocess.run(
                [sys.executable, str(VERIFY_CAPSULE_SCRIPT), str(capsule_path)],
                text=True, encoding="utf-8", capture_output=True,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertTrue(json.loads(valid.stdout)["valid"])

            (run_dir / "builder.diff").write_text("replaced\n", encoding="utf-8")
            invalid = subprocess.run(
                [sys.executable, str(VERIFY_CAPSULE_SCRIPT), str(capsule_path)],
                text=True, encoding="utf-8", capture_output=True,
            )
            self.assertEqual(invalid.returncode, 2)
            receipt = json.loads(invalid.stdout)
            self.assertFalse(receipt["valid"])
            self.assertIn("diff", receipt["mismatches"])


if __name__ == "__main__":
    unittest.main()
