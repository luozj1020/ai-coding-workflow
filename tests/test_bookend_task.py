"""Behavioral tests for the durable Bookend control plane."""

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bookend-task.py"
PROFILES = ROOT / "profiles"

RISK_KEYS = (
    "public_api",
    "data_model",
    "security",
    "migration",
    "permission",
    "concurrency",
    "cross_module",
    "production_impact",
)


def task_value(task_id="bookend-test"):
    return {
        "schema_version": 1,
        "id": task_id,
        "mode": "builder",
        "goal": "Exercise the Bookend controller",
        "profiles": ["base"],
        "scope": {"write_paths": ["README.md"], "forbidden_paths": []},
        "acceptance": [
            {
                "id": "ac-1",
                "description": "change is validated",
                "validation_id": "val-1",
            }
        ],
        "risk": {key: "no" for key in RISK_KEYS},
        "handoff": {"must_do": ["report result"]},
        "validation": [{"id": "val-1", "command": ["python", "-V"]}],
        "stop_conditions": ["stop on semantic contract conflict"],
        "extensions": {"routing_hints": {"predicted_diff_lines": 10}},
    }


def write_executor(path, mode):
    path.write_text(
        textwrap.dedent(f"""
        import argparse, hashlib, json, os, time
        from pathlib import Path

        parser = argparse.ArgumentParser()
        parser.add_argument("task")
        parser.add_argument("--run-dir-base", required=True)
        parser.add_argument("--json", action="store_true")
        args, _ = parser.parse_known_args()
        epoch = int(os.environ.get("AIWF_BOOKEND_EPOCH", "1"))
        mode = {mode!r}
        if mode == "rollover-unproven":
            print(json.dumps({{
                "status": "failed",
                "bookend_state": "epoch_expired",
                "continuation_safe": True,
            }}))
            raise SystemExit(76)
        if mode == "rollover" and epoch == 1:
            receipt = Path(args.run_dir_base) / "continuation-receipt.json"
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps({{
                "schema_version": 1,
                "kind": "bookend-epoch-continuation",
                "logical_task_id": os.environ["AIWF_BOOKEND_LOGICAL_TASK_ID"],
                "contract_hash": os.environ["AIWF_BOOKEND_CONTRACT_HASH"],
                "epoch": epoch,
                "owner": "claude",
                "prior_write_grant_revoked": True,
                "no_active_writer": True,
                "stable_state_hash": "sha256:" + "0" * 64,
            }}, sort_keys=True), encoding="utf-8")
            print(json.dumps({{
                "status": "failed",
                "bookend_state": "epoch_expired",
                "continuation_safe": True,
                "continuation_receipt": str(receipt),
                "continuation_receipt_sha256": "sha256:" + hashlib.sha256(receipt.read_bytes()).hexdigest(),
            }}))
            raise SystemExit(76)
        if mode == "semantic-unproven":
            print(json.dumps({{
                "status": "failed",
                "bookend_state": "semantic_blocked",
                "failure_status": "semantic-decision-required",
            }}))
            raise SystemExit(10)
        if mode == "semantic":
            receipt = Path(args.run_dir_base) / "semantic-block.json"
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps({{
                "schema_version": 1,
                "kind": "bookend-semantic-block",
                "logical_task_id": os.environ["AIWF_BOOKEND_LOGICAL_TASK_ID"],
                "contract_hash": os.environ["AIWF_BOOKEND_CONTRACT_HASH"],
                "epoch": epoch,
                "blocking_acceptance": ["ac-1"],
                "decision_required": "choose externally observable behavior",
                "execution_failure_only": False,
            }}, sort_keys=True), encoding="utf-8")
            print(json.dumps({{
                "status": "failed",
                "bookend_state": "semantic_blocked",
                "failure_status": "semantic-decision-required",
                "semantic_block_receipt": str(receipt),
                "semantic_block_receipt_sha256": "sha256:" + hashlib.sha256(receipt.read_bytes()).hexdigest(),
            }}))
            raise SystemExit(10)
        if mode == "runtime":
            print(json.dumps({{
                "status": "failed",
                "bookend_state": "runtime_blocked",
                "error": "transport unavailable",
            }}))
            raise SystemExit(1)
        if mode == "fail-then-succeed" and epoch == 1:
            print(json.dumps({{
                "status": "failed",
                "bookend_state": "runtime_blocked",
                "error": "test failures in module X",
            }}))
            raise SystemExit(1)
        if mode == "persist-state":
            prior = os.environ.get("AIWF_BOOKEND_PRODUCT_WORKTREE")
            if prior and epoch > 1:
                product = Path(prior) / "product.txt"
                if not product.exists():
                    print(json.dumps({{"status": "failed", "error": "product state lost"}}))
                    raise SystemExit(1)
                content = product.read_text(encoding="utf-8")
                if content.strip() != "partial":
                    print(json.dumps({{"status": "failed", "error": "unexpected state"}}))
                    raise SystemExit(1)
                product.write_text("complete", encoding="utf-8")
                run_dir = Path(args.run_dir_base) / "fake-run"
                run_dir.mkdir(parents=True)
                diff = run_dir / "product.diff"
                diff.write_text("diff --git a/a.py b/a.py\\n@@ -1 +1 @@\\n-old\\n+new\\n", encoding="utf-8")
                (run_dir / "dispatch.stdout").write_text(f"Diff: {{diff}}\\n", encoding="utf-8")
                for name in ("result.json", "evidence.json", "acceptance-result.json", "review-ladder-result.json", "artifact-manifest.json"):
                    (run_dir / name).write_text("{{}}\\n", encoding="utf-8")
                print(json.dumps({{
                    "status": "completed",
                    "bookend_state": "done_candidate",
                    "acceptance_status": "passed",
                    "run_dir": str(run_dir),
                    "product_worktree": prior,
                }}))
                raise SystemExit(0)
            # Create worktree under the repo's .worktrees/ boundary so the
            # receipt generator's boundary check passes.
            import subprocess as _sp
            repo_root = _sp.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True
            ).stdout.strip()
            wt = str(Path(repo_root) / ".worktrees" / "bookend-test-wt")
            Path(wt).mkdir(parents=True, exist_ok=True)
            _sp.run(["git", "init", wt], capture_output=True)
            _sp.run(["git", "-C", wt, "config", "user.email", "test@test"], capture_output=True)
            _sp.run(["git", "-C", wt, "config", "user.name", "test"], capture_output=True)
            (Path(wt) / "product.txt").write_text("partial", encoding="utf-8")
            _sp.run(["git", "-C", wt, "add", "-A"], capture_output=True)
            _sp.run(["git", "-C", wt, "commit", "-m", "init"], capture_output=True)
            print(json.dumps({{
                "status": "failed",
                "bookend_state": "runtime_blocked",
                "error": "validation failed",
                "product_worktree": wt,
            }}))
            raise SystemExit(1)
        if mode == "slow":
            time.sleep(1.0)

        run_dir = Path(args.run_dir_base) / "fake-run"
        run_dir.mkdir(parents=True)
        diff = run_dir / "product.diff"
        diff.write_text("diff --git a/a.py b/a.py\\n@@ -1 +1 @@\\n-old\\n+new\\n", encoding="utf-8")
        (run_dir / "dispatch.stdout").write_text(f"Diff: {{diff}}\\n", encoding="utf-8")
        for name in (
            "result.json", "evidence.json", "acceptance-result.json",
            "review-ladder-result.json", "artifact-manifest.json",
        ):
            (run_dir / name).write_text("{{}}\\n", encoding="utf-8")
        print(json.dumps({{
            "status": "completed",
            "bookend_state": "done_candidate",
            "acceptance_status": "passed",
            "run_dir": str(run_dir),
        }}))
    """),
        encoding="utf-8",
    )


class BookendTaskTests(unittest.TestCase):
    def run_submit(self, tmp, mode="done", max_epochs=3, task_data=None):
        tmp_path = Path(tmp)
        task = tmp_path / "task.json"
        task.write_text(json.dumps(task_data or task_value()), encoding="utf-8")
        executor = tmp_path / "fake-executor.py"
        write_executor(executor, mode)
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "submit",
                str(task),
                "--repo",
                str(ROOT),
                "--profiles-dir",
                str(PROFILES),
                "--run-dir-base",
                str(tmp_path / "runs"),
                "--executor",
                str(executor),
                "--max-epochs",
                str(max_epochs),
                "--foreground",
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        value = json.loads(proc.stdout) if proc.stdout.strip() else None
        return proc, value

    def test_done_candidate_emits_complete_projection_and_wake_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(state["state"], "review_ready")
            self.assertTrue(state["codex_wakeup_required"])
            projection = json.loads(
                Path(state["review_projection"]).read_text(encoding="utf-8")
            )
            self.assertTrue(projection["coverage_valid"])
            self.assertEqual(projection["unclassified_byte_count"], 0)
            self.assertEqual(projection["coverage"][0]["start_byte"], 0)
            self.assertEqual(
                projection["coverage"][0]["end_byte"],
                projection["diff"]["bytes"],
            )
            wake = json.loads(Path(state["review_request"]).read_text(encoding="utf-8"))
            self.assertEqual(wake["codex_action"], "final-semantic-review")
            self.assertFalse(wake["merge_authorized"])

            review = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "review-input",
                    str(Path(state["control_dir"]) / "bookend-state.json"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(review.returncode, 0, review.stderr)
            self.assertEqual(json.loads(review.stdout)["kind"], "final-review")

    def test_review_input_revokes_wakeup_when_bound_diff_drifts(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            wake = json.loads(Path(state["review_request"]).read_text(encoding="utf-8"))
            Path(wake["diff"]["path"]).write_text("tampered\n", encoding="utf-8")
            state_path = Path(state["control_dir"]) / "bookend-state.json"
            review = subprocess.run(
                [sys.executable, str(SCRIPT), "review-input", str(state_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(review.returncode, 2)
            current = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(current["state"], "runtime_blocked")
            self.assertFalse(current["codex_wakeup_required"])
            self.assertIn("artifact hash mismatch", current["blocker"])

    def test_epoch_expiry_continues_without_codex_wakeup(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp, mode="rollover", max_epochs=2)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(state["state"], "review_ready")
            self.assertEqual(state["epoch"], 2)
            events = (Path(state["control_dir"]) / "bookend-events.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn('"state": "recovering"', events)
            self.assertEqual(events.count('"event": "codex_wakeup_requested"'), 1)

    def test_semantic_block_is_the_only_early_codex_wakeup(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp, mode="semantic")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(state["state"], "semantic_blocked")
            self.assertTrue(state["codex_wakeup_required"])
            wake = json.loads(Path(state["review_request"]).read_text(encoding="utf-8"))
            self.assertEqual(wake["codex_action"], "bounded-contract-delta")

    def test_runtime_failure_does_not_wake_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp, mode="runtime", max_epochs=1)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertEqual(state["state"], "runtime_blocked")
            self.assertFalse(state["codex_wakeup_required"])
            self.assertIsNone(state["review_request"])

    def test_runtime_failure_continues_across_epochs_until_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp, mode="runtime", max_epochs=3)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertEqual(state["state"], "runtime_blocked")
            self.assertEqual(state["epoch"], 3)
            state_path = Path(state["control_dir"]) / "bookend-state.json"
            final = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(final["recovery"], "convergence-continue")
            events = (Path(state["control_dir"]) / "bookend-events.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn('"state": "recovering"', events)
            self.assertEqual(events.count('"event": "codex_wakeup_requested"'), 0)

    def test_convergence_continue_reaches_done_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp, mode="fail-then-succeed", max_epochs=3)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(state["state"], "review_ready")
            self.assertEqual(state["epoch"], 2)
            state_path = Path(state["control_dir"]) / "bookend-state.json"
            final = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(final["recovery"], "convergence-continue")
            events = (Path(state["control_dir"]) / "bookend-events.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn('"state": "recovering"', events)
            self.assertEqual(events.count('"event": "codex_wakeup_requested"'), 1)

    def test_product_state_persists_across_convergence_epochs(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp, mode="persist-state", max_epochs=2)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(state["state"], "review_ready")
            self.assertEqual(state["epoch"], 2)
            state_path = Path(state["control_dir"]) / "bookend-state.json"
            final = json.loads(state_path.read_text(encoding="utf-8"))
            wt = final["product_worktree"]
            self.assertTrue(
                Path(wt, "product.txt").exists(),
                "product worktree must survive across epochs",
            )
            self.assertEqual(
                Path(wt, "product.txt").read_text(encoding="utf-8"),
                "complete",
                "epoch 2 must build on epoch 1 product state, not restart from scratch",
            )

    def test_unproven_semantic_claim_does_not_wake_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp, mode="semantic-unproven")
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertEqual(state["state"], "runtime_blocked")
            self.assertFalse(state["codex_wakeup_required"])
            self.assertIn("semantic block receipt is missing", state["blocker"])

    def test_epoch_budget_exhaustion_does_not_wake_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp, mode="rollover", max_epochs=1)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertEqual(state["state"], "budget_exhausted")
            self.assertFalse(state["codex_wakeup_required"])

    def test_epoch_rollover_without_single_writer_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, state = self.run_submit(tmp, mode="rollover-unproven", max_epochs=2)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertEqual(state["state"], "runtime_blocked")
            self.assertFalse(state["codex_wakeup_required"])
            self.assertIn("receipt is missing", state["blocker"])

    def test_aiwf_submit_routes_to_bookend_controller(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "aiwf.py"), "submit", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Freeze and submit a durable Bookend task", proc.stdout)

    def test_large_task_shape_is_advisory_inside_frozen_bookend_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = task_value("large-bookend")
            task["scope"]["write_paths"] = [
                "README.md",
                "README_CN.md",
                "SKILL.md",
                "AGENTS.md",
                "references/operating-model.md",
                "references/review-policy.md",
            ]
            task["extensions"]["task_shape"] = {
                "responsibilities": ["one atomic framework rewrite"]
            }
            proc, state = self.run_submit(tmp, task_data=task)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(state["state"], "review_ready")
            freeze = json.loads(
                Path(state["freeze_result"]).read_text(encoding="utf-8")
            )
            self.assertEqual(freeze["final_decision"], "claude-dispatch-ready")

    def test_default_submit_returns_before_supervisor_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task = tmp_path / "task.json"
            task.write_text(json.dumps(task_value("async-bookend")), encoding="utf-8")
            executor = tmp_path / "fake-executor.py"
            write_executor(executor, "slow")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "submit",
                    str(task),
                    "--repo",
                    str(ROOT),
                    "--profiles-dir",
                    str(PROFILES),
                    "--run-dir-base",
                    str(tmp_path / "runs"),
                    "--executor",
                    str(executor),
                    "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            submitted = json.loads(proc.stdout)
            self.assertFalse(submitted["terminal"])
            state_path = Path(submitted["control_dir"]) / "bookend-state.json"
            deadline = __import__("time").monotonic() + 8
            state = submitted
            while __import__("time").monotonic() < deadline:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("terminal"):
                    break
                __import__("time").sleep(0.05)
            self.assertEqual(state["state"], "review_ready")
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
