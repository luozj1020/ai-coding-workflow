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
            self.assertTrue((repo / "ai" / "plan-task-template.md").exists())
            self.assertTrue((repo / "ai" / "plan-findings-template.md").exists())
            self.assertTrue((repo / "ai" / "plan-progress-template.md").exists())
            self.assertTrue((repo / "ai" / "check-worktree.sh").exists())
            self.assertTrue((repo / "ai" / "run-loop.sh").exists())
            self.assertTrue((repo / "ai" / "status-claude.sh").exists())
            self.assertTrue((repo / "ai" / "watch-claude.sh").exists())
            self.assertTrue((repo / "ai" / "kill-claude.sh").exists())
            self.assertTrue((repo / "ai" / "cleanup-worktree.sh").exists())
            self.assertTrue((repo / "ai" / "pwsh-utf8.ps1").exists())
            self.assertTrue((repo / "ai" / "install_context_tools.py").exists())
            self.assertTrue((repo / "ai" / "summarize-loop-run.py").exists())
            self.assertTrue((repo / "ai" / "init-plan.py").exists())
            self.assertTrue((repo / "ai" / "session-catchup.py").exists())
            self.assertTrue((repo / ".worktrees" / ".gitkeep").exists())

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
            self.assertIn('git worktree add -b "$BRANCH_NAME" "$WORKTREE_DIR" HEAD ||', dispatch)
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
            self.assertIn("permission/tool approval blocker", review)
            self.assertIn("mixed-role task", review)
            self.assertIn("Direction / Boundary Acknowledgement", review)
            self.assertIn("acknowledgement loops", review)
            self.assertIn("New Handoff Contract", review)
            self.assertIn("### Testing Responsibility", review)
            self.assertIn("user-requested, acceptance-critical, or out of scope", review)
            self.assertIn("Validation Contract", task_template)
            self.assertIn("## Task Mode", task_template)
            self.assertIn("builder / checker-test", task_template)
            self.assertIn("Mixed-task guard", task_template)
            self.assertIn("## Phase Responsibility Matrix", task_template)
            self.assertIn("## Stall / Ambiguity Triage", task_template)
            self.assertIn("## Delegation Restoration Gate", task_template)
            self.assertIn("HEAD contains required prior context for Claude?", task_template)
            self.assertIn("Dirty source blocks reliable Claude dispatch?", task_template)
            self.assertIn("Permission/tool approval risk?", task_template)
            self.assertIn("## Direction Review Gate", task_template)
            self.assertIn("## Direction / Boundary Acknowledgement", task_template)
            self.assertIn("Maximum acknowledgement rounds", task_template)
            self.assertIn("Anti-loop rule", task_template)
            self.assertIn("## Execution Progress", task_template)
            self.assertIn("Builder may run narrow sanity checks?", task_template)
            self.assertIn("Broad acceptance test execution owner", task_template)
            self.assertIn("## Task Card Views", task_template)
            self.assertIn("Do not maintain a second hand-written Claude card", task_template)
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
            self.assertIn("## Unknowns", task_template)
            self.assertIn("Unknown-unknown scan request", task_template)
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
            self.assertIn("Delegation blocker", evidence_template)
            self.assertIn("HEAD contained required prior context?", evidence_template)
            self.assertIn("permission/tool approval", evidence_template)
            self.assertIn("## Direction / Boundary Acknowledgement Follow-up", evidence_template)
            self.assertIn("Repeated same approval request after proceed?", evidence_template)
            self.assertIn("## Testing Responsibility Follow-up", evidence_template)
            self.assertIn("## Evidence Gap Recovery", evidence_template)
            self.assertIn("Evidence reconstructed by Codex?", evidence_template)
            self.assertIn("## Delegation Continuity", evidence_template)
            self.assertIn("Whole task accepted?", evidence_template)
            self.assertIn("## Control-Plane Salvage", evidence_template)
            self.assertIn("First-round direction salvaged", evidence_template)
            self.assertIn("Codex direct intervention requested?", evidence_template)
            self.assertIn("## Unknowns and Deviations", evidence_template)
            self.assertIn("## Reviewer Briefing", evidence_template)
            self.assertIn("Plan Match", evidence_template)
            self.assertIn("Validation Confidence", evidence_template)
            self.assertIn("Reviewer Should Check", evidence_template)
            self.assertIn("Deviations From Plan", evidence_template)
            self.assertIn("Loop Engineering Validation Contract", agents)
            self.assertIn("Codex Intervention Policy", agents)
            self.assertIn("not permission for Codex to patch", agents)
            self.assertIn("Prior-session Claude failures are carry-forward context", agents)
            self.assertIn("Missing Claude `result.json`, `CLAUDE_REPORT.md`, or acceptance evidence is an evidence gap", agents)
            self.assertIn("current-task repeated failure", agents)
            self.assertIn("salvage any reviewer-accepted first-round direction", agents)
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
            self.assertIn('CLAUDE_PROGRESS_FILE="$(parse_path "Claude Progress" "$DISPATCH_LOG")"', run_loop)
            self.assertIn('"$CLAUDE_PID_FILE" "$PROGRESS_FILE"', run_loop)
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
            self.assertIn('### Claude Self-Reported Progress', run_loop)
            self.assertIn('Review-to-Next-Task Contract', run_loop)
            self.assertIn('New Handoff Contract', run_loop)
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
            self.assertIn("## LSP / Codegraph Evidence", task_card)
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


if __name__ == "__main__":
    unittest.main()
