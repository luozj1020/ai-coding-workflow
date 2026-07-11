import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-parallel-plan.py"


def python_exe() -> str:
    return "python3" if os.name != "nt" else "python"


def write_plan(tmp: pathlib.Path, plan: dict, name: str = "plan.json") -> pathlib.Path:
    """Write a plan JSON file and create stub task card files for each task."""
    path = tmp / name
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    for task in plan.get("tasks", []):
        if any(ch in task.get("task_card", "") for ch in ("\t", "\n", "\r")):
            continue
        card = tmp / task["task_card"]
        card.parent.mkdir(parents=True, exist_ok=True)
        if not card.exists():
            card.write_text(f"# Task {task['id']}\n", encoding="utf-8")
    return path


def run_validator(plan_path: pathlib.Path):
    """Run the validator and return (returncode, stdout, stderr)."""
    return subprocess.run(
        [python_exe(), str(SCRIPT), "--plan", str(plan_path)],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def minimal_plan(**overrides) -> dict:
    """Return a minimal valid plan dict, with overrides applied."""
    plan = {
        "schema_version": 1,
        "group_id": "test-group",
        "max_concurrency": 2,
        "failure_policy": "skip-dependents",
        "tasks": [
            {"id": "task-a", "task_card": "task-a.md", "depends_on": []},
        ],
    }
    plan.update(overrides)
    return plan


def fork_join_plan() -> dict:
    """A -> B, A -> C, B -> D, C -> D (diamond)."""
    return {
        "schema_version": 1,
        "group_id": "diamond",
        "max_concurrency": 2,
        "failure_policy": "skip-dependents",
        "tasks": [
            {"id": "task-a", "task_card": "task-a.md", "depends_on": []},
            {"id": "task-b", "task_card": "task-b.md", "depends_on": ["task-a"]},
            {"id": "task-c", "task_card": "task-c.md", "depends_on": ["task-a"]},
            {"id": "task-d", "task_card": "task-d.md", "depends_on": ["task-b", "task-c"]},
        ],
    }


# ---------------------------------------------------------------------------
# Test 1: Validator accepts valid plan, preserves resolved card path with
#          empty depends_on.
# ---------------------------------------------------------------------------

class TestValidatorAcceptsValidPlan(unittest.TestCase):
    def test_valid_plan_accepted_and_emits_tsv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = write_plan(tmp_path, minimal_plan())
            result = run_validator(plan_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.strip().splitlines()
            # META records: schema_version, group_id, max_concurrency, failure_policy, plan_path, plan_dir
            meta_lines = [l for l in lines if l.startswith("META\t")]
            task_lines = [l for l in lines if l.startswith("TASK\t")]
            self.assertEqual(len(meta_lines), 6)
            self.assertEqual(len(task_lines), 1)
            fields = task_lines[0].split("\t")
            # TASK\t<id>\t<task_card>\t<deps_csv>\t<resolved>
            self.assertEqual(fields[0], "TASK")
            self.assertEqual(fields[1], "task-a")
            self.assertEqual(fields[2], "task-a.md")
            self.assertEqual(fields[3], "__none__")  # empty depends_on → sentinel
            # resolved path must exist and contain the task card
            self.assertTrue(fields[4].endswith("task-a.md") or fields[4].endswith("task-a.md"),
                            f"resolved path should end with task-a.md, got {fields[4]}")

    def test_empty_depends_on_preserves_resolved_path(self):
        """Test 1 explicitly: independent task's resolved card path is preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = write_plan(tmp_path, minimal_plan())
            result = run_validator(plan_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            task_lines = [l for l in result.stdout.splitlines() if l.startswith("TASK\t")]
            fields = task_lines[0].split("\t")
            resolved = pathlib.Path(fields[4])
            self.assertTrue(resolved.is_file(),
                            f"resolved card path {resolved} should exist on disk")


# ---------------------------------------------------------------------------
# Test 2: Validator rejects invalid plans before dispatch.
# ---------------------------------------------------------------------------

class TestValidatorRejectsInvalid(unittest.TestCase):

    def _assert_rejects(self, plan_dict, expected_fragment, plan_name="bad.json"):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = write_plan(tmp_path, plan_dict, name=plan_name)
            result = run_validator(plan_path)
            self.assertNotEqual(result.returncode, 0,
                                f"expected failure for: {expected_fragment}\nstdout: {result.stdout}")
            self.assertIn(expected_fragment, result.stderr,
                          f"stderr should contain {expected_fragment!r}, got: {result.stderr}")

    def test_unknown_top_level_key(self):
        self._assert_rejects(
            {**minimal_plan(), "bogus_key": 42},
            "unknown top-level keys",
        )

    def test_unsupported_schema_version(self):
        self._assert_rejects(
            {**minimal_plan(), "schema_version": 99},
            "unsupported schema_version",
        )

    def test_missing_schema_version(self):
        plan = minimal_plan()
        del plan["schema_version"]
        self._assert_rejects(plan, "missing required key: schema_version")

    def test_missing_task_card_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan = minimal_plan()
            plan["tasks"][0]["task_card"] = "nonexistent.md"
            plan_path = tmp_path / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            result = run_validator(plan_path)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("task_card not found", result.stderr)

    def test_duplicate_task_id(self):
        plan = {
            **minimal_plan(),
            "tasks": [
                {"id": "dup", "task_card": "a.md", "depends_on": []},
                {"id": "dup", "task_card": "b.md", "depends_on": []},
            ],
        }
        self._assert_rejects(plan, "duplicate id")

    def test_duplicate_task_card(self):
        plan = {
            **minimal_plan(),
            "tasks": [
                {"id": "t1", "task_card": "shared.md", "depends_on": []},
                {"id": "t2", "task_card": "shared.md", "depends_on": []},
            ],
        }
        self._assert_rejects(plan, "duplicate task_card")

    def test_duplicate_dependency_entry(self):
        """Same dependency listed twice in depends_on must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan = {
                "schema_version": 1,
                "group_id": "dup-dep",
                "failure_policy": "skip-dependents",
                "tasks": [
                    {"id": "base", "task_card": "base.md", "depends_on": []},
                    {"id": "child", "task_card": "child.md", "depends_on": ["base", "base"]},
                ],
            }
            plan_path = write_plan(tmp_path, plan)
            result = run_validator(plan_path)
            self.assertNotEqual(result.returncode, 0,
                                "duplicate depends_on entries should be rejected")
            self.assertIn("duplicate", result.stderr.lower())

    def test_self_dependency(self):
        plan = {
            **minimal_plan(),
            "tasks": [
                {"id": "loop", "task_card": "loop.md", "depends_on": ["loop"]},
            ],
        }
        self._assert_rejects(plan, "self-dependency")

    def test_unknown_dependency(self):
        plan = {
            **minimal_plan(),
            "tasks": [
                {"id": "orphan", "task_card": "orphan.md", "depends_on": ["ghost"]},
            ],
        }
        self._assert_rejects(plan, "unknown dependency")

    def test_cyclic_dependency(self):
        plan = {
            **minimal_plan(),
            "tasks": [
                {"id": "a", "task_card": "a.md", "depends_on": ["b"]},
                {"id": "b", "task_card": "b.md", "depends_on": ["a"]},
            ],
        }
        self._assert_rejects(plan, "dependency cycle")

    def test_tab_in_group_id_rejected(self):
        """Tabs in text fields would break TSV transport."""
        self._assert_rejects(
            {**minimal_plan(), "group_id": "bad\tgroup"},
            "TSV-unsafe characters",
        )

    def test_newline_in_group_id_rejected(self):
        self._assert_rejects(
            {**minimal_plan(), "group_id": "bad\ngroup"},
            "TSV-unsafe characters",
        )

    def test_control_char_in_task_id_rejected(self):
        plan = {
            **minimal_plan(),
            "tasks": [
                {"id": "bad\x01id", "task_card": "a.md", "depends_on": []},
            ],
        }
        self._assert_rejects(plan, "TSV-unsafe characters")

    def test_tab_in_task_card_rejected(self):
        """Tabs in task_card would break TSV transport."""
        plan = {
            **minimal_plan(),
            "tasks": [
                {"id": "ok", "task_card": "bad\tcard.md", "depends_on": []},
            ],
        }
        self._assert_rejects(plan, "TSV-unsafe characters")

    def test_boolean_concurrency_rejected(self):
        """isinstance(True, int) is True; validator must reject booleans explicitly."""
        self._assert_rejects(
            {**minimal_plan(), "max_concurrency": True},
            "not a boolean",
        )

    def test_zero_concurrency_rejected(self):
        self._assert_rejects(
            {**minimal_plan(), "max_concurrency": 0},
            "max_concurrency must be >= 1",
        )

    def test_unsupported_failure_policy(self):
        self._assert_rejects(
            {**minimal_plan(), "failure_policy": "stop-new"},
            "unsupported failure_policy",
        )


if __name__ == "__main__":
    unittest.main()
