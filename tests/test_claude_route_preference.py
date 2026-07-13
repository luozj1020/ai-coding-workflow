"""Tests for scripts/claude-route-preference.py."""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude-route-preference.py"


def load_module():
    spec = importlib.util.spec_from_file_location("claude_route_preference", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestStatePath(unittest.TestCase):
    """Test 1: state path resolution precedence."""

    def test_explicit_env_overrides_all(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            custom = os.path.join(tmp, "custom-route.json")
            with mock.patch.dict(os.environ, {"AIWF_CLAUDE_ROUTE_STATE": custom}):
                self.assertEqual(str(mod._state_path()), custom)

    def test_xdg_state_home(self):
        mod = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            env = {"XDG_STATE_HOME": tmp, "AIWF_CLAUDE_ROUTE_STATE": ""}
            with mock.patch.dict(os.environ, env, clear=False):
                # Remove the explicit override so XDG takes effect
                os.environ.pop("AIWF_CLAUDE_ROUTE_STATE", None)
                path = mod._state_path()
                self.assertIn("ai-coding-workflow", str(path))
                self.assertIn("claude-route.json", str(path))

    def test_default_fallback_path(self):
        mod = load_module()
        env = {"XDG_STATE_HOME": "", "LOCALAPPDATA": "", "AIWF_CLAUDE_ROUTE_STATE": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("AIWF_CLAUDE_ROUTE_STATE", None)
            os.environ.pop("XDG_STATE_HOME", None)
            os.environ.pop("LOCALAPPDATA", None)
            path = mod._state_path()
            self.assertIn(".local/state/ai-coding-workflow/claude-route.json", str(path))


class TestResolve(unittest.TestCase):
    """Test 2-3: resolve returns learned route or safe fallback."""

    def _run_resolve(self, state_content=None, fallback="direct"):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                if state_content is not None:
                    pathlib.Path(state_file).write_text(state_content, encoding="utf-8")
                mod = load_module()
                # Run resolve directly
                import argparse
                ns = argparse.Namespace(fallback=fallback)
                old_stdout = sys.stdout
                sys.stdout = captured = __import__("io").StringIO()
                try:
                    rc = mod.cmd_resolve(ns)
                finally:
                    sys.stdout = old_stdout
                return rc, captured.getvalue().strip()

    def test_missing_state_returns_fallback(self):
        """Test: missing state -> direct fallback."""
        rc, output = self._run_resolve(state_content=None)
        self.assertEqual(rc, 0)
        self.assertEqual(output, "direct")

    def test_missing_state_returns_custom_fallback(self):
        rc, output = self._run_resolve(state_content=None, fallback="inherit")
        self.assertEqual(rc, 0)
        self.assertEqual(output, "inherit")

    def test_valid_learned_direct(self):
        """Test: valid learned direct read."""
        record = json.dumps({
            "schema_version": 1,
            "route": "direct",
            "recorded_at": "2026-07-13T12:00:00Z",
            "source": "dispatch-success",
        })
        rc, output = self._run_resolve(state_content=record)
        self.assertEqual(rc, 0)
        self.assertEqual(output, "direct")

    def test_valid_learned_inherit(self):
        """Test: valid learned inherit read."""
        record = json.dumps({
            "schema_version": 1,
            "route": "inherit",
            "recorded_at": "2026-07-13T12:00:00Z",
            "source": "dispatch-success",
        })
        rc, output = self._run_resolve(state_content=record)
        self.assertEqual(rc, 0)
        self.assertEqual(output, "inherit")

    def test_corrupt_json_returns_fallback(self):
        """Test: invalid/corrupt JSON -> safe fallback."""
        rc, output = self._run_resolve(state_content="{not valid json")
        self.assertEqual(rc, 0)
        self.assertEqual(output, "direct")

    def test_unknown_route_returns_fallback(self):
        """Test: unknown route value -> safe fallback."""
        record = json.dumps({
            "schema_version": 1,
            "route": "socks5",
            "recorded_at": "2026-07-13T12:00:00Z",
            "source": "test",
        })
        rc, output = self._run_resolve(state_content=record)
        self.assertEqual(rc, 0)
        self.assertEqual(output, "direct")

    def test_empty_file_returns_fallback(self):
        """Test: empty file -> safe fallback."""
        rc, output = self._run_resolve(state_content="")
        self.assertEqual(rc, 0)
        self.assertEqual(output, "direct")

    def test_non_dict_json_returns_fallback(self):
        """Test: JSON array instead of object -> safe fallback."""
        rc, output = self._run_resolve(state_content='["direct"]')
        self.assertEqual(rc, 0)
        self.assertEqual(output, "direct")


class TestRecord(unittest.TestCase):
    """Test 4: atomic record with only allowed keys."""

    def test_record_creates_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                import argparse
                ns = argparse.Namespace(route="inherit", source="dispatch-success")
                rc = mod.cmd_record(ns)
                self.assertEqual(rc, 0)
                self.assertTrue(os.path.exists(state_file))
                data = json.loads(pathlib.Path(state_file).read_text(encoding="utf-8"))
                self.assertEqual(data["route"], "inherit")
                self.assertEqual(data["schema_version"], 1)
                self.assertEqual(data["source"], "dispatch-success")
                self.assertIn("recorded_at", data)

    def test_record_contains_only_allowed_keys(self):
        """Test: atomic record contains only allowed keys, no secrets."""
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                import argparse
                ns = argparse.Namespace(route="direct", source="test")
                mod.cmd_record(ns)
                data = json.loads(pathlib.Path(state_file).read_text(encoding="utf-8"))
                allowed_keys = {"schema_version", "route", "recorded_at", "source"}
                self.assertEqual(set(data.keys()), allowed_keys)
                # No secret-bearing fields
                for key in data:
                    self.assertNotIn("token", key.lower())
                    self.assertNotIn("url", key.lower())
                    self.assertNotIn("proxy", key.lower())
                    self.assertNotIn("prompt", key.lower())
                    self.assertNotIn("repo", key.lower())

    def test_record_rejects_invalid_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                import argparse
                ns = argparse.Namespace(route="socks5", source="test")
                rc = mod.cmd_record(ns)
                self.assertEqual(rc, 1)
                self.assertFalse(os.path.exists(state_file))

    def test_record_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                import argparse

                ns1 = argparse.Namespace(route="direct", source="first")
                mod.cmd_record(ns1)

                ns2 = argparse.Namespace(route="inherit", source="second")
                mod.cmd_record(ns2)

                data = json.loads(pathlib.Path(state_file).read_text(encoding="utf-8"))
                self.assertEqual(data["route"], "inherit")
                self.assertEqual(data["source"], "second")

    def test_record_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "deep", "nested", "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                import argparse
                ns = argparse.Namespace(route="inherit", source="test")
                rc = mod.cmd_record(ns)
                self.assertEqual(rc, 0)
                self.assertTrue(os.path.exists(state_file))


class TestRecordWriteFailure(unittest.TestCase):
    """Test 9: persistence write failure is advisory."""

    def test_record_warns_on_write_failure(self):
        """Write to an invalid path; should warn and signal advisory failure."""
        mod = load_module()
        import argparse
        # Use a path inside a non-existent nested directory that cannot be created
        # (a file blocks mkdir)
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "blocker")
            pathlib.Path(blocker).write_text("", encoding="utf-8")
            state_file = os.path.join(blocker, "cannot", "create", "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                ns = argparse.Namespace(route="direct", source="test")
                old_stderr = sys.stderr
                sys.stderr = captured = __import__("io").StringIO()
                try:
                    rc = mod.cmd_record(ns)
                finally:
                    sys.stderr = old_stderr
                self.assertEqual(rc, 1)
                self.assertIn("Warning", captured.getvalue())


class TestShow(unittest.TestCase):
    """Test: show command for diagnostics."""

    def test_show_no_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                import argparse
                ns = argparse.Namespace()
                old_stdout = sys.stdout
                sys.stdout = captured = __import__("io").StringIO()
                try:
                    rc = mod.cmd_show(ns)
                finally:
                    sys.stdout = old_stdout
                data = json.loads(captured.getvalue())
                self.assertEqual(data["status"], "no_valid_record")

    def test_show_with_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                import argparse
                ns_record = argparse.Namespace(route="inherit", source="test")
                mod.cmd_record(ns_record)

                ns_show = argparse.Namespace()
                old_stdout = sys.stdout
                sys.stdout = captured = __import__("io").StringIO()
                try:
                    rc = mod.cmd_show(ns_show)
                finally:
                    sys.stdout = old_stdout
                data = json.loads(captured.getvalue())
                self.assertEqual(data["status"], "ok")
                self.assertEqual(data["route"], "inherit")


class TestMain(unittest.TestCase):
    """Test: main entry point."""

    def test_no_command_returns_1(self):
        mod = load_module()
        rc = mod.main([])
        self.assertEqual(rc, 1)

    def test_resolve_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                rc = mod.main(["resolve"])
                self.assertEqual(rc, 0)


class TestSourceValidation(unittest.TestCase):
    """Test 5: source validation regex and Python 3.9 compatibility."""

    def test_valid_sources_accepted(self):
        """Valid sources matching ^[A-Za-z0-9._-]{1,64}$ are recorded."""
        for source in ("dispatch", "test.run", "my_source-v1", "ABC.123"):
            with self.subTest(source=source):
                with tempfile.TemporaryDirectory() as tmp:
                    state_file = os.path.join(tmp, "claude-route.json")
                    env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
                    with mock.patch.dict(os.environ, env):
                        mod = load_module()
                        import argparse
                        ns = argparse.Namespace(route="direct", source=source)
                        rc = mod.cmd_record(ns)
                        self.assertEqual(rc, 0)
                        data = json.loads(
                            pathlib.Path(state_file).read_text(encoding="utf-8")
                        )
                        self.assertEqual(data["source"], source)

    def test_invalid_source_rejected(self):
        """Invalid sources (spaces, too long, special chars) are rejected."""
        for source in ("", "has space", "a" * 65, "bad/slash", "bad@at"):
            with self.subTest(source=source):
                with tempfile.TemporaryDirectory() as tmp:
                    state_file = os.path.join(tmp, "claude-route.json")
                    env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
                    with mock.patch.dict(os.environ, env):
                        mod = load_module()
                        import argparse
                        ns = argparse.Namespace(route="direct", source=source)
                        old_stderr = sys.stderr
                        sys.stderr = captured = __import__("io").StringIO()
                        try:
                            rc = mod.cmd_record(ns)
                        finally:
                            sys.stderr = old_stderr
                        self.assertEqual(rc, 1)
                        self.assertFalse(os.path.exists(state_file))
                        self.assertIn("source must match", captured.getvalue())


class TestDefaultVsLearned(unittest.TestCase):
    """Test 3: default-vs-learned distinction via --fallback."""

    def test_resolve_empty_fallback_when_no_record(self):
        """With --fallback '' and no record, output is empty (not 'direct')."""
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                import argparse
                ns = argparse.Namespace(fallback="")
                old_stdout = sys.stdout
                sys.stdout = captured = __import__("io").StringIO()
                try:
                    rc = mod.cmd_resolve(ns)
                finally:
                    sys.stdout = old_stdout
                self.assertEqual(rc, 0)
                self.assertEqual(captured.getvalue().strip(), "")

    def test_resolve_learned_when_record_exists(self):
        """With a valid record, resolve ignores fallback and prints learned route."""
        record = json.dumps({
            "schema_version": 1,
            "route": "inherit",
            "recorded_at": "2026-07-13T12:00:00Z",
            "source": "dispatch-success",
        })
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "claude-route.json")
            pathlib.Path(state_file).write_text(record, encoding="utf-8")
            env = {"AIWF_CLAUDE_ROUTE_STATE": state_file}
            with mock.patch.dict(os.environ, env):
                mod = load_module()
                import argparse
                ns = argparse.Namespace(fallback="")
                old_stdout = sys.stdout
                sys.stdout = captured = __import__("io").StringIO()
                try:
                    rc = mod.cmd_resolve(ns)
                finally:
                    sys.stdout = old_stdout
                self.assertEqual(rc, 0)
                self.assertEqual(captured.getvalue().strip(), "inherit")


class TestPathHomeFailure(unittest.TestCase):
    """Test 6: OSError/RuntimeError from Path.home is handled gracefully."""

    def _clear_state_env(self):
        """Return env dict that forces _state_path to reach Path.home()."""
        return {"XDG_STATE_HOME": "", "LOCALAPPDATA": "", "AIWF_CLAUDE_ROUTE_STATE": ""}

    def _pop_state_env(self):
        for key in ("AIWF_CLAUDE_ROUTE_STATE", "XDG_STATE_HOME", "LOCALAPPDATA"):
            os.environ.pop(key, None)

    def test_resolve_handles_path_home_failure(self):
        """cmd_resolve prints fallback when Path.home() raises."""
        mod = load_module()
        import argparse
        ns = argparse.Namespace(fallback="direct")
        with mock.patch.object(mod.Path, "home", side_effect=RuntimeError("no home")):
            with mock.patch.dict(os.environ, self._clear_state_env(), clear=False):
                self._pop_state_env()
                old_stdout = sys.stdout
                sys.stdout = captured = __import__("io").StringIO()
                try:
                    rc = mod.cmd_resolve(ns)
                finally:
                    sys.stdout = old_stdout
                self.assertEqual(rc, 0)
                self.assertEqual(captured.getvalue().strip(), "direct")

    def test_show_handles_path_home_failure(self):
        """cmd_show emits diagnostic JSON when Path.home() raises."""
        mod = load_module()
        import argparse
        ns = argparse.Namespace()
        with mock.patch.object(mod.Path, "home", side_effect=RuntimeError("no home")):
            with mock.patch.dict(os.environ, self._clear_state_env(), clear=False):
                self._pop_state_env()
                old_stdout = sys.stdout
                sys.stdout = captured = __import__("io").StringIO()
                try:
                    rc = mod.cmd_show(ns)
                finally:
                    sys.stdout = old_stdout
                data = json.loads(captured.getvalue())
                self.assertEqual(rc, 1)
                self.assertEqual(data["status"], "error")
                self.assertIn("no home", data["error"])

    def test_record_handles_path_home_failure(self):
        """cmd_record warns and returns 1 when Path.home() raises."""
        mod = load_module()
        import argparse
        ns = argparse.Namespace(route="direct", source="test")
        with mock.patch.object(mod.Path, "home", side_effect=RuntimeError("no home")):
            with mock.patch.dict(os.environ, self._clear_state_env(), clear=False):
                self._pop_state_env()
                old_stderr = sys.stderr
                sys.stderr = captured = __import__("io").StringIO()
                try:
                    rc = mod.cmd_record(ns)
                finally:
                    sys.stderr = old_stderr
                self.assertEqual(rc, 1)
                self.assertIn("Warning", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
