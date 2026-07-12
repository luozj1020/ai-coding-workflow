import json, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class CppBazelHandoffTests(unittest.TestCase):
    def test_detector_is_stable_json(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,"WORKSPACE").touch()
            out=subprocess.check_output([sys.executable,ROOT/"scripts/detect-cpp-bazel.py","--repo",d,"--validation-policy","skip"],text=True)
            data=json.loads(out); self.assertTrue(data["cpp_bazel"]); self.assertEqual(data["validation_state"],"skipped-by-policy")
    def test_handoff_is_preview_only_and_valid_shape(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.check_call([sys.executable,ROOT/"scripts/generate-handoff.py","T-1","--output-dir",d,"--repo-url","git@example/repo.git","--branch","main","--sha","abcdef123456","--target","//x:y"])
            data=json.loads(Path(d,"manifest.json").read_text()); self.assertEqual(data["schema_version"],1)
            for name in ("local-publish.sh","remote-validate.sh"):
                text=Path(d,name).read_text(); self.assertIn("echo",text)
                subprocess.run(["bash","-n"],input=text,text=True,check=True)
    def test_hostile_task_id_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run=subprocess.run([sys.executable,ROOT/"scripts/generate-handoff.py","../bad","--output-dir",d,"--repo-url","x","--branch","main","--sha","abcdef1"],capture_output=True)
            self.assertNotEqual(run.returncode,0)
    def test_installer_lists_assets(self):
        text=(ROOT/"scripts/install_workflow.py").read_text()
        for name in ("detect-cpp-bazel.py","generate-handoff.py","handoff-v1.schema.json","cpp-bazel.json","manual-remote-validation.json"): self.assertIn(name,text)

if __name__=="__main__": unittest.main()
