import os
import json
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "scripts" / "dispatch-to-claude.sh"
TEMP_ROOT = ROOT / ".worktrees" / "dirty-source-guard-tests"

def find_bash():
    if sys.platform == "win32":
        for candidate in [
            pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
            pathlib.Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ]:
            if candidate.exists():
                return str(candidate)
    return "bash"


BASH = find_bash()


def remove_tree(path):
    def make_writable(func, failing_path, _exc_info):
        try:
            os.chmod(failing_path, stat.S_IWRITE)
            func(failing_path)
        except OSError:
            pass

    shutil.rmtree(path, onerror=make_writable)


class DirtySourceGuardBehaviorTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.case_root = pathlib.Path(tempfile.mkdtemp(prefix="case-", dir=str(TEMP_ROOT)))
        self.repo = self.case_root / "repo"
        self.fake_bin = self.case_root / "fake-bin"
        self.repo.mkdir()
        self.fake_bin.mkdir()
        self._write_fake_claude()
        self._run(["git", "init"], cwd=self.repo)
        self._run(["git", "config", "user.email", "test@example.com"], cwd=self.repo)
        self._run(["git", "config", "user.name", "Test User"], cwd=self.repo)
        (self.repo / "README.md").write_text("# fixture\n", encoding="utf-8")
        (self.repo / "scripts").mkdir()
        shutil.copy2(DISPATCH, self.repo / "scripts" / "dispatch-to-claude.sh")
        self._run(["git", "add", "README.md", "scripts/dispatch-to-claude.sh"], cwd=self.repo)
        self._run(["git", "commit", "-m", "init"], cwd=self.repo)

    def tearDown(self):
        if getattr(self, "repo", None) and self.repo.exists():
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(self.repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
        if self.case_root.exists():
            remove_tree(self.case_root)
        try:
            TEMP_ROOT.rmdir()
        except OSError:
            pass

    def _write_fake_claude(self):
        fake = self.fake_bin / "claude"
        with open(fake, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "#!/usr/bin/env bash\n"
                "cat >/dev/null\n"
                "case \"${FAKE_CLAUDE_MODE:-success}\" in\n"
                "  fail-empty)\n"
                "    exit 42\n"
                "    ;;\n"
                "  stage-change)\n"
                "    printf '# staged by claude\\n' > README.md\n"
                "    git add README.md\n"
                "    ;;\n"
                "esac\n"
                "printf '%s\\n' '{\"total_cost_usd\":0,\"usage\":{\"input_tokens\":0,\"output_tokens\":0}}'\n"
            )
        os.chmod(fake, 0o755)

    def _run(self, args, cwd=None, env=None, timeout=60):
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            args,
            cwd=str(cwd or self.repo),
            env=merged_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=True,
        )

    def _write_task_card(self):
        task = self.repo / "task-cards" / "PROJ.md"
        task.parent.mkdir(exist_ok=True)
        task.write_text("# Task\n\nNo-op dispatch fixture.\n", encoding="utf-8")
        return task

    def _dispatch(self, task_arg="task-cards/PROJ.md", extra_env=None):
        env = {
            "PATH": str(self.fake_bin) + os.pathsep + os.environ.get("PATH", ""),
            "CLAUDE_CODE_TIMEOUT_SECONDS": "30",
            "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
            "CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS": "0",
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [BASH, "scripts/dispatch-to-claude.sh", task_arg],
            cwd=str(self.repo),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
        )

    def _artifact_path(self, stdout, label):
        prefix = label + ":"
        for line in stdout.splitlines():
            if line.startswith(prefix):
                return pathlib.Path(line.split(":", 1)[1].strip())
        self.fail(f"missing artifact label {label!r} in output:\n{stdout}")

    def test_clean_repo_with_tracked_task_card_succeeds(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Complete", result.stdout)
        self.assertIn("Checker Report:", result.stdout)
        self.assertTrue(list((self.repo / ".worktrees").glob("claude-*.checker-report.md")))

    def test_untracked_task_card_only_succeeds(self):
        self._write_task_card()

        result = self._dispatch("task-cards/PROJ.md")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Complete", result.stdout)

    def test_untracked_task_card_with_dot_slash_succeeds(self):
        self._write_task_card()

        result = self._dispatch("./task-cards/PROJ.md")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Complete", result.stdout)

    def test_unrelated_untracked_file_blocks(self):
        self._write_task_card()
        (self.repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")

        result = self._dispatch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale HEAD", result.stderr)
        self.assertIn("scratch.txt", result.stderr)
        worktrees = self.repo / ".worktrees"
        artifacts = sorted(p.name for p in worktrees.glob("claude-*")) if worktrees.exists() else []
        self.assertEqual([], artifacts)

    def test_tracked_diff_blocks(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)
        (self.repo / "README.md").write_text("# changed\n", encoding="utf-8")

        result = self._dispatch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tracked changes", result.stderr)
        self.assertIn("README.md", result.stderr)

    def test_staged_diff_blocks(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)
        (self.repo / "README.md").write_text("# staged\n", encoding="utf-8")
        self._run(["git", "add", "README.md"], cwd=self.repo)

        result = self._dispatch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Staged changes", result.stderr)
        self.assertIn("README.md", result.stderr)

    def test_allow_dirty_override_succeeds_with_warning(self):
        self._write_task_card()
        (self.repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")

        result = self._dispatch(extra_env={"CLAUDE_CODE_ALLOW_DIRTY_SOURCE": "1"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1", result.stderr)
        self.assertIn("Dispatch Complete", result.stdout)
        self.assertIn("Source status saved to:", result.stdout)
        self.assertTrue(list((self.repo / ".worktrees").glob("claude-*.source-status.txt")))

    def test_claude_early_exit_without_result_gets_fallback_artifacts(self):
        self._write_task_card()

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "fail-empty"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("claude exited with non-zero status 42", result.stderr)
        result_file = self._artifact_path(result.stdout, "Result")
        report_file = self._artifact_path(result.stdout, "Report")
        progress_file = self._artifact_path(result.stdout, "Claude Progress")
        raw_result_file = self._artifact_path(result.stdout, "Raw Result")
        data = json.loads(result_file.read_text(encoding="utf-8"))
        self.assertTrue(data["fallback"])
        self.assertEqual(data["claude_exit_status"], 42)
        self.assertTrue(raw_result_file.exists())
        report = report_file.read_text(encoding="utf-8")
        self.assertIn("Claude exit status: 42", report)
        self.assertIn("Fallback result generated: yes", report)
        progress = progress_file.read_text(encoding="utf-8")
        self.assertIn("dispatch-started", progress)
        self.assertIn("- [ ] Context gathered", progress)

    def test_staged_claude_changes_are_in_combined_diff(self):
        self._write_task_card()

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "stage-change"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        diff_file = self._artifact_path(result.stdout, "Diff")
        diffstat_file = self._artifact_path(result.stdout, "Diffstat")
        diff = diff_file.read_text(encoding="utf-8")
        diffstat = diffstat_file.read_text(encoding="utf-8")
        self.assertIn("## Staged Diff", diff)
        self.assertIn("+# staged by claude", diff)
        self.assertIn("## Staged Changes", diffstat)
        self.assertIn("README.md", diffstat)


if __name__ == "__main__":
    unittest.main()
