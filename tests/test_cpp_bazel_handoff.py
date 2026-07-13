import json, os, subprocess, sys, tempfile, unittest
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
                if os.name != "nt":
                    subprocess.run(["bash","-n"],input=text,text=True,check=True)
    def test_context_maps_sources_to_bounded_targets(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/"pkg").mkdir()
            (root/"WORKSPACE").touch(); (root/"pkg/core.cc").touch(); (root/"pkg/core_test.cc").touch()
            (root/"pkg/BUILD.bazel").write_text('''cc_library(\n    name = "core",\n    srcs = ["core.cc"],\n    deps = ["//base:util"],\n)\ncc_test(\n    name = "core_test",\n    srcs = ["core_test.cc"],\n    deps = [":core"],\n)\n''')
            out=subprocess.check_output([sys.executable,ROOT/"scripts/build-bazel-context.py","--repo",d,"--file","pkg/core.cc","--file","pkg/core_test.cc"],text=True)
            data=json.loads(out)
            self.assertEqual([t["label"] for t in data["candidate_targets"]],["//pkg:core","//pkg:core_test"])
            self.assertEqual(data["candidate_test_targets"],["//pkg:core_test"])
            self.assertEqual(data["validation_commands"],["bazel test //pkg:core_test --test_output=errors"])
            self.assertIn("//base:util",data["candidate_targets"][0]["deps"])
    def test_context_does_not_escape_repository(self):
        with tempfile.TemporaryDirectory() as d:
            out=subprocess.check_output([sys.executable,ROOT/"scripts/build-bazel-context.py","--repo",d,"--file","../outside.cc"],text=True)
            self.assertEqual(json.loads(out)["files"][0]["status"],"outside-repository")
    def test_hostile_task_id_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run=subprocess.run([sys.executable,ROOT/"scripts/generate-handoff.py","../bad","--output-dir",d,"--repo-url","x","--branch","main","--sha","abcdef1"],capture_output=True)
            self.assertNotEqual(run.returncode,0)
    def test_installer_lists_assets(self):
        text=(ROOT/"scripts/install_workflow.py").read_text()
        for name in ("detect-cpp-bazel.py","build-bazel-context.py","generate-handoff.py","handoff-v1.schema.json","cpp-bazel.json","manual-remote-validation.json"): self.assertIn(name,text)

if __name__=="__main__": unittest.main()
