import importlib.util
import os
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
    def run_installer(self, repo, *extra_args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(repo)] + list(extra_args),
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
            self.assertTrue((repo / "ai" / "spec-template.md").exists())
            self.assertTrue((repo / "ai" / "plan-task-template.md").exists())
            self.assertTrue((repo / "ai" / "plan-findings-template.md").exists())
            self.assertTrue((repo / "ai" / "plan-progress-template.md").exists())
            self.assertTrue((repo / "ai" / "check-worktree.sh").exists())
            self.assertTrue((repo / "ai" / "run-codex-spark.sh").exists())
            self.assertTrue((repo / "ai" / "run-parallel-loop.sh").exists())
            self.assertTrue((repo / "ai" / "run-loop.sh").exists())
            self.assertTrue((repo / "ai" / "status-claude.sh").exists())
            self.assertTrue((repo / "ai" / "watch-claude.sh").exists())
            self.assertTrue((repo / "ai" / "kill-claude.sh").exists())
            self.assertTrue((repo / "ai" / "cleanup-worktree.sh").exists())
            self.assertTrue((repo / "ai" / "pwsh-utf8.ps1").exists())
            self.assertTrue((repo / "ai" / "code-search-service.py").exists())
            self.assertTrue((repo / "ai" / "install_context_tools.py").exists())
            self.assertTrue((repo / "ai" / "locate-code.py").exists())
            self.assertTrue((repo / "ai" / "summarize-loop-run.py").exists())
            self.assertTrue((repo / "ai" / "benchmark-loop-runs.py").exists())
            self.assertTrue((repo / "ai" / "init-spec.py").exists())
            self.assertTrue((repo / "ai" / "plan-to-task-cards.py").exists())
            self.assertTrue((repo / "ai" / "init-plan.py").exists())
            self.assertTrue((repo / "ai" / "session-catchup.py").exists())
            self.assertTrue((repo / "ai" / "validate-parallel-plan.py").exists())
            self.assertTrue((repo / ".worktrees" / ".gitkeep").exists())
            gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("/.worktrees/*", gitignore)
            self.assertIn("!/.worktrees/.gitkeep", gitignore)

    def test_install_preserves_gitignore_and_adds_worktree_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            (repo / ".gitignore").write_text("node_modules/\n.worktrees/\n", encoding="utf-8")

            self.run_installer(repo)
            self.run_installer(repo)

            lines = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertIn("node_modules/", lines)
            self.assertNotIn(".worktrees/", lines)
            self.assertEqual(lines.count("/.worktrees/*"), 1)
            self.assertEqual(lines.count("!/.worktrees/.gitkeep"), 1)

    def test_local_only_uses_git_info_exclude_without_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)

            result = self.run_installer(repo, "--local-only")

            self.assertIn("local-only control plane", result.stdout)
            self.assertFalse((repo / ".gitignore").exists())
            exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
            self.assertIn("/AGENTS.md", exclude)
            self.assertIn("/CLAUDE.md", exclude)
            self.assertIn("/ai/", exclude)
            self.assertIn("/.worktrees/", exclude)

    def test_local_only_warns_without_git_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            result = self.run_installer(repo, "--local-only")

            self.assertIn("git info/exclude unavailable", result.stdout)
            self.assertFalse((repo / ".gitignore").exists())

    def test_help_does_not_create_repository_named_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )

        self.assertIn("--update-workflow-files", result.stdout)
        self.assertIn("--local-only", result.stdout)
        self.assertFalse((ROOT / "--help").exists())

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

    def test_existing_plain_workflow_files_are_outdated_until_explicit_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)
            dispatch = repo / "ai" / "dispatch-to-claude.sh"
            dispatch.write_text("# local old dispatch\n", encoding="utf-8")

            second = self.run_installer(repo)

            self.assertIn("outdated: ai/dispatch-to-claude.sh", second.stdout)
            self.assertIn("--update-workflow-files", second.stdout)
            self.assertEqual(dispatch.read_text(encoding="utf-8"), "# local old dispatch\n")

    def test_update_workflow_files_refreshes_existing_plain_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)
            dispatch = repo / "ai" / "dispatch-to-claude.sh"
            dispatch.write_text("# local old dispatch\n", encoding="utf-8")

            second = self.run_installer(repo, "--update-workflow-files")

            self.assertIn("updated: ai/dispatch-to-claude.sh", second.stdout)
            self.assertIn("You are the executor in a Codex/Claude Code workflow.", dispatch.read_text(encoding="utf-8"))

    def test_installed_agent_rules_include_execution_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            claude = (repo / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("Token Budget and Delegation Contract", agents)
            self.assertIn("test-execution responsibility", agents)
            self.assertIn("Claude may write tests", agents)
            self.assertIn("renders a smaller Claude execution card", agents)
            self.assertIn("Codex-only planning fields", agents)
            self.assertIn("Builder Claude", agents)
            self.assertIn("Checker/Test Claude", agents)
            self.assertIn("Builder tasks do not write acceptance tests", agents)
            self.assertIn("permission/tool approval risk", agents)
            self.assertIn("misdiagnosed as Claude execution failure", agents)
            self.assertIn("Dirty source or stale HEAD is a delegation blocker", agents)
            self.assertIn("restore a reliable Claude base", agents)
            self.assertIn("partial diff matches the plan", agents)
            self.assertIn("Avoid acknowledgement loops", agents)
            self.assertIn("acknowledgement only", agents)
            self.assertIn("Seeded and fallback reports are not valid", agents)
            self.assertIn("AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT", agents)
            self.assertIn("previous untracked task cards are delegation blockers", agents)
            self.assertIn("CLAUDE_CODE_NETWORK_MONITOR=1", agents)
            self.assertIn("network/proxy/auth", agents)
            self.assertIn("monitoring escalation ladder", agents)
            self.assertIn("L4 kill only after multiple evidence sources agree", agents)
            self.assertIn("Spec Gate", agents)
            self.assertIn("Codex Spark Gate", agents)
            self.assertIn("Worktree / Large Repo Strategy Gate", agents)
            self.assertIn("Parallel Execution Gate", agents)
            self.assertIn("gpt-5.3-codex-spark", agents)
            self.assertIn("parallel-planner", agents)
            self.assertIn("read-only advisory", agents)
            self.assertIn("strict schema-v1 JSON proposal only", agents)
            self.assertIn("never executes or dispatches", agents)
            self.assertIn("Root Cause Gate", agents)
            self.assertIn("Test-First / TDD Contract", agents)
            self.assertIn("Finish Branch Gate", agents)
            self.assertIn("one blocking acknowledgement per task or phase", agents)
            self.assertIn("Phase Responsibility Matrix", agents)
            self.assertIn("Evidence compression", claude)
            self.assertIn("CLAUDE_TASK_CARD.md", claude)
            self.assertIn("Builder tasks", claude)
            self.assertIn("Checker/Test tasks", claude)
            self.assertIn("Stall / Ambiguity Triage", claude)
            self.assertIn("orchestration ambiguity", claude)
            self.assertIn("Execution Progress", claude)
            self.assertIn("Direction and boundary acknowledgement", claude)
            self.assertIn("Do not turn acknowledgement into a loop", claude)
            self.assertIn("continue implementation in the same run", claude)
            self.assertIn("not a valid final report", claude)
            self.assertIn("Spec, root cause, and test-first discipline", claude)
            self.assertIn("failing test or failing evidence before production edits", claude)
            self.assertIn("Codex Spark evidence", claude)
            self.assertIn("Parallel Execution Gate", claude)

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
            self.assertIn('CLAUDE_CODE_NETWORK_MONITOR="${CLAUDE_CODE_NETWORK_MONITOR:-0}"', dispatch)
            self.assertIn('CLAUDE_CODE_EXECUTION_PROFILE="${CLAUDE_CODE_EXECUTION_PROFILE:-balanced}"', dispatch)
            self.assertIn('DEFAULT_WORKTREE_STRATEGY="fresh"', dispatch)
            self.assertIn('DEFAULT_TASK_CARD_VIEW="compact"', dispatch)
            self.assertIn('DEFAULT_PROMPT_PROFILE="brief"', dispatch)
            self.assertIn('DEFAULT_EVIDENCE_MODE="full"', dispatch)
            self.assertIn('DEFAULT_WORKTREE_STRATEGY="reuse-managed"', dispatch)
            self.assertIn('DEFAULT_REUSE_WORKTREE_RESET="0"', dispatch)
            self.assertIn('DEFAULT_EVIDENCE_MODE="summary"', dispatch)
            self.assertIn('CLAUDE_CODE_WORKTREE_STRATEGY="${CLAUDE_CODE_WORKTREE_STRATEGY:-$DEFAULT_WORKTREE_STRATEGY}"', dispatch)
            self.assertIn('CLAUDE_CODE_LARGE_REPO_MODE="${CLAUDE_CODE_LARGE_REPO_MODE:-$DEFAULT_LARGE_REPO_MODE}"', dispatch)
            self.assertIn('CLAUDE_CODE_TASK_CARD_VIEW="${CLAUDE_CODE_TASK_CARD_VIEW:-$DEFAULT_TASK_CARD_VIEW}"', dispatch)
            self.assertIn('CLAUDE_CODE_PROMPT_PROFILE="${CLAUDE_CODE_PROMPT_PROFILE:-$DEFAULT_PROMPT_PROFILE}"', dispatch)
            self.assertIn('CLAUDE_CODE_EVIDENCE_MODE="${CLAUDE_CODE_EVIDENCE_MODE:-$DEFAULT_EVIDENCE_MODE}"', dispatch)
            self.assertIn('CLAUDE_CODE_CHECKER_DISCOVER="${CLAUDE_CODE_CHECKER_DISCOVER:-$DEFAULT_CHECKER_DISCOVER}"', dispatch)
            self.assertIn('REUSE_WORKTREE_DIR="${WORKTREE_ROOT}/reuse/claude-managed"', dispatch)
            self.assertIn('CLAUDE_CODE_NETWORK_HEALTHCHECK_URL', dispatch)
            self.assertIn('NETWORK_FILE="${WORKTREE_ROOT}/${TASK_ID}.network.log"', dispatch)
            self.assertIn("write_network_header", dispatch)
            self.assertIn("capture_network_snapshot", dispatch)
            self.assertIn("network_socket_output", dispatch)
            self.assertIn("network_monitor=${CLAUDE_CODE_NETWORK_MONITOR}", dispatch)
            self.assertIn('PID_FILE="${WORKTREE_ROOT}/${TASK_ID}.pid"', dispatch)
            self.assertIn('PROGRESS_FILE="${WORKTREE_ROOT}/${TASK_ID}.progress.log"', dispatch)
            self.assertIn('CLAUDE_PROGRESS_FILE="${WORKTREE_ROOT}/${TASK_ID}.claude-progress.md"', dispatch)
            self.assertIn("TASK_CARD_FULL.md", dispatch)
            self.assertIn("CLAUDE_TASK_CARD.md", dispatch)
            self.assertIn("render_claude_task_card", dispatch)
            self.assertIn("Codex-only planning and control-plane sections are omitted", dispatch)
            self.assertIn("--- CLAUDE EXECUTION CARD ---", dispatch)
            self.assertIn('cat "${WORKTREE_DIR}/CLAUDE_TASK_CARD.md" >> "${WORKTREE_DIR}/CLAUDE_PROMPT.md"', dispatch)
            self.assertIn('|| name == "Direction Review Gate"', dispatch)
            self.assertIn('Update it before doing substantial exploration or edits', dispatch)
            self.assertIn("First identify the task mode", dispatch)
            self.assertIn("mixed-exception", dispatch)
            self.assertIn("orchestration ambiguity", dispatch)
            self.assertIn("permission/tool approval blocker", dispatch)
            self.assertIn("Phase Responsibility Matrix", dispatch)
            self.assertIn("Builder tasks implement and report direction", dispatch)
            self.assertIn("Checker/Test tasks write or update tests", dispatch)
            self.assertIn("Execution Progress", dispatch)
            self.assertIn("Direction / Boundary Acknowledgement", dispatch)
            self.assertIn("Do not create an acknowledgement loop", dispatch)
            self.assertIn("continue implementation in the same run", dispatch)
            self.assertIn('FALLBACK_REPORT_MARKER="AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT"', dispatch)
            self.assertIn("Dispatch evidence classification", dispatch)
            self.assertIn("This fallback report is not a valid Claude report.", dispatch)
            self.assertIn('claude_progress_bytes=${CLAUDE_PROGRESS_BYTES}', dispatch)
            self.assertIn('claude_task_bytes=${CLAUDE_TASK_BYTES}', dispatch)
            self.assertIn('worktree_changes=${WORKTREE_CHANGES}', dispatch)
            self.assertIn('worktree_changed=${WORKTREE_CHANGED}', dispatch)
            self.assertIn('LAST_WORKTREE_DIGEST="$(worktree_digest)"', dispatch)
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
            self.assertIn('delegation blocker, not a Codex takeover trigger', dispatch)
            self.assertIn('Restore delegation first', dispatch)
            self.assertIn('grep -vxF "$TASK_CARD_REL"', dispatch)
            self.assertIn('Phase-gate requirements:', dispatch)
            self.assertIn('## Execution Phases', dispatch)
            self.assertIn('Wait policy requirements:', dispatch)
            self.assertIn('## Wait Policy', dispatch)
            self.assertIn('Unknowns and decision gates:', dispatch)
            self.assertIn('## Execution Readiness Gate', dispatch)
            self.assertIn('## Unknowns', dispatch)
            self.assertIn('## Decision Gates', dispatch)
            self.assertIn('## Handoff Contract', dispatch)
            self.assertIn('Plan Match: full / partial / off-plan', dispatch)
            self.assertIn('Validation Confidence: high / medium / low', dispatch)
            self.assertIn('Reviewer Should Check', dispatch)
            self.assertIn('Deviations From Plan', dispatch)
            self.assertIn('before running long validation commands', dispatch)
            self.assertIn('BASE_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"', dispatch)
            self.assertIn('git worktree add -b "$branch_name" "$WORKTREE_DIR" "$BASE_COMMIT" ||', dispatch)
            self.assertIn("CLAUDE_CODE_REUSE_WORKTREE_RESET=1", dispatch)
            self.assertIn("CLAUDE_CODE_LARGE_REPO_MODE=1 avoids expensive untracked-file scans", dispatch)
            self.assertIn("compact_skip_section", dispatch)
            self.assertIn("Full patch generation was skipped to reduce large-repository I/O and review-token cost.", dispatch)
            self.assertIn("Execution Profile:", dispatch)
            self.assertIn("Prompt Profile:", dispatch)
            self.assertIn("Evidence Mode:", dispatch)
            self.assertIn("CLAUDE_CODE_CHECKER_COMMANDS", dispatch)
            self.assertIn('CHECKER_REPORT_FILE="${WORKTREE_ROOT}/${TASK_ID}.checker-report.md"', dispatch)
            self.assertIn('CHECK_SCRIPT="${SCRIPT_DIR}/check-worktree.sh"', dispatch)
            self.assertIn('Checker Report:', dispatch)
            self.assertIn('RAW_RESULT_FILE="${WORKTREE_ROOT}/${TASK_ID}.result.raw.txt"', dispatch)
            self.assertIn("DISPATCH-SEEDED-PROGRESS", dispatch)
            self.assertIn("DISPATCH-SEEDED-REPORT", dispatch)
            self.assertIn("Remove the dispatcher seeded-report marker", dispatch)
            self.assertIn('ensure_result_json "missing_or_invalid_result_json"', dispatch)
            self.assertIn('## Staged Diff', dispatch)
            self.assertIn('git diff --cached', dispatch)
            self.assertIn('Raw Result:', dispatch)
            self.assertIn("## Testing Responsibility", dispatch)
            self.assertIn("user-requested, acceptance-critical", dispatch)
            self.assertIn("Treat writing/updating test code and running test commands as separate responsibilities", dispatch)

    def test_installed_checker_helper_and_review_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            checker = (repo / "ai" / "check-worktree.sh").read_text(encoding="utf-8")
            review = (repo / "ai" / "review-with-codex.sh").read_text(encoding="utf-8")
            task_template = (repo / "ai" / "task-card-template.md").read_text(encoding="utf-8")
            evidence_template = (repo / "ai" / "evidence-packet-template.md").read_text(encoding="utf-8")
            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            claude = (repo / "CLAUDE.md").read_text(encoding="utf-8")

            self.assertIn("Checker Report", checker)
            self.assertIn("ALL GREEN", checker)
            self.assertIn("FAILED", checker)
            self.assertIn("--command", checker)
            self.assertIn("--task-card", checker)
            self.assertIn("Local validation is disabled", checker)
            self.assertIn("SKIPPED", checker)
            self.assertIn("Checker Mutation Guard", checker)
            self.assertIn("checker-report.md", review)
            self.assertIn("Treat checker evidence as first-class", review)
            self.assertIn("Unknowns / Decision Gates", review)
            self.assertIn("Deviations From Plan", review)
            self.assertIn("Reviewer Understanding", review)
            self.assertIn("Review-to-Next-Task Contract", review)
            self.assertIn("direct-intervention threshold", review)
            self.assertIn("NOT enough for Codex takeover", review)
            self.assertIn("Prior-session Claude failures are context only", review)
            self.assertIn("Missing result/report/acceptance prose is an evidence gap", review)
            self.assertIn("tests/evidence only", review)
            self.assertIn("control-plane salvage", review)
            self.assertIn("preserve the reviewer-accepted first-round direction", review)
            self.assertIn("Delegation Continuity Gate", review)
            self.assertIn("phase accepted with follow-up required", review)
            self.assertIn("Respect Task Mode", review)
            self.assertIn("dispatch a checker-test Claude task", review)
            self.assertIn("Task Mode / Direction Review", review)
            self.assertIn("Phase Responsibility", review)
            self.assertIn("Stall / Ambiguity Triage", review)
            self.assertIn("Delegation Restoration", review)
            self.assertIn("Dirty source alone is not enough for Codex takeover", review)
            self.assertIn("network/proxy/auth/model wait", review)
            self.assertIn("process socket states", review)
            self.assertIn("permission/tool approval blocker", review)
            self.assertIn("mixed-role task", review)
            self.assertIn("Direction / Boundary Acknowledgement", review)
            self.assertIn("acknowledgement loops", review)
            self.assertIn("New Handoff Contract", review)
            self.assertIn("### Testing Responsibility", review)
            self.assertIn("user-requested, acceptance-critical, or out of scope", review)
            self.assertIn("Check Spec Gate if present", review)
            self.assertIn("Check Root Cause Gate", review)
            self.assertIn("Check Test-First / TDD Contract", review)
            self.assertIn("Check Finish Branch Gate", review)
            self.assertIn("Check Small Change Fast Path Gate", review)
            self.assertIn("Check Codex Spark Gate", review)
            self.assertIn("task-size classification", review)
            self.assertIn("routing recommendation", review)
            self.assertIn("accepted_suggestions", review)
            self.assertIn("ignored_suggestions", review)
            self.assertIn("conflicts_with_claude", review)
            self.assertIn("acceptance_satisfied_by_spark", review)
            self.assertIn("Check Worktree / Large Repo Strategy Gate", review)
            self.assertIn("Claude Context Packet", review)
            self.assertIn("Check Parallel Execution Gate", review)
            self.assertIn("### Codex Spark Gate", review)
            self.assertIn("### Worktree / Large Repo Strategy", review)
            self.assertIn("### Parallel Execution Gate", review)
            self.assertIn("### Spec Gate", review)
            self.assertIn("### Root Cause Gate", review)
            self.assertIn("### Test-First / TDD Contract", review)
            self.assertIn("### Finish Branch Gate", review)
            self.assertIn("### Small Change Fast Path", review)
            self.assertIn("New Spec / Spark / Parallel / Root Cause / TDD / Finish Branch requirements", review)
            self.assertIn("Validation Contract", task_template)
            self.assertIn("Local validation allowed?", task_template)
            self.assertIn("```bash validation", task_template)
            self.assertIn("## Task Mode", task_template)
            self.assertIn("builder / checker-test", task_template)
            self.assertIn("Mixed-task guard", task_template)
            self.assertIn("## Phase Responsibility Matrix", task_template)
            self.assertIn("## Stall / Ambiguity Triage", task_template)
            self.assertIn("## Delegation Restoration Gate", task_template)
            self.assertIn("## Worktree / Large Repo Strategy Gate", task_template)
            self.assertIn("CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed", task_template)
            self.assertIn("CLAUDE_CODE_LARGE_REPO_MODE=1", task_template)
            self.assertIn("HEAD contains required prior context for Claude?", task_template)
            self.assertIn("Dirty source blocks reliable Claude dispatch?", task_template)
            self.assertIn("Permission/tool approval risk?", task_template)
            self.assertIn("Network diagnostics needed?", task_template)
            self.assertIn("Escalation confirmations before details", task_template)
            self.assertIn("Monitor escalation ladder", task_template)
            self.assertIn("## Direction Review Gate", task_template)
            self.assertIn("## Direction / Boundary Acknowledgement", task_template)
            self.assertIn("Maximum acknowledgement rounds", task_template)
            self.assertIn("Anti-loop rule", task_template)
            self.assertIn("## Small Change Fast Path Gate", task_template)
            self.assertIn("Reason for skipping Claude dispatch", task_template)
            self.assertIn("## Execution Progress", task_template)
            self.assertIn("Builder may run narrow sanity checks?", task_template)
            self.assertIn("Broad acceptance test execution owner", task_template)
            self.assertIn("## Task Card Views", task_template)
            self.assertIn("Execution profile", task_template)
            self.assertIn("CLAUDE_CODE_EXECUTION_PROFILE=safe", task_template)
            self.assertIn("CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo", task_template)
            self.assertIn("Do not maintain a second hand-written Claude card", task_template)
            self.assertIn("## Claude Context Packet", task_template)
            self.assertIn("Target files/modules", task_template)
            self.assertIn("Do not read / do not modify", task_template)
            self.assertIn("first-round direction Codex will salvage", task_template)
            self.assertIn("## Delegation Continuity Gate", task_template)
            self.assertIn("Remaining implementation/test-writing phases", task_template)
            self.assertIn("Continuation After Accept", task_template)
            self.assertIn("## Testing Responsibility", task_template)
            self.assertIn("Claude must write or update tests?", task_template)
            self.assertIn("Acceptance evidence owner", task_template)
            self.assertIn("Evidence-only redispatch allowed?", task_template)
            self.assertIn("acceptance-critical", task_template)
            self.assertIn("## Execution Readiness Gate", task_template)
            self.assertIn("## Goal Loop Contract", task_template)
            self.assertIn("Loop type", task_template)
            self.assertIn("## Codex Spark Gate", task_template)
            self.assertIn("gpt-5.3-codex-spark", task_template)
            self.assertIn("task-size-classifier", task_template)
            self.assertIn("Spark can replace Claude Builder?", task_template)
            self.assertIn("Spark result can satisfy acceptance?", task_template)
            self.assertIn("advisory input only", task_template)
            self.assertIn("Micro-builder max files", task_template)
            self.assertIn("Micro-builder public API or contract risk?", task_template)
            self.assertIn("When `Spark purpose` is `auto`", task_template)
            self.assertIn("## Small Change Fast Path Follow-up", evidence_template)
            self.assertIn("Claude dispatch skipped?", evidence_template)
            self.assertIn("## Codex Spark Follow-up", evidence_template)
            self.assertIn("Task size classification", evidence_template)
            self.assertIn("Spark routing recommendation", evidence_template)
            self.assertIn("Spark requested mode", evidence_template)
            self.assertIn("accepted_suggestions", evidence_template)
            self.assertIn("ignored_suggestions", evidence_template)
            self.assertIn("conflicts_with_claude", evidence_template)
            self.assertIn("acceptance_satisfied_by_spark", evidence_template)
            self.assertIn("## Parallel Execution Gate", task_template)
            self.assertIn("ai/run-parallel-loop.sh", task_template)
            self.assertIn("## Parallel Execution Follow-up", evidence_template)
            self.assertIn("Automatic merge performed?", evidence_template)
            self.assertIn("Success signal", task_template)
            self.assertIn("Benchmark tags", task_template)
            self.assertIn("## Advisor Gate", task_template)
            self.assertIn("Advisor required?", task_template)
            self.assertIn("Read-only orientation required before advisor?", task_template)
            self.assertIn("Required before state-changing edit?", task_template)
            self.assertIn("Reconcile conflicts with local evidence?", task_template)
            self.assertIn("## Spec Gate", task_template)
            self.assertIn("Spec artifact", task_template)
            self.assertIn("Plan/task-card derivation path", task_template)
            self.assertIn("## Root Cause Gate", task_template)
            self.assertIn("Root cause required before fix?", task_template)
            self.assertIn("Fix targets root cause, not symptom?", task_template)
            self.assertIn("## Test-First / TDD Contract", task_template)
            self.assertIn("Failing test required before production change?", task_template)
            self.assertIn("Red evidence command/artifact", task_template)
            self.assertIn("## Finish Branch Gate", task_template)
            self.assertIn("verification rerun fresh", task_template)
            self.assertIn("## Unknowns", task_template)
            self.assertIn("Unknown-unknown scan request", task_template)
            self.assertIn("Questions that would change architecture", task_template)
            self.assertIn("Reference examples / source-of-truth files", task_template)
            self.assertIn("Deviation recording path", task_template)
            self.assertIn("## Decision Gates", task_template)
            self.assertIn("## Handoff Contract", task_template)
            self.assertIn("Prior-session failure evidence", task_template)
            self.assertIn("Codex direct intervention eligible?", task_template)
            self.assertIn("Checker Report", evidence_template)
            self.assertIn("## Task Mode / Direction Review Follow-up", evidence_template)
            self.assertIn("Checker/Test should be dispatched next?", evidence_template)
            self.assertIn("## Phase Responsibility Follow-up", evidence_template)
            self.assertIn("## Stall / Ambiguity Triage Follow-up", evidence_template)
            self.assertIn("## Delegation Restoration Follow-up", evidence_template)
            self.assertIn("## Worktree / Large Repo Follow-up", evidence_template)
            self.assertIn("Untracked patch evidence skipped?", evidence_template)
            self.assertIn("Claude Context Packet provided?", evidence_template)
            self.assertIn("Broad repository search avoided?", evidence_template)
            self.assertIn("Delegation blocker", evidence_template)
            self.assertIn("HEAD contained required prior context?", evidence_template)
            self.assertIn("permission/tool approval", evidence_template)
            self.assertIn("## Direction / Boundary Acknowledgement Follow-up", evidence_template)
            self.assertIn("Repeated same approval request after proceed?", evidence_template)
            self.assertIn("## Testing Responsibility Follow-up", evidence_template)
            self.assertIn("Validation command source", evidence_template)
            self.assertIn("Local validation allowed by task card?", evidence_template)
            self.assertIn("Codex rerun required for blocked validation", evidence_template)
            self.assertIn("## Evidence Gap Recovery", evidence_template)
            self.assertIn("Evidence reconstructed by Codex?", evidence_template)
            self.assertIn("Network log", evidence_template)
            self.assertIn("Network monitor enabled?", evidence_template)
            self.assertIn("## Delegation Continuity", evidence_template)
            self.assertIn("Whole task accepted?", evidence_template)
            self.assertIn("## Control-Plane Salvage", evidence_template)
            self.assertIn("First-round direction salvaged", evidence_template)
            self.assertIn("Codex direct intervention requested?", evidence_template)
            self.assertIn("## Unknowns and Deviations", evidence_template)
            self.assertIn("## Goal Loop Result", evidence_template)
            self.assertIn("Success signal met?", evidence_template)
            self.assertIn("Stop rule reached", evidence_template)
            self.assertIn("## Advisor Follow-up", evidence_template)
            self.assertIn("Advisor consulted?", evidence_template)
            self.assertIn("Advisor stop reason / truncation", evidence_template)
            self.assertIn("Conflict with local evidence?", evidence_template)
            self.assertIn("## Spec Follow-up", evidence_template)
            self.assertIn("Implementation matched spec?", evidence_template)
            self.assertIn("## Root Cause Follow-up", evidence_template)
            self.assertIn("Root cause identified?", evidence_template)
            self.assertIn("## Test-First / TDD Follow-up", evidence_template)
            self.assertIn("Red evidence artifact", evidence_template)
            self.assertIn("## Finish Branch Follow-up", evidence_template)
            self.assertIn("## Reviewer Briefing", evidence_template)
            self.assertIn("Plan Match", evidence_template)
            self.assertIn("Validation Confidence", evidence_template)
            self.assertIn("Reviewer Should Check", evidence_template)
            self.assertIn("Deviations From Plan", evidence_template)
            self.assertIn("Locator used?", evidence_template)
            self.assertIn("Loop Engineering Validation Contract", agents)
            self.assertIn("do not use web search", agents)
            self.assertIn("local helper initialization", agents)
            self.assertIn("default `--mode auto` is for low-risk routing", agents)
            self.assertIn("task-size-classifier", agents)
            self.assertIn("Claude Context Packet", agents)
            self.assertIn("locate-code.py", agents)
            self.assertIn("cannot replace Claude Builder ownership", agents)
            self.assertIn("exact task-card validation commands", agents)
            self.assertIn("Local validation allowed?", agents)
            self.assertIn("Codex Intervention Policy", agents)
            self.assertIn("not permission for Codex to patch", agents)
            self.assertIn("Prior-session Claude failures are carry-forward context", agents)
            self.assertIn("Missing Claude `result.json`, `CLAUDE_REPORT.md`, or acceptance evidence is an evidence gap", agents)
            self.assertIn("current-task repeated failure", agents)
            self.assertIn("salvage any reviewer-accepted first-round direction", agents)
            self.assertIn("Small Change Fast Path Gate", agents)
            self.assertIn("Small low-risk edits may stay Codex-owned", agents)
            self.assertIn("accepting one Claude round closes only that phase", agents)
            self.assertIn("remaining implementation/test-writing phases stay Claude-owned", agents)
            self.assertIn("Context Lifecycle", agents)
            self.assertIn("decision gates", agents)
            self.assertIn("Handoff Contract", agents)
            self.assertIn("Loop stop rules", claude)
            self.assertIn("Progress memory", claude)
            self.assertIn("Current Phase", claude)
            self.assertIn("Execution Readiness Gate", claude)
            self.assertIn("Handoff Contract", claude)
            self.assertIn("Unknowns and Decision Gates", claude)
            self.assertIn("tests/evidence only", claude)
            self.assertIn("--no-discover --command", claude)
            self.assertIn("Local validation allowed?", claude)
            self.assertIn("approval or sandbox policy blocks validation", claude)
            self.assertIn("which phases remain for the next Claude dispatch", claude)

    def test_installed_run_loop_preserves_dispatch_observability_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            run_loop = (repo / "ai" / "run-loop.sh").read_text(encoding="utf-8")
            self.assertIn("Dispatch execution requires Claude Code", run_loop)
            self.assertIn("doctor_workflow.py to verify readiness", run_loop)
            self.assertIn('CLAUDE_PID_FILE="$(parse_path "Claude PID" "$DISPATCH_LOG")"', run_loop)
            self.assertIn('RAW_RESULT_FILE="$(parse_path "Raw Result" "$DISPATCH_LOG")"', run_loop)
            self.assertIn("CLAUDE_CODE_ADAPTIVE_WAIT", run_loop)
            self.assertIn("adaptive_timeout_observed", run_loop)
            self.assertIn("Adaptive dispatch timeout", run_loop)
            self.assertIn('PROGRESS_FILE="$(parse_path "Progress Log" "$DISPATCH_LOG")"', run_loop)
            self.assertIn('NETWORK_FILE="$(parse_path "Network Log" "$DISPATCH_LOG")"', run_loop)
            self.assertIn('CLAUDE_PROGRESS_FILE="$(parse_path "Claude Progress" "$DISPATCH_LOG")"', run_loop)
            self.assertIn('"$CLAUDE_PID_FILE" "$PROGRESS_FILE"', run_loop)
            self.assertIn('"$PROGRESS_FILE" "$NETWORK_FILE" "$CLAUDE_PID_FILE"', run_loop)
            self.assertIn('"$REPORT_FILE" "$RAW_RESULT_FILE"', run_loop)
            self.assertIn('CHECKER_REPORT_FILE="$(parse_path "Checker Report" "$DISPATCH_LOG")"', run_loop)
            self.assertIn('"$CHECKER_REPORT_FILE"', run_loop)
            self.assertIn('### Checker Report', run_loop)
            self.assertIn('QUALITY_SUMMARY="${RUN_DIR}/loop-quality-summary.md"', run_loop)
            self.assertIn('QUALITY_JSON="${RUN_DIR}/loop-quality-summary.json"', run_loop)
            self.assertIn('SUMMARY_SCRIPT="${SCRIPT_DIR}/summarize-loop-run.py"', run_loop)
            self.assertIn('write_quality_summary', run_loop)
            self.assertIn('LOOP_EVENTS="${RUN_DIR}/loop-events.jsonl"', run_loop)
            self.assertIn('write_loop_event "run_start"', run_loop)
            self.assertIn('write_loop_event "decision"', run_loop)
            self.assertIn('### Claude Progress', run_loop)
            self.assertIn('### Claude Network Diagnostics', run_loop)
            self.assertIn('### Claude Self-Reported Progress', run_loop)
            self.assertIn('Review-to-Next-Task Contract', run_loop)
            self.assertIn('New Handoff Contract', run_loop)
            self.assertIn('"$CLAUDE_PROGRESS_FILE" "$PROGRESS_FILE" "$NETWORK_FILE" "$CLAUDE_PID_FILE" 2>&1 | tee "$REVIEW_OUTPUT"', run_loop)

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
            self.assertIn('kill -0 "$pid"', status)
            self.assertIn('tail -20 "$PROGRESS_FILE"', status)
            self.assertIn('select_claude_progress_file', status)
            self.assertIn('tail -40 "$TAIL_CLAUDE_PROGRESS_FILE"', status)
            self.assertIn("Evidence: $EVIDENCE_STATE", status)
            self.assertIn("AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT", status)
            self.assertIn('NETWORK_FILE="${PREFIX}.network.log"', status)
            self.assertIn("Network: $NETWORK_SUMMARY", status)
            self.assertIn("## Network Tail", status)
            self.assertIn("Monitor policy: $MONITOR_POLICY", status)
            self.assertIn("Machine monitor:", status)
            self.assertIn("monitor_level=", status)
            self.assertIn("corroborate with progress, status, diff, and network evidence", status)
            self.assertIn('Partial Worktree Triage', status)
            self.assertIn('Wait Policy:', status)
            self.assertIn('Action: $ACTION', status)
            self.assertIn('Risk summary: $RISK_SUMMARY', status)
            self.assertIn('review the partial diff against the task card', status)
            self.assertIn('CLAUDE CODE WATCH', watch)
            self.assertIn('WAIT_PROFILE="${CLAUDE_CODE_WAIT_PROFILE:-medium}"', watch)
            self.assertIn('recommended_action', watch)
            self.assertIn('REVIEW_PARTIAL_DIFF', watch)
            self.assertIn('CONSIDER_INTERRUPT', watch)
            self.assertIn('LIKELY_STUCK', watch)
            self.assertIn('partial_risk_summary', watch)
            self.assertIn('progress_bar', watch)
            self.assertIn('LIVE_CLAUDE_PROGRESS_FILE', watch)
            self.assertIn('NETWORK_FILE="${PREFIX}.network.log"', watch)
            self.assertIn("NETWORK  :", watch)
            self.assertIn("MONITOR  :", watch)
            self.assertIn("MACHINE  :", watch)
            self.assertIn("monitor_level=", watch)
            self.assertIn("CLAUDE_CODE_MONITOR_ESCALATION_CONFIRMATIONS", watch)
            self.assertIn("--escalation-confirmations", watch)
            self.assertIn("monitor_suspect_count", watch)
            self.assertIn("## Network Tail", watch)
            self.assertIn('select_claude_progress_file', watch)
            self.assertIn('stuck_reason', watch)
            self.assertIn('partial-implementation-present', watch)
            self.assertIn('Partial Worktree', watch)
            self.assertIn('changes=${worktree_changes}', watch)
            self.assertIn('--details', watch)
            self.assertIn('--plain', watch)
            self.assertIn('--stale-after', watch)
            self.assertIn('--wait-profile', watch)
            self.assertIn('--startup-grace', watch)
            self.assertIn('--interrupt-after', watch)
            self.assertIn('--escalation-confirmations', watch)
            self.assertIn('Sending TERM to Claude process', kill)
            self.assertIn('kill -9 "$PID"', kill)
            self.assertIn('Claude process is still running', cleanup)
            self.assertIn('git worktree remove', cleanup)
            self.assertIn('Evidence artifacts were preserved', cleanup)

    # --- Process-role monitoring tests ---

    def _setup_worktree(self, repo, task_id):
        """Create minimal worktree artifacts for status/watch tests."""
        worktrees = repo / ".worktrees"
        (worktrees / task_id).mkdir(parents=True, exist_ok=True)
        (worktrees / f"{task_id}.progress.log").write_text(
            "[2099-01-01 00:00:00] Claude still running: pid=1, elapsed_seconds=5, "
            "quiet_seconds=5, result_bytes=0, status_bytes=0, report_bytes=0, "
            "claude_progress_bytes=12\n",
            encoding="utf-8",
        )
        (worktrees / f"{task_id}.result.json").write_text("", encoding="utf-8")
        (worktrees / f"{task_id}.status.txt").write_text("", encoding="utf-8")
        (worktrees / f"{task_id}.claude-progress.md").write_text(
            "Working on validation\n", encoding="utf-8"
        )
        return worktrees

    def test_status_claude_machine_line_has_required_fields(self):
        """Status machine line must contain monitor_level/action/evidence_state/
        quiet_seconds/suspect_count plus dispatcher/claude/checker/overall_running."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-000000"
            worktrees = self._setup_worktree(repo, task_id)
            # All roles stopped: write non-existent PIDs
            (worktrees / f"{task_id}.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.dispatcher.pid").write_text("99998", encoding="utf-8")
            (worktrees / f"{task_id}.claude.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.checker.pid").write_text("99997", encoding="utf-8")

            bash_exe = load_module()._find_bash()
            result = subprocess.run(
                [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id],
                cwd=str(repo),
                text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=True,
            )
            machine = [l for l in result.stdout.splitlines() if "Machine monitor:" in l]
            self.assertTrue(machine, "Expected a Machine monitor line in status output")
            line = machine[0]
            for field in ("monitor_level=", "action=", "evidence_state=",
                          "quiet_seconds=", "suspect_count=",
                          "dispatcher=", "claude=", "checker=",
                          "overall_running=", "running="):
                self.assertIn(field, line, f"Missing {field} in machine line")

    def test_status_claude_overall_running_includes_dispatcher(self):
        """overall_running=yes when only dispatcher is alive (finalization phase),
        but running must be no because Claude is not executing."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-000000"
            worktrees = self._setup_worktree(repo, task_id)
            # Use a Bash/MSYS-visible PID; a native Windows Python PID is not
            # necessarily meaningful to Git Bash `kill -0`.
            bash_exe = load_module()._find_bash()
            sleeper = subprocess.Popen(
                [bash_exe, "-c", "echo $$; exec sleep 60"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8",
            )
            my_pid = sleeper.stdout.readline().strip()
            (worktrees / f"{task_id}.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.dispatcher.pid").write_text(my_pid, encoding="utf-8")
            (worktrees / f"{task_id}.claude.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.checker.pid").write_text("99997", encoding="utf-8")

            try:
                result = subprocess.run(
                    [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id],
                    cwd=str(repo), text=True, encoding="utf-8", errors="replace",
                    capture_output=True, check=True,
                )
            finally:
                sleeper.terminate()
                sleeper.wait(timeout=10)
            self.assertIn("Overall running: yes", result.stdout)
            # Claude must explicitly be not-running
            self.assertIn("Claude: not-running", result.stdout)
            # Machine line: overall_running=yes but running=no
            machine = [l for l in result.stdout.splitlines() if "Machine monitor:" in l]
            self.assertTrue(machine)
            line = machine[0]
            self.assertIn("overall_running=yes", line)
            self.assertIn("running=no", line)

    def test_status_claude_overall_running_no_when_all_stopped(self):
        """overall_running=no when all roles are stopped."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-000000"
            worktrees = self._setup_worktree(repo, task_id)
            (worktrees / f"{task_id}.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.dispatcher.pid").write_text("99998", encoding="utf-8")
            (worktrees / f"{task_id}.claude.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.checker.pid").write_text("99997", encoding="utf-8")

            bash_exe = load_module()._find_bash()
            result = subprocess.run(
                [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id],
                cwd=str(repo),
                text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=True,
            )
            self.assertIn("Overall running: no", result.stdout)
            self.assertIn("Dispatcher: not-running", result.stdout)
            self.assertIn("Claude: not-running", result.stdout)
            self.assertIn("Checker: not-running", result.stdout)

    def test_status_claude_legacy_pid_fallback(self):
        """When new .claude.pid is missing but legacy .pid exists, Claude state
        falls back to the legacy PID file."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-000000"
            worktrees = self._setup_worktree(repo, task_id)
            # Only legacy PID file; no .claude.pid
            (worktrees / f"{task_id}.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.dispatcher.pid").write_text("99998", encoding="utf-8")
            # No .claude.pid file
            (worktrees / f"{task_id}.checker.pid").write_text("99997", encoding="utf-8")

            bash_exe = load_module()._find_bash()
            result = subprocess.run(
                [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id],
                cwd=str(repo),
                text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=True,
            )
            # Claude state should fall back to legacy .pid (which has 99999 = not-running)
            self.assertIn("Claude: not-running", result.stdout)
            # Legacy PID display
            self.assertIn("PID: 99999", result.stdout)

    def test_watch_plain_once_emits_machine_line(self):
        """watch-claude.sh --plain --once must emit a machine line for fast
        terminal artifacts."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-000000"
            worktrees = self._setup_worktree(repo, task_id)
            (worktrees / f"{task_id}.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.dispatcher.pid").write_text("99998", encoding="utf-8")
            (worktrees / f"{task_id}.claude.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.checker.pid").write_text("99997", encoding="utf-8")

            bash_exe = load_module()._find_bash()
            result = subprocess.run(
                [bash_exe, str(repo / "ai" / "watch-claude.sh"),
                 task_id, "--plain", "--once"],
                cwd=str(repo),
                text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=True,
            )
            machine = [l for l in result.stdout.splitlines()
                       if "machine:" in l and "monitor_level=" in l]
            self.assertTrue(machine,
                            "Expected a machine line in --plain --once output")
            line = machine[0]
            for field in ("monitor_level=", "action=", "evidence_state=",
                          "quiet_seconds=", "suspect_count=",
                          "running=", "overall_running=",
                          "dispatcher=", "claude=", "checker="):
                self.assertIn(field, line, f"Missing {field} in watch machine line")

    def test_watch_machine_line_overall_running_includes_dispatcher(self):
        """Watch machine line overall_running=yes when only dispatcher is alive."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-000000"
            worktrees = self._setup_worktree(repo, task_id)
            bash_exe = load_module()._find_bash()
            sleeper = subprocess.Popen(
                [bash_exe, "-c", "echo $$; exec sleep 60"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8",
            )
            my_pid = sleeper.stdout.readline().strip()
            (worktrees / f"{task_id}.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.dispatcher.pid").write_text(my_pid, encoding="utf-8")
            (worktrees / f"{task_id}.claude.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.checker.pid").write_text("99997", encoding="utf-8")

            try:
                result = subprocess.run(
                    [bash_exe, str(repo / "ai" / "watch-claude.sh"),
                     task_id, "--plain", "--once"],
                    cwd=str(repo), text=True, encoding="utf-8", errors="replace",
                    capture_output=True, check=True,
                )
            finally:
                sleeper.terminate()
                sleeper.wait(timeout=10)
            machine = [l for l in result.stdout.splitlines()
                       if "machine:" in l and "monitor_level=" in l]
            self.assertTrue(machine)
            line = machine[0]
            self.assertIn("overall_running=yes", line)
            self.assertIn("running=no", line)
            self.assertIn("dispatcher=running", line)
            self.assertIn("claude=not-running", line)

    def test_status_claude_role_pid_display(self):
        """Status output must show Dispatcher/Claude/Checker role lines."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-000000"
            worktrees = self._setup_worktree(repo, task_id)
            (worktrees / f"{task_id}.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.dispatcher.pid").write_text("99998", encoding="utf-8")
            (worktrees / f"{task_id}.claude.pid").write_text("99999", encoding="utf-8")
            (worktrees / f"{task_id}.checker.pid").write_text("99997", encoding="utf-8")

            bash_exe = load_module()._find_bash()
            result = subprocess.run(
                [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id],
                cwd=str(repo),
                text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=True,
            )
            self.assertIn("Dispatcher: ", result.stdout)
            self.assertIn("Claude: ", result.stdout)
            self.assertIn("Checker: ", result.stdout)
            self.assertIn("Overall running: ", result.stdout)

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
            self.assertIn("## Goal Loop Contract", task_card)
            self.assertIn("Success signal", task_card)
            self.assertIn("Stop on repeated failure?", task_card)
            self.assertIn("## Advisor Gate", task_card)
            self.assertIn("Advisor result visibility", task_card)
            self.assertIn("Fallback if advisor unavailable or cap reached", task_card)
            self.assertIn("## Spec Gate", task_card)
            self.assertIn("## Root Cause Gate", task_card)
            self.assertIn("## Test-First / TDD Contract", task_card)
            self.assertIn("## Finish Branch Gate", task_card)
            self.assertIn("## Parallel Execution Gate", task_card)
            self.assertIn("## Execution Readiness Gate", task_card)
            self.assertIn("Task is implementation-ready", task_card)
            self.assertIn("## Execution Phases", task_card)
            self.assertIn("Stop Before Next Phase?", task_card)
            self.assertIn("## Wait Policy", task_card)
            self.assertIn("Wait profile", task_card)
            self.assertIn("Partial diff review rule", task_card)
            self.assertIn("## Unknowns", task_card)
            self.assertIn("## Decision Gates", task_card)
            self.assertIn("## Handoff Contract", task_card)
            self.assertIn("## Claude Dispatch Progress", evidence)
            self.assertIn("## Phase Execution Evidence", evidence)
            self.assertIn("## Unknowns and Deviations", evidence)
            self.assertIn("## Goal Loop Result", evidence)
            self.assertIn("## Advisor Follow-up", evidence)
            self.assertIn("## Spec Follow-up", evidence)
            self.assertIn("## Root Cause Follow-up", evidence)
            self.assertIn("## Test-First / TDD Follow-up", evidence)
            self.assertIn("## Finish Branch Follow-up", evidence)
            self.assertIn("## Parallel Execution Follow-up", evidence)
            self.assertIn("## Reviewer Briefing", evidence)
            self.assertIn("Plan Match", evidence)
            self.assertIn("Validation Confidence", evidence)
            self.assertIn("Reviewer Should Check", evidence)
            self.assertIn("Claude PID", evidence)
            self.assertIn("Progress log", evidence)
            self.assertIn("Timed out?", evidence)
            self.assertIn("Fallback report generated?", evidence)

    def test_installed_templates_include_token_budget_and_delegation_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            task_card = (repo / "ai" / "task-card-template.md").read_text(encoding="utf-8")
            evidence = (repo / "ai" / "evidence-packet-template.md").read_text(encoding="utf-8")
            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            claude = (repo / "CLAUDE.md").read_text(encoding="utf-8")

            # Task card must have token budget and delegation fields
            self.assertIn("## Codex Context Budget", task_card)
            self.assertIn("## LSP / Locator / CodeGraph Evidence", task_card)
            self.assertIn("## High-Token Delegation Gate", task_card)
            self.assertIn("## Evidence Compression Requirements", task_card)

            # Evidence packet must have context budget and compression fields
            self.assertIn("## Context Budget Used", evidence)
            self.assertIn("## High-Token Work Delegated", evidence)
            self.assertIn("## Compressed Evidence Summary", evidence)

            # AGENTS.md managed section must describe the delegation contract
            self.assertIn("## Token Budget and Delegation Contract", agents)
            self.assertIn("low-token evidence", agents)
            self.assertIn("compressed evidence", agents)

            # CLAUDE.md managed section must describe evidence compression
            self.assertIn("Evidence compression", claude)
            self.assertIn("summaries and artifact paths", claude)

    def test_installed_templates_include_spark_stage_routing(self):
        """Required checks 1-7: validate Spark stage documentation propagation."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            task_template = (repo / "ai" / "task-card-template.md").read_text(encoding="utf-8")

            # Check 1: preflight-bundle and postflight-bundle present
            self.assertIn("preflight-bundle", agents)
            self.assertIn("postflight-bundle", agents)
            self.assertIn("preflight-bundle", task_template)
            self.assertIn("postflight-bundle", task_template)

            # Check 1: AI_SPARK_BUDGET_MODE present
            self.assertIn("AI_SPARK_BUDGET_MODE", agents)
            self.assertIn("AI_SPARK_BUDGET_MODE", task_template)

            # Check 1: balanced, aggressive, conservative present
            self.assertIn("balanced", agents)
            self.assertIn("aggressive", agents)
            self.assertIn("conservative", agents)

            # Check 1: at-most-three invocation recommendation
            self.assertIn("at most three", agents)
            self.assertIn("at most three", task_template)

            # Check 1: merge guardrails
            self.assertIn("never authorizes merge", agents)
            self.assertIn("strong Codex review remains required", agents)

            # Check 2: task-card template exposes budget mode, pipeline stage,
            # roles, call recommendation, and all nine new explicit modes
            nine_modes = [
                "observe-synthesizer", "task-card-drafter", "context-packet-builder",
                "preflight-bundle", "direction-precheck", "acceptance-matrix",
                "postflight-bundle", "revision-drafter", "lesson-extractor",
            ]
            for mode in nine_modes:
                self.assertIn(mode, task_template, f"Missing mode {mode} in task-card template")
            self.assertIn("Budget mode", task_template)
            self.assertIn("Pipeline stage", task_template)
            self.assertIn("Roles used", task_template)
            self.assertIn("Call cap recommendation", task_template)

            # Check 4: parallel-planner and micro-builder documentation preserved
            self.assertIn("parallel-planner", agents)
            self.assertIn("micro-builder", agents)
            self.assertIn("parallel-planner", task_template)
            self.assertIn("micro-builder", task_template)

            # Check 5: no stale classifier wording - auto resolves to stage bundle
            self.assertIn("resolves to an applicable stage bundle", agents)
            self.assertIn("resolves to an applicable stage bundle", task_template)

            # Check 6: prefer "stage routing" over "default role selection"
            self.assertNotIn("default role selection", agents)

            # Check 7: no Sol/Terra/Luna model-tier routing
            self.assertNotIn("model-tier routing in this change", "")  # sanity
            self.assertIn("no model-tier routing in this change", agents)

    def test_readme_stage_routing_terminology(self):
        """Check 3+6: READMEs explain stage routing and use correct terminology."""
        # READMEs live in the source repository, not in the installed target.
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_cn = (ROOT / "README_CN.md").read_text(encoding="utf-8")

        # Check 3: English README explains stage routing
        self.assertIn("stage bundle", readme)
        self.assertIn("preflight-bundle", readme)
        self.assertIn("postflight-bundle", readme)
        self.assertIn("AI_SPARK_BUDGET_MODE", readme)

        # Check 3: Chinese README explains stage routing
        self.assertIn("preflight-bundle", readme_cn)
        self.assertIn("postflight-bundle", readme_cn)
        self.assertIn("AI_SPARK_BUDGET_MODE", readme_cn)

        # Check 3: multi-report metrics in both READMEs
        self.assertIn("helper invocation count", readme)
        self.assertIn("auto-disable occurrences", readme)
        self.assertIn("helper 调用次数", readme_cn)

        # Check 6: no stale "default role selection" in READMEs
        self.assertNotIn("default role selection", readme)
        self.assertNotIn("默认角色选择", readme_cn)
        self.assertIn("stage routing / bundle selection", readme)
        self.assertIn("阶段路由 / 包选择", readme_cn)

    def test_installed_task_card_template_has_exact_controlled_builder_rows(self):
        """Required test 3: installed task-card-template.md contains every exact
        controlled-builder row with the correct field labels and example values."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            template = (repo / "ai" / "task-card-template.md").read_text(encoding="utf-8")

            required_rows = [
                "Controlled-builder authorized? | no / yes",
                "Controlled-builder allowed paths | ...",
                "Max files | not allowed / 3",
                "Max diff lines | not allowed / 1-200",
                "Public API risk | not allowed / no",
                "Data model risk | not allowed / no",
                "Security risk | not allowed / no",
                "Migration risk | not allowed / no",
                "Permission risk | not allowed / no",
                "Concurrency risk | not allowed / no",
                "Cross-module risk | not allowed / no",
                "Existing pattern | file reference / not applicable",
                "Source-of-truth reference | file reference / not applicable",
                "Validation command | exact narrow command / not applicable",
            ]
            for row in required_rows:
                self.assertIn(row, template, f"Missing exact row in task-card template: {row}")

            # Combined placeholder row that would break exact parsing must be removed
            self.assertNotIn("Controlled-builder risk exclusions", template)

    def test_installed_run_codex_spark_has_direct_minimal_full_and_controlled_support(self):
        """Required test 2: installed ai/run-codex-spark.sh contains direct/minimal/full,
        controlled-builder, allow-write and max-diff-lines support."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            spark = (repo / "ai" / "run-codex-spark.sh").read_text(encoding="utf-8")

            # Result delivery modes
            self.assertIn("direct", spark)
            self.assertIn("minimal", spark)
            self.assertIn("full", spark)

            # Controlled-builder support
            self.assertIn("controlled-builder", spark)
            self.assertIn("--allow-write", spark)
            self.assertIn("--max-diff-lines", spark)

    def test_installed_agents_docs_controlled_builder_and_delivery_modes(self):
        """Required test 4: installed AGENTS.md documents direct observability tradeoff,
        minimal/full audit choice, controlled-builder isolation, exact path/cap,
        and no merge/acceptance."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")

            # Direct observability tradeoff
            self.assertIn("direct", agents)
            self.assertIn("no file-backed metrics", agents)

            # Minimal/full audit choice
            self.assertIn("minimal", agents)
            self.assertIn("full", agents)
            self.assertIn("audit", agents.lower())

            # Controlled-builder isolation
            self.assertIn("controlled-builder", agents)
            self.assertIn("isolated", agents.lower())

            # Exact path and cap
            self.assertIn("--allow-write", agents)
            self.assertIn("--max-diff-lines", agents)

            # No merge/acceptance
            self.assertIn("never", agents.lower())
            self.assertIn("merge", agents.lower())

    def test_readme_docs_controlled_builder_and_no_model_tier_routing(self):
        """Required test 5: source English and Chinese READMEs document the same
        controlled-builder feature and no model-tier routing."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_cn = (ROOT / "README_CN.md").read_text(encoding="utf-8")

        # Both READMEs document controlled-builder
        self.assertIn("controlled-builder", readme)
        self.assertIn("controlled-builder", readme_cn)

        # Both READMEs document direct/minimal/full delivery modes
        self.assertIn("direct", readme)
        self.assertIn("minimal", readme)
        self.assertIn("full", readme)
        self.assertIn("direct", readme_cn)
        self.assertIn("minimal", readme_cn)
        self.assertIn("full", readme_cn)

        # Negative guarantee: both READMEs explicitly state no model-tier routing
        self.assertIn("no model-tier routing in this change", readme)
        self.assertIn("无模型层级路由", readme_cn)

        # No Sol/Terra/Luna routing names in either README
        for name in ("Sol", "Terra", "Luna"):
            self.assertNotIn(name, readme)
            self.assertNotIn(name, readme_cn)

    def test_installed_templates_preserve_micro_builder_parallel_planner_and_stage_bundles(self):
        """Required test 6: preserve micro-builder, parallel-planner, stage bundles,
        and prior installer tests (spot check key content)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)

            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            template = (repo / "ai" / "task-card-template.md").read_text(encoding="utf-8")

            # Micro-builder preserved
            self.assertIn("micro-builder", agents)
            self.assertIn("micro-builder", template)

            # Parallel-planner preserved
            self.assertIn("parallel-planner", agents)
            self.assertIn("parallel-planner", template)

            # Stage bundles preserved
            self.assertIn("preflight-bundle", agents)
            self.assertIn("postflight-bundle", agents)
            self.assertIn("preflight-bundle", template)
            self.assertIn("postflight-bundle", template)

            # Key prior content preserved
            self.assertIn("gpt-5.3-codex-spark", agents)
            self.assertIn("task-size-classifier", agents)
            self.assertIn("advisory", agents.lower())

    def test_installed_dispatch_has_runtime_identity_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")

            self.assertIn("RUNTIME_JSON=", dispatch)
            self.assertIn("schema_version", dispatch)
            self.assertIn('"worktree"', dispatch)
            self.assertIn('"source_repository"', dispatch)
            self.assertIn('"base_commit"', dispatch)
            self.assertIn('"pid_files"', dispatch)
            self.assertIn("Runtime identity saved to:", dispatch)
            self.assertIn("Runtime Identity:", dispatch)

    def test_installed_dispatch_has_retry_in_place_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")

            self.assertIn("validate_retry_in_place", dispatch)
            self.assertIn("CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID", dispatch)
            self.assertIn("prior runtime.json not found", dispatch)
            self.assertIn("retry-in-place", dispatch)
            self.assertIn("reuse-managed", dispatch)
            self.assertIn("unknown untracked files", dispatch)
            self.assertIn("TASK_CARD.md", dispatch)
            self.assertIn("CLAUDE_REPORT.md", dispatch)
            self.assertIn("CLAUDE_PROGRESS.md", dispatch)
            self.assertIn(".retry-lock-", dispatch)
            self.assertIn("reservation already exists", dispatch)

    def test_installed_status_has_runtime_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            status = (repo / "ai" / "status-claude.sh").read_text(encoding="utf-8")

            self.assertIn("RUNTIME_JSON=", status)
            self.assertIn("RUNTIME_DIAGNOSTIC", status)
            self.assertIn("runtime.json present but worktree field missing", status)
            self.assertIn("runtime.json worktree outside .worktrees/ boundary", status)
            self.assertIn("runtime.json worktree directory missing", status)
            self.assertIn("Runtime:", status)

    def test_installed_watch_has_runtime_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            watch = (repo / "ai" / "watch-claude.sh").read_text(encoding="utf-8")

            self.assertIn("RUNTIME_JSON=", watch)
            self.assertIn("RUNTIME_DIAGNOSTIC", watch)
            self.assertIn("runtime.json present but worktree field missing", watch)
            self.assertIn("runtime.json worktree outside .worktrees/ boundary", watch)
            self.assertIn("runtime.json worktree directory missing", watch)

    def test_installed_dispatch_has_child_exit_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")

            self.assertIn("Claude child exited:", dispatch)
            self.assertIn("transitioning to finalization immediately", dispatch)

    def test_installed_dispatch_has_builder_mode_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
            self.assertIn("CLAUDE_CODE_BUILDER_MODE", dispatch)
            self.assertIn("execution-only", dispatch)
            self.assertIn("CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS", dispatch)
            self.assertIn("first_progress_timeout", dispatch)
            self.assertIn("execution_only_keep_section", dispatch)
            self.assertIn("execution-only Builder mode", dispatch)
            self.assertIn("Do NOT restate or redesign the plan", dispatch)
            self.assertIn("builder_mode", dispatch)
            self.assertIn("first_progress_signal", dispatch)


if __name__ == "__main__":
    unittest.main()
