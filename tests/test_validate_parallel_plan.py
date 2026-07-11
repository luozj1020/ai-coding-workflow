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


# ---------------------------------------------------------------------------
# Test: Dispatch constraint validation
# ---------------------------------------------------------------------------

class TestDispatchConstraints(unittest.TestCase):
    """Test deterministic dispatch constraints: write scope overlap, owned
    contracts, base commit, and validation ownership."""

    def _make_task_card(self, path: pathlib.Path, gate_fields: dict):
        """Write a task card with given Parallel Execution Gate fields."""
        rows = ["| Field | Value |", "|-------|-------|"]
        for k, v in gate_fields.items():
            rows.append(f"| {k} | {v} |")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Task\n\n## Parallel Execution Gate\n\n" + "\n".join(rows) + "\n",
            encoding="utf-8",
        )

    def _make_plan_with_cards(self, tmp: pathlib.Path, tasks_config: list[dict]) -> pathlib.Path:
        """Create a plan JSON and task card files from a list of task configs.

        Each config: {"id": str, "depends_on": list, "gate": dict}
        """
        plan = {
            "schema_version": 1,
            "group_id": "dispatch-test",
            "max_concurrency": 2,
            "failure_policy": "skip-dependents",
            "tasks": [],
        }
        for tc in tasks_config:
            card_name = f"cards/{tc['id']}.md"
            plan["tasks"].append({
                "id": tc["id"],
                "task_card": card_name,
                "depends_on": tc.get("depends_on", []),
            })
            self._make_task_card(tmp / card_name, tc.get("gate", {}))

        plan_path = tmp / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return plan_path

    def _run_dispatch_validation(self, plan_path: pathlib.Path, base_commit: str = None):
        """Run validate_dispatch_constraints via subprocess."""
        cmd = [python_exe(), str(SCRIPT), "--plan", str(plan_path), "--validate-dispatch"]
        if base_commit:
            cmd.extend(["--expected-base-commit", base_commit])
        return subprocess.run(
            cmd,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

    def test_parent_child_write_scope_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = self._make_plan_with_cards(tmp_path, [
                {"id": "t1", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/auth",
                    "Owned contracts": "",
                    "Base commit": "abc123",
                    "Validation owner": "t1",
                    "Validation command": "pytest tests/t1",
                }},
                {"id": "t2", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/auth/login.py",
                    "Owned contracts": "",
                    "Base commit": "abc123",
                    "Validation owner": "t2",
                    "Validation command": "pytest tests/t2",
                }},
            ])
            result = self._run_dispatch_validation(plan_path)
            self.assertEqual(result.returncode, 1, f"expected failure\nstdout: {result.stdout}\nstderr: {result.stderr}")
            self.assertIn("write scope overlap", result.stderr)
            self.assertIn("Serial fallback recommended", result.stderr)

    def test_exact_write_scope_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = self._make_plan_with_cards(tmp_path, [
                {"id": "t1", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/shared.py",
                    "Base commit": "abc123",
                    "Validation owner": "t1",
                    "Validation command": "pytest",
                }},
                {"id": "t2", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/shared.py",
                    "Base commit": "abc123",
                    "Validation owner": "t2",
                    "Validation command": "pytest",
                }},
            ])
            result = self._run_dispatch_validation(plan_path)
            self.assertEqual(result.returncode, 1)
            self.assertIn("write scope overlap", result.stderr)

    def test_non_overlapping_scopes_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = self._make_plan_with_cards(tmp_path, [
                {"id": "t1", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/auth",
                    "Base commit": "abc123",
                    "Validation owner": "t1",
                    "Validation command": "pytest tests/t1",
                }},
                {"id": "t2", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/api",
                    "Base commit": "abc123",
                    "Validation owner": "t2",
                    "Validation command": "pytest tests/t2",
                }},
            ])
            result = self._run_dispatch_validation(plan_path)
            self.assertEqual(result.returncode, 0, f"expected pass\nstderr: {result.stderr}")

    def test_owned_contract_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = self._make_plan_with_cards(tmp_path, [
                {"id": "t1", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/a",
                    "Owned contracts": "user-auth-api",
                    "Base commit": "abc123",
                    "Validation owner": "t1",
                    "Validation command": "pytest",
                }},
                {"id": "t2", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/b",
                    "Owned contracts": "user-auth-api",
                    "Base commit": "abc123",
                    "Validation owner": "t2",
                    "Validation command": "pytest",
                }},
            ])
            result = self._run_dispatch_validation(plan_path)
            self.assertEqual(result.returncode, 1)
            self.assertIn("owned contract overlap", result.stderr)
            self.assertIn("user-auth-api", result.stderr)

    def test_base_commit_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = self._make_plan_with_cards(tmp_path, [
                {"id": "t1", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/a",
                    "Base commit": "aaa111",
                    "Validation owner": "t1",
                    "Validation command": "pytest",
                }},
                {"id": "t2", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/b",
                    "Base commit": "bbb222",
                    "Validation owner": "t2",
                    "Validation command": "pytest",
                }},
            ])
            result = self._run_dispatch_validation(plan_path)
            self.assertEqual(result.returncode, 1)
            self.assertIn("base commit mismatch", result.stderr)

    def test_expected_base_commit_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = self._make_plan_with_cards(tmp_path, [
                {"id": "t1", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/a",
                    "Base commit": "aaa111",
                    "Validation owner": "t1",
                    "Validation command": "pytest",
                }},
                {"id": "t2", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/b",
                    "Base commit": "aaa111",
                    "Validation owner": "t2",
                    "Validation command": "pytest",
                }},
            ])
            result = self._run_dispatch_validation(plan_path, base_commit="expected999")
            self.assertEqual(result.returncode, 1)
            self.assertIn("base commit mismatch", result.stderr)
            self.assertIn("expected999", result.stderr)

    def test_missing_validation_owner_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = self._make_plan_with_cards(tmp_path, [
                {"id": "t1", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/a",
                    "Base commit": "abc123",
                    # No Validation owner or command
                }},
                {"id": "t2", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/b",
                    "Base commit": "abc123",
                    "Validation owner": "t2",
                    "Validation command": "pytest",
                }},
            ])
            result = self._run_dispatch_validation(plan_path)
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing validation owner", result.stderr)

    def test_serial_fallback_contains_deterministic_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = self._make_plan_with_cards(tmp_path, [
                {"id": "task-a", "gate": {
                    "Allowed files/modules": "src/shared",
                    "Base commit": "abc123",
                }},
                {"id": "task-b", "depends_on": ["task-a"], "gate": {
                    "Allowed files/modules": "src/shared/sub",
                    "Base commit": "abc123",
                }},
            ])
            result = self._run_dispatch_validation(plan_path)
            self.assertEqual(result.returncode, 1)
            # Serial fallback should list task order
            self.assertIn("task-a", result.stderr)
            self.assertIn("task-b", result.stderr)
            self.assertIn("Serial fallback recommended", result.stderr)

    def test_empty_contracts_no_overlap_error(self):
        """Empty owned contracts should not trigger overlap errors."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            plan_path = self._make_plan_with_cards(tmp_path, [
                {"id": "t1", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/a",
                    "Owned contracts": "",
                    "Base commit": "abc123",
                    "Validation owner": "t1",
                    "Validation command": "pytest",
                }},
                {"id": "t2", "gate": {
                    "Parallel allowed?": "yes",
                    "Allowed files/modules": "src/b",
                    "Owned contracts": "",
                    "Base commit": "abc123",
                    "Validation owner": "t2",
                    "Validation command": "pytest",
                }},
            ])
            result = self._run_dispatch_validation(plan_path)
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            self.assertNotIn("owned contract overlap", result.stderr)


# ---------------------------------------------------------------------------
# Test: Extract gate field helper
# ---------------------------------------------------------------------------

class TestExtractGateField(unittest.TestCase):
    """Test the gate field extraction from markdown task cards."""

    def test_extract_allowed_files(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("vpp", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        content = (
            "# Task\n\n"
            "## Parallel Execution Gate\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Parallel allowed? | yes |\n"
            "| Allowed files/modules | src/auth, src/api |\n"
        )
        result = mod.extract_gate_field(content, "Parallel Execution Gate", "Allowed files/modules")
        self.assertEqual(result, "src/auth, src/api")

    def test_extract_missing_field_returns_empty(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("vpp", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        content = "# Task\n\n## Parallel Execution Gate\n\n| Field | Value |\n|-------|-------|\n"
        result = mod.extract_gate_field(content, "Parallel Execution Gate", "Nonexistent field")
        self.assertEqual(result, "")

    def test_is_parent_or_child(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("vpp", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        self.assertTrue(mod.is_parent_or_child("src/auth", "src/auth/login.py"))
        self.assertTrue(mod.is_parent_or_child("src/auth/login.py", "src/auth"))
        self.assertTrue(mod.is_parent_or_child("src/auth", "src/auth"))
        self.assertFalse(mod.is_parent_or_child("src/auth", "src/api"))
        self.assertFalse(mod.is_parent_or_child("src/a", "src/abc"))  # not prefix match


if __name__ == "__main__":
    unittest.main()
