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
CHECK_WORKTREE = ROOT / "scripts" / "check-worktree.sh"
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
                "if [ -n \"${FAKE_CLAUDE_PROMPT_CAPTURE:-}\" ]; then\n"
                "  cat > \"${FAKE_CLAUDE_PROMPT_CAPTURE}\"\n"
                "else\n"
                "  cat >/dev/null\n"
                "fi\n"
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

    def test_dispatch_prompts_claude_with_execution_card_projection(self):
        task = self._write_task_card()
        task.write_text(
            "\n".join(
                [
                    "# Task Card",
                    "",
                    "## Goal",
                    "",
                    "Implement visible work.",
                    "",
                    "## Goal Loop Contract",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Success signal | README changed |",
                    "",
                    "## Task Mode",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Mode | builder |",
                    "",
                    "## Direction / Boundary Acknowledgement",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Required before editing? | yes |",
                    "| Blocking Codex approval required? | yes |",
                    "| Maximum acknowledgement rounds | 1 |",
                    "",
                    "## Handoff Contract",
                    "",
                    "| Field | Items |",
                    "|-------|-------|",
                    "| Must do | Edit README |",
                    "",
                    "## Codex Context Budget",
                    "",
                    "| Metric | Target |",
                    "|--------|--------|",
                    "| Max Codex context tokens | 1000 |",
                    "",
                    "## High-Token Delegation Gate",
                    "",
                    "- [ ] Full repository scan",
                    "",
                    "## Delegation Continuity Gate",
                    "",
                    "| Check | Value |",
                    "|-------|-------|",
                    "| Remaining implementation/test-writing phases | phase B |",
                    "",
                    "## Direction Review Gate",
                    "",
                    "| Check | Value |",
                    "|-------|-------|",
                    "| Builder diff matches planned direction? | yes/no/partial |",
                    "",
                    "## Acceptance Criteria",
                    "",
                    "- [ ] README changed",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        capture = self.case_root / "captured-prompt.md"

        result = self._dispatch(extra_env={"FAKE_CLAUDE_PROMPT_CAPTURE": str(capture)})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        prompt = capture.read_text(encoding="utf-8")
        self.assertIn("--- CLAUDE EXECUTION CARD ---", prompt)
        self.assertIn("Core rules:", prompt)
        self.assertIn("Implement visible work.", prompt)
        self.assertIn("## Task Mode", prompt)
        self.assertIn("## Direction / Boundary Acknowledgement", prompt)
        self.assertIn("Maximum acknowledgement rounds", prompt)
        self.assertIn("## Handoff Contract", prompt)
        self.assertIn("## Acceptance Criteria", prompt)
        self.assertNotIn("## Codex Context Budget", prompt)
        self.assertNotIn("Max Codex context tokens", prompt)
        self.assertNotIn("## High-Token Delegation Gate", prompt)
        self.assertNotIn("## Delegation Continuity Gate", prompt)
        self.assertNotIn("## Direction Review Gate", prompt)
        self.assertNotIn("## Goal Loop Contract", prompt)
        self.assertNotIn("Phase-gate requirements:", prompt)

        worktree = self._artifact_path(result.stdout, "Worktree")
        full_card = (worktree / "TASK_CARD_FULL.md").read_text(encoding="utf-8")
        claude_card = (worktree / "CLAUDE_TASK_CARD.md").read_text(encoding="utf-8")
        self.assertIn("## Codex Context Budget", full_card)
        self.assertIn("## Goal Loop Contract", full_card)
        self.assertNotIn("## Codex Context Budget", claude_card)
        self.assertNotIn("## Direction Review Gate", claude_card)
        self.assertNotIn("## Goal Loop Contract", claude_card)
        self.assertIn("## Task Mode", claude_card)
        self.assertIn("## Direction / Boundary Acknowledgement", claude_card)
        self.assertIn("## Acceptance Criteria", claude_card)

    def test_safe_execution_profile_restores_standard_prompt_and_execution_view(self):
        task = self._write_task_card()
        task.write_text(
            "\n".join(
                [
                    "# Task Card",
                    "",
                    "## Goal",
                    "",
                    "Implement visible work.",
                    "",
                    "## Goal Loop Contract",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Success signal | README changed |",
                    "",
                    "## Task Mode",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Mode | builder |",
                    "",
                    "## Acceptance Criteria",
                    "",
                    "- [ ] README changed",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        capture = self.case_root / "safe-profile-prompt.md"

        result = self._dispatch(
            extra_env={
                "CLAUDE_CODE_EXECUTION_PROFILE": "safe",
                "FAKE_CLAUDE_PROMPT_CAPTURE": str(capture),
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        prompt = capture.read_text(encoding="utf-8")
        self.assertIn("Phase-gate requirements:", prompt)
        self.assertIn("## Goal Loop Contract", prompt)
        self.assertIn("Execution Profile: safe", result.stdout)
        self.assertIn("Prompt Profile:  standard", result.stdout)

    def test_fast_large_repo_profile_uses_summary_evidence(self):
        self._write_task_card()

        result = self._dispatch(
            extra_env={
                "CLAUDE_CODE_EXECUTION_PROFILE": "fast-large-repo",
                "FAKE_CLAUDE_MODE": "stage-change",
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Execution Profile: fast-large-repo", result.stdout)
        self.assertIn("Worktree Strategy: reuse-managed", result.stdout)
        self.assertIn("Large Repo Mode: 1", result.stdout)
        self.assertIn("Evidence Mode:   summary", result.stdout)
        worktree = self._artifact_path(result.stdout, "Worktree")
        self.assertEqual(worktree, self.repo / ".worktrees" / "reuse" / "claude-managed")
        diff = self._artifact_path(result.stdout, "Diff").read_text(encoding="utf-8")
        self.assertIn("Evidence mode: summary", diff)
        self.assertIn("Full patch generation was skipped", diff)
        self.assertIn("README.md", diff)
        self.assertNotIn("# staged by claude", diff)

        second = self._dispatch(extra_env={"CLAUDE_CODE_EXECUTION_PROFILE": "fast-large-repo"})
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("reusable managed worktree already exists", second.stderr)
        self.assertIn("CLAUDE_CODE_REUSE_WORKTREE_RESET=1", second.stderr)

    def test_checker_progress_distinguishes_validation_skipped_by_policy(self):
        shutil.copy2(CHECK_WORKTREE, self.repo / "scripts" / "check-worktree.sh")
        self._run(["git", "add", "scripts/check-worktree.sh"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add checker"], cwd=self.repo)
        task = self._write_task_card()
        task.write_text(
            "# Task\n\n"
            "## Validation Contract\n\n"
            "| Check | Command | Required? | Notes |\n"
            "|-------|---------|-----------|-------|\n"
            "| Local validation allowed? | no | required | commands only |\n\n"
            "```bash validation\n"
            "false\n"
            "```\n",
            encoding="utf-8",
        )

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        checker = self._artifact_path(result.stdout, "Checker Report").read_text(encoding="utf-8")
        self.assertIn("artifact collection OK; validation skipped by policy", progress)
        self.assertIn("SKIPPED by policy", checker)
        self.assertNotIn("Checker helper completed: ALL GREEN", progress)

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

    def test_reuse_managed_worktree_requires_explicit_reset_when_existing(self):
        self._write_task_card()

        first = self._dispatch(extra_env={"CLAUDE_CODE_WORKTREE_STRATEGY": "reuse-managed"})

        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        worktree = self._artifact_path(first.stdout, "Worktree")
        self.assertEqual(worktree, self.repo / ".worktrees" / "reuse" / "claude-managed")
        self.assertIn("Worktree Strategy: reuse-managed", first.stdout)

        second = self._dispatch(extra_env={"CLAUDE_CODE_WORKTREE_STRATEGY": "reuse-managed"})

        self.assertNotEqual(second.returncode, 0)
        self.assertIn("reusable managed worktree already exists", second.stderr)
        self.assertIn("CLAUDE_CODE_REUSE_WORKTREE_RESET=1", second.stderr)

        third = self._dispatch(
            extra_env={
                "CLAUDE_CODE_WORKTREE_STRATEGY": "reuse-managed",
                "CLAUDE_CODE_REUSE_WORKTREE_RESET": "1",
            }
        )

        self.assertEqual(third.returncode, 0, third.stderr + third.stdout)
        self.assertEqual(self._artifact_path(third.stdout, "Worktree"), worktree)
        source_status = self._artifact_path(third.stdout, "Source Status").read_text(encoding="utf-8")
        self.assertIn("- Strategy: reuse-managed", source_status)
        self.assertIn("- Reuse reset allowed: 1", source_status)

    def test_large_repo_mode_skips_expensive_untracked_evidence(self):
        self._write_task_card()
        (self.repo / "scratch.txt").write_text("dirty but intentionally skipped in large mode\n", encoding="utf-8")

        result = self._dispatch(extra_env={"CLAUDE_CODE_LARGE_REPO_MODE": "1"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Large Repo Mode: 1", result.stdout)
        source_status = self._artifact_path(result.stdout, "Source Status").read_text(encoding="utf-8")
        diffstat = self._artifact_path(result.stdout, "Diffstat").read_text(encoding="utf-8")
        untracked = self._artifact_path(result.stdout, "Untracked Files").read_text(encoding="utf-8")
        self.assertIn("- Large repo mode: 1", source_status)
        self.assertIn("skipped: CLAUDE_CODE_LARGE_REPO_MODE=1", source_status)
        self.assertIn("skipped: CLAUDE_CODE_LARGE_REPO_MODE=1", diffstat)
        self.assertIn("skipped: CLAUDE_CODE_LARGE_REPO_MODE=1", untracked)

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
        self.assertIn("AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT", report)
        self.assertIn("This fallback report is not a valid Claude report.", report)
        self.assertIn("Evidence classification: seeded report only", report)
        self.assertIn("Implementation changes: 0", report)
        self.assertIn("Claude exit status: 42", report)
        self.assertIn("Fallback result generated: yes", report)
        progress = progress_file.read_text(encoding="utf-8")
        self.assertIn("dispatch-started", progress)
        self.assertIn("- [ ] Context gathered", progress)

    def test_network_monitor_writes_metadata_log_when_enabled(self):
        self._write_task_card()

        result = self._dispatch(extra_env={"CLAUDE_CODE_NETWORK_MONITOR": "1"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        network_file = self._artifact_path(result.stdout, "Network Log")
        progress_file = self._artifact_path(result.stdout, "Progress Log")
        network = network_file.read_text(encoding="utf-8")
        progress = progress_file.read_text(encoding="utf-8")
        self.assertIn("Claude Network Diagnostics", network)
        self.assertIn("Network monitoring is metadata-only", network)
        self.assertIn("CLAUDE_CODE_NETWORK_MONITOR: 1", network)
        self.assertIn("CLAUDE_CODE_PROXY_MODE:", network)
        self.assertIn("Socket Snapshots", network)
        self.assertIn("Final network snapshot:", progress)

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
