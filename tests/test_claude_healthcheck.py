import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude-healthcheck.py"
spec = importlib.util.spec_from_file_location("claude_healthcheck", SCRIPT)
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class ClaudeHealthcheckTests(unittest.TestCase):
    def test_config_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"env": {
                "ANTHROPIC_BASE_URL": "https://token:secret@example.cn/api",
                "ANTHROPIC_AUTH_TOKEN": "never-print-me",
                "ANTHROPIC_MODEL": "domestic-model",
            }}), encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                result = health.configuration(path)
            self.assertEqual(result["base_url_origin"], "https://example.cn")
            self.assertEqual(result["model"], "domestic-model")
            self.assertTrue(result["auth_configured"])
            self.assertNotIn("never-print-me", json.dumps(result))

    def test_environment_overrides_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://old.invalid"}}')
            with mock.patch.dict(os.environ, {
                "ANTHROPIC_BASE_URL": "https://new.example/v1",
                "ANTHROPIC_MODEL": "new-model",
            }, clear=True):
                result = health.configuration(path)
            self.assertEqual(result["base_url_origin"], "https://new.example")
            self.assertEqual(result["model"], "new-model")

    def test_http_error_means_endpoint_reachable(self):
        error = __import__("urllib.error").error.HTTPError("u", 401, "", {}, None)
        with mock.patch.object(health.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(health.probe("https://example.cn", 1)["status"], "reachable")

    def test_probe_is_advisory_unless_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://example.cn"}}')
            with mock.patch.object(health, "probe", return_value={
                "status": "unreachable", "http_status": None, "error": "timeout"
            }), mock.patch.object(health.shutil, "which", return_value="claude"):
                self.assertEqual(health.main(["--settings", str(path), "--probe", "--json"]), 0)
                self.assertEqual(health.main(["--settings", str(path), "--require-probe", "--json"]), 1)

    def test_installer_and_doctor_register_helper(self):
        self.assertIn('"claude-healthcheck.py"', (ROOT / "scripts/install_workflow.py").read_text())
        self.assertIn("ai/claude-healthcheck.py", (ROOT / "scripts/doctor_workflow.py").read_text())


if __name__ == "__main__":
    unittest.main()
