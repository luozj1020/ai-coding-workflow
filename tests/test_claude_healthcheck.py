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
    def test_interaction_probe_uses_fixed_minimal_prompt(self):
        completed = mock.Mock(returncode=0, stdout="你好！\n", stderr="")
        with mock.patch.object(health.subprocess, "run", return_value=completed) as run, \
             mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid"}, clear=True):
            result = health.interaction_probe("direct", 40, "你好")
        self.assertTrue(result["success"])
        self.assertEqual(run.call_args.args[0], ["claude", "-p", "你好", "--bare", "--no-session-persistence", "--output-format", "json"])
        self.assertNotIn("HTTPS_PROXY", run.call_args.kwargs["env"])

    def test_interaction_probe_classifies_workspace_trust_without_raw_output(self):
        completed = mock.Mock(
            returncode=1, stdout="", stderr="this workspace has not been trusted",
        )
        with mock.patch.object(health.subprocess, "run", return_value=completed):
            result = health.interaction_probe("inherit", 40, "你好")
        self.assertEqual(result["failure_category"], "workspace-not-trusted")
        self.assertNotIn("stderr", result)

    def test_interaction_probe_classifies_socket_failure(self):
        completed = mock.Mock(
            returncode=1, stdout="", stderr="Unable to connect to API (FailedToOpenSocket)",
        )
        with mock.patch.object(health.subprocess, "run", return_value=completed):
            result = health.interaction_probe("inherit", 40, "你好")
        self.assertEqual(result["failure_category"], "transport")

    def test_interaction_probe_extracts_json_usage_without_response_content(self):
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "result": "secret response text",
                "usage": {"input_tokens": 4, "output_tokens": 2},
                "total_cost_usd": 0.02,
                "duration_ms": 120,
                "model": "test-model",
            }),
            stderr="",
        )
        with mock.patch.object(health.subprocess, "run", return_value=completed):
            result = health.interaction_probe("inherit", 40, "你好")
        self.assertEqual(result["tokens_in"], 4)
        self.assertEqual(result["tokens_out"], 2)
        self.assertEqual(result["cost_usd"], 0.02)
        self.assertNotIn("result", result)
        self.assertNotIn("secret response text", json.dumps(result))

    def test_default_interaction_prompt_is_hello(self):
        value = {"route": "inherit", "success": True, "exit_code": 0,
                 "elapsed_seconds": 0.1, "timed_out": False}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://example.cn"}}')
            with mock.patch.object(health, "interaction_probe", return_value=value) as probe_call, \
                 mock.patch.object(health.shutil, "which", return_value="claude"), \
                 mock.patch("builtins.print"):
                self.assertEqual(health.main(["--settings", str(path),
                                              "--interaction-route", "inherit", "--json"]), 0)
        self.assertEqual(probe_call.call_args.args[2], "你好")
        self.assertEqual(probe_call.call_args.args[1], 60.0)

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

    def test_interaction_compare_recommends_fastest_success(self):
        def fake(route, timeout, prompt):
            return {"route": route, "success": True, "exit_code": 0,
                    "elapsed_seconds": 1.0 if route == "inherit" else 2.0, "timed_out": False}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://example.cn"}}')
            with mock.patch.object(health, "interaction_probe", side_effect=fake), \
                 mock.patch.object(health.shutil, "which", return_value="claude"):
                self.assertEqual(health.main(["--settings", str(path), "--interaction-route", "compare", "--json"]), 0)

    def test_interaction_auto_stops_after_first_success(self):
        value = {"route": "inherit", "success": True, "exit_code": 0,
                 "elapsed_seconds": 1.0, "timed_out": False}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://example.cn"}}')
            with mock.patch.object(health, "interaction_probe", return_value=value) as probe_call, \
                 mock.patch.object(health.shutil, "which", return_value="claude"), \
                 mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy:1"}, clear=True):
                self.assertEqual(health.main(["--settings", str(path), "--interaction-route", "auto", "--json"]), 0)
            self.assertEqual(probe_call.call_count, 1)

    def test_failed_interaction_is_inconclusive_in_network_sandbox(self):
        value = {"route": "inherit", "success": False, "exit_code": None,
                 "elapsed_seconds": 30.0, "timed_out": True}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://example.cn"}}')
            with mock.patch.object(health, "interaction_probe", return_value=value), \
                 mock.patch.object(health.shutil, "which", return_value="claude"), \
                 mock.patch.dict(os.environ, {"CODEX_SANDBOX_NETWORK_DISABLED": "1"}, clear=True), \
                 mock.patch("builtins.print") as output:
                self.assertEqual(health.main(["--settings", str(path), "--interaction-route", "inherit", "--json"]), 0)
            payload = json.loads(output.call_args.args[0])
            self.assertEqual(payload["interaction_conclusion"], "inconclusive-restricted-environment")
            self.assertIsNone(payload["recommended_proxy_mode"])

    def test_failed_interaction_is_failure_outside_network_sandbox(self):
        value = {"route": "inherit", "success": False, "exit_code": 1,
                 "elapsed_seconds": 1.0, "timed_out": False}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://example.cn"}}')
            with mock.patch.object(health, "interaction_probe", return_value=value), \
                 mock.patch.object(health.shutil, "which", return_value="claude"), \
                 mock.patch.dict(os.environ, {}, clear=True), mock.patch("builtins.print"):
                self.assertEqual(health.main(["--settings", str(path), "--interaction-route", "inherit", "--json"]), 1)

    def test_explicit_settings_skips_path_home_on_cleared_env(self):
        """Windows regression: --settings flag must not trigger Path.home()."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"env": {
                "ANTHROPIC_BASE_URL": "https://example.cn",
            }}), encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch("pathlib.Path.home", side_effect=RuntimeError("should not be called")), \
                 mock.patch.object(health.shutil, "which", return_value="claude"):
                result = health.main(["--settings", str(path), "--json"])
            self.assertEqual(result, 0)

    def test_installer_and_doctor_register_helper(self):
        self.assertIn('"claude-healthcheck.py"', (ROOT / "scripts/install_workflow.py").read_text())
        self.assertIn("ai/claude-healthcheck.py", (ROOT / "scripts/doctor_workflow.py").read_text())

    # --- Unified minimal API probing tests ---

    def test_interaction_probe_command_includes_bare_and_no_session_persistence(self):
        """Command includes --bare, --no-session-persistence, fixed prompt, and JSON output;
        usage extraction is preserved."""
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "result": "response text",
                "usage": {"input_tokens": 7, "output_tokens": 3},
                "total_cost_usd": 0.015,
                "duration_ms": 90,
                "model": "probe-model",
            }),
            stderr="",
        )
        with mock.patch.object(health.subprocess, "run", return_value=completed) as run, \
             mock.patch.dict(os.environ, {}, clear=True):
            result = health.interaction_probe("inherit", 40, "你好")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "claude")
        self.assertEqual(cmd[1], "-p")
        self.assertEqual(cmd[2], "你好")
        self.assertIn("--bare", cmd)
        self.assertIn("--no-session-persistence", cmd)
        self.assertIn("--output-format", cmd)
        self.assertIn("json", cmd)
        # Usage extraction preserved.
        self.assertTrue(result["success"])
        self.assertTrue(result["usage_extracted"])
        self.assertEqual(result["tokens_in"], 7)
        self.assertEqual(result["tokens_out"], 3)
        self.assertEqual(result["cost_usd"], 0.015)
        self.assertEqual(result["model"], "probe-model")
        self.assertNotIn("result", result)
        self.assertNotIn("response text", json.dumps(result))

    # --- Probe environment attribution tests ---

    def test_probe_environment_auto_respects_sandbox_marker(self):
        """auto mode returns restricted when CODEX_SANDBOX_NETWORK_DISABLED=1."""
        with mock.patch.dict(os.environ, {"CODEX_SANDBOX_NETWORK_DISABLED": "1"}, clear=True):
            self.assertTrue(health.restricted_network_environment("auto"))

    def test_probe_environment_auto_returns_unrestricted_without_marker(self):
        """auto mode returns unrestricted when sandbox marker is absent."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(health.restricted_network_environment("auto"))

    def test_probe_environment_host_ignores_sandbox_marker(self):
        """host mode returns unrestricted even when CODEX_SANDBOX_NETWORK_DISABLED=1."""
        with mock.patch.dict(os.environ, {"CODEX_SANDBOX_NETWORK_DISABLED": "1"}, clear=True):
            self.assertFalse(health.restricted_network_environment("host"))

    def test_probe_environment_sandbox_forces_restricted(self):
        """sandbox mode returns restricted regardless of environment."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(health.restricted_network_environment("sandbox"))


if __name__ == "__main__":
    unittest.main()
