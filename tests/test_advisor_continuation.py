import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "prepare-advisor-continuation.py"
spec = importlib.util.spec_from_file_location("prepare_advisor_continuation", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class AdvisorContinuationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.output = Path(self.tmp) / "output"
        self.output.mkdir()
        self.worktree = Path(self.tmp) / "worktree"
        self.worktree.mkdir()

    def _classification(self, eligible=True, reason=None):
        return {
            "advisor_continuation_eligible": eligible,
            "advisor_rejection_reason": reason,
            "failure_class": "semantic-blocker" if eligible else reason,
            "interaction_state": "useful-progress",
        }

    def _write_class(self, eligible=True, reason=None):
        p = Path(self.tmp) / "classification.json"
        p.write_text(json.dumps(self._classification(eligible, reason)))
        return p

    def _evidence(self, name="evidence.txt", content="some evidence"):
        p = self.worktree / name
        p.write_text(content)
        return str(p)

    def _run(self, **overrides):
        defaults = dict(
            task_id="test-task",
            phase="implement",
            worktree=str(self.worktree),
            question="What should I do about X?",
            evidence=[],
            forbidden=[],
            completed_work="Did A and B",
            response_file=None,
            output_dir=str(self.output),
        )
        defaults.update(overrides)
        class_file = self._write_class(
            eligible="not-eligible" not in str(defaults.get("question", "")),
        )
        defaults["classification_file"] = class_file
        # Convert to argv
        argv = []
        for k, v in defaults.items():
            if v is None:
                continue
            flag = f"--{k.replace('_', '-')}"
            if isinstance(v, bool):
                if v:
                    argv.append(flag)
            elif isinstance(v, list):
                for item in v:
                    argv.extend([flag, str(item)])
            else:
                argv.extend([flag, str(v)])
        return mod.main(argv)

    def test_eligible_writes_packet(self):
        ev = self._evidence()
        rc = self._run(evidence=[ev])
        self.assertEqual(rc, 0)
        self.assertTrue((self.output / "advisor-packet.json").exists())
        self.assertTrue((self.output / "advisor-packet.md").exists())
        packet = json.loads((self.output / "advisor-packet.json").read_text())
        self.assertEqual(packet["task_id"], "test-task")
        self.assertIn("blocker_question", packet)

    def test_rejected_writes_decision_exits_2(self):
        class_file = self._write_class(eligible=False, reason="no-useful-evidence")
        rc = mod.main([
            "--classification-file", str(class_file),
            "--task-id", "t1", "--phase", "p1",
            "--worktree", str(self.worktree),
            "--question", "What now?",
            "--completed-work", "done",
            "--output-dir", str(self.output),
        ])
        self.assertEqual(rc, 2)
        self.assertTrue((self.output / "advisor-decision.json").exists())
        decision = json.loads((self.output / "advisor-decision.json").read_text())
        self.assertFalse(decision["eligible"])
        self.assertEqual(decision["reason"], "no-useful-evidence")
        self.assertFalse((self.output / "advisor-packet.json").exists())

    def test_response_creates_continuation_card(self):
        response = self.worktree / "response.txt"
        response.write_text("Do X then Y")
        ev = self._evidence()
        rc = self._run(evidence=[ev], response_file=str(response))
        self.assertEqual(rc, 0)
        self.assertTrue((self.output / "advisor-decision.json").exists())
        self.assertTrue((self.output / "advisor-continuation-card.md").exists())
        card = (self.output / "advisor-continuation-card.md").read_text()
        self.assertIn("same-worktree retry", card)
        self.assertIn("Do X then Y", card)
        self.assertIn("CLAUDE_PROGRESS.md", card)
        self.assertIn("CLAUDE_REPORT.md", card)

    def test_outside_evidence_rejected(self):
        outside = Path(self.tmp) / "outside" / "file.txt"
        outside.parent.mkdir()
        outside.write_text("outside")
        class_file = self._write_class(eligible=True)
        rc = mod.main([
            "--classification-file", str(class_file),
            "--task-id", "t1", "--phase", "p1",
            "--worktree", str(self.worktree),
            "--question", "What now?",
            "--evidence", str(outside),
            "--completed-work", "done",
            "--output-dir", str(self.output),
        ])
        self.assertEqual(rc, 2)
        decision = json.loads((self.output / "advisor-decision.json").read_text())
        self.assertIn("outside-scope", decision["reason"])

    def test_oversized_evidence_rejected(self):
        big = self.worktree / "big.txt"
        big.write_text("x" * (101 * 1024))  # 101KB
        class_file = self._write_class(eligible=True)
        rc = mod.main([
            "--classification-file", str(class_file),
            "--task-id", "t1", "--phase", "p1",
            "--worktree", str(self.worktree),
            "--question", "What now?",
            "--evidence", str(big),
            "--completed-work", "done",
            "--output-dir", str(self.output),
        ])
        self.assertEqual(rc, 2)
        decision = json.loads((self.output / "advisor-decision.json").read_text())
        self.assertIn("oversized", decision["reason"])

    def test_empty_question_rejected(self):
        class_file = self._write_class(eligible=True)
        rc = mod.main([
            "--classification-file", str(class_file),
            "--task-id", "t1", "--phase", "p1",
            "--worktree", str(self.worktree),
            "--question", "   ",
            "--completed-work", "done",
            "--output-dir", str(self.output),
        ])
        self.assertEqual(rc, 2)
        decision = json.loads((self.output / "advisor-decision.json").read_text())
        self.assertEqual(decision["reason"], "empty-question")

    def test_empty_response_rejected(self):
        response = self.worktree / "empty.txt"
        response.write_text("   ")
        class_file = self._write_class(eligible=True)
        rc = mod.main([
            "--classification-file", str(class_file),
            "--task-id", "t1", "--phase", "p1",
            "--worktree", str(self.worktree),
            "--question", "What now?",
            "--completed-work", "done",
            "--response-file", str(response),
            "--output-dir", str(self.output),
        ])
        self.assertEqual(rc, 2)
        decision = json.loads((self.output / "advisor-decision.json").read_text())
        self.assertEqual(decision["reason"], "empty-response")

    def test_required_packet_fields(self):
        ev = self._evidence()
        rc = self._run(evidence=[ev], forbidden=["/forbidden/path"])
        self.assertEqual(rc, 0)
        packet = json.loads((self.output / "advisor-packet.json").read_text())
        for key in ("task_id", "phase", "worktree", "blocker_question",
                     "evidence", "forbidden_paths", "completed_work", "classification",
                     "call_cap", "stop_conditions"):
            self.assertIn(key, packet)
        self.assertEqual(packet["call_cap"], 1)
        self.assertTrue(packet["stop_conditions"])

    def test_unified_cli_and_installer_registration(self):
        self.assertIn('"advisor-continuation":"prepare-advisor-continuation.py"',
                      (ROOT / "scripts" / "aiwf.py").read_text(encoding="utf-8"))
        self.assertIn('("prepare-advisor-continuation.py", "ai/prepare-advisor-continuation.py")',
                      (ROOT / "scripts" / "install_workflow.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
