import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


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

    def test_write_file_is_atomic_and_preserves_existing_mode(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "managed.txt"
            target.write_text("old\n", encoding="utf-8")
            target.chmod(0o640)

            module.write_file(str(target), "new\r\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
            self.assertEqual(list(target.parent.glob(".aiwf-update-*")), [])

    def test_write_file_failure_preserves_previous_file(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "managed.txt"
            target.write_text("old\n", encoding="utf-8")

            with mock.patch.object(module.os, "replace", side_effect=OSError("injected")):
                with self.assertRaisesRegex(OSError, "injected"):
                    module.write_file(str(target), "new\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(list(target.parent.glob(".aiwf-update-*")), [])

    def test_manifest_validation_fails_before_project_creation(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            assets = pathlib.Path(tmp) / "assets"
            scripts = pathlib.Path(tmp) / "scripts"
            assets.mkdir()
            scripts.mkdir()

            with self.assertRaisesRegex(ValueError, "missing required source files"):
                module.validate_install_manifest(str(assets), str(scripts))

    def test_manifest_validation_rejects_unreadable_source(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "broken.md"
            source.write_bytes(b"\xff")
            with mock.patch.object(
                module,
                "build_install_manifest",
                return_value=[(str(source), "ai/broken.md")],
            ):
                with self.assertRaisesRegex(ValueError, "unreadable required source files"):
                    module.validate_install_manifest(tmp, tmp)

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
            self.assertTrue((repo / "ai" / "task-card-components" / "catalog.md").exists())
            self.assertTrue((repo / "ai" / "task-card-components" / "core.md").exists())
            self.assertTrue((repo / "ai" / "compose_task_card.py").exists())
            self.assertTrue((repo / "ai" / "task-card-components" / "exploratory-builder.md").exists())
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
            self.assertTrue((repo / "ai" / "process-identity.py").exists())
            self.assertTrue((repo / "ai" / "dispatch-preflight.py").exists())
            self.assertTrue((repo / "ai" / "archive-control-files.py").exists())
            self.assertTrue((repo / "ai" / "build-takeover-receipt.py").exists())
            self.assertTrue((repo / "ai" / "create-dirty-snapshot.py").exists())
            self.assertTrue((repo / "ai" / "enforce-checker-contract.py").exists())
            self.assertTrue((repo / "ai" / "compare-transfer-pilot.py").exists())
            self.assertTrue((repo / "ai" / "install_context_tools.py").exists())
            self.assertTrue((repo / "ai" / "locate-code.py").exists())
            self.assertTrue((repo / "ai" / "summarize-loop-run.py").exists())
            self.assertTrue((repo / "ai" / "benchmark-loop-runs.py").exists())
            self.assertTrue((repo / "ai" / "model-usage.py").exists())
            self.assertTrue((repo / "ai" / "economics-experiment.py").exists())
            self.assertTrue((repo / "ai" / "init-spec.py").exists())
            self.assertTrue((repo / "ai" / "plan-to-task-cards.py").exists())
            self.assertTrue((repo / "ai" / "solution-contract.py").exists())
            self.assertTrue((repo / "ai" / "schemas" / "solution-contract-v1.schema.json").exists())
            self.assertTrue((repo / "ai" / "init-plan.py").exists())
            self.assertTrue((repo / "ai" / "session-catchup.py").exists())
            self.assertTrue((repo / "ai" / "validate-parallel-plan.py").exists())
            self.assertTrue((repo / "ai" / "spark_control_protocol.py").exists())
            self.assertTrue((repo / "ai" / "assess-parallel-opportunity.py").exists())
            self.assertTrue((repo / "ai" / "task_schema.py").exists())
            self.assertTrue((repo / "ai" / "compose-profiles.py").exists())
            self.assertTrue((repo / "ai" / "lint-task-card.py").exists())
            self.assertTrue((repo / "ai" / "render-task-card.py").exists())
            self.assertTrue((repo / "ai" / "schemas" / "task-card-v1.schema.json").exists())
            self.assertTrue((repo / "ai" / "review_decision.py").exists())
            self.assertTrue((repo / "ai" / "parse-review-decision.py").exists())
            self.assertTrue((repo / "ai" / "schemas" / "review-decision-v1.schema.json").exists())
            self.assertTrue((repo / "ai" / "workflow_state.py").exists())
            self.assertTrue((repo / "ai" / "handoff_protocol.py").exists())
            self.assertTrue((repo / "ai" / "hypothesis_ledger.py").exists())
            self.assertTrue((repo / "ai" / "evidence_store.py").exists())
            self.assertTrue((repo / "ai" / "init-workflow-state.py").exists())
            self.assertTrue((repo / "ai" / "apply-workflow-delta.py").exists())
            self.assertTrue((repo / "ai" / "validate-workflow-state.py").exists())
            self.assertTrue((repo / "ai" / "recover-workflow-state.py").exists())
            self.assertTrue((repo / "ai" / "render-task-card-from-state.py").exists())
            self.assertTrue((repo / "ai" / "build-handoff-delta.py").exists())
            self.assertTrue((repo / "ai" / "validate-handoff-ack.py").exists())
            self.assertTrue((repo / "ai" / "merge-handoff-ack.py").exists())
            self.assertTrue((repo / "ai" / "update-hypothesis-ledger.py").exists())
            self.assertTrue((repo / "ai" / "check-revisited-hypothesis.py").exists())
            self.assertTrue((repo / "ai" / "evidence-store.py").exists())
            self.assertTrue((repo / "ai" / "evidence-invalidate.py").exists())
            self.assertTrue((repo / "ai" / "context-broker.py").exists())
            self.assertTrue((repo / "ai" / "schemas" / "workflow-state.schema.json").exists())
            self.assertTrue((repo / "ai" / "schemas" / "workflow-event.schema.json").exists())
            self.assertTrue((repo / "ai" / "schemas" / "handoff-delta.schema.json").exists())
            self.assertTrue((repo / "ai" / "schemas" / "handoff-ack.schema.json").exists())
            self.assertTrue((repo / "ai" / "schemas" / "rejected-hypothesis.schema.json").exists())
            self.assertTrue((repo / "ai" / "schemas" / "evidence-object.schema.json").exists())
            self.assertTrue((repo / "ai" / "schemas" / "context-query.schema.json").exists())
            self.assertTrue((repo / "ai" / "schemas" / "context-response.schema.json").exists())
            self.assertTrue((repo / "ai" / "profiles" / "base.json").exists())
            self.assertTrue((repo / "ai" / "profiles" / "bugfix.json").exists())
            self.assertTrue((repo / "ai" / "examples" / "fix-typo-in-readme.json").exists())
            self.assertTrue((repo / "ai" / "examples" / "real-project-task.json").exists())
            self.assertTrue((repo / "ai" / "examples" / "model-pricing.json").exists())
            self.assertTrue((repo / ".worktrees" / ".gitkeep").exists())
            gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("/.worktrees/*", gitignore)
            self.assertIn("!/.worktrees/.gitkeep", gitignore)

    def test_structured_assets_are_idempotent_and_updatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            first = self.run_installer(repo)
            # Structured assets created on first run
            self.assertTrue((repo / "ai" / "schemas" / "task-card-v1.schema.json").exists())
            self.assertTrue((repo / "ai" / "profiles" / "base.json").exists())
            self.assertTrue((repo / "ai" / "profiles" / "bugfix.json").exists())
            self.assertTrue((repo / "ai" / "examples" / "fix-typo-in-readme.json").exists())
            self.assertIn("created: ai/schemas/task-card-v1.schema.json", first.stdout)
            self.assertIn("created: ai/profiles/base.json", first.stdout)

            second = self.run_installer(repo)
            # Structured assets skipped on second run (idempotent)
            self.assertIn("skipped: ai/schemas/task-card-v1.schema.json", second.stdout)
            self.assertIn("skipped: ai/profiles/base.json", second.stdout)

            # Tamper and refresh
            (repo / "ai" / "profiles" / "base.json").write_text("{}", encoding="utf-8")
            third = self.run_installer(repo, "--update-workflow-files")
            self.assertIn("updated: ai/profiles/base.json", third.stdout)

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
            self.assertIn("## AI Coding Workflow Core", content)
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
            self.assertLess(len(agents.encode("utf-8")), 12_000)
            self.assertIn("## Claude-First Ownership", agents)
            self.assertIn("Minimize scarce Codex work", agents)
            self.assertIn("workflow bypassed:", agents)
            self.assertIn("task-card-components/catalog.md", agents)
            self.assertIn("Builder Claude", agents)
            self.assertIn("Checker/Test Claude", agents)
            self.assertIn("conditional, not automatic", agents)
            self.assertIn("checker skipped: deterministic evidence sufficient", agents)
            self.assertIn("two current-task rounds", agents)
            self.assertIn("Seeded/fallback reports never count", agents)
            self.assertIn("On-Demand References", agents)
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

    def test_installed_dispatch_resolves_proxy_mode_with_safe_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
            self.assertIn('CLAUDE_CODE_PROXY_MODE="direct"', dispatch)
            self.assertIn('_ROUTE_SOURCE="explicit"', dispatch)
            self.assertIn('_ROUTE_SOURCE="default"', dispatch)
            self.assertIn('resolve --fallback ""', dispatch)
            self.assertIn('_ROUTE_SOURCE="learned"', dispatch)
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
            self.assertIn("compact_keep_section", dispatch)
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
            self.assertIn("reviewer-owned bounded correction", review)
            self.assertIn("Spark estimation is optional", review)
            self.assertIn("Prior-session Claude failures are context only", review)
            self.assertIn("Missing result/report/acceptance prose is an evidence gap", review)
            self.assertIn("missing task-card-required tests or evidence", review)
            self.assertIn("fresh owner route", review)
            self.assertIn("preserve the accepted implementation direction", review)
            self.assertIn("Classify Claude evidence explicitly", review)
            # Structured review decision JSON contract (replaces removed prose phrase)
            self.assertIn("parse-review-decision.py", review)
            self.assertIn("Review Decision:", review)
            self.assertIn("schema_version", review)
            self.assertIn('"decision": "accept|revise|split|reject"', review)
            self.assertIn('"scope": "phase|whole-task"', review)
            self.assertIn("JSON decision is authoritative", review)
            self.assertIn("structured decision required", review.lower())
            self.assertIn("Respect Task Mode", review)
            self.assertIn("checker skipped: deterministic evidence sufficient", review)
            self.assertIn("materially reduces Codex work", review)
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
            self.assertLess(len(agents.encode("utf-8")), 12_000)
            self.assertIn("Do not browse the web", agents)
            self.assertIn("ai/locate-code.py", agents)
            self.assertIn("task-card-components/catalog.md", agents)
            self.assertIn("reviewer-owned correction", agents)
            self.assertIn("references/review-policy.md", agents)
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
            self.assertIn("which phases remain for a fresh owner route", claude)

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
            # Run-loop uses Review Decision: JSON path, not prose grep for decision words
            self.assertIn("REVIEW_DECISION_FILE", run_loop)
            self.assertIn("grep '^Review Decision:'", run_loop)
            self.assertIn('data.get("decision"', run_loop)
            self.assertIn("missing_review_decision", run_loop)

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
            self.assertIn('kill -0 "$(tr -d', status)
            self.assertIn('claude-process-state.py', status)
            self.assertIn('PROCESS_STATE_HELPER', status)
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
                [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id, "--details"],
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
                    [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id, "--details"],
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
                [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id, "--details"],
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
                [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id, "--details"],
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

    def test_watch_suppresses_unchanged_heartbeat_snapshots(self):
        """A live but unchanged dispatch emits its initial snapshot once; elapsed
        and quiet-second heartbeats must not continuously wake observers."""
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
            watcher = None
            try:
                live_pid = sleeper.stdout.readline().strip()
                (worktrees / f"{task_id}.dispatcher.pid").write_text(live_pid, encoding="utf-8")
                (worktrees / f"{task_id}.claude.pid").write_text("99999", encoding="utf-8")
                (worktrees / f"{task_id}.checker.pid").write_text("99997", encoding="utf-8")
                watcher = subprocess.Popen(
                    [bash_exe, str(repo / "ai" / "watch-claude.sh"),
                     task_id, "--plain", "--interval", "1"],
                    cwd=str(repo), text=True, encoding="utf-8", errors="replace",
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                try:
                    stdout, _ = watcher.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    watcher.terminate()
                    stdout, _ = watcher.communicate(timeout=10)
            finally:
                if watcher is not None and watcher.poll() is None:
                    watcher.kill()
                    watcher.wait(timeout=10)
                sleeper.terminate()
                sleeper.wait(timeout=10)

            snapshots = [line for line in stdout.splitlines() if " state=" in line]
            self.assertEqual(snapshots, [snapshots[0]] if snapshots else [], stdout)
            self.assertEqual(len(snapshots), 1, stdout)

    def test_monitor_helper_is_installed_for_wait_and_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            monitor = repo / "ai" / "monitor-claude.sh"
            self.assertTrue(monitor.exists())
            self.assertTrue((repo / "ai" / "claude-monitor-decision.py").exists())
            self.assertTrue((repo / "ai" / "codegraph-worktree-guard.py").exists())
            self.assertTrue((repo / "ai" / "parallel-task-gate.py").exists())
            text = monitor.read_text(encoding="utf-8")
            for action in ("wait)", "decision)"):
                self.assertIn(action, text)
            self.assertIn("monitor-events.log", text)
            self.assertIn("--mode monitor-triage", text)
            watch = (repo / "ai" / "watch-claude.sh").read_text(encoding="utf-8")
            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
            self.assertIn("deferred-machine-monitor", watch)
            self.assertIn('event=material-change running=yes terminal=no', dispatch)
            self.assertIn('event=terminal running=no terminal=yes', dispatch)
            self.assertIn('Wait for Event:', dispatch)
            self.assertNotIn("ps -o pid,ppid,stat,etime,cmd --ppid", text + watch + dispatch)
            self.assertNotIn("date '+%T'", text + watch + dispatch)

    def test_monitor_wait_returns_existing_terminal_event_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-event-wait"
            worktrees = repo / ".worktrees"
            worktrees.mkdir(exist_ok=True)
            terminal = (
                f"monitor_event source=dispatcher task_id={task_id} "
                "event=terminal running=no terminal=yes exit_status=0 dispatch_outcome=success\n"
            )
            (worktrees / f"{task_id}.monitor-events.log").write_text(terminal, encoding="utf-8")
            result = subprocess.run(
                [load_module()._find_bash(), str(repo / "ai" / "monitor-claude.sh"),
                 "wait", task_id, "--until", "terminal", "--timeout", "2", "--spark", "off"],
                cwd=str(repo), text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=True, timeout=10,
            )
            self.assertTrue(result.stdout.startswith(terminal), result.stdout)
            self.assertIn("triage_source=local", result.stdout)
            self.assertIn("spark_status=disabled", result.stdout)

    def test_monitor_wait_blocks_until_new_material_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-material-wait"
            worktrees = repo / ".worktrees"
            worktrees.mkdir(exist_ok=True)
            event_log = worktrees / f"{task_id}.monitor-events.log"
            event_log.write_text(
                f"monitor_event source=dispatcher task_id={task_id} event=started running=yes terminal=no\n",
                encoding="utf-8",
            )
            bash_exe = load_module()._find_bash()
            sleeper = subprocess.Popen(
                [bash_exe, "-c", "echo $$; exec sleep 20"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8",
            )
            waiter = None
            try:
                pid = sleeper.stdout.readline().strip()
                sleeper.stdout.close()
                (worktrees / f"{task_id}.dispatcher.pid").write_text(pid, encoding="utf-8")
                waiter = subprocess.Popen(
                    [bash_exe, str(repo / "ai" / "monitor-claude.sh"), "wait", task_id,
                     "--until", "material", "--interval", "1", "--timeout", "5",
                     "--spark", "off"],
                    cwd=str(repo), text=True, encoding="utf-8", errors="replace",
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                time.sleep(1.2)
                with event_log.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"monitor_event source=dispatcher task_id={task_id} "
                        "event=child-exited running=no terminal=no exit_status=0\n"
                    )
                time.sleep(1.2)
                self.assertIsNone(waiter.poll(), "child exit is not a review-ready boundary")
                material = (
                    f"monitor_event source=dispatcher task_id={task_id} "
                    "event=material-change running=yes terminal=no worktree_changes=1\n"
                )
                with event_log.open("a", encoding="utf-8") as handle:
                    handle.write(material)
                stdout, stderr = waiter.communicate(timeout=10)
                self.assertEqual(waiter.returncode, 0, stderr)
                self.assertTrue(stdout.startswith(material), stdout)
                self.assertIn("triage_source=local", stdout)
                self.assertIn("spark_status=disabled", stdout)
            finally:
                if waiter is not None and waiter.poll() is None:
                    waiter.kill()
                    waiter.wait(timeout=10)
                sleeper.terminate()
                sleeper.wait(timeout=10)

    def test_status_defaults_to_bounded_local_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-compact"
            worktrees = self._setup_worktree(repo, task_id)
            for suffix in ("pid", "dispatcher.pid", "claude.pid", "checker.pid"):
                (worktrees / f"{task_id}.{suffix}").write_text("999999", encoding="utf-8")
            result = subprocess.run(
                [load_module()._find_bash(), str(repo / "ai" / "status-claude.sh"), task_id],
                cwd=str(repo), text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=True,
            )
            self.assertIn("decision=", result.stdout)
            self.assertIn("interrupt_authorized=no", result.stdout)
            self.assertNotIn("Process roles", result.stdout)
            self.assertLess(len(result.stdout), 2048)

    def test_monitor_decision_sends_only_compact_packet_to_spark(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-spark-triage"
            worktrees = self._setup_worktree(repo, task_id)
            for suffix in ("pid", "dispatcher.pid", "claude.pid", "checker.pid"):
                (worktrees / f"{task_id}.{suffix}").write_text("999999", encoding="utf-8")
            capture = repo / "compact-input.json"
            fake = repo / "ai" / "run-codex-spark.sh"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "while [ $# -gt 0 ]; do\n"
                "  if [ \"$1\" = --brief-file ]; then cp \"$2\" \"$FAKE_CAPTURE\"; shift 2; else shift; fi\n"
                "done\n"
                "printf '%s\\n' 'decision=inspect' 'confidence=medium' "
                "'reason_code=bounded-review' 'summary=compressed idle diagnosis' "
                "'codex_review_required=yes' 'interrupt_authorized=no'\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            env = os.environ.copy()
            env["FAKE_CAPTURE"] = str(capture)
            result = subprocess.run(
                [load_module()._find_bash(), str(repo / "ai" / "monitor-claude.sh"),
                 "decision", task_id, "--spark", "on"],
                cwd=str(repo), env=env, text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=True,
            )
            packet = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(packet["interrupt_authorized"], "no")
            self.assertNotIn("process_listing", packet)
            self.assertLess(len(json.dumps(packet)), 4096)
            self.assertIn("triage_source=spark", result.stdout)
            self.assertIn("summary=compressed idle diagnosis", result.stdout)
            self.assertIn("compression_source=spark", result.stdout)
            self.assertIn("raw_evidence_forwarded=no", result.stdout)
            self.assertIn("codex_review_required=yes", result.stdout)
            self.assertIn("interrupt_authorized=no", result.stdout)
            self.assertIn("execution_phase=unknown", result.stdout)
            self.assertIn("implementation_complete=unknown", result.stdout)
            self.assertIn("completion_ready=unknown", result.stdout)
            self.assertIn("finish_recommended=no", result.stdout)

    def test_monitor_wait_compresses_boundary_through_spark(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-wait-spark"
            worktrees = self._setup_worktree(repo, task_id)
            terminal = (
                f"monitor_event source=dispatcher task_id={task_id} "
                "event=terminal running=no terminal=yes exit_status=1 dispatch_outcome=timeout\n"
            )
            (worktrees / f"{task_id}.monitor-events.log").write_text(terminal, encoding="utf-8")
            capture = repo / "wait-compact-input.json"
            fake = repo / "ai" / "run-codex-spark.sh"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "while [ $# -gt 0 ]; do\n"
                "  if [ \"$1\" = --brief-file ]; then cp \"$2\" \"$FAKE_CAPTURE\"; shift 2; else shift; fi\n"
                "done\n"
                "printf '%s\\n' 'decision=inspect' 'confidence=high' "
                "'reason_code=compressed-timeout'\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            env = os.environ.copy()
            env["FAKE_CAPTURE"] = str(capture)
            result = subprocess.run(
                [load_module()._find_bash(), str(repo / "ai" / "monitor-claude.sh"),
                 "wait", task_id, "--until", "terminal", "--spark", "on"],
                cwd=str(repo), env=env, text=True, encoding="utf-8", errors="replace",
                capture_output=True, check=True, timeout=20,
            )

            self.assertTrue(result.stdout.startswith(terminal), result.stdout)
            self.assertIn("triage_source=spark", result.stdout)
            self.assertIn("reason_code=compressed-timeout", result.stdout)
            packet = json.loads(capture.read_text(encoding="utf-8"))
            self.assertNotIn("process_listing", packet)

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

    def test_watch_reports_visibility_unknown_in_restricted_sandbox(self):
        """Invisible outer-namespace PIDs must not trigger duplicate dispatch."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            task_id = "claude-20990101-visibility"
            worktrees = self._setup_worktree(repo, task_id)
            for suffix in ("dispatcher.pid", "claude.pid"):
                (worktrees / f"{task_id}.{suffix}").write_text("99999999", encoding="utf-8")
            (worktrees / f"{task_id}.progress.log").write_text(
                "Claude process started: pid=99999999\nClaude still running: elapsed_seconds=30 quiet_seconds=0\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["CODEX_SANDBOX_NETWORK_DISABLED"] = "1"
            result = subprocess.run(
                [load_module()._find_bash(), str(repo / "ai" / "watch-claude.sh"), task_id, "--plain", "--once"],
                cwd=str(repo), env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, check=True,
            )
            self.assertIn("overall_running=unknown", result.stdout)
            self.assertIn("CHECK_OUTSIDE_SANDBOX_DO_NOT_REDISPATCH", result.stdout)
            self.assertIn("visibility-unknown", result.stdout)

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
                [bash_exe, str(repo / "ai" / "status-claude.sh"), task_id, "--details"],
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
            self.assertIn("## High-Token Work Routing Gate", task_card)
            self.assertIn("## Evidence Compression Requirements", task_card)

            # Evidence packet must have context budget and compression fields
            self.assertIn("## Context Budget Used", evidence)
            self.assertIn("## High-Token Work Routed", evidence)
            self.assertIn("## Compressed Evidence Summary", evidence)

            # AGENTS.md keeps only the Claude-first ownership contract; details are on-demand.
            self.assertIn("## Claude-First Ownership", agents)
            self.assertIn("Route every frozen implementation slice", agents)
            self.assertIn("compact summaries and paths", agents)

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
            routing = (ROOT / "references" / "routing-and-spark.md").read_text(encoding="utf-8")

            # Check 1: preflight-bundle and postflight-bundle present
            self.assertIn("preflight-bundle", agents)
            self.assertIn("postflight-bundle", routing)
            self.assertIn("preflight-bundle", task_template)
            self.assertIn("postflight-bundle", task_template)

            # Check 1: AI_SPARK_BUDGET_MODE present
            self.assertIn("Spark Roles", routing)
            self.assertIn("AI_SPARK_BUDGET_MODE", task_template)

            # Check 1: balanced, aggressive, conservative present
            self.assertIn("direct", routing)
            self.assertIn("minimal", routing)
            self.assertIn("full", routing)

            # Check 1: bounded opt-in invocation recommendation
            self.assertIn("one bounded estimate", routing)
            self.assertIn("at most one uncertain-route estimate", task_template)
            self.assertIn("terminal-evidence compression", routing)

            # Check 1: merge guardrails
            self.assertIn("cannot satisfy acceptance", agents)
            self.assertIn("authorize merge", agents)

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
            self.assertIn("parallel-planner", routing)
            self.assertIn("micro-builder", routing)
            self.assertIn("parallel-planner", task_template)
            self.assertIn("micro-builder", task_template)

            # Check 5: auto is value-triggered after deterministic routing
            self.assertIn("auto", routing)
            self.assertIn("Deterministic ROUTE is the default", task_template)
            self.assertIn("concrete Claude candidate", task_template)

            # Check 6: prefer "stage routing" over "default role selection"
            self.assertNotIn("default role selection", agents)

            # Check 7: no Sol/Terra/Luna model-tier routing
            self.assertNotIn("model-tier routing in this change", "")  # sanity
            self.assertNotIn("Sol", routing)
            self.assertNotIn("Terra", routing)
            self.assertNotIn("Luna", routing)

    def test_readme_stage_routing_terminology(self):
        """Check 3+6: READMEs explain stage routing and use correct terminology."""
        # READMEs live in the source repository, not in the installed target.
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_cn = (ROOT / "README_CN.md").read_text(encoding="utf-8")

        self.assertIn("## Should you use this Skill?", readme)
        self.assertIn("## 是否应该使用这个 Skill？", readme_cn)
        self.assertIn("workflow bypassed:", readme)
        self.assertIn("workflow bypassed:", readme_cn)

        # Check 3: English README explains stage routing
        self.assertIn("stage bundle", readme)
        self.assertIn("preflight-bundle", readme)
        self.assertIn("postflight-bundle", readme)
        self.assertIn("Spark is advisory", readme)

        # Check 3: Chinese README explains stage routing
        self.assertIn("preflight-bundle", readme_cn)
        self.assertIn("postflight-bundle", readme_cn)
        self.assertIn("Spark 是建议", readme_cn)

        # Check 3: multi-report metrics in both READMEs
        self.assertIn("helper invocation count", readme)
        self.assertIn("auto-disable occurrences", readme)
        self.assertIn("helper 调用次数", readme_cn)

        # Check 6: no stale "default role selection" in READMEs
        self.assertNotIn("default role selection", readme)
        self.assertNotIn("默认角色选择", readme_cn)
        self.assertIn("short, value-triggered stage routing", readme)
        self.assertIn("短小、按价值触发的阶段路由", readme_cn)

    def test_english_and_chinese_readmes_share_recent_workflow_entrypoints(self):
        """Keep recently added setup and parallel-routing entry points bilingual."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_cn = (ROOT / "README_CN.md").read_text(encoding="utf-8")
        shared_markers = [
            "--setup-current",
            "--setup-repo",
            "--auto-setup",
            "assess-parallel-opportunity.py",
            "serial-obvious",
            "parallel-candidate",
            "--max-concurrency 2",
        ]
        for marker in shared_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, readme)
                self.assertIn(marker, readme_cn)

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
            routing = (ROOT / "references" / "routing-and-spark.md").read_text(encoding="utf-8")

            # Detailed Spark delivery/write modes are loaded on demand.
            self.assertIn("direct", routing)
            self.assertIn("minimal", routing)
            self.assertIn("full", routing)
            self.assertIn("controlled-builder", routing)
            self.assertIn("--allow-write", routing)
            self.assertIn("--max-diff-lines", routing)

            # No merge/acceptance
            self.assertIn("cannot satisfy acceptance", agents)
            self.assertIn("authorize merge", agents)

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

        # No standalone Sol/Terra/Luna routing names in either README.
        for name in ("Sol", "Terra", "Luna"):
            self.assertNotRegex(readme, rf"\b{name}\b")
            self.assertNotRegex(readme_cn, rf"\b{name}\b")

    def test_installed_templates_preserve_micro_builder_parallel_planner_and_stage_bundles(self):
        """Required test 6: preserve micro-builder, parallel-planner, stage bundles,
        and prior installer tests (spot check key content)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)

            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            template = (repo / "ai" / "task-card-template.md").read_text(encoding="utf-8")
            routing = (ROOT / "references" / "routing-and-spark.md").read_text(encoding="utf-8")

            # Micro-builder preserved
            self.assertIn("micro-builder", routing)
            self.assertIn("micro-builder", template)

            # Parallel-planner preserved
            self.assertIn("parallel-planner", routing)
            self.assertIn("parallel-planner", template)

            # Stage bundles preserved
            self.assertIn("preflight-bundle", agents)
            self.assertIn("postflight-bundle", routing)
            self.assertIn("preflight-bundle", template)
            self.assertIn("postflight-bundle", template)

            # Key prior content preserved
            self.assertIn("ownership_profile=claude-first", agents)
            self.assertIn("Spark Roles", routing)
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

    def test_installs_report_diff_consistency_helper_and_dispatch_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            helper = repo / "ai" / "verify-claude-report.py"
            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
            aiwf = (repo / "ai" / "aiwf.py").read_text(encoding="utf-8")

            self.assertTrue(helper.is_file())
            self.assertIn("REPORT_CONSISTENCY_FILE", dispatch)
            self.assertIn("verify-claude-report.py", dispatch)
            self.assertIn('"verify-claude-report":"verify-claude-report.py"', aiwf)

    def test_installs_repository_scale_helper_and_worktree_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            self.run_installer(repo)
            helper = repo / "ai" / "repository-scale.py"
            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
            aiwf = (repo / "ai" / "aiwf.py").read_text(encoding="utf-8")

            self.assertTrue(helper.is_file())
            self.assertIn('"repository-scale":"repository-scale.py"', aiwf)
            self.assertIn('"worktree_setup_seconds"', dispatch)

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
            self.assertIn("entering bounded terminal drain", dispatch)
            self.assertIn("dispatcher finalizing artifacts", dispatch)

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
            self.assertIn("solution planner in a Codex/Claude Code workflow", dispatch)
            self.assertIn("batch executor in a Codex/Claude Code workflow", dispatch)
            self.assertIn("exploratory executor", dispatch)
            self.assertIn("'solution-planning', 'batch', or 'exploratory'", dispatch)
            self.assertIn("Do NOT restate or redesign the plan", dispatch)
            self.assertIn("builder_mode", dispatch)
            self.assertIn("first_progress_signal", dispatch)

    def test_installed_route_and_api_availability_helpers(self):
        """Installer must copy learned route and API availability helpers."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            helper = repo / "ai" / "claude-route-preference.py"
            self.assertTrue(helper.exists(), "claude-route-preference.py should be installed")
            content = helper.read_text(encoding="utf-8")
            self.assertIn("resolve", content)
            self.assertIn("record", content)
            self.assertIn("show", content)
            self.assertIn("schema_version", content)
            self.assertIn("atomic", content.lower())
            availability = repo / "ai" / "claude-api-availability.py"
            self.assertTrue(availability.exists(), "claude-api-availability.py should be installed")
            self.assertIn("context_hash", availability.read_text(encoding="utf-8"))

            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
            self.assertIn("claude-route-preference.py", dispatch)
            self.assertIn("claude-api-availability.py", dispatch)
            self.assertIn("_ROUTE_SOURCE", dispatch)
            self.assertIn("route_source=", dispatch)
            self.assertIn("learned", dispatch)


    def test_external_integration_gate_propagated_to_installed_assets(self):
        """Installed task template, AGENTS.md, and dispatch script must retain
        the external integration gate and safety phrases."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"

            self.run_installer(repo)

            template = (repo / "ai" / "task-card-template.md").read_text(encoding="utf-8")
            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            dispatch = (repo / "ai" / "dispatch-to-claude.sh").read_text(encoding="utf-8")

            # Task template must have the Claude External Integration Gate section
            self.assertIn("## Claude External Integration Gate", template)
            self.assertIn("External integrations allowed?", template)
            self.assertIn("MCP config paths", template)
            self.assertIn("Plugin paths", template)
            self.assertIn("Strict MCP isolation?", template)
            self.assertIn("--bare", template)
            self.assertIn("fail-closed", template)
            self.assertIn("repository-relative", template)
            self.assertIn("does not widen built-in Bash/Edit permissions", template)

            # AGENTS keeps the invariant compact; detailed syntax stays in task/dispatcher.
            self.assertIn("External MCP/plugins are default-off", agents)
            self.assertIn("do not widen Bash/Edit authority", agents)

            # Dispatcher must have the external integration gate variables and validation
            self.assertIn("_EXTERNAL_INTEGRATIONS_ALLOWED", dispatch)
            self.assertIn("_MCP_CONFIG_PATHS_RAW", dispatch)
            self.assertIn("_PLUGIN_PATHS_RAW", dispatch)
            self.assertIn("_STRICT_MCP_ISOLATION", dispatch)
            self.assertIn("validate_external_integration_paths", dispatch)
            self.assertIn("must be yes when external integrations are allowed", dispatch)
            self.assertIn("--bare", dispatch)
            self.assertIn("MCP config file not found", dispatch)
            self.assertIn("plugin path not found", dispatch)

    def test_readme_docs_external_integration_gate_and_single_monitor_owner(self):
        """Source and installed READMEs document the integration gate and
        dispatcher-owned monitoring contract."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_cn = (ROOT / "README_CN.md").read_text(encoding="utf-8")
        installed_readme = (ROOT / "assets" / "README.md").read_text(encoding="utf-8")

        # English source README
        self.assertIn("## Claude External Integrations", readme)
        self.assertIn("default-off", readme)
        self.assertIn("--bare", readme)
        self.assertIn("repository-relative", readme)
        self.assertIn("External integrations do not widen built-in Bash/Edit permissions", readme)
        self.assertIn("does not perform global config scan", readme)
        self.assertIn("contents and secrets are never recorded", readme)
        self.assertIn("monitor-claude.sh wait", readme)

        # Chinese source README
        self.assertIn("## Claude 外部集成", readme_cn)
        self.assertIn("默认关闭", readme_cn)
        self.assertIn("--bare", readme_cn)
        self.assertIn("仓库相对", readme_cn)
        self.assertIn("不会扩大内置 Bash/Edit 权限", readme_cn)
        self.assertIn("monitor-claude.sh wait", readme_cn)

        # Installed README (assets/README.md)
        self.assertIn("## Claude External Integrations", installed_readme)
        self.assertIn("default-off", installed_readme)
        self.assertIn("--bare", installed_readme)
        self.assertIn("repository-relative", installed_readme)
        self.assertIn("External integrations do not widen built-in Bash/Edit permissions", installed_readme)

if __name__ == "__main__":
    unittest.main()
