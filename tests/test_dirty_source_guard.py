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


def start_bash_sleeper():
    proc = subprocess.Popen(
        [BASH, "-c", "echo $$; exec sleep 60"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    return proc, proc.stdout.readline().strip()


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
                "  approval-blocked|approval-incomplete|approval-unrelated)\n"
                "    mkdir -p tests\n"
                "    printf '# checker edit\\n' > tests/test_fixture.py\n"
                "    if [ \"${FAKE_CLAUDE_MODE}\" = approval-incomplete ]; then\n"
                "      printf '# incomplete report\\n' > CLAUDE_REPORT.md\n"
                "    else\n"
                "      cat > CLAUDE_REPORT.md <<'REPORT_EOF'\n"
                "# Claude Modification Report\n\n"
                "## Requirements Summary\nChecker validation.\n\n"
                "## Files Changed\n- tests/test_fixture.py\n\n"
                "## Acceptance Criteria Mapping\n- test edit complete\n\n"
                "## Out-of-Scope Confirmation\nNo out-of-scope changes.\n\n"
                "## Plan Match\nfull\n\n"
                "## Checks Run\n- python -m pytest: blocked by approval\n\n"
                "Implementation and test edits complete.\n"
                "REPORT_EOF\n"
                "    fi\n"
                "    if [ \"${FAKE_CLAUDE_MODE}\" = approval-unrelated ]; then\n"
                "      sed -i 's/python -m pytest: blocked by approval/no validation command assigned/' CLAUDE_REPORT.md\n"
                "      printf 'Deployment approval required. Test edits complete.\\n' > CLAUDE_PROGRESS.md\n"
                "      echo 'deployment approval required' >&2\n"
                "    else\n"
                "      printf 'Test edits complete. Validation command blocked by approval.\\n' > CLAUDE_PROGRESS.md\n"
                "      echo 'validation command requires permission approval' >&2\n"
                "    fi\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-4}\"\n"
                "    ;;\n"
                "  seed-only)\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  worktree-change)\n"
                "    printf '# worktree change\\n' > NEW_FILE.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  progress-update)\n"
                "    printf 'Real progress update.\\n' > CLAUDE_PROGRESS.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  valid-report)\n"
                "    cat > CLAUDE_REPORT.md <<'REPORT_EOF'\n"
                "# Claude Report\n\n"
                "## Requirements Summary\nDone.\n\n"
                "## Files Changed\n- README.md\n\n"
                "## Acceptance Criteria Mapping\n- complete\n\n"
                "## Out-of-Scope Confirmation\nNone.\n\n"
                "## Plan Match\nfull\n\n"
                "## Checks Run\n- passed\n\n"
                "Implementation complete.\n"
                "REPORT_EOF\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  blocker-recorded)\n"
                "    printf 'Dispatcher-created draft. Permission blocker encountered.\\n' > CLAUDE_REPORT.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  api-error-no-diff)\n"
                "    printf '%s\\n' '{\"is_error\":true,\"result\":\"API Error: Connection closed mid-response\",\"total_cost_usd\":0}'\n"
                "    exit 0\n"
                "    ;;\n"
                "  api-error-with-diff)\n"
                "    printf '# api error work\\n' > README.md\n"
                "    printf '%s\\n' '{\"is_error\":true,\"result\":\"API Error: Connection closed mid-response\",\"total_cost_usd\":0}'\n"
                "    exit 0\n"
                "    ;;\n"
                "  diff-without-report)\n"
                "    printf '# diff work\\n' > README.md\n"
                "    ;;\n"
                "  success)\n"
                "    cat > CLAUDE_REPORT.md <<'REPORT_EOF'\n"
                "# Claude Modification Report\n\n"
                "## Requirements Summary\nDispatch completed.\n\n"
                "## Files Changed\n- README.md\n\n"
                "## Acceptance Criteria Mapping\n- task complete\n\n"
                "## Out-of-Scope Confirmation\nNone.\n\n"
                "## Plan Match\nfull\n\n"
                "## Checks Run\n- bash -n scripts/dispatch-to-claude.sh: passed\n\n"
                "Implementation complete.\n"
                "REPORT_EOF\n"
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

    def _write_low_risk_checker_card(self, omit=None, duplicate=None):
        rows = [
            "Public API risk", "Data model risk", "Security risk", "Migration risk",
            "Permission risk", "Concurrency risk", "Cross-module risk", "Production impact",
        ]
        lines = [
            "# Checker", "", "## Task Mode", "", "| Field | Value |", "|---|---|",
            "| Mode | checker-test |", "", "## Checker Reuse Risk Gate", "",
            "| Field | Value |", "|---|---|",
        ]
        for field in rows:
            if field != omit:
                lines.append("| {} | no |".format(field))
        if duplicate:
            lines.append("| {} | no |".format(duplicate))
        task = self.repo / "task-cards" / "CHECKER.md"
        task.parent.mkdir(exist_ok=True)
        task.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return task

    def _write_builder_task_card(self):
        task = self.repo / "task-cards" / "BUILDER.md"
        task.parent.mkdir(exist_ok=True)
        task.write_text(
            "# Task Card\n\n"
            "## Goal\n\nImplement visible work.\n\n"
            "## Task Mode\n\n"
            "| Field | Value |\n|---|---|\n| Mode | builder |\n\n"
            "## Claude Context Packet\n\n"
            "| Field | Value |\n|---|---|\n| Target files/modules | README.md |\n\n"
            "## Handoff Contract\n\nEdit README.\n\n"
            "## Acceptance Criteria\n\n- README changed\n\n"
            "## Testing Responsibility\n\nBuilder runs tests.\n\n"
            "## Validation Contract\n\n"
            "```validation\ntrue\n```\n\n"
            "## Required Report\n\nReport files changed.\n\n"
            "## Implementation Notes\n\nSome implementation notes.\n\n"
            "## Review Checklist\n\n- [ ] Code reviewed\n\n",
            encoding="utf-8",
        )
        return task

    def test_low_risk_checker_defaults_to_reuse_managed(self):
        self._write_low_risk_checker_card()
        result = self._dispatch("task-cards/CHECKER.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Worktree Strategy: reuse-managed", result.stdout)
        self.assertNotIn("Updating files:", result.stdout + result.stderr)

    def test_missing_risk_cannot_be_replaced_by_duplicate(self):
        self._write_low_risk_checker_card(
            omit="Cross-module risk", duplicate="Security risk"
        )
        result = self._dispatch("task-cards/CHECKER.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Worktree Strategy: fresh", result.stdout)

    def test_explicit_fresh_overrides_checker_reuse(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"CLAUDE_CODE_WORKTREE_STRATEGY": "fresh"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Worktree Strategy: fresh", result.stdout)

    def test_approval_blocked_checker_converges_before_fake_exit(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"FAKE_CLAUDE_MODE": "approval-blocked", "FAKE_CLAUDE_SLEEP_SECONDS": "8"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("approval-blocked early convergence", progress.lower())
        self.assertIn("approval_blocked_early_convergence", status)

    def test_approval_convergence_requires_complete_report(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"FAKE_CLAUDE_MODE": "approval-incomplete", "FAKE_CLAUDE_SLEEP_SECONDS": "3"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("approval-blocked early convergence", progress.lower())

    def test_approval_convergence_ignores_unrelated_approval(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"FAKE_CLAUDE_MODE": "approval-unrelated", "FAKE_CLAUDE_SLEEP_SECONDS": "3"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("approval-blocked early convergence", progress.lower())

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

    # --- Runtime identity and retry-in-place tests ---

    def _do_fresh_dispatch(self):
        """Run a fresh dispatch and return (result, worktree_path, runtime_dict)."""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)
        result = self._dispatch()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        worktree = self._artifact_path(result.stdout, "Worktree")
        runtime_path = self._artifact_path(result.stdout, "Runtime Identity")
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        return result, worktree, runtime

    def test_fresh_dispatch_writes_valid_runtime_json(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        runtime_path = self._artifact_path(result.stdout, "Runtime Identity")
        self.assertTrue(runtime_path.exists())
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(runtime["schema_version"], 1)
        self.assertIn("task_id", runtime)
        self.assertIn("worktree", runtime)
        self.assertIn("base_commit", runtime)
        self.assertIn("source_repository", runtime)
        self.assertIn("branch", runtime)
        self.assertIn("strategy", runtime)
        self.assertIn("pid_files", runtime)
        self.assertIn("dispatcher", runtime["pid_files"])
        self.assertIn("claude", runtime["pid_files"])
        self.assertIn("checker", runtime["pid_files"])
        self.assertIn("pid", runtime["pid_files"])
        worktree = self._artifact_path(result.stdout, "Worktree")
        self.assertEqual(pathlib.Path(runtime["worktree"]), worktree)
        self.assertEqual(pathlib.Path(runtime["source_repository"]), self.repo)
        self.assertEqual(len(runtime["base_commit"]), 40)
        self.assertEqual(runtime["strategy"], "fresh")
        self.assertNotIn("retry_of", runtime)

    def test_retry_in_place_reuses_prior_worktree_with_new_task_id(self):
        _, first_worktree, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]

        second = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        second_worktree = self._artifact_path(second.stdout, "Worktree")
        second_runtime_path = self._artifact_path(second.stdout, "Runtime Identity")
        second_runtime = json.loads(second_runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(first_worktree, second_worktree)
        self.assertNotEqual(first_runtime["task_id"], second_runtime["task_id"])
        self.assertEqual(second_runtime["retry_of"], prior_task_id)
        self.assertEqual(second_runtime["strategy"], "retry-in-place")
        self.assertIn("retry-in-place", second.stdout)

    def test_retry_in_place_rejects_missing_runtime_json(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": "nonexistent-task-id"}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prior runtime.json not found", result.stderr)

    def test_retry_in_place_rejects_unknown_untracked_files(self):
        _, first_worktree, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        (first_worktree / "scratch.txt").write_text("dirty\n", encoding="utf-8")

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown untracked files", result.stderr)
        self.assertIn("scratch.txt", result.stderr)

    def test_retry_in_place_accepts_known_control_files(self):
        _, first_worktree, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        for name in ["CLAUDE_REPORT.md", "CLAUDE_PROGRESS.md", "TASK_CARD.md",
                      "TASK_CARD_FULL.md", "CLAUDE_TASK_CARD.md", "CLAUDE_PROMPT.md"]:
            (first_worktree / name).write_text(f"# {name}\n", encoding="utf-8")

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("retry-in-place", result.stdout)

    def test_retry_in_place_rejects_managed_prior_strategy(self):
        self._write_task_card()
        first = self._dispatch(
            extra_env={"CLAUDE_CODE_WORKTREE_STRATEGY": "reuse-managed"}
        )
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        first_runtime = json.loads(
            self._artifact_path(first.stdout, "Runtime Identity").read_text(encoding="utf-8")
        )
        prior_task_id = first_runtime["task_id"]

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reuse-managed", result.stderr)

    def test_retry_in_place_rejects_live_role_pid(self):
        _, _, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        pid_file = self.repo / ".worktrees" / f"{prior_task_id}.claude.pid"
        sleeper, bash_pid = start_bash_sleeper()
        try:
            pid_file.write_text(bash_pid, encoding="utf-8")
            result = self._dispatch(
                extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
            )
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=10)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("still running", result.stderr)

    def test_retry_in_place_rejects_competing_reservation(self):
        _, _, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        reservation = self.repo / ".worktrees" / f".retry-lock-{prior_task_id}"
        reservation.mkdir()

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reservation already exists", result.stderr)

    def test_retry_in_place_rejects_source_diff_in_worktree(self):
        _, first_worktree, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        (first_worktree / "README.md").write_text("# modified\n", encoding="utf-8")

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tracked changes", result.stderr)

    def test_progress_log_includes_child_exit_transition(self):
        self._write_task_card()

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("Claude child exited:", progress)
        self.assertIn("transitioning to finalization immediately", progress)

    # --- Execution-only builder mode and first-progress timeout tests ---

    def test_invalid_builder_mode_fails_before_worktree_creation(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"CLAUDE_CODE_BUILDER_MODE": "invalid"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLAUDE_CODE_BUILDER_MODE must be", result.stderr)
        worktrees = self.repo / ".worktrees"
        artifacts = sorted(p.name for p in worktrees.glob("claude-*")) if worktrees.exists() else []
        self.assertEqual([], artifacts)

    def test_execution_only_non_builder_fails_before_worktree_creation(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"CLAUDE_CODE_BUILDER_MODE": "execution-only"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("execution-only requires task mode 'builder'", result.stderr)

    def test_standard_defaults_to_timeout_zero_and_preserves_headings(self):
        self._write_builder_task_card()
        result = self._dispatch("task-cards/BUILDER.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("First Progress:  0s timeout", result.stdout)
        worktree = self._artifact_path(result.stdout, "Worktree")
        claude_card = (worktree / "CLAUDE_TASK_CARD.md").read_text(encoding="utf-8")
        self.assertIn("## Task Mode", claude_card)
        self.assertIn("## Goal", claude_card)
        self.assertIn("## Acceptance Criteria", claude_card)
        self.assertNotIn("execution-only view", claude_card.lower())

    def test_execution_only_defaults_to_timeout_120_renders_smaller_card_with_short_prompt(self):
        self._write_builder_task_card()
        capture = self.case_root / "execution-only-prompt.md"
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "FAKE_CLAUDE_PROMPT_CAPTURE": str(capture),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("First Progress:  120s timeout", result.stdout)
        self.assertIn("Builder Mode:    execution-only", result.stdout)
        worktree = self._artifact_path(result.stdout, "Worktree")
        claude_card = (worktree / "CLAUDE_TASK_CARD.md").read_text(encoding="utf-8")
        self.assertIn("## Goal", claude_card)
        self.assertIn("## Task Mode", claude_card)
        self.assertIn("## Handoff Contract", claude_card)
        self.assertIn("## Acceptance Criteria", claude_card)
        self.assertIn("## Validation Contract", claude_card)
        self.assertNotIn("## Implementation Notes", claude_card)
        self.assertNotIn("## Review Checklist", claude_card)
        self.assertIn("execution-only view", claude_card.lower())
        prompt = capture.read_text(encoding="utf-8")
        self.assertIn("execution-only Builder mode", prompt)
        self.assertIn("Do NOT restate or redesign the plan", prompt)
        self.assertIn("--- CLAUDE EXECUTION CARD ---", prompt)

    def test_seed_only_stopped_at_short_deadline(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("first_progress_timeout", progress.lower())
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("First-progress timed out: yes", status)

    def test_source_diff_prevents_first_progress_timeout(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "worktree-change",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("first_progress_detected=1", progress)
        self.assertIn("signal=worktree_change", progress)

    def test_non_seeded_progress_update_prevents_first_progress_timeout(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "progress-update",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("first_progress_detected=1", progress)
        self.assertIn("signal=progress_updated", progress)

    def test_valid_report_prevents_first_progress_timeout(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "valid-report",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("first_progress_detected=1", progress)
        self.assertIn("signal=valid_report", progress)

    def test_blocker_recorded_prevents_first_progress_timeout(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "blocker-recorded",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("first_progress_detected=1", progress)
        self.assertIn("signal=blocker_recorded", progress)

    def test_fallback_evidence_records_first_progress_timeout_no_acceptance(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        result_file = self._artifact_path(result.stdout, "Result")
        data = json.loads(result_file.read_text(encoding="utf-8"))
        self.assertTrue(data.get("first_progress_timeout"))
        self.assertEqual(data.get("builder_mode"), "execution-only")
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("First-progress timed out: yes", status)
        self.assertNotIn("acceptance", status.lower())
        self.assertNotIn("takeover", status.lower())
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("acceptance", progress.lower())
        self.assertNotIn("takeover", progress.lower())


    # --- Semantic result error detection and dispatch outcome tests ---

    def test_semantic_api_error_detected_with_no_diff(self):
        """exit 0 + is_error=true + API Error + no diff → api_error_without_diff"""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "api-error-no-diff"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Semantic result error: yes", status)
        self.assertIn("api_error:", status)
        self.assertIn("Dispatch outcome: api_error_without_diff", status)
        self.assertIn("Implementation changes: 0", status)
        # Raw result must not be discarded
        result_file = self._artifact_path(result.stdout, "Result")
        data = json.loads(result_file.read_text(encoding="utf-8"))
        self.assertTrue(data.get("is_error"))
        self.assertIn("API Error:", data.get("result", ""))

    def test_semantic_api_error_detected_with_diff(self):
        """exit 0 + is_error=true + API Error + with diff → api_error_with_diff"""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "api-error-with-diff"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Semantic result error: yes", status)
        self.assertIn("Dispatch outcome: api_error_with_diff", status)
        self.assertIn("Implementation changes: 1", status)
        # Evidence classification should still reflect diff presence
        self.assertIn("Evidence classification: diff without report", status)

    def test_normal_success_result_remains_success(self):
        """exit 0 + normal result → dispatch_outcome=success"""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertNotIn("Semantic result error: yes", status)
        self.assertIn("Dispatch outcome: success", status)

    def test_diff_without_valid_report_remains_recoverable_diff_evidence(self):
        """exit 0 + normal result + diff + no valid report → diff without report"""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "diff-without-report"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Evidence classification: diff without report", status)
        self.assertIn("Implementation changes: 1", status)
        self.assertIn("Dispatch outcome: success", status)
        self.assertNotIn("Dispatch outcome: no_useful_progress", status)

    def test_approval_blocked_not_classified_as_no_progress(self):
        """approval-blocked with test-only diff is not no_useful_progress"""
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"FAKE_CLAUDE_MODE": "approval-blocked", "FAKE_CLAUDE_SLEEP_SECONDS": "8"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Dispatch outcome: approval_blocked", status)
        self.assertNotIn("Dispatch outcome: no_useful_progress", status)


if __name__ == "__main__":
    unittest.main()
