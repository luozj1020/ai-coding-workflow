from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "scripts" / "build-recovery-delta.py"
CAPSULE = ROOT / "scripts" / "build-execution-capsule.py"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, text=True, capture_output=True, check=check,
    )


class RecoveryDeltaTests(unittest.TestCase):
    def write_card(self, root: Path) -> Path:
        card = root / "revision.md"
        card.write_text(
            "# Revision\n\n"
            "## Goal\n\nRepair the narrow implementation.\n\n"
            "## Task Mode\n\n| Field | Value |\n|---|---|\n| Mode | builder |\n\n"
            "## Scope\n\n- Write paths: src/example.py\n\n"
            "## Revision Delta\n\n- Restore the missing branch only.\n\n"
            "## Handoff Contract\n\n- Must do: preserve the existing public API.\n\n"
            "## Acceptance Criteria\n\n- [ ] The narrow branch is restored.\n\n"
            "## Validation Contract\n\n- Exact check: python -m pytest tests/test_example.py -q\n\n"
            "## Stop Conditions\n\n- Stop if public API scope changes.\n\n"
            "## Required Report\n\n- Changed paths and exact check result.\n",
            encoding="utf-8",
        )
        return card

    @staticmethod
    def write_classification(root: Path, failure_class: str, action: str, *, retry: bool = False) -> Path:
        path = root / "attempt.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "failure_class": failure_class,
            "recommended_action": action,
            "same_worktree_retry_eligible": retry,
            "untrusted_model_text": "do not copy this into the recovery delta",
        }), encoding="utf-8")
        return path

    def test_builds_receipt_bound_narrow_recovery_and_embeds_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_card(root)
            classification = self.write_classification(
                root, "model-no-progress", "narrow-and-redispatch-once",
            )
            delta = root / "delta.md"
            receipt = root / "delta.json"
            completed = run(
                str(RECOVERY), "--task-card", str(card),
                "--attempt-classification", str(classification),
                "--output", str(delta), "--receipt", str(receipt),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            text = delta.read_text(encoding="utf-8")
            value = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertIn("aiwf-recovery-delta-v1", text)
            self.assertIn("model-no-progress", text)
            self.assertNotIn("untrusted_model_text", text)
            self.assertNotIn("do not copy this", text)
            self.assertEqual(value["failure_class"], "model-no-progress")
            self.assertEqual(
                value["delta_sha256"],
                "sha256:" + hashlib.sha256(delta.read_bytes()).hexdigest(),
            )

            capsule = root / "capsule.md"
            capsule_receipt = root / "capsule.json"
            embedded = run(
                str(CAPSULE), "--task-card", str(card), "--output", str(capsule),
                "--mode", "bootstrap", "--require-complete-contract",
                "--recovery-delta", str(delta), "--recovery-delta-receipt", str(receipt),
                "--receipt", str(capsule_receipt),
            )
            self.assertEqual(embedded.returncode, 0, embedded.stderr)
            rendered = capsule.read_text(encoding="utf-8")
            capsule_value = json.loads(capsule_receipt.read_text(encoding="utf-8"))
            self.assertIn("## Bounded Recovery Delta", rendered)
            self.assertEqual(
                capsule_value["recovery_delta"]["failure_class"], "model-no-progress",
            )
            self.assertEqual(
                capsule_value["hard_contract_coverage"]["status"], "complete",
            )

    def test_refuses_transport_and_unspecified_revision_scope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_card(root)
            transport = self.write_classification(
                root, "transient-transport", "retry-same-worktree-once", retry=True,
            )
            failed = run(
                str(RECOVERY), "--task-card", str(card),
                "--attempt-classification", str(transport),
                "--output", str(root / "ignored.md"), "--receipt", str(root / "ignored.json"),
                check=False,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("cannot create a bounded recovery delta", failed.stderr)

            incomplete = root / "incomplete.md"
            incomplete.write_text("## Goal\n\nOnly a goal.\n", encoding="utf-8")
            no_scope = self.write_classification(
                root, "acknowledgement-only", "narrow-and-redispatch-once",
            )
            failed = run(
                str(RECOVERY), "--task-card", str(incomplete),
                "--attempt-classification", str(no_scope),
                "--output", str(root / "ignored2.md"), "--receipt", str(root / "ignored2.json"),
                check=False,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("needs Revision Delta or Required Revisions", failed.stderr)

    def test_capsule_rejects_mutated_recovery_delta(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_card(root)
            classification = self.write_classification(
                root, "report-evidence-mismatch", "narrow-and-redispatch-once",
            )
            delta = root / "delta.md"
            receipt = root / "delta.json"
            self.assertEqual(run(
                str(RECOVERY), "--task-card", str(card),
                "--attempt-classification", str(classification),
                "--output", str(delta), "--receipt", str(receipt),
            ).returncode, 0)
            delta.write_text(delta.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
            failed = run(
                str(CAPSULE), "--task-card", str(card), "--output", str(root / "out.md"),
                "--mode", "bootstrap", "--recovery-delta", str(delta),
                "--recovery-delta-receipt", str(receipt), check=False,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("does not match its bytes", failed.stderr)


if __name__ == "__main__":
    unittest.main()
