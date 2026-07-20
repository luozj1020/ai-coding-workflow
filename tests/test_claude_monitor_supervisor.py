from __future__ import annotations

import json
import importlib.util
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude-monitor-supervisor.py"


def load_supervisor():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("claude_monitor_supervisor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClaudeMonitorSupervisorTests(unittest.TestCase):
    def _script(self, path: pathlib.Path, text: str) -> pathlib.Path:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_windows_termination_does_not_use_posix_process_groups(self):
        supervisor = load_supervisor()
        watch = mock.Mock()
        watch.poll.return_value = None
        with mock.patch.object(supervisor.os, "name", "nt"):
            supervisor._terminate_watch(watch)
            supervisor._terminate_watch(watch, force=True)
        watch.terminate.assert_called_once_with()
        watch.kill.assert_called_once_with()

    def test_windows_paths_are_normalized_for_bash(self):
        supervisor = load_supervisor()
        windows_style_path = pathlib.Path(r"C:\temp\watch.sh")
        with mock.patch.object(supervisor.os, "name", "nt"):
            normalized = supervisor._bash_path(windows_style_path)
        self.assertEqual(normalized, "/c/temp/watch.sh")

    def test_windows_does_not_request_posix_session_and_gets_exit_grace(self):
        supervisor = load_supervisor()
        with mock.patch.object(supervisor.os, "name", "nt"):
            self.assertFalse(supervisor._start_new_session())
            self.assertEqual(supervisor._natural_exit_grace_seconds(), 5)

    def test_windows_stale_bash_wrapper_is_success_after_monitor_event(self):
        supervisor = load_supervisor()
        watch = mock.Mock()
        watch.poll.return_value = None
        watch.wait.side_effect = [subprocess.TimeoutExpired("bash", 5), 1]
        with mock.patch.object(supervisor.os, "name", "nt"):
            result = supervisor._finish_watch(watch, saw_monitor_event=True)
        self.assertEqual(result, 0)
        watch.terminate.assert_called_once_with()

    def test_ambiguous_event_invokes_bounded_spark_triage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            event_log = root / "events.log"
            capture = root / "monitor-called"
            watcher = self._script(root / "watch.sh", "#!/usr/bin/env bash\necho 'monitor_event action=CONSIDER_INTERRUPT'\n")
            decision = self._script(
                root / "decision.py",
                "#!/usr/bin/env python3\nimport json\nprint(json.dumps({'decision':'inspect','reason_code':'ambiguous-stall'}))\n",
            )
            monitor = self._script(
                root / "monitor.sh",
                "#!/usr/bin/env bash\nprintf x > \"$MONITOR_CAPTURE\"\nprintf '%s\\n' 'decision=inspect' 'confidence=medium' 'reason_code=bounded-review' 'triage_source=spark' 'codex_review_required=yes' 'interrupt_authorized=no'\n",
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--task-id", "claude-test",
                 "--repo-root", str(root), "--watch-script", str(watcher),
                 "--monitor-script", str(monitor), "--decision-helper", str(decision),
                 "--event-log", str(event_log), "--spark", "auto",
                 "--spark-min-interval", "0"],
                env={**os.environ, "MONITOR_CAPTURE": str(capture)},
                text=True, capture_output=True, timeout=20,
            )
            diagnostic = event_log.read_text(encoding="utf-8") if event_log.exists() else "<missing>"
            self.assertEqual(result.returncode, 0, f"{result.stderr}\nevents={diagnostic}")
            self.assertTrue(capture.exists())
            text = event_log.read_text(encoding="utf-8")
            self.assertIn("spark_monitor_event", text)
            self.assertIn("interrupt_authorized=no", text)
            self.assertIn("finish_recommended=no", text)

    def test_stable_continue_never_invokes_monitor_model_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            event_log = root / "events.log"
            capture = root / "monitor-called"
            watcher = self._script(root / "watch.sh", "#!/usr/bin/env bash\necho 'monitor_event action=CONTINUE_WAITING'\n")
            decision = self._script(
                root / "decision.py",
                "#!/usr/bin/env python3\nimport json\nprint(json.dumps({'decision':'continue','reason_code':'recent-growth'}))\n",
            )
            monitor = self._script(root / "monitor.sh", f"#!/usr/bin/env bash\nprintf x > '{capture}'\n")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--task-id", "claude-test",
                 "--repo-root", str(root), "--watch-script", str(watcher),
                 "--monitor-script", str(monitor), "--decision-helper", str(decision),
                 "--event-log", str(event_log), "--spark", "auto",
                 "--spark-min-interval", "0"],
                text=True, capture_output=True, timeout=20,
            )
            diagnostic = event_log.read_text(encoding="utf-8") if event_log.exists() else "<missing>"
            self.assertEqual(result.returncode, 0, f"{result.stderr}\nevents={diagnostic}")
            self.assertFalse(capture.exists())
            self.assertNotIn("spark_monitor_event", event_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
