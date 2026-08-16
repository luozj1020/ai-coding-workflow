import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude-monitor-decision.py"


def load_module():
    spec = importlib.util.spec_from_file_location("claude_monitor_decision", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class ClaudeMonitorDecisionTests(unittest.TestCase):
    def make_case(self, monitor_event):
        temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(temporary.name)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        worktrees = root / ".worktrees"
        task_id = "claude-20990101-compact"
        (worktrees / task_id).mkdir(parents=True)
        (worktrees / f"{task_id}.progress.log").write_text(
            "Claude still running: elapsed_seconds=700 quiet_seconds=650\n",
            encoding="utf-8",
        )
        (worktrees / f"{task_id}.monitor-events.log").write_text(
            monitor_event + "\n", encoding="utf-8"
        )
        args = argparse.Namespace(
            repo_root=root, task_id=task_id, max_changed_paths=8,
            max_summary_chars=240, stale_after=120, interrupt_after=600,
            confirmations=3,
        )
        return temporary, args

    def test_recent_growth_continues_without_codex_review(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event monitor_level=L2 action=inspect evidence_state=diff "
            "quiet_seconds=650 suspect_count=4 elapsed_seconds=700 artifact_growth=yes running=yes"
        )
        with temporary, mock.patch.object(module, "role_state", return_value="running"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "continue")
        self.assertEqual(value["interrupt_authorized"], "no")

    def test_corroborated_l3_stall_is_only_an_interrupt_candidate(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event monitor_level=L3 action=inspect evidence_state=no-report "
            "quiet_seconds=650 suspect_count=3 elapsed_seconds=700 artifact_growth=no running=yes"
        )
        with temporary, mock.patch.object(module, "role_state", return_value="running"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "interrupt-candidate")
        self.assertEqual(value["codex_review_required"], "yes")
        self.assertEqual(value["interrupt_authorized"], "no")

    def test_completion_ready_waits_for_voluntary_exit_even_when_stale(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event monitor_level=L3 action=inspect evidence_state=diff+report "
            "quiet_seconds=650 suspect_count=4 elapsed_seconds=700 artifact_growth=no running=yes"
        )
        progress = args.repo_root / ".worktrees" / args.task_id / "CLAUDE_PROGRESS.md"
        progress.write_text(
            "- Execution Phase: tail\n"
            "- Implementation Complete: yes\n"
            "- Assigned Tail Work: bounded self-review and report\n"
            "- Tail Work Complete: yes\n"
            "- Completion Ready: yes\n"
            "- Next Check: exit\n",
            encoding="utf-8",
        )
        with temporary, mock.patch.object(module, "role_state", return_value="running"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "continue")
        self.assertEqual(value["reason_code"], "completion-ready-awaiting-voluntary-exit")
        self.assertEqual(value["finish_recommended"], "yes")
        self.assertEqual(value["interrupt_authorized"], "no")

    def test_edit_ready_is_not_reported_as_durable_progress(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event event=material-change execution_state=implementation-ready "
            "edit_ready=1 product_idle_seconds=0 idle_confirmations=0 running=yes"
        )
        with temporary, mock.patch.object(module, "role_state", return_value="running"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "continue")
        self.assertEqual(value["reason_code"], "editing-ready-awaiting-durable-write")
        self.assertEqual(value["edit_ready"], "yes")
        self.assertEqual(value["product_changes"], 0)

    def test_product_idle_candidate_requests_bounded_diagnosis(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event event=material-change execution_state=implementation-idle "
            "edit_ready=1 product_idle_seconds=190 idle_confirmations=1 "
            "worktree_changes=2 running=yes"
        )
        with temporary, mock.patch.object(module, "role_state", return_value="running"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "inspect")
        self.assertEqual(value["reason_code"], "product-edit-idle-candidate")
        self.assertEqual(value["product_idle_seconds"], 190)
        self.assertEqual(value["idle_confirmations"], 1)

    def test_dispatcher_event_resolves_pid_namespace_visibility_for_diagnosis(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event event=material-change execution_state=implementation-idle "
            "edit_ready=1 product_idle_seconds=190 idle_confirmations=1 "
            "worktree_changes=2 running=yes"
        )
        with temporary, mock.patch.object(module, "role_state", return_value="visibility-unknown"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "inspect")
        self.assertEqual(value["reason_code"], "product-edit-idle-candidate")
        self.assertEqual(value["running"], "yes")
        self.assertEqual(value["dispatcher_observed_running"], "yes")
        self.assertEqual(value["process_visibility"], "restricted")
        self.assertEqual(value["interrupt_authorized"], "no")

    def test_visibility_remains_unknown_without_dispatcher_liveness_evidence(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event event=material-change execution_state=implementation-idle "
            "product_idle_seconds=190 running=unknown"
        )
        with temporary, mock.patch.object(module, "role_state", return_value="visibility-unknown"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "visibility-unknown")
        self.assertEqual(value["running"], "unknown")
        self.assertEqual(value["dispatcher_observed_running"], "no")

    def test_external_blocker_and_named_tool_wait_are_distinct(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event event=material-change execution_state=external-blocked running=yes"
        )
        with temporary, mock.patch.object(module, "role_state", return_value="running"):
            value = module.snapshot(args)
            self.assertEqual(value["decision"], "inspect")
            self.assertEqual(value["reason_code"], "confirmed-external-blocker")
            monitor = args.repo_root / ".worktrees" / f"{args.task_id}.monitor-events.log"
            monitor.write_text(
                "monitor_event event=material-change execution_state=waiting-tool running=yes\n",
                encoding="utf-8",
            )
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "continue")
        self.assertEqual(value["reason_code"], "named-tool-wait")

    def test_last_product_event_survives_later_advisory_event(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event event=material-change execution_state=implementation-ready "
            "product_changes=2 product_delta_from_baseline=1 running=yes"
        )
        monitor = args.repo_root / ".worktrees" / f"{args.task_id}.monitor-events.log"
        with monitor.open("a", encoding="utf-8") as handle:
            handle.write(
                "monitor_event event=extension-evaluation-pending running=yes "
                "terminal=no advisor_state=running\n"
            )
        with temporary, mock.patch.object(module, "role_state", return_value="running"):
            value = module.snapshot(args)
        self.assertEqual(
            value["last_verified_product_event"],
            "material-change;execution_state=implementation-ready;"
            "product_changes=2;product_delta_from_baseline=1",
        )

    def test_terminal_snapshot_exposes_separate_outcome_gates(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event event=terminal execution_state=tail-work running=no terminal=yes"
        )
        outcome = args.repo_root / ".worktrees" / f"{args.task_id}.outcome.json"
        outcome.write_text(json.dumps({
            "dispatch_success": True,
            "artifact_valid": False,
            "validation_success": "missing-evidence",
            "semantic_acceptance": "pending-codex-review",
            "completion_state": "needs-review",
        }), encoding="utf-8")
        (args.repo_root / ".worktrees" / f"{args.task_id}.progress.log").write_text(
            "Final dispatch outcome: success\n", encoding="utf-8",
        )
        with temporary, mock.patch.object(module, "role_state", return_value="not-running"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "terminal")
        self.assertEqual(value["lifecycle_state"], "terminal")
        self.assertEqual(value["startup_state"], "completed")
        self.assertEqual(value["usable"], "no")
        self.assertIn("artifact-validation-required", value["usability_reasons"])
        self.assertTrue(value["dispatch_success"])
        self.assertFalse(value["artifact_valid"])
        self.assertEqual(value["completion_state"], "needs-review")

    def test_custom_preflight_runtime_id_is_monitorable(self):
        module = load_module()
        worktrees = pathlib.Path("/tmp")
        self.assertEqual(
            module.normalize_task("preflight-materials-a", worktrees),
            "preflight-materials-a",
        )

    def test_diff_without_report_has_explicit_awaiting_review_state(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event event=terminal execution_state=tail-work "
            "product_changes=1 evidence_state=diff-without-report "
            "running=no terminal=yes"
        )
        outcome = args.repo_root / ".worktrees" / f"{args.task_id}.outcome.json"
        outcome.write_text(json.dumps({
            "dispatch_success": True,
            "artifact_valid": False,
            "validation_success": "missing-evidence",
            "semantic_acceptance": "pending-codex-review",
            "completion_state": "needs-review",
            "operator_state": "implementation-stable-awaiting-review",
            "evidence_state": "diff-without-report",
            "product_changes": 1,
        }), encoding="utf-8")
        with temporary, mock.patch.object(module, "role_state", return_value="not-running"):
            value = module.snapshot(args)
        self.assertEqual(value["decision"], "terminal")
        self.assertEqual(value["lifecycle_state"], "terminal")
        self.assertEqual(value["running"], "no")
        self.assertEqual(
            value["operator_state"],
            "implementation-stable-awaiting-review",
        )
        self.assertEqual(
            value["last_verified_product_event"],
            "terminal;execution_state=tail-work;product_changes=1",
        )

    def test_terminal_markdown_status_cannot_invent_product_changes(self):
        module = load_module()
        temporary, args = self.make_case(
            "monitor_event event=terminal execution_state=context-acquisition "
            "product_changes=0 control_changes=5 worktree_changes=5 "
            "evidence_state=seeded-report-only running=no terminal=yes"
        )
        worktrees = args.repo_root / ".worktrees"
        (worktrees / f"{args.task_id}.worktree-status.txt").write_text(
            "# Worktree Status After Execution\n\n"
            "## Tracked Changes (git diff --stat)\n(none)\n\n"
            "## Staged Changes (git diff --cached --stat)\n(none)\n\n"
            "## Untracked Files (excluding dispatch scaffolding)\n(none)\n",
            encoding="utf-8",
        )
        with temporary, mock.patch.object(module, "role_state", return_value="not-running"):
            value = module.snapshot(args)
        self.assertEqual(value["product_changes"], 0)
        self.assertEqual(value["control_changes"], 5)
        self.assertEqual(value["worktree_changes"], 5)
        self.assertEqual(value["evidence_state"], "seeded report only")
        self.assertEqual(value["changed_paths"], [])

    def test_cli_json_is_bounded_and_machine_readable(self):
        temporary, args = self.make_case(
            "monitor_event monitor_level=L1 action=wait evidence_state=none "
            "quiet_seconds=10 suspect_count=0 elapsed_seconds=20 artifact_growth=yes running=yes"
        )
        with temporary:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "snapshot", "--repo-root", str(args.repo_root),
                 "--task-id", args.task_id, "--format", "json"],
                text=True, encoding="utf-8", errors="replace", capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["interrupt_authorized"], "no")
        self.assertTrue(value["collected_at"])
        self.assertTrue(value["observed_at"]["monitor_event"])
        self.assertEqual(value["product_changes"], 0)
        self.assertEqual(value["control_changes"], 0)
        self.assertLess(len(result.stdout), 4096)


if __name__ == "__main__":
    unittest.main()
