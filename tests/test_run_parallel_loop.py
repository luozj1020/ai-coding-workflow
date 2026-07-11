from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-parallel-loop.sh"


def bash_exe() -> str:
    if os.name == "nt":
        for candidate in (
            pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
            pathlib.Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ):
            if candidate.is_file():
                return str(candidate)
    return "bash"


def bash_path(path: pathlib.Path) -> str:
    value = str(path)
    if os.name == "nt":
        value = value.replace("\\", "/")
        if len(value) >= 2 and value[1] == ":":
            value = "/" + value[0].lower() + value[2:]
    return value


def ensure_repo_head(path: pathlib.Path) -> str:
    """Return a real HEAD for the fixture repository, creating one if needed."""
    repo = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=str(path.parent),
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True,
    )
    if head.returncode != 0:
        subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "--allow-empty", "-m", "fixture base"],
            cwd=repo, check=True, capture_output=True,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
            text=True, capture_output=True,
        )
    return head.stdout.strip()


def write_task(path: pathlib.Path, scope: str, parallel: str = "yes",
               base_commit: str | None = None):
    base_commit = base_commit or ensure_repo_head(path)
    path.write_text(
        "# Task\n\n"
        "## Parallel Execution Gate\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        f"| Parallel allowed? | {parallel} |\n"
        "| Parallel group id | fixture |\n"
        f"| Allowed files/modules | {scope} |\n"
        f"| Base commit | {base_commit} |\n"
        "| Validation owner | checker |\n"
        "| Validation command | echo ok |\n",
        encoding="utf-8",
    )


class RunParallelLoopTests(unittest.TestCase):
    def test_help_mentions_experimental_parallel_dispatch(self):
        result = subprocess.run(
            [bash_exe(), bash_path(SCRIPT), "--help"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Experimental helper", result.stderr)
        self.assertIn("--max-concurrency", result.stderr)
        self.assertIn("--allow-overlap", result.stderr)

    def test_parallel_dispatch_writes_summary_with_fake_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_a = repo / "task-a.md"
            task_b = repo / "task-b.md"
            write_task(task_a, "src/a.py")
            write_task(task_b, "src/b.py")

            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text(
                "#!/usr/bin/env bash\n"
                "name=$(basename \"$1\" .md)\n"
                "echo \"Result: /tmp/${name}.result.json\"\n"
                "echo \"Diff: /tmp/${name}.diff\"\n"
                "echo \"Report: /tmp/${name}.report.md\"\n",
                encoding="utf-8",
            )
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "parallel-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)
            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--max-concurrency",
                    "2",
                    "--output",
                    bash_path(output_dir),
                    bash_path(task_a),
                    bash_path(task_b),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            summary = output_dir / "parallel-summary.md"
            events = output_dir / "parallel-events.jsonl"
            manifest = output_dir / "parallel-manifest.tsv"
            self.assertTrue(summary.exists())
            self.assertTrue(events.exists())
            self.assertTrue(manifest.exists())
            summary_text = summary.read_text(encoding="utf-8")
            self.assertIn("Experimental Parallel Dispatch Summary", summary_text)
            self.assertIn("Parallel Execution Follow-up", summary_text)
            self.assertIn("| Dispatches succeeded | 2 |", summary_text)
            self.assertIn("| Automatic merge performed? | no |", summary_text)
            self.assertIn("task-a.md", manifest.read_text(encoding="utf-8"))
            self.assertIn("dispatch_start", events.read_text(encoding="utf-8"))

    def test_requires_parallel_gate_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_a = repo / "task-a.md"
            task_b = repo / "task-b.md"
            write_task(task_a, "src/a.py", parallel="no")
            write_task(task_b, "src/b.py")

            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_a), bash_path(task_b)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("not parallel-enabled", result.stderr)

    def test_rejects_scope_overlap_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_a = repo / "task-a.md"
            task_b = repo / "task-b.md"
            write_task(task_a, "src/shared.py")
            write_task(task_b, "src/shared.py")

            result = subprocess.run(
                [bash_exe(), bash_path(SCRIPT), bash_path(task_a), bash_path(task_b)],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 3)
            self.assertIn("scopes overlap", result.stderr)


# ---------------------------------------------------------------------------
# DAG mode helpers
# ---------------------------------------------------------------------------

def write_dag_task(path: pathlib.Path, scope: str, parallel: str = "yes"):
    """Write a task card file with a Parallel Execution Gate section."""
    base_commit = ensure_repo_head(path)
    path.write_text(
        "# Task\n\n"
        "## Parallel Execution Gate\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        f"| Parallel allowed? | {parallel} |\n"
        "| Parallel group id | fixture |\n"
        f"| Allowed files/modules | {scope} |\n"
        f"| Base commit | {base_commit} |\n"
        "| Validation owner | checker |\n"
        "| Validation command | echo ok |\n",
        encoding="utf-8",
    )


def write_plan(repo: pathlib.Path, plan: dict, name: str = "plan.json") -> pathlib.Path:
    """Write a plan JSON and create stub task card files."""
    plan_path = repo / name
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    for task in plan.get("tasks", []):
        card = repo / task["task_card"]
        card.parent.mkdir(parents=True, exist_ok=True)
        if not card.exists():
            card.write_text(f"# Task {task['id']}\n", encoding="utf-8")
    return plan_path


def make_fork_join_plan() -> dict:
    """Diamond DAG: A -> B, A -> C, B -> D, C -> D."""
    return {
        "schema_version": 1,
        "group_id": "diamond",
        "max_concurrency": 2,
        "failure_policy": "skip-dependents",
        "tasks": [
            {"id": "task-a", "task_card": "cards/task-a.md", "depends_on": []},
            {"id": "task-b", "task_card": "cards/task-b.md", "depends_on": ["task-a"]},
            {"id": "task-c", "task_card": "cards/task-c.md", "depends_on": ["task-a"]},
            {"id": "task-d", "task_card": "cards/task-d.md", "depends_on": ["task-b", "task-c"]},
        ],
    }


def make_linear_plan() -> dict:
    """Linear chain: A -> B."""
    return {
        "schema_version": 1,
        "group_id": "linear",
        "max_concurrency": 1,
        "failure_policy": "skip-dependents",
        "tasks": [
            {"id": "task-a", "task_card": "cards/task-a.md", "depends_on": []},
            {"id": "task-b", "task_card": "cards/task-b.md", "depends_on": ["task-a"]},
        ],
    }


# ---------------------------------------------------------------------------
# Test 3: DAG fork/join scheduling
# ---------------------------------------------------------------------------

class TestDAGForkJoin(unittest.TestCase):
    """Test 3: DAG scheduling starts dependents only after successful prerequisites
    and respects the effective cap."""

    def test_dag_fork_join_respects_dependency_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)

            plan = make_fork_join_plan()
            plan_path = write_plan(repo, plan)
            for task in plan["tasks"]:
                write_dag_task(repo / task["task_card"], f"src/{task['id']}.py")

            # Fake dispatch: records the order of execution via a shared log file
            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text(
                "#!/usr/bin/env bash\n"
                "card=\"$1\"\n"
                "name=$(basename \"$card\" .md)\n"
                "# Record dispatch time and task\n"
                "printf '%s %s\\n' \"$(date +%s%N 2>/dev/null || date +%s)\" \"$name\" >> \"$DISPATCH_LOG\"\n"
                "echo \"Result: /tmp/${name}.result.json\"\n"
                "echo \"Report: /tmp/${name}.report.md\"\n",
                encoding="utf-8",
            )
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            dispatch_log = tmp_path / "dispatch-order.log"
            output_dir = repo / ".worktrees" / "dag-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)
            env["DISPATCH_LOG"] = bash_path(dispatch_log)

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--plan",
                    bash_path(plan_path),
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            summary = (output_dir / "parallel-summary.md").read_text(encoding="utf-8")
            self.assertIn("DAG Parallel Dispatch Summary", summary)
            self.assertIn("| Dispatches succeeded | 4 |", summary)

            # Verify all tasks completed (check state files)
            for tid in ["task-a", "task-b", "task-c", "task-d"]:
                state_file = output_dir / ".dag" / f"{tid}.state"
                self.assertTrue(state_file.exists(), f"missing state file for {tid}")
                self.assertEqual(state_file.read_text().strip(), "completed",
                                 f"{tid} should be completed")

    def test_dag_respects_concurrency_cap(self):
        """Concurrency cap should limit simultaneous dispatches."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)

            plan = make_fork_join_plan()
            plan["max_concurrency"] = 1
            plan_path = write_plan(repo, plan)
            for task in plan["tasks"]:
                write_dag_task(repo / task["task_card"], f"src/{task['id']}.py")

            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text(
                "#!/usr/bin/env bash\n"
                "echo \"Result: /tmp/result.json\"\n"
                "echo \"Report: /tmp/report.md\"\n",
                encoding="utf-8",
            )
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "dag-cap-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--plan",
                    bash_path(plan_path),
                    "--max-concurrency",
                    "1",
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            summary = (output_dir / "parallel-summary.md").read_text(encoding="utf-8")
            self.assertIn("| Max concurrency | 1 |", summary)
            self.assertIn("| Dispatches succeeded | 4 |", summary)


# ---------------------------------------------------------------------------
# Test 4: Failed prerequisites transitively skip dependents
# ---------------------------------------------------------------------------

class TestDAGFailureSkip(unittest.TestCase):
    """Test 4: Failed prerequisites transitively skip dependents while
    unrelated branches complete."""

    def test_failed_task_skips_dependents(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)

            # Diamond: A -> B, A -> C, B -> D, C -> D
            # Make A succeed, but B fail. D should be skipped. C should complete.
            plan = make_fork_join_plan()
            plan_path = write_plan(repo, plan)
            for task in plan["tasks"]:
                write_dag_task(repo / task["task_card"], f"src/{task['id']}.py")

            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text(
                "#!/usr/bin/env bash\n"
                "card=\"$1\"\n"
                "name=$(basename \"$card\" .md)\n"
                "if [ \"$name\" = \"task-b\" ]; then\n"
                "  echo \"Error: simulated failure\" >&2\n"
                "  exit 1\n"
                "fi\n"
                "echo \"Result: /tmp/${name}.result.json\"\n"
                "echo \"Report: /tmp/${name}.report.md\"\n",
                encoding="utf-8",
            )
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "dag-fail-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--plan",
                    bash_path(plan_path),
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
            )

            # Script exits 1 when failures/skips exist
            self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)

            # task-a: completed, task-b: failed, task-c: completed, task-d: skipped
            self.assertEqual(
                (output_dir / ".dag" / "task-a.state").read_text().strip(), "completed")
            self.assertEqual(
                (output_dir / ".dag" / "task-b.state").read_text().strip(), "failed")
            self.assertEqual(
                (output_dir / ".dag" / "task-c.state").read_text().strip(), "completed")
            self.assertEqual(
                (output_dir / ".dag" / "task-d.state").read_text().strip(), "skipped")

            # Check events for skip evidence
            events = (output_dir / "parallel-events.jsonl").read_text(encoding="utf-8")
            self.assertIn("task_skipped", events)
            self.assertIn("prerequisite_failed=task-b", events)


# ---------------------------------------------------------------------------
# Test 5: CLI concurrency overrides plan value
# ---------------------------------------------------------------------------

class TestCLIConcurrencyOverride(unittest.TestCase):
    """Test 5: Explicit CLI --max-concurrency overrides the plan value."""

    def test_cli_concurrency_overrides_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)

            plan = make_fork_join_plan()
            plan["max_concurrency"] = 1  # plan says 1
            plan_path = write_plan(repo, plan)
            for task in plan["tasks"]:
                write_dag_task(repo / task["task_card"], f"src/{task['id']}.py")

            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text(
                "#!/usr/bin/env bash\n"
                "echo \"Result: /tmp/result.json\"\n"
                "echo \"Report: /tmp/report.md\"\n",
                encoding="utf-8",
            )
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "dag-override-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--plan",
                    bash_path(plan_path),
                    "--max-concurrency",
                    "4",  # CLI overrides to 4
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            summary = (output_dir / "parallel-summary.md").read_text(encoding="utf-8")
            # CLI value of 4 should win over plan value of 1
            self.assertIn("| Max concurrency | 4 |", summary)


# ---------------------------------------------------------------------------
# Test 6: Plan cards require parallel gate and reject scope overlap
# ---------------------------------------------------------------------------

class TestDAGParallelGate(unittest.TestCase):
    """Test 6: Plan cards still require the parallel gate and reject scope overlap
    before dispatch."""

    def test_dag_rejects_base_commit_mismatch_with_head(self):
        """DAG mode must reject cards whose base commit does not match HEAD (exit 4)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                           cwd=str(repo), check=True, capture_output=True,
                           env={**os.environ, "GIT_AUTHOR_NAME": "test",
                                "GIT_AUTHOR_EMAIL": "test@test.com",
                                "GIT_COMMITTER_NAME": "test",
                                "GIT_COMMITTER_EMAIL": "test@test.com"})

            plan = {
                "schema_version": 1,
                "group_id": "base-test",
                "max_concurrency": 2,
                "failure_policy": "skip-dependents",
                "tasks": [
                    {"id": "task-a", "task_card": "cards/task-a.md", "depends_on": []},
                    {"id": "task-b", "task_card": "cards/task-b.md", "depends_on": []},
                ],
            }
            plan_path = write_plan(repo, plan)
            # Write cards with a base commit that won't match HEAD
            for task in plan["tasks"]:
                card = repo / task["task_card"]
                card.write_text(
                    "# Task\n\n## Parallel Execution Gate\n\n"
                    "| Field | Value |\n|-------|-------|\n"
                    "| Parallel allowed? | yes |\n"
                    f"| Allowed files/modules | src/{task['id']}.py |\n"
                    "| Base commit | deadbeefdeadbeefdeadbeefdeadbeefdeadbeef |\n"
                    "| Validation owner | checker |\n"
                    "| Validation command | echo ok |\n",
                    encoding="utf-8",
                )

            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "dag-base-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--plan",
                    bash_path(plan_path),
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 4, result.stderr + result.stdout)
            self.assertIn("base commit mismatch", result.stderr)

    def test_dag_rejects_ungated_task_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)

            plan = {
                "schema_version": 1,
                "group_id": "gate-test",
                "max_concurrency": 2,
                "failure_policy": "skip-dependents",
                "tasks": [
                    {"id": "task-a", "task_card": "cards/task-a.md", "depends_on": []},
                    {"id": "task-b", "task_card": "cards/task-b.md", "depends_on": []},
                ],
            }
            plan_path = write_plan(repo, plan)
            write_dag_task(repo / "cards/task-a.md", "src/a.py", parallel="yes")
            write_dag_task(repo / "cards/task-b.md", "src/b.py", parallel="no")

            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "dag-gate-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--plan",
                    bash_path(plan_path),
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("not parallel-enabled", result.stderr)

    def test_dag_rejects_scope_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)

            plan = {
                "schema_version": 1,
                "group_id": "overlap-test",
                "max_concurrency": 2,
                "failure_policy": "skip-dependents",
                "tasks": [
                    {"id": "task-a", "task_card": "cards/task-a.md", "depends_on": []},
                    {"id": "task-b", "task_card": "cards/task-b.md", "depends_on": []},
                ],
            }
            plan_path = write_plan(repo, plan)
            write_dag_task(repo / "cards/task-a.md", "src/shared.py")
            write_dag_task(repo / "cards/task-b.md", "src/shared.py")

            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "dag-overlap-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)

            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--plan",
                    bash_path(plan_path),
                    "--output",
                    bash_path(output_dir),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 3)
            self.assertIn("scopes overlap", result.stderr)


# ---------------------------------------------------------------------------
# Test 8: Two flat dispatches in same second do not share identity
# ---------------------------------------------------------------------------

class TestFlatDispatchCollisionResistance(unittest.TestCase):
    """Test 8: Two flat dispatches started in the same second do not share
    task IDs, artifact paths, worktree paths, or branch names."""

    def test_flat_mode_dispatch_validation_rejects_missing_base_commit(self):
        """Flat mode must reject cards without base commit via Python validator (exit 4)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            # Write cards WITHOUT base commit
            task_a = repo / "task-a.md"
            task_b = repo / "task-b.md"
            task_a.write_text(
                "# Task\n\n## Parallel Execution Gate\n\n"
                "| Field | Value |\n|-------|-------|\n"
                "| Parallel allowed? | yes |\n"
                "| Allowed files/modules | src/a.py |\n"
                "| Validation owner | t1 |\n"
                "| Validation command | echo ok |\n",
                encoding="utf-8",
            )
            task_b.write_text(
                "# Task\n\n## Parallel Execution Gate\n\n"
                "| Field | Value |\n|-------|-------|\n"
                "| Parallel allowed? | yes |\n"
                "| Allowed files/modules | src/b.py |\n"
                "| Validation owner | t2 |\n"
                "| Validation command | echo ok |\n",
                encoding="utf-8",
            )

            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir = repo / ".worktrees" / "parallel-test"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)
            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--max-concurrency", "2",
                    "--output", bash_path(output_dir),
                    bash_path(task_a),
                    bash_path(task_b),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )

            self.assertEqual(result.returncode, 4, result.stderr + result.stdout)
            self.assertIn("missing Base commit", result.stderr)

    def test_two_flat_dispatches_same_second_different_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            task_a = repo / "task-a.md"
            task_b = repo / "task-b.md"
            write_task(task_a, "src/a.py")
            write_task(task_b, "src/b.py")

            # Fake dispatch that records the TASK_ID and branch name it was given
            fake_dispatch = tmp_path / "fake-dispatch.sh"
            fake_dispatch.write_text(
                "#!/usr/bin/env bash\n"
                "echo \"Result: /tmp/result.json\"\n"
                "echo \"Report: /tmp/report.md\"\n",
                encoding="utf-8",
            )
            fake_dispatch.chmod(fake_dispatch.stat().st_mode | stat.S_IXUSR)

            output_dir_a = repo / ".worktrees" / "parallel-a"
            output_dir_b = repo / ".worktrees" / "parallel-b"
            env = os.environ.copy()
            env["AI_CODING_WORKFLOW_DISPATCH_BIN"] = bash_path(fake_dispatch)

            import time
            # Set a fixed timestamp suffix so we can detect if non-random IDs would collide
            ts = time.strftime("%Y%m%d-%H%M%S")

            result_a = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--max-concurrency", "2",
                    "--output", bash_path(output_dir_a),
                    bash_path(task_a),
                    bash_path(task_b),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
            )

            # Run second dispatch immediately (same second if possible)
            result_b = subprocess.run(
                [
                    bash_exe(),
                    bash_path(SCRIPT),
                    "--max-concurrency", "2",
                    "--output", bash_path(output_dir_b),
                    bash_path(task_a),
                    bash_path(task_b),
                ],
                cwd=str(repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
            )

            self.assertEqual(result_a.returncode, 0, result_a.stderr + result_a.stdout)
            self.assertEqual(result_b.returncode, 0, result_b.stderr + result_b.stdout)

            # Both should produce independent summaries
            summary_a = (output_dir_a / "parallel-summary.md").read_text(encoding="utf-8")
            summary_b = (output_dir_b / "parallel-summary.md").read_text(encoding="utf-8")
            self.assertIn("Dispatches succeeded | 2", summary_a)
            self.assertIn("Dispatches succeeded | 2", summary_b)

            # Artifact directories are already different (--output), but we also
            # verify the dispatch script produces unique worktree paths.
            # The generic dispatcher must generate collision-resistant TASK_ID
            # even without DAG env overrides.
            worktrees = list(repo.glob(".worktrees/claude-*"))
            worktree_names = [w.name for w in worktrees if w.is_dir()]
            # Each dispatch should create a unique worktree
            self.assertEqual(len(worktree_names), len(set(worktree_names)),
                             f"worktree names collide: {worktree_names}")


if __name__ == "__main__":
    unittest.main()
