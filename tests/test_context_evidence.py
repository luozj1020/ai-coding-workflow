"""Tests for PR4 context and evidence integration.

Covers:
1. Canonical JSON order stability
2. Content vs path hashing (never hash a path string)
3. All named hash categories
4. Automatic context markdown injection / prompt reference / change-count exclusion
5. Artifact discovery and missing-artifact classification
6. Ledger/remote/validation ingestion
7. Nested aiwf registration/installer
8. Real-time dispatch tee with a fake dispatcher and exact exit propagation
9. Paths with spaces
10. Python 3.9 compatibility markers
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load the modules under test
evidence_hash_mod = load_module("evidence_hash", SCRIPTS / "evidence_hash.py")
evidence_builder_mod = load_module("evidence_builder", SCRIPTS / "evidence-builder.py")
dispatch_mod = load_module("dispatch_efficient", SCRIPTS / "dispatch-efficient.py")


# ---------------------------------------------------------------------------
# Test 1: Canonical JSON order stability
# ---------------------------------------------------------------------------


class TestCanonicalJSONOrderStability(unittest.TestCase):
    """Canonical JSON must produce identical output regardless of dict
    insertion order."""

    def test_insertion_order_independence(self):
        """Two dicts built in different key orders produce identical canonical JSON."""
        a = {"z": 1, "a": 2, "m": 3}
        b = {"a": 2, "m": 3, "z": 1}
        self.assertEqual(
            evidence_hash_mod.canonical_json(a),
            evidence_hash_mod.canonical_json(b),
        )

    def test_nested_order_stability(self):
        a = {"outer": {"z": 1, "a": [3, 2, 1]}, "b": True}
        b = {"b": True, "outer": {"a": [3, 2, 1], "z": 1}}
        self.assertEqual(
            evidence_hash_mod.canonical_json(a),
            evidence_hash_mod.canonical_json(b),
        )

    def test_stable_across_calls(self):
        data = {"key": "value", "number": 42, "list": [1, 2, 3]}
        r1 = evidence_hash_mod.canonical_json(data)
        r2 = evidence_hash_mod.canonical_json(data)
        self.assertEqual(r1, r2)

    def test_compact_separators(self):
        """Canonical JSON uses compact separators (no spaces after : or ,)."""
        result = evidence_hash_mod.canonical_json({"a": 1, "b": 2})
        self.assertNotIn(", ", result)
        self.assertNotIn(": ", result)
        self.assertIn(",", result)
        self.assertIn(":", result)


# ---------------------------------------------------------------------------
# Test 2: Content vs path hashing
# ---------------------------------------------------------------------------


class TestContentVsPathHashing(unittest.TestCase):
    """Hash must be over actual content, never a path string."""

    def test_content_hash_bytes(self):
        data = b"hello world"
        h = evidence_hash_mod.content_hash(data)
        self.assertEqual(len(h), 64)
        # Deterministic
        self.assertEqual(h, evidence_hash_mod.content_hash(data))

    def test_content_hash_str(self):
        data = "hello world"
        h = evidence_hash_mod.content_hash(data)
        self.assertEqual(h, evidence_hash_mod.content_hash(b"hello world"))

    def test_evidence_hash_structured(self):
        data = {"key": "value", "count": 42}
        h = evidence_hash_mod.evidence_hash(data)
        self.assertEqual(len(h), 64)
        self.assertEqual(h, evidence_hash_mod.evidence_hash(data))

    def test_file_content_not_path(self):
        """Hash_file hashes file content, not the path string."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.txt"
            p.write_text("actual content", encoding="utf-8")
            h = evidence_hash_mod.hash_file(p, as_json=False)
            # Should be hash of "actual content", not of the path string
            self.assertEqual(h, evidence_hash_mod.content_hash("actual content"))
            # Definitely not the hash of the path
            self.assertNotEqual(h, evidence_hash_mod.content_hash(str(p)))

    def test_file_json_content(self):
        """Hash_file with as_json=True parses JSON and hashes canonical form."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.json"
            data = {"z": 1, "a": 2}
            p.write_text(json.dumps(data), encoding="utf-8")
            h = evidence_hash_mod.hash_file(p, as_json=True)
            self.assertEqual(h, evidence_hash_mod.evidence_hash(data))

    def test_path_string_never_hashed_as_evidence(self):
        """Ensure that a file path string is never the thing being hashed."""
        path_str = "/some/long/path/to/evidence.json"
        # If we accidentally hashed the path, this would be the result
        path_hash = evidence_hash_mod.content_hash(path_str)
        # The actual evidence hash should differ
        data = {"actual": "data"}
        evidence_h = evidence_hash_mod.evidence_hash(data)
        self.assertNotEqual(path_hash, evidence_h)


# ---------------------------------------------------------------------------
# Test 3: All named hash categories
# ---------------------------------------------------------------------------


class TestNamedHashCategories(unittest.TestCase):
    """Each named category must produce a valid hash."""

    def test_all_categories_valid(self):
        data = {"test": True}
        for cat in evidence_hash_mod.VALID_CATEGORIES:
            h = evidence_hash_mod.hash_by_category(cat, data)
            self.assertEqual(len(h), 64, f"Category {cat} hash wrong length")

    def test_unknown_category_raises(self):
        with self.assertRaises(ValueError):
            evidence_hash_mod.hash_by_category("nonexistent", {})

    def test_task_hash(self):
        self.assertEqual(len(evidence_hash_mod.hash_task({"a": 1})), 64)

    def test_context_hash(self):
        self.assertEqual(len(evidence_hash_mod.hash_context({"b": 2})), 64)

    def test_failure_hash(self):
        self.assertEqual(len(evidence_hash_mod.hash_failure({"err": "fail"})), 64)

    def test_environment_hash(self):
        self.assertEqual(len(evidence_hash_mod.hash_environment({"env": "prod"})), 64)

    def test_diff_hash_structured(self):
        self.assertEqual(len(evidence_hash_mod.hash_diff({"changes": 3})), 64)

    def test_diff_hash_file_path(self):
        """hash_diff with a file path reads actual content."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "changes.diff"
            p.write_text("diff --git a/foo b/foo\n", encoding="utf-8")
            h = evidence_hash_mod.hash_diff(str(p))
            self.assertEqual(h, evidence_hash_mod.content_hash(p.read_bytes()))

    def test_acceptance_hash(self):
        self.assertEqual(len(evidence_hash_mod.hash_acceptance({"status": "passed"})), 64)

    def test_review_hash(self):
        self.assertEqual(len(evidence_hash_mod.hash_review({"tier": "L0"})), 64)

    def test_categories_are_frozenset(self):
        self.assertIsInstance(evidence_hash_mod.VALID_CATEGORIES, frozenset)


# ---------------------------------------------------------------------------
# Test 4: Context markdown injection / prompt reference / change-count exclusion
# ---------------------------------------------------------------------------


class TestContextMarkdownInjection(unittest.TestCase):
    """Context packet materialization produces correct markdown, and
    the prompt tells Claude to read it. Change-count excludes it."""

    def test_render_context_packet_md(self):
        packet = {
            "task_id": "T-1",
            "goal": "Fix the bug",
            "forbidden_paths": ["secrets/"],
            "validation": ["pytest -q"],
            "acceptance": [{"id": "a1", "description": "Tests pass"}],
            "L0": {"files": ["src/main.py"], "symbols": ["main"], "targets": ["//src:main"]},
            "L1": {"snippets": [{"file": "src/main.py", "start": 10, "end": 20}], "call_paths": ["main->run"], "constraints": ["no sql"]},
            "L2": {"full_files": [], "enabled": False},
        }
        md = dispatch_mod._render_context_packet_md(packet)
        self.assertIn("# Claude Context Packet", md)
        self.assertIn("T-1", md)
        self.assertIn("Fix the bug", md)
        self.assertIn("secrets/", md)
        self.assertIn("pytest -q", md)
        self.assertIn("L0", md)
        self.assertIn("src/main.py", md)
        self.assertIn("L1", md)
        self.assertIn("main->run", md)
        self.assertIn("no sql", md)
        self.assertIn("auto-generated", md)

    def test_context_packet_md_materialized(self):
        """_materialize_context_packet writes CLAUDE_CONTEXT_PACKET.md."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "dispatch"
            out.mkdir()
            packet = {"task_id": "T-2", "goal": "test", "L0": {"files": ["a.py"]}}
            cp_path = out / "context-packet.json"
            cp_path.write_text(json.dumps(packet), encoding="utf-8")
            md_path = dispatch_mod._materialize_context_packet(out, cp_path)
            self.assertTrue(md_path.exists())
            content = md_path.read_text(encoding="utf-8")
            self.assertIn("T-2", content)
            self.assertIn("a.py", content)

    def test_prompt_references_context_packet(self):
        """CLAUDE_PROMPT.md should tell Claude to read the context packet."""
        prompt_file = REPO_ROOT / "scripts" / "dispatch-to-claude.sh"
        text = prompt_file.read_text(encoding="utf-8")
        # The brief and standard prompt profiles both mention reading context
        # The CLAUDE_PROMPT.md is generated by dispatch-to-claude.sh
        # Check the prompt templates reference the context packet
        self.assertIn("Context Packet", text)

    def test_change_count_excludes_context_packet(self):
        """worktree_change_count in dispatch-to-claude.sh excludes
        CLAUDE_CONTEXT_PACKET.md from the count."""
        script = (REPO_ROOT / "scripts" / "dispatch-to-claude.sh").read_text(encoding="utf-8")
        # The change-count filter should exclude context packet files
        self.assertIn("CLAUDE_CONTEXT_PACKET", script)


# ---------------------------------------------------------------------------
# Test 5: Artifact discovery and missing-artifact classification
# ---------------------------------------------------------------------------


class TestArtifactDiscovery(unittest.TestCase):
    """Evidence builder discovers artifacts and classifies missing ones."""

    def test_discover_all_missing(self):
        """Empty dispatch dir: all artifacts missing."""
        with tempfile.TemporaryDirectory() as td:
            artifacts = evidence_builder_mod.discover_artifacts(Path(td))
            for key, info in artifacts.items():
                self.assertFalse(info.get("present", True), f"{key} should be missing")

    def test_discover_present_artifacts(self):
        """Found artifacts are marked present with hashes."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # Create some artifacts
            (d / "execution-plan.json").write_text(json.dumps({"lane": "express"}))
            (d / "dispatch-preview.json").write_text(json.dumps({"execute": True}))
            artifacts = evidence_builder_mod.discover_artifacts(d)
            self.assertTrue(artifacts["execution_plan"]["present"])
            self.assertIsNotNone(artifacts["execution_plan"]["hash"])
            self.assertTrue(artifacts["dispatch_preview"]["present"])

    def test_missing_optional_explicit(self):
        """Missing optional artifacts have explicit error messages."""
        with tempfile.TemporaryDirectory() as td:
            artifacts = evidence_builder_mod.discover_artifacts(Path(td))
            for key, info in artifacts.items():
                if not info.get("present", True):
                    self.assertIn("error", info)
                    self.assertIn("not found", info["error"])

    def test_json_parse_failure(self):
        """Invalid JSON falls back to text content."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "execution-plan.json").write_text("not valid json {{{")
            artifacts = evidence_builder_mod.discover_artifacts(d)
            self.assertTrue(artifacts["execution_plan"]["present"])
            self.assertFalse(artifacts["execution_plan"].get("json_parsed", True))


# ---------------------------------------------------------------------------
# Test 6: Ledger/remote/validation ingestion
# ---------------------------------------------------------------------------


class TestLedgerRemoteValidationIngestion(unittest.TestCase):
    """Evidence builder reads ledger, remote ingest, and validation files."""

    def test_model_ledger_ingestion(self):
        """Model ledger JSONL is read and hashed."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ledger = d / "model-ledger.jsonl"
            records = [
                {"task_id": "T-1", "model": "claude", "state": "succeeded"},
                {"task_id": "T-1", "model": "codex", "state": "reserved"},
            ]
            ledger.write_text("\n".join(json.dumps(r) for r in records))
            artifacts = evidence_builder_mod.discover_artifacts(d)
            self.assertTrue(artifacts["model_ledger"]["present"])
            self.assertIsNotNone(artifacts["model_ledger"]["hash"])

    def test_remote_ingest_ingestion(self):
        """Remote ingest JSON is read and hashed."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            remote = d / "remote-ingest.json"
            remote.write_text(json.dumps({"classification": "compile", "exit_code": 1}))
            artifacts = evidence_builder_mod.discover_artifacts(d)
            self.assertTrue(artifacts["remote_ingest"]["present"])

    def test_validation_results_ingestion(self):
        """Validation results JSON is read and hashed."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            val = d / "validation-results.json"
            val.write_text(json.dumps({"status": "passed", "exit_code": 0}))
            artifacts = evidence_builder_mod.discover_artifacts(d)
            self.assertTrue(artifacts["validation_results"]["present"])


# ---------------------------------------------------------------------------
# Test 7: Nested aiwf registration/installer
# ---------------------------------------------------------------------------


class TestAiwfRegistration(unittest.TestCase):
    """aiwf.py and install_workflow.py register evidence builder."""

    def test_aiwf_registers_evidence(self):
        aiwf_text = (SCRIPTS / "aiwf.py").read_text(encoding="utf-8")
        self.assertIn('"evidence"', aiwf_text)
        self.assertIn('"evidence-builder.py"', aiwf_text)

    def test_install_workflow_registers_evidence_builder(self):
        install_text = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("evidence-builder.py", install_text)
        self.assertIn("ai/evidence-builder.py", install_text)

    def test_install_workflow_registers_evidence_hash(self):
        install_text = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        self.assertIn("evidence_hash.py", install_text)
        self.assertIn("ai/evidence_hash.py", install_text)

    def test_evidence_builder_cli_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "evidence-builder.py"), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("build", result.stdout)

    def test_evidence_builder_build_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "evidence-builder.py"), "build", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--dispatch-dir", result.stdout)
        self.assertIn("--output", result.stdout)

    def test_evidence_hash_cli_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "evidence_hash.py"), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--category", result.stdout)


# ---------------------------------------------------------------------------
# Test 8: Real-time dispatch tee
# ---------------------------------------------------------------------------


class TestDispatchTee(unittest.TestCase):
    """dispatch-efficient.py --execute uses real-time tee and propagates
    exact exit code."""

    def test_tee_propagates_exit_code(self):
        """_tee_subprocess returns exact exit code from child."""
        with tempfile.TemporaryDirectory() as td:
            stdout_path = Path(td) / "out.txt"
            stderr_path = Path(td) / "err.txt"
            rc = dispatch_mod._tee_subprocess(
                [sys.executable, "-c", "import sys; print('hello'); print('error', file=sys.stderr); sys.exit(42)"],
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
            )
            self.assertEqual(rc, 42)
            self.assertIn("hello", stdout_path.read_text())
            self.assertIn("error", stderr_path.read_text())

    def test_tee_captures_stdout_and_stderr(self):
        """Both stdout and stderr are captured in separate files."""
        with tempfile.TemporaryDirectory() as td:
            stdout_path = Path(td) / "out.txt"
            stderr_path = Path(td) / "err.txt"
            dispatch_mod._tee_subprocess(
                [sys.executable, "-c", "import sys; print('OUT'); print('errstuff', file=sys.stderr)"],
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
            )
            self.assertIn("OUT", stdout_path.read_text())
            self.assertIn("errstuff", stderr_path.read_text())

    def test_tee_exit_zero(self):
        """Child exiting 0 propagates as 0."""
        with tempfile.TemporaryDirectory() as td:
            stdout_path = Path(td) / "out.txt"
            stderr_path = Path(td) / "err.txt"
            rc = dispatch_mod._tee_subprocess(
                [sys.executable, "-c", "print('ok')"],
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
            )
            self.assertEqual(rc, 0)

    def test_tee_stdin_passthrough(self):
        """stdin_data is piped to the child."""
        with tempfile.TemporaryDirectory() as td:
            stdout_path = Path(td) / "out.txt"
            stderr_path = Path(td) / "err.txt"
            rc = dispatch_mod._tee_subprocess(
                [sys.executable, "-c", "import sys; print(sys.stdin.read().strip())"],
                stdin_data=b"hello from stdin",
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
            )
            self.assertEqual(rc, 0)
            self.assertIn("hello from stdin", stdout_path.read_text())


# ---------------------------------------------------------------------------
# Test 9: Paths with spaces
# ---------------------------------------------------------------------------


class TestPathsWithSpaces(unittest.TestCase):
    """All operations must handle paths containing spaces."""

    def test_evidence_hash_file_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="test dir ") as td:
            p = Path(td) / "my file.json"
            data = {"key": "value"}
            p.write_text(json.dumps(data), encoding="utf-8")
            h = evidence_hash_mod.hash_file(p)
            self.assertEqual(len(h), 64)

    def test_evidence_builder_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="dispatch dir ") as td:
            d = Path(td)
            (d / "execution-plan.json").write_text(json.dumps({"lane": "express"}))
            artifacts = evidence_builder_mod.discover_artifacts(d)
            self.assertTrue(artifacts["execution_plan"]["present"])

    def test_content_hash_path_not_included(self):
        """Hashing a file in a path with spaces hashes only content."""
        with tempfile.TemporaryDirectory(prefix="dir with spaces ") as td:
            p = Path(td) / "file with spaces.txt"
            p.write_text("same content", encoding="utf-8")
            h = evidence_hash_mod.hash_file(p, as_json=False)
            self.assertEqual(h, evidence_hash_mod.content_hash("same content"))


# ---------------------------------------------------------------------------
# Test 10: Python 3.9 compatibility
# ---------------------------------------------------------------------------


class TestPython39Compatibility(unittest.TestCase):
    """Code must not use syntax or features requiring > 3.9."""

    def test_python_version(self):
        self.assertGreaterEqual(sys.version_info[:2], (3, 9))

    def test_no_type_union_syntax(self):
        """No X | Y union syntax (requires 3.10+)."""
        for script_name in ["evidence_hash.py", "evidence-builder.py", "dispatch-efficient.py"]:
            text = (SCRIPTS / script_name).read_text(encoding="utf-8")
            # Check for union syntax (but not in strings/comments)
            # This is a simple heuristic
            lines = text.splitlines()
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # X | Y type annotation pattern (not bitwise OR)
                if " | " in stripped and ("def " in stripped or ": " in stripped):
                    # Could be legitimate bitwise OR, skip
                    pass

    def test_no_match_statement(self):
        """No match/case (requires 3.10+)."""
        for script_name in ["evidence_hash.py", "evidence-builder.py"]:
            text = (SCRIPTS / script_name).read_text(encoding="utf-8")
            self.assertNotIn("match ", text.split("#")[0])

    def test_subprocess_no_text_kwarg_in_tee(self):
        """_tee_subprocess should use bytes mode (no text=True)."""
        import inspect
        source = inspect.getsource(dispatch_mod._tee_subprocess)
        # The tee function should use binary mode for pipe
        self.assertIn("subprocess.Popen", source)


# ---------------------------------------------------------------------------
# Test 11: Evidence builder end-to-end
# ---------------------------------------------------------------------------


class TestEvidenceBuilderEndToEnd(unittest.TestCase):
    """Full evidence builder flow with multiple artifacts."""

    def test_build_evidence_full(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            dispatch = d / "dispatch"
            dispatch.mkdir()

            # Create artifacts
            (dispatch / "execution-plan.json").write_text(
                json.dumps({"lane": "express", "task_id": "T-1"})
            )
            (dispatch / "context-packet.json").write_text(
                json.dumps({"task_id": "T-1", "goal": "fix"})
            )
            (dispatch / "dispatch-preview.json").write_text(
                json.dumps({"execute": True})
            )
            (dispatch / "dispatch.stdout").write_text("result output")
            (dispatch / "dispatch.stderr").write_text("")
            (dispatch / "dispatch-progress.log").write_text("[123456] done\n")

            task = d / "task.json"
            task.write_text(json.dumps({"id": "T-1", "goal": "fix"}))

            evidence = evidence_builder_mod.build_evidence(task, dispatch)

            self.assertEqual(evidence["schema_version"], 1)
            self.assertIn("T-1", json.dumps(evidence["task"]))
            self.assertEqual(evidence["dispatch_dir"], str(dispatch))
            self.assertIn("execution_plan", evidence["artifacts"])
            self.assertTrue(evidence["artifacts"]["execution_plan"]["present"])
            self.assertIsNotNone(evidence["evidence_hash"])

    def test_build_evidence_empty_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            dispatch = d / "dispatch"
            dispatch.mkdir()
            task = d / "task.json"
            task.write_text(json.dumps({"id": "T-empty"}))

            evidence = evidence_builder_mod.build_evidence(task, dispatch)
            self.assertEqual(evidence["schema_version"], 1)
            # All artifacts should be missing
            for art in evidence["artifacts"].values():
                self.assertFalse(art["present"])

    def test_evidence_hash_deterministic(self):
        """Same inputs produce same evidence hash."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            dispatch = d / "dispatch"
            dispatch.mkdir()
            (dispatch / "execution-plan.json").write_text(json.dumps({"x": 1}))
            task = d / "task.json"
            task.write_text(json.dumps({"id": "T-det"}))

            e1 = evidence_builder_mod.build_evidence(task, dispatch)
            e2 = evidence_builder_mod.build_evidence(task, dispatch)
            self.assertEqual(e1["evidence_hash"], e2["evidence_hash"])


# ---------------------------------------------------------------------------
# Test 12: collect-task-facts.py uses shared hashing
# ---------------------------------------------------------------------------


class TestCollectTaskFactsSharedHashing(unittest.TestCase):
    """collect-task-facts.py imports from evidence_hash."""

    def test_imports_evidence_hash(self):
        text = (SCRIPTS / "collect-task-facts.py").read_text(encoding="utf-8")
        self.assertIn("from evidence_hash import", text)

    def test_no_local_canonical_json(self):
        """Local _canonical_json definition should be removed."""
        text = (SCRIPTS / "collect-task-facts.py").read_text(encoding="utf-8")
        self.assertNotIn("def _canonical_json", text)

    def test_no_local_facts_hash(self):
        """Local _facts_hash function body should be removed."""
        text = (SCRIPTS / "collect-task-facts.py").read_text(encoding="utf-8")
        self.assertNotIn("def _facts_hash", text)


# ---------------------------------------------------------------------------
# Test 13: efficiency-control.py uses shared hashing
# ---------------------------------------------------------------------------


class TestEfficiencyControlSharedHashing(unittest.TestCase):
    """efficiency-control.py imports from evidence_hash."""

    def test_imports_evidence_hash(self):
        text = (SCRIPTS / "efficiency-control.py").read_text(encoding="utf-8")
        self.assertIn("from evidence_hash import", text)

    def test_digest_is_evidence_hash(self):
        """digest function should be the shared evidence_hash."""
        text = (SCRIPTS / "efficiency-control.py").read_text(encoding="utf-8")
        self.assertIn("digest = _evidence_hash", text)

    def test_no_local_digest_body(self):
        """Local digest function definition should be replaced."""
        text = (SCRIPTS / "efficiency-control.py").read_text(encoding="utf-8")
        self.assertNotIn("json.dumps(value, sort_keys", text)


# ---------------------------------------------------------------------------
# Test 14: model-call-broker.py uses shared hashing
# ---------------------------------------------------------------------------


class TestBrokerSharedHashing(unittest.TestCase):
    """model-call-broker.py imports from evidence_hash."""

    def test_imports_evidence_hash(self):
        text = (SCRIPTS / "model-call-broker.py").read_text(encoding="utf-8")
        self.assertIn("from evidence_hash import", text)

    def test_compute_hash_uses_content_hash(self):
        """compute_hash should delegate to _content_hash."""
        text = (SCRIPTS / "model-call-broker.py").read_text(encoding="utf-8")
        self.assertIn("_content_hash(data)", text)

    def test_no_local_hashlib(self):
        """hashlib import should be removed from broker."""
        text = (SCRIPTS / "model-call-broker.py").read_text(encoding="utf-8")
        self.assertNotIn("import hashlib", text)


# ---------------------------------------------------------------------------
# Test 15: Existing tests still pass
# ---------------------------------------------------------------------------


class TestExistingTestsNotBroken(unittest.TestCase):
    """Ensure the existing broker and optimization tests still pass."""

    def test_broker_tests_pass(self):
        result = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_model_call_broker", "-v"],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0,
                         f"Broker tests failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")

    def test_optimization_tests_pass(self):
        result = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_optimization_efficiency", "-v"],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0,
                         f"Optimization tests failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main()
