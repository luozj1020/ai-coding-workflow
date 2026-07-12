import json,subprocess,sys,tempfile,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
def run(script,*args,check=True): return subprocess.run([sys.executable,R/'scripts'/script,*map(str,args)],text=True,capture_output=True,check=check)
class OptimizationEfficiencyTests(unittest.TestCase):
 def test_four_lanes_and_budgets(self):
  cases=[({"files":1,"diff_lines":20,"exact_validation":True,"risks":{}},"express"),({"files":3,"risks":{}},"standard"),({"files":1,"risks":{"security":"yes"}},"assured"),({"failure_type":"network","risks":{}},"recovery")]
  with tempfile.TemporaryDirectory() as d:
   for i,(data,lane) in enumerate(cases):
    p=Path(d)/str(i);p.write_text(json.dumps(data));self.assertEqual(json.loads(run('route-task.py',p).stdout)['lane'],lane)
 def test_acceptance_and_review_ladder(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a';p.write_text(json.dumps({"allowed_paths":["src"],"changed_files":["src/a.py"],"validation_exit_code":0,"diff_lines":2}));result=json.loads(run('evaluate-acceptance.py',p).stdout);self.assertEqual(result['status'],'passed')
   p.write_text(json.dumps({**result,"lane":"express"}));self.assertEqual(json.loads(run('select-review-tier.py',p).stdout)['tier'],'L0-local')
 def test_duplicate_evidence_guard(self):
  with tempfile.TemporaryDirectory() as d:
   ledger=Path(d)/'l';args=['record','--ledger',ledger,'--run-id','r','--task-id','t','--stage','builder','--model','claude','--max-calls','2','--input','i','--evidence','e'];self.assertEqual(run('quota-ledger.py',*args).returncode,0);self.assertEqual(run('quota-ledger.py',*args,check=False).returncode,2)
 def test_context_cache_roundtrip_and_bound(self):
  with tempfile.TemporaryDirectory() as d:
   meta=Path(d)/'m';content=Path(d)/'c';meta.write_text(json.dumps({"commit":"a","targets":["x"]}));content.write_text('x'*100);put=run('context-cache.py','put','--cache',d,'--meta',meta,'--content',content,'--max-bytes','10');dest=Path(put.stdout.strip());self.assertEqual(len(json.loads(dest.read_text())['content']),10)
 def test_handoff_batches_targets_and_ingest(self):
  with tempfile.TemporaryDirectory() as d:
   run('generate-handoff.py','T','--output-dir',d,'--repo-url','u','--branch','b','--sha','abcdef1','--target','//a:a','--target','//b:b');text=(Path(d)/'remote-validate.sh').read_text();self.assertEqual(text.count('bazel test'),1);self.assertTrue((Path(d)/'remote-update.sh').exists())
   log=Path(d)/'log';log.write_text('abcdef1234567890abcdef1234567890abcdef12\nERROR: x.cc:12 compile failed');out=json.loads(run('validation-ingest.py',log,'--expected-sha','abcdef1','--exit-code','1').stdout);self.assertEqual(out['classification'],'compile');self.assertTrue(out['sha_matches'])
if __name__=='__main__':unittest.main()
