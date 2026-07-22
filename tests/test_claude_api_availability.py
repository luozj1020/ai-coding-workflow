import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from datetime import timedelta
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude-api-availability.py"
SPEC = importlib.util.spec_from_file_location("claude_api_availability", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class ClaudeApiAvailabilityTests(unittest.TestCase):
    def args(self, root: Path, **overrides):
        values = {
            "state": root / "state.json",
            "repository": root,
            "route": "direct",
            "environment": "auto",
            "claude_command": "/usr/bin/claude",
            "source": "test-probe",
            "ttl": 3600,
            "reason": "transport-suspected",
        }
        values.update(overrides)
        return Namespace(**values)

    def test_success_is_reused_only_for_matching_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            self.assertEqual(MOD.record(args), 0)
            with mock.patch("builtins.print") as output:
                self.assertEqual(MOD.check(args), 0)
            value = json.loads(output.call_args.args[0])
            self.assertTrue(value["cache_valid"])
            self.assertEqual(value["interaction_conclusion"], "available")

            mismatch = self.args(root, route="inherit")
            with mock.patch("builtins.print"):
                self.assertEqual(MOD.check(mismatch), 1)

    def test_expired_success_requires_live_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root, ttl=60)
            MOD.record(args)
            value = json.loads(args.state.read_text(encoding="utf-8"))
            value["recorded_at"] = MOD.timestamp(MOD.now_utc() - timedelta(seconds=61))
            args.state.write_text(json.dumps(value), encoding="utf-8")
            with mock.patch("builtins.print") as output:
                self.assertEqual(MOD.check(args), 1)
            self.assertEqual(json.loads(output.call_args.args[0])["status"], "expired")

    def test_suspicion_invalidates_prior_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            MOD.record(args)
            MOD.invalidate(args)
            with mock.patch("builtins.print") as output:
                self.assertEqual(MOD.check(args), 1)
            self.assertEqual(json.loads(output.call_args.args[0])["status"], "invalidated")


if __name__ == "__main__":
    unittest.main()
