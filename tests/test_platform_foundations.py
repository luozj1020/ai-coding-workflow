import json,subprocess,sys,tempfile,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
class PlatformTests(unittest.TestCase):
 def test_benchmark_corpus(self):
  d=json.loads(subprocess.check_output([sys.executable,R/'scripts/run-benchmark-suite.py','--cases',R/'benchmarks/cases'],text=True)); self.assertEqual(d['count'],8)
 def test_learn_roundtrip(self):
  with tempfile.TemporaryDirectory() as d:
   s=Path(d)/'l.jsonl'; subprocess.check_call([sys.executable,R/'scripts/learn-store.py','add','--store',s,'--task','T','--lesson','L','--tag','bazel']); out=subprocess.check_output([sys.executable,R/'scripts/learn-store.py','query','--store',s,'--tag','bazel'],text=True); self.assertEqual(json.loads(out)['trust'],'generated-reviewed')
 def test_migrate_preview(self):
  out=subprocess.check_output([sys.executable,R/'scripts/aiwf.py','migrate'],text=True); self.assertIn('migration_mode=preview',out)
if __name__=='__main__':unittest.main()
