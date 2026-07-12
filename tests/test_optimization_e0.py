import importlib.util, json, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def load(name, path):
    spec=importlib.util.spec_from_file_location(name, ROOT/path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
packet=load("e0_packet","scripts/build_review_packet.py")
events=load("event_writer_e0","scripts/event_writer.py")
resume=load("resume_e0","scripts/resume-run.py")

class OptimizationE0Tests(unittest.TestCase):
    def test_packet_cap_acceptance_and_protocol(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); card=root/"card.md"; diff=root/"chosen.diff"
            card.write_text("## Acceptance Criteria\n| ID | Description |\n|---|---|\n| AC-1 | works |\n",encoding="utf-8")
            diff.write_text("diff --git a/a b/a\n@@ -1 +1 @@\n-"+"x"*50000+"\n+y\n",encoding="utf-8")
            (root/"unrelated.diff").write_text("diff --git a/b b/b\n@@ -1 +1 @@\n-x\n+y\n")
            value=packet.build_review_packet(root,task_card=card,diff_file=diff)
            prompt=packet.render_review_prompt(value)
            self.assertEqual(value["acceptance_matrix"][0]["id"],"AC-1")
            self.assertIn("Required Review Decision JSON",prompt)
            self.assertLessEqual(len(prompt.encode()),32768)
            self.assertEqual(value["changed_files"],[{"path":"a","status":"modified"}])
    def test_json_acceptance(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"task.json"; p.write_text(json.dumps({"acceptance":[{"id":"A","description":"d"}]}))
            self.assertEqual(packet.parse_acceptance_matrix(p)[0]["id"],"A")
    def test_cross_process_parent_link(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"events.jsonl"
            first=events.build_event(run_id="r",task_id="t",event="setup_complete")
            second=events.build_event(run_id="r",task_id="t",event="dispatch_complete",phase="dispatch")
            events.EventWriter(p).append(first); events.EventWriter(p).append(second)
            self.assertEqual(second["parent_event_id"],first["event_id"])
    def test_manifest_and_event_integrity_block_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/"artifact-manifest.json").write_text(json.dumps({"schema_version":1,"entries":[{"path":"../escape","sha256":"","size":-1}]}))
            entries, manifest_errors=resume.validate_manifest(root); self.assertTrue(manifest_errors)
            one=events.build_event(run_id="r",task_id="t",event="setup_complete")
            two=events.build_event(run_id="other",task_id="t",event="dispatch_complete",phase="dispatch",parent_event_id="bad")
            (root/"loop-events.jsonl").write_text("\n".join(json.dumps(x) for x in (one,two)))
            loaded,event_errors=resume.load_events(root); self.assertTrue(event_errors)
            plan=resume.build_resume_plan(root,loaded,entries,manifest_errors,event_errors)
            self.assertFalse(plan["resume_safe"])

if __name__=="__main__": unittest.main()
