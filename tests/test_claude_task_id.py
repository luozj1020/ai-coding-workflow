import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude_task_id.py"
MONITOR = ROOT / "scripts" / "monitor-claude.sh"


def bash_exe() -> str:
    if os.name == "nt":
        for candidate in (
            pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
            pathlib.Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ):
            if candidate.is_file():
                return str(candidate)
    return "bash"


def bash_path(path: pathlib.Path) -> str:
    value = str(path)
    if os.name == "nt":
        value = value.replace("\\", "/")
        if len(value) >= 2 and value[1] == ":":
            value = "/" + value[0].lower() + value[2:]
    return value


def load_module():
    spec = importlib.util.spec_from_file_location("claude_task_id", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class ClaudeTaskIdTests(unittest.TestCase):
    def test_custom_preflight_id_is_a_valid_runtime_id(self):
        module = load_module()
        self.assertEqual(
            module.normalize_task_id("preflight-materials-a"),
            "preflight-materials-a",
        )

    def test_artifact_path_normalizes_to_runtime_id(self):
        module = load_module()
        self.assertEqual(
            module.normalize_task_id(
                "/repo/.worktrees/preflight-materials-a.dispatcher.pid",
                artifact_input=True,
            ),
            "preflight-materials-a",
        )

    def test_unsafe_or_ambiguous_ids_fail_closed(self):
        module = load_module()
        for value in ("", ".", "..", "bad/id", "bad id", "-leading"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.normalize_task_id(value)

    def test_cli_prints_only_the_normalized_id(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "normalize",
                "/repo/.worktrees/preflight-materials-a.runtime.json",
                "--artifact-input",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "preflight-materials-a\n")

    def test_monitor_accepts_the_same_custom_runtime_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = pathlib.Path(temporary) / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "init"], cwd=repo, check=True, capture_output=True
            )
            runtime_id = "preflight-materials-a"
            worktrees = repo / ".worktrees"
            (worktrees / runtime_id).mkdir(parents=True)
            (worktrees / f"{runtime_id}.progress.log").write_text(
                "Final dispatch outcome: evidence_tail_incomplete\n",
                encoding="utf-8",
            )
            (worktrees / f"{runtime_id}.monitor-events.log").write_text(
                "monitor_event event=terminal running=no terminal=yes "
                "product_changes=1 evidence_state=diff-without-report\n",
                encoding="utf-8",
            )
            (worktrees / f"{runtime_id}.outcome.json").write_text(
                json.dumps(
                    {
                        "dispatch_success": True,
                        "artifact_valid": False,
                        "validation_success": "missing-evidence",
                        "semantic_acceptance": "pending-codex-review",
                        "completion_state": "needs-review",
                        "operator_state": "implementation-stable-awaiting-review",
                        "evidence_state": "diff-without-report",
                        "product_changes": 1,
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    bash_exe(),
                    bash_path(MONITOR),
                    "decision",
                    runtime_id,
                    "--json",
                    "--spark",
                    "off",
                ],
                cwd=repo,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["task_id"], runtime_id)
            self.assertEqual(value["lifecycle_state"], "terminal")
            self.assertEqual(
                value["operator_state"],
                "implementation-stable-awaiting-review",
            )


if __name__ == "__main__":
    unittest.main()
