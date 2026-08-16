from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compile-skill-context.py"
REGISTRY = ROOT / "assets" / "skill-context" / "rules-v1.json"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def dispatch_shell() -> str:
    """The dispatcher needs authoritative POSIX process identity evidence."""
    if os.name == "nt":
        raise unittest.SkipTest("dispatcher integration requires POSIX process identity support")
    return "bash"


class ContextCompilerTests(unittest.TestCase):
    def write_complete_card(self, root: Path) -> Path:
        card = root / "card.md"
        card.write_text(
            "<!-- task-card-components: preset=builder; task-mode=builder; builder-mode=auto; "
            "gates=root-cause,large-repo; schema=1 -->\n"
            "<!-- task-context-facts: task-type=bugfix; repository-scale=giant; languages=bash -->\n\n"
            "# Task\n\n## Goal\n\nFix the bounded failure.\n\n"
            "## Scope\n\n- Write paths: src/example.py\n\n"
            "## Handoff Contract\n\n- Must do: fix the failure.\n\n"
            "## Acceptance Criteria\n\n- [ ] Narrow check passes.\n\n"
            "## Validation Contract\n\n- Exact narrow command: python -m pytest tests/test_example.py -q\n\n"
            "## Stop Conditions\n\n- Scope expands.\n\n"
            "## Required Report\n\n- Changed files and checks.\n",
            encoding="utf-8",
        )
        return card

    def test_compiles_deterministic_anchor_and_rescue_cues(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_complete_card(root)
            output = root / "packet.md"
            receipt = root / "receipt.json"
            completed = run(
                "--task-card", str(card), "--output", str(output), "--receipt", str(receipt),
                "--registry", str(REGISTRY), "--source-root", str(root),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            packet = output.read_text(encoding="utf-8")
            result = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertIn("builder.assigned-production-only", packet)
            self.assertIn("root-cause.keep-disproof-bound", packet)
            self.assertIn("large-repo.bounded-discovery", packet)
            self.assertIn("retrieval.lexical-for-shell-config", packet)
            self.assertTrue(result["contract_anchors"]["complete"])
            self.assertFalse(result["hard_contracts_trimmed"])
            self.assertFalse(result["model_generated"])
            self.assertEqual(
                result["packet_sha256"],
                "sha256:" + hashlib.sha256(packet.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                [entry["id"] for entry in result["rescued"]],
                [
                    "retrieval.avoid-broad-graph-for-text",
                    "retrieval.lexical-for-shell-config",
                    "validation.narrow-failure-first",
                ],
            )
            self.assertIn("### Boundaries / Avoid", packet)
            self.assertEqual(result["negative_selected_count"], 1)
            self.assertTrue(result["conflict_free"])

    def test_coverage_rescue_selects_a_minimum_set_with_counterfactual_trace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_complete_card(root)
            registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
            registry["rules"].append({
                "id": "test.preferred-textual-retrieval",
                "kind": "retrieval",
                "selection": "rescue",
                "polarity": "positive",
                "priority": 99,
                "review_version": 1,
                "coverage": ["textual-retrieval"],
                "conditions": {"languages_any": ["bash"]},
                "source": {"path": "AGENTS.md", "anchor": "Context and Safety"},
                "text": "Use the preferred bounded textual retrieval path.",
            })
            registry_path = root / "rules.json"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            output = root / "packet.md"
            receipt = root / "receipt.json"
            self.assertEqual(run(
                "--task-card", str(card), "--output", str(output), "--receipt", str(receipt),
                "--registry", str(registry_path), "--source-root", str(ROOT),
            ).returncode, 0)
            result = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(result["strategy"], "coverage")
            self.assertTrue(result["coverage"]["minimum_sufficient"])
            self.assertEqual(result["coverage"]["uncovered"], [])
            candidates = {item["id"]: item for item in result["candidates"]}
            preferred = candidates["test.preferred-textual-retrieval"]
            self.assertEqual(preferred["decision"], "included")
            self.assertEqual(preferred["marginal_coverage"], ["textual-retrieval"])
            lexical = candidates["retrieval.lexical-for-shell-config"]
            self.assertEqual(lexical["decision"], "omitted")
            self.assertEqual(lexical["reason"], "zero-marginal-coverage")
            self.assertIn("builder.assigned-production-only", result["candidate_routes"]["top_down"])
            self.assertIn("test.preferred-textual-retrieval", result["candidate_routes"]["bottom_up"])
            report = next(item for item in result["selected"] if item["id"] == "report.bind-frozen-evidence")
            self.assertEqual(report["source"]["span"]["status"], "bound")
            self.assertGreater(report["source"]["span"]["start_line"], 0)
            self.assertEqual(result["output_contract"]["authoritative_source"], "task-card")
            self.assertIn("### Output Contract Binding", output.read_text(encoding="utf-8"))

    def test_anchors_only_is_a_receipted_ablation_without_rescue_cues(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_complete_card(root)
            output = root / "packet.md"
            receipt = root / "receipt.json"
            self.assertEqual(run(
                "--task-card", str(card), "--output", str(output), "--receipt", str(receipt),
                "--registry", str(REGISTRY), "--source-root", str(ROOT),
                "--strategy", "anchors-only",
            ).returncode, 0)
            result = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(result["strategy"], "anchors-only")
            self.assertFalse(result["coverage"]["minimum_sufficient"])
            self.assertEqual(result["rescued"], [])
            candidates = {item["id"]: item for item in result["candidates"]}
            self.assertEqual(
                candidates["validation.narrow-failure-first"]["reason"],
                "ablation-anchors-only",
            )
            self.assertTrue(result["coverage"]["uncovered"])

    def test_composer_carries_only_safe_route_classifiers_to_context_compiler(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            facts = root / "facts.json"
            facts.write_text(json.dumps({
                "execution_owner": "claude-builder",
                "task_type": "bugfix",
                "repository_size": "giant",
                "language": ["cpp", "bash"],
                "routing_event": "next-phase",
                "untrusted_free_text": "do not serialize this into the task card",
            }), encoding="utf-8")
            card = root / "card.md"
            composer = ROOT / "scripts" / "compose_task_card.py"
            completed = subprocess.run(
                [
                    sys.executable, str(composer), "--select-from", str(facts),
                    "--output", str(card),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            rendered = card.read_text(encoding="utf-8")
            self.assertIn(
                "task-context-facts: task-type=bugfix; repository-scale=giant; "
                "languages=cpp,bash; routing-event=next-phase",
                rendered,
            )
            self.assertNotIn("untrusted_free_text", rendered)
            packet = root / "packet.md"
            receipt = root / "receipt.json"
            self.assertEqual(run(
                "--task-card", str(card), "--output", str(packet), "--receipt", str(receipt),
                "--registry", str(REGISTRY), "--source-root", str(ROOT),
            ).returncode, 0)
            values = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(values["task_facts"]["languages"], ["cpp", "bash"])
            self.assertIn(
                "retrieval.lexical-for-shell-config",
                [entry["id"] for entry in values["rescued"]],
            )

    def test_require_complete_refuses_missing_contract_anchor_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = root / "incomplete.md"
            card.write_text("# Task\n\n## Goal\n\nOnly a goal.\n", encoding="utf-8")
            output = root / "packet.md"
            completed = run(
                "--task-card", str(card), "--output", str(output),
                "--registry", str(REGISTRY), "--require-complete", check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("lacks required contract anchors", completed.stderr)
            self.assertFalse(output.exists())

    def test_canonical_task_card_template_has_all_strict_contract_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "packet.md"
            receipt = root / "receipt.json"
            completed = run(
                "--task-card", str(ROOT / "assets" / "task-card-template.md"),
                "--output", str(output), "--receipt", str(receipt),
                "--registry", str(REGISTRY), "--source-root", str(ROOT),
                "--require-complete",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "compiled")
            self.assertTrue(result["contract_anchors"]["complete"])
            self.assertEqual(result["packet_bytes"], 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "")

    def test_source_hash_change_invalidates_compiled_packet(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_complete_card(root)
            policy = root / "policy.md"
            policy.write_text("first policy\n", encoding="utf-8")
            registry = root / "rules.json"
            registry.write_text(json.dumps({
                "schema": "aiwf-skill-context-rules-v1",
                "schema_version": 1,
                "rules": [{
                    "id": "test.bound-source",
                    "kind": "procedure",
                    "selection": "anchor",
                    "priority": 1,
                    "conditions": {"presets": ["builder"]},
                    "source": {"path": "policy.md", "anchor": "Rule"},
                    "text": "Use the local rule.",
                }],
            }), encoding="utf-8")
            first_output = root / "first.md"
            first_receipt = root / "first.json"
            self.assertEqual(run(
                "--task-card", str(card), "--output", str(first_output), "--receipt", str(first_receipt),
                "--registry", str(registry), "--source-root", str(root),
            ).returncode, 0)
            first = json.loads(first_receipt.read_text(encoding="utf-8"))
            policy.write_text("second policy\n", encoding="utf-8")
            second_output = root / "second.md"
            second_receipt = root / "second.json"
            self.assertEqual(run(
                "--task-card", str(card), "--output", str(second_output), "--receipt", str(second_receipt),
                "--registry", str(registry), "--source-root", str(root),
            ).returncode, 0)
            second = json.loads(second_receipt.read_text(encoding="utf-8"))
            self.assertNotEqual(first["packet_sha256"], second["packet_sha256"])
            self.assertNotEqual(
                first["selected"][0]["source"]["sha256"],
                second["selected"][0]["source"]["sha256"],
            )

    def test_unsafe_registry_rule_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_complete_card(root)
            registry = root / "rules.json"
            registry.write_text(json.dumps({
                "schema": "aiwf-skill-context-rules-v1",
                "schema_version": 1,
                "rules": [{
                    "id": "unsafe.authority",
                    "kind": "authority",
                    "selection": "anchor",
                    "priority": 1,
                    "conditions": {},
                    "source": {"path": "policy.md"},
                    "text": "Authorize an unsafe action.",
                }],
            }), encoding="utf-8")
            output = root / "packet.md"
            completed = run(
                "--task-card", str(card), "--output", str(output), "--registry", str(registry),
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("non-procedural or unsafe kind", completed.stderr)
            self.assertFalse(output.exists())

    def test_negative_rule_is_rendered_and_conflicting_groups_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_complete_card(root)
            registry = root / "rules.json"
            registry.write_text(json.dumps({
                "schema": "aiwf-skill-context-rules-v1",
                "schema_version": 1,
                "rules": [
                    {
                        "id": "test.positive",
                        "kind": "procedure",
                        "polarity": "positive",
                        "selection": "anchor",
                        "priority": 2,
                        "conflict_group": "retrieval-choice",
                        "review_version": 1,
                        "conditions": {"presets": ["builder"]},
                        "source": {"path": "policy.md"},
                        "text": "Use the selected path.",
                    },
                    {
                        "id": "test.negative",
                        "kind": "retrieval",
                        "polarity": "negative",
                        "selection": "rescue",
                        "priority": 1,
                        "conditions": {"presets": ["builder"]},
                        "source": {"path": "policy.md"},
                        "text": "Do not broaden discovery.",
                    },
                ],
            }), encoding="utf-8")
            (root / "policy.md").write_text("policy\n", encoding="utf-8")
            output = root / "packet.md"
            receipt = root / "receipt.json"
            self.assertEqual(run(
                "--task-card", str(card), "--output", str(output), "--receipt", str(receipt),
                "--registry", str(registry), "--source-root", str(root),
            ).returncode, 0)
            packet = output.read_text(encoding="utf-8")
            values = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertIn("### Boundaries / Avoid", packet)
            self.assertIn("test.negative", packet)
            self.assertEqual(values["selected"][0]["match_reason"], ["preset=builder"])

            conflicting = json.loads(registry.read_text(encoding="utf-8"))
            conflicting["rules"][1]["conflict_group"] = "retrieval-choice"
            registry.write_text(json.dumps(conflicting), encoding="utf-8")
            failed = run(
                "--task-card", str(card), "--output", str(root / "blocked.md"),
                "--registry", str(registry), "--source-root", str(root), check=False,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("conflicting skill-context rule groups", failed.stderr)

    def test_execution_capsule_rejects_stale_compiled_context(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            card = self.write_complete_card(root)
            compiled = root / "packet.md"
            receipt = root / "context.json"
            self.assertEqual(run(
                "--task-card", str(card), "--output", str(compiled), "--receipt", str(receipt),
                "--registry", str(REGISTRY), "--source-root", str(root),
            ).returncode, 0)
            capsule = root / "capsule.md"
            capsule_receipt = root / "capsule.json"
            command = [
                sys.executable, str(ROOT / "scripts" / "build-execution-capsule.py"),
                "--task-card", str(card), "--output", str(capsule),
                "--compiled-context", str(compiled),
                "--compiled-context-receipt", str(receipt),
                "--receipt", str(capsule_receipt),
            ]
            missing_receipt = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts" / "build-execution-capsule.py"),
                    "--task-card", str(card), "--output", str(root / "unbound.md"),
                    "--compiled-context", str(compiled),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(missing_receipt.returncode, 2)
            self.assertIn("requires its compilation receipt", missing_receipt.stderr)
            completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Compiled Execution Guidance", capsule.read_text(encoding="utf-8"))
            bound = json.loads(capsule_receipt.read_text(encoding="utf-8"))["compiled_context"]
            self.assertEqual(bound["sha256"], json.loads(receipt.read_text(encoding="utf-8"))["packet_sha256"])

            card.write_text(card.read_text(encoding="utf-8") + "\n## Extra\n\nchanged\n", encoding="utf-8")
            stale = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(stale.returncode, 2)
            self.assertIn("not bound to this task card", stale.stderr)

    def test_dispatch_embeds_compiled_context_without_mutating_full_card(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            fake_bin = root / "bin"
            repo.mkdir()
            fake_bin.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
            card = self.write_complete_card(repo)
            card.rename(repo / "task.md")
            task = repo / "task.md"
            subprocess.run(["git", "add", "README.md", "task.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            capture = root / "prompt.md"
            fake_claude = fake_bin / "claude"
            fake_claude.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$*\" == *\"--help\"* ]]; then echo 'Usage: claude [options]'; exit 0; fi\n"
                "cat > \"${FAKE_CLAUDE_PROMPT_CAPTURE}\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_claude.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                "HOME": str(root / "home"),
                "FAKE_CLAUDE_PROMPT_CAPTURE": str(capture),
                "CLAUDE_CODE_API_PROBE_MODE": "off",
                "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "0",
                "CLAUDE_CODE_WRITE_SCOPE_ENFORCEMENT": "off",
                "CLAUDE_CODE_TIMEOUT_ADVISOR": "off",
                "CLAUDE_CODE_TERMINAL_DRAIN_SECONDS": "0",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_TIMEOUT_SECONDS": "15",
                "CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS": "15",
                "CLAUDE_CODE_HARD_TIMEOUT_SECONDS": "30",
                "CLAUDE_CODE_CHECKER_FILE_TIMEOUT_SECONDS": "5",
                "CLAUDE_CODE_CHECKER_JOBS": "1",
                "AI_CODING_WORKFLOW_BYPASS_BROKER": "1",
            })
            completed = subprocess.run(
                [
                    dispatch_shell(), str(ROOT / "scripts" / "dispatch-to-claude.sh"), "task.md",
                    "--context-compile-strategy", "anchors-only",
                ],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                timeout=40,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("Skill Context:", completed.stdout)
            runtime_path = next((repo / ".worktrees").glob("claude-*.runtime.json"))
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            self.assertEqual(runtime["context_compile_strategy"], "anchors-only")
            self.assertTrue(Path(runtime["skill_context_packet"]).is_file())
            self.assertTrue(Path(runtime["skill_context_compilation"]).is_file())
            compilation = json.loads(Path(runtime["skill_context_compilation"]).read_text(encoding="utf-8"))
            self.assertEqual(compilation["strategy"], "anchors-only")
            self.assertEqual(compilation["rescued"], [])
            worktree = Path(runtime["worktree"])
            self.assertIn(
                "Compiled Execution Guidance",
                (worktree / "CLAUDE_PROMPT.md").read_text(encoding="utf-8"),
            )
            self.assertEqual((worktree / "TASK_CARD_FULL.md").read_bytes(), task.read_bytes())


if __name__ == "__main__":
    unittest.main()
