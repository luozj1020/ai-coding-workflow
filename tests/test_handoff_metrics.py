"""Focused tests for cross-model handoff measurement and summaries."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recorder = load_module("record_handoff_event", SCRIPTS / "record-handoff-event.py")
summarizer = load_module("summarize_handoff_metrics", SCRIPTS / "summarize-handoff-metrics.py")
loop_summary = load_module("summarize_loop_run_handoff", SCRIPTS / "summarize-loop-run.py")


def detail(**overrides):
    values = {
        "sender": "codex",
        "receiver": "claude",
        "task_type": "builder",
        "dispatch_outcome": "success",
    }
    values.update(overrides)
    return recorder.build_handoff_detail(**values)


class HandoffDetailTests(unittest.TestCase):
    def test_unknown_is_explicit_for_unobserved_measurements(self):
        value = detail(payload_bytes=123)
        self.assertEqual(value["payload_bytes"], 123)
        self.assertEqual(value["receiver_reads_before_first_action"], "unknown")
        self.assertEqual(value["handoff_revision_count"], "unknown")

    def test_negative_and_boolean_measurements_are_rejected(self):
        with self.assertRaises(ValueError):
            detail(payload_bytes=-1)
        with self.assertRaises(ValueError):
            detail(payload_bytes=True)
        with self.assertRaises(ValueError):
            detail(payload_bytes=1.5)

    def test_schema_is_strict_and_contains_every_metric(self):
        schema = json.loads(
            (ROOT / "schemas" / "handoff-event-v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(detail()))


class HandoffRecordingTests(unittest.TestCase):
    def test_records_hash_chained_run_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run-events.jsonl"
            first = recorder.record_handoff(
                path, run_id="run-1", task_id="task-1", detail=detail(payload_bytes=10)
            )
            second = recorder.record_handoff(
                path, run_id="run-1", task_id="task-1", detail=detail(payload_bytes=20)
            )
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["event"] for event in events], ["handoff_recorded"] * 2)
            self.assertEqual(events[1]["parent_event_id"], first)
            self.assertEqual(events[1]["event_id"], second)

    def test_cli_rejects_non_jsonl_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "record-handoff-event.py"),
                    "--events-path", str(Path(tmp) / "events.txt"),
                    "--run-id", "run-1",
                    "--task-id", "task-1",
                    "--sender", "codex",
                    "--receiver", "claude",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(".jsonl", result.stderr)


class HandoffSummaryTests(unittest.TestCase):
    def test_known_ratios_and_task_type_grouping(self):
        events = []
        for task_type in ("builder", "builder"):
            events.append({
                "event": "handoff_recorded",
                "detail": detail(
                    task_type=task_type,
                    payload_bytes=100,
                    repeated_payload_bytes=25,
                    novel_payload_bytes=75,
                    context_objects_requested=4,
                    context_cache_hits=3,
                    handoff_revision_count=0,
                ),
            })
        value = summarizer.summarize_events(events)
        self.assertEqual(value["handoff_count"], 2)
        self.assertEqual(value["totals"]["payload_bytes"], 200)
        self.assertEqual(value["payload_redundancy_rate"], 0.25)
        self.assertEqual(value["context_cache_hit_rate"], 0.75)
        self.assertEqual(value["by_task_type"], {"builder": 2})

    def test_one_unknown_prevents_misleading_total_or_ratio(self):
        events = [
            {"event": "handoff_recorded", "detail": detail(payload_bytes=100)},
            {"event": "handoff_recorded", "detail": detail(payload_bytes="unknown")},
        ]
        value = summarizer.summarize_events(events)
        self.assertEqual(value["totals"]["payload_bytes"], "unknown")
        self.assertEqual(value["payload_redundancy_rate"], "unknown")
        self.assertEqual(value["unknown_counts"]["payload_bytes"], 1)

    def test_invalid_handoff_detail_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run-events.jsonl"
            path.write_text(
                json.dumps({"event": "handoff_recorded", "detail": {"payload_bytes": 1}}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                summarizer.summarize_paths([path])

    def test_loop_summary_discovers_run_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recorder.record_handoff(
                root / "run-events.jsonl",
                run_id="run-1",
                task_id="task-1",
                detail=detail(payload_bytes=10),
            )
            value = loop_summary.summarize(root)
            self.assertEqual(value["handoff_metrics"]["handoff_count"], 1)
            markdown = loop_summary.render_markdown(value)
            self.assertIn("## Cross-model Handoffs", markdown)


class HandoffIntegrationTests(unittest.TestCase):
    def test_dispatcher_has_one_terminal_recorder_call(self):
        dispatch = (SCRIPTS / "dispatch-to-claude.sh").read_text(encoding="utf-8")
        self.assertEqual(dispatch.count('--events-path "$HANDOFF_EVENTS_PATH"'), 1)
        self.assertIn("Handoff event recorded", dispatch)

    def test_integrated_runner_points_dispatcher_at_canonical_run_events(self):
        runner = (SCRIPTS / "run-workflow.py").read_text(encoding="utf-8")
        self.assertIn('dispatch_env["AI_WORKFLOW_HANDOFF_EVENTS_PATH"]', runner)
        self.assertNotIn("record_handoff(", runner)

    def test_installer_registers_helpers_and_schema(self):
        installer = (SCRIPTS / "install_workflow.py").read_text(encoding="utf-8")
        for name in (
            "record-handoff-event.py",
            "summarize-handoff-metrics.py",
            "handoff-event-v1.schema.json",
        ):
            self.assertIn(name, installer)


if __name__ == "__main__":
    unittest.main()
