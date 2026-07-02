import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_workflow.py"
BEGIN_MARKER = "<!-- AI-CODING-WORKFLOW:BEGIN managed -->"


def load_module():
    spec = importlib.util.spec_from_file_location("install_workflow", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallWorkflowTests(unittest.TestCase):
    def run_installer(self, repo):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(repo)],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            first = self.run_installer(repo)
            second = self.run_installer(repo)

            self.assertIn("created: AGENTS.md", first.stdout)
            self.assertIn("skipped: AGENTS.md", second.stdout)
            self.assertTrue((repo / "AGENTS.md").exists())
            self.assertTrue((repo / "CLAUDE.md").exists())
            self.assertTrue((repo / "ai" / "task-card-template.md").exists())
            self.assertTrue((repo / "ai" / "run-loop.sh").exists())
            self.assertTrue((repo / "ai" / "status-claude.sh").exists())
            self.assertTrue((repo / "ai" / "watch-claude.sh").exists())
            self.assertTrue((repo / "ai" / "kill-claude.sh").exists())
            self.assertTrue((repo / "ai" / "cleanup-worktree.sh").exists())
            self.assertTrue((repo / "ai" / "pwsh-utf8.ps1").exists())
            self.assertTrue((repo / ".worktrees" / ".gitkeep").exists())

    def test_update_preserves_user_owned_content(self):
        module = load_module()
        old_managed = "\n".join(
            [
                "# Agents",
                "",
                module.BEGIN_MARKER,
                "old managed content",
                module.END_MARKER,
                "",
                "## Project-specific rules",
                "",
                "Keep this repository rule.",
                "",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "AGENTS.md").write_text(old_managed, encoding="utf-8")

            self.run_installer(repo)

            content = (repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("## Core Principle", content)
            self.assertIn("Keep this repository rule.", content)
            self.assertNotIn("old managed content", content)

    def test_claude_import_is_deduplicated_and_near_top(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / "CLAUDE.md").write_text(
                "# Claude Code Configuration\n\nCustom note.\n\n@AGENTS.md\n",
                encoding="utf-8",
            )

            self.run_installer(repo)

            lines = (repo / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines.count("@AGENTS.md"), 1)
            self.assertLess(lines.index("@AGENTS.md"), lines.index(BEGIN_MARKER))

    def test_installed_dispatch_defaults_claude_to_direct_proxy_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
            self.assertIn('CLAUDE_CODE_PROXY_MODE="${CLAUDE_CODE_PROXY_MODE:-direct}"', dispatch)
            self.assertIn('CLAUDE_CODE_PROXY_MODE" = "inherit"', dispatch)
            self.assertIn('unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY', dispatch)
            self.assertIn('unset http_proxy https_proxy all_proxy no_proxy', dispatch)

    def test_installed_dispatch_has_observability_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
            self.assertIn('CLAUDE_CODE_TIMEOUT_SECONDS="${CLAUDE_CODE_TIMEOUT_SECONDS:-600}"', dispatch)
            self.assertIn('CLAUDE_CODE_HEARTBEAT_SECONDS="${CLAUDE_CODE_HEARTBEAT_SECONDS:-30}"', dispatch)
            self.assertIn('CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS="${CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS:-0}"', dispatch)
            self.assertIn('PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.pid"', dispatch)
            self.assertIn('PROGRESS_FILE="${WORKTREE_ROOT}/${TASK_ID}.progress.log"', dispatch)
            self.assertIn('CLAUDE_PROGRESS_FILE="${WORKTREE_ROOT}/${TASK_ID}.claude-progress.md"', dispatch)
            self.assertIn('maintain `CLAUDE_PROGRESS.md`', dispatch)
            self.assertIn('claude_progress_bytes=${CLAUDE_PROGRESS_BYTES}', dispatch)
            self.assertIn('Claude process started: pid=${CLAUDE_PID}', dispatch)
            self.assertIn('Claude still running: pid=${CLAUDE_PID}', dispatch)
            self.assertIn('quiet_seconds=${QUIET_SECONDS}', dispatch)
            self.assertIn('Claude finished by no-output timeout', dispatch)
            self.assertIn('runtime timeout', dispatch)
            self.assertIn('Claude Progress:', dispatch)
            self.assertIn('Progress Log:', dispatch)
            self.assertIn('WATCH_SCRIPT="${SCRIPT_DIR}/watch-claude.sh"', dispatch)
            self.assertIn('Watch Progress:', dispatch)
            self.assertIn('Watch Details:', dispatch)
            self.assertIn('command -v claude', dispatch)
            self.assertIn('claude CLI is not installed or not in PATH', dispatch)
            self.assertIn('CLAUDE_CODE_ALLOW_DIRTY_SOURCE', dispatch)
            self.assertIn('Source worktree is dirty. Claude would run from stale HEAD.', dispatch)
            self.assertIn('grep -vxF "$TASK_CARD_REL"', dispatch)
            self.assertIn('Phase-gate requirements:', dispatch)
            self.assertIn('## Execution Phases', dispatch)
            self.assertIn('before running long validation commands', dispatch)
            self.assertIn('git worktree add -b "$BRANCH_NAME" "$WORKTREE_DIR" HEAD ||', dispatch)

    def test_installed_run_loop_preserves_dispatch_observability_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            run_loop = (repo / "ai" / "run-loop.sh").read_text(encoding="utf-8")
            self.assertIn("Dispatch execution requires Claude Code", run_loop)
            self.assertIn("doctor_workflow.py to verify readiness", run_loop)
            self.assertIn('CLAUDE_PID_FILE="$(parse_path "Claude PID" "$DISPATCH_LOG")"', run_loop)
            self.assertIn('PROGRESS_FILE="$(parse_path "Progress Log" "$DISPATCH_LOG")"', run_loop)
            self.assertIn('CLAUDE_PROGRESS_FILE="$(parse_path "Claude Progress" "$DISPATCH_LOG")"', run_loop)
            self.assertIn('"$CLAUDE_PID_FILE" "$PROGRESS_FILE"', run_loop)
            self.assertIn('### Claude Progress', run_loop)
            self.assertIn('### Claude Self-Reported Progress', run_loop)
            self.assertIn('"$PROGRESS_FILE" "$CLAUDE_PID_FILE" 2>&1 | tee "$REVIEW_OUTPUT"', run_loop)

    def test_installed_powershell_utf8_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            helper = (repo / "ai" / "pwsh-utf8.ps1").read_text(encoding="utf-8")
            self.assertIn("[Console]::OutputEncoding", helper)
            self.assertIn("PYTHONUTF8", helper)
            self.assertIn("PYTHONIOENCODING", helper)
            self.assertIn("chcp.com 65001", helper)
            self.assertIn("AI-CODING-WORKFLOW:BEGIN utf8", helper)

    def test_installed_claude_operations_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            status = (repo / "ai" / "status-claude.sh").read_text(encoding="utf-8")
            watch = (repo / "ai" / "watch-claude.sh").read_text(encoding="utf-8")
            kill = (repo / "ai" / "kill-claude.sh").read_text(encoding="utf-8")
            cleanup = (repo / "ai" / "cleanup-worktree.sh").read_text(encoding="utf-8")

            self.assertIn('*.progress.log', status)
            self.assertIn('kill -0 "$PID"', status)
            self.assertIn('tail -20 "$PROGRESS_FILE"', status)
            self.assertIn('tail -40 "$CLAUDE_PROGRESS_FILE"', status)
            self.assertIn('CLAUDE CODE WATCH', watch)
            self.assertIn('progress_bar', watch)
            self.assertIn('LIVE_CLAUDE_PROGRESS_FILE', watch)
            self.assertIn('select_claude_progress_file', watch)
            self.assertIn('stuck_reason', watch)
            self.assertIn('--details', watch)
            self.assertIn('--plain', watch)
            self.assertIn('--stale-after', watch)
            self.assertIn('Sending TERM to Claude process', kill)
            self.assertIn('kill -9 "$PID"', kill)
            self.assertIn('Claude process is still running', cleanup)
            self.assertIn('git worktree remove', cleanup)
            self.assertIn('Evidence artifacts were preserved', cleanup)

    def test_installed_watch_handles_progress_without_checklist(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)
            worktrees = repo / ".worktrees"
            task_id = "claude-20990101-000000"
            (worktrees / task_id).mkdir()
            (worktrees / f"{task_id}.pid").write_text("", encoding="utf-8")
            (worktrees / f"{task_id}.progress.log").write_text(
                "[2099-01-01 00:00:00] Claude still running: pid=1, elapsed_seconds=5, quiet_seconds=5, result_bytes=0, status_bytes=0, report_bytes=0, claude_progress_bytes=12\n",
                encoding="utf-8",
            )
            (worktrees / f"{task_id}.result.json").write_text("", encoding="utf-8")
            (worktrees / f"{task_id}.status.txt").write_text("", encoding="utf-8")
            (worktrees / f"{task_id}.claude-progress.md").write_text(
                "Working on validation\n", encoding="utf-8"
            )

            bash_exe = load_module()._find_bash()
            result = subprocess.run(
                [bash_exe, str(repo / "ai" / "watch-claude.sh"), task_id, "--once"],
                cwd=str(repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=True,
            )

            self.assertIn("PROGRESS : [##------------------] 10%", result.stdout)
            self.assertNotIn("integer expression expected", result.stderr)

    def test_installed_templates_include_control_plane_and_progress_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            task_card = (repo / "ai" / "task-card-template.md").read_text(encoding="utf-8")
            evidence = (repo / "ai" / "evidence-packet-template.md").read_text(encoding="utf-8")
            self.assertIn("## Task Type", task_card)
            self.assertIn("## Executor", task_card)
            self.assertIn("## Control-Plane Exception Rationale", task_card)
            self.assertIn("## Execution Phases", task_card)
            self.assertIn("Stop Before Next Phase?", task_card)
            self.assertIn("## Claude Dispatch Progress", evidence)
            self.assertIn("## Phase Execution Evidence", evidence)
            self.assertIn("Claude PID", evidence)
            self.assertIn("Progress log", evidence)
            self.assertIn("Timed out?", evidence)
            self.assertIn("Fallback report generated?", evidence)


if __name__ == "__main__":
    unittest.main()
