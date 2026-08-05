import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude-extension-capsule.py"


def load_module():
    spec = importlib.util.spec_from_file_location("claude_extension_capsule", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ClaudeExtensionCapsuleTests(unittest.TestCase):
    def test_collects_bounded_activity_without_thinking_or_tool_payloads(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_id = "11111111-2222-3333-4444-555555555555"
            transcript = root / "projects" / f"{session_id}.jsonl"
            transcript.parent.mkdir()
            rows = [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "private reasoning must not persist"},
                            {
                                "type": "text",
                                "text": "Editing the declared target; token=super-secret-value",
                            },
                            {
                                "type": "tool_use",
                                "name": "Edit",
                                "input": {
                                    "file_path": "src/allowed.py",
                                    "new_string": "sensitive source payload",
                                },
                            },
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "user task input must not become assistant activity",
                            },
                            {
                                "type": "tool_result",
                                "is_error": False,
                                "content": "entire source file must not persist",
                            }
                        ]
                    },
                },
            ]
            transcript.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )

            events, assistant, reasoning_bytes = module._collect_events(
                [transcript], max_bytes=65536, max_events=16
            )

            serialized = json.dumps(events)
            self.assertIn("assistant-reasoning-activity", serialized)
            self.assertIn("src/allowed.py", serialized)
            self.assertNotIn("private reasoning", serialized)
            self.assertNotIn("sensitive source payload", serialized)
            self.assertNotIn("entire source file", serialized)
            self.assertNotIn("user task input", serialized)
            self.assertNotIn("user task input", assistant)
            self.assertIn("[REDACTED]", assistant)
            self.assertGreater(reasoning_bytes, 0)

    def test_session_selection_never_falls_back_to_another_lineage(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transcript = root / "different-session.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            self.assertEqual(module._candidate_transcripts(root, "wanted-session"), [])

    def test_product_state_is_reduced_to_paths_count_and_hash(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "product_hash": "abc",
                        "incremental_product_change_count": 2,
                        "product_changed_paths": ["a.py", "b.py"],
                        "product_path_hashes": {"a.py": "secret-detail"},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                module._load_product_state(state),
                {
                    "product_hash": "abc",
                    "product_change_count": 2,
                    "product_changed_paths": ["a.py", "b.py"],
                },
            )

    def test_runtime_status_is_redacted_and_requires_recent_mtime(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            status = Path(temporary) / "status.txt"
            status.write_text("Edit active token=secret-value\n", encoding="utf-8")
            sampled = int(status.stat().st_mtime)
            excerpt, recent = module._recent_status(status, sampled, 10)
            self.assertTrue(recent)
            self.assertIn("[REDACTED]", excerpt)
            _, stale = module._recent_status(status, sampled + 11, 10)
            self.assertFalse(stale)


if __name__ == "__main__":
    unittest.main()
