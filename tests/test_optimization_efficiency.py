import json,subprocess,sys,tempfile,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
def run(script,*args,check=True): return subprocess.run([sys.executable,R/'scripts'/script,*map(str,args)],text=True,capture_output=True,check=check)
class OptimizationEfficiencyTests(unittest.TestCase):
 def test_four_lanes_and_budgets(self):
  no={k:'no' for k in ('public_api','data_model','security','migration','permission','concurrency','cross_module','production_impact')}
  cases=[({"target_files_count":1,"predicted_diff_lines":20,"exact_validation":True,"effective_risks":no},"express"),({"target_files_count":3,"predicted_diff_lines":20,"exact_validation":True,"effective_risks":no},"standard"),({"target_files_count":1,"predicted_diff_lines":20,"exact_validation":True,"effective_risks":{**no,"security":"yes"}},"assured"),({"failure_type":"network","effective_risks":no},"recovery")]
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
 def test_control_plane_context_cache_review_and_dispatch_preview(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);hints=root/'h.json';card=root/'task.md';out=root/'run';cache=root/'cache';card.write_text('| Mode | builder |\n');no={k:'no' for k in ('public_api','data_model','security','migration','permission','concurrency','cross_module','production_impact')};hints.write_text(json.dumps({'task_id':'T','goal':'g','target_files_count':1,'predicted_diff_lines':5,'exact_validation':True,'effective_risks':no,'target_files':['a.py'],'validation':['pytest -q']}))
   run('efficiency-control.py','prepare','--facts',hints,'--task-card',card,'--output-dir',out,'--cache-dir',cache);plan=json.loads((out/'execution-plan.json').read_text());self.assertEqual(plan['lane'],'express');self.assertTrue(plan['execution']['single_pass_allowed']);self.assertFalse(plan['context']['cache_reused'])
   run('efficiency-control.py','prepare','--facts',hints,'--task-card',card,'--output-dir',out,'--cache-dir',cache);self.assertTrue(json.loads((out/'execution-plan.json').read_text())['context']['cache_reused'])
   preview=run('dispatch-efficient.py','--plan',out/'execution-plan.json','--task-card',card,'--output-dir',out/'dispatch');self.assertEqual(json.loads(preview.stdout)['execute'],False);self.assertIn('mixed-exception',(out/'dispatch/single-pass-task-card.md').read_text())
   evidence=root/'e.json';evidence.write_text(json.dumps({'allowed_paths':['a.py'],'changed_files':['a.py'],'validation_exit_code':0,'diff_lines':2}));run('efficiency-control.py','review','--plan',out/'execution-plan.json','--evidence',evidence,'--milestone','final-candidate','--output',out/'review.json');self.assertEqual(json.loads((out/'review.json').read_text())['review']['tier'],'L0-local')
 def test_recovery_and_remote_precheck(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip();manifest=root/'m.json';manifest.write_text(json.dumps({'schema_version':1,'repository':{'sha':sha},'validation':{'targets':['//a:a']}}));self.assertEqual(run('remote-precheck.py',manifest,'--repo',R).returncode,0)
   ingest=root/'i.json';ingest.write_text(json.dumps({'classification':'network'}));self.assertEqual(json.loads(run('route-recovery.py',ingest).stdout)['model'],None);ingest.write_text(json.dumps({'classification':'compile'}));self.assertEqual(json.loads(run('route-recovery.py',ingest).stdout)['model'],'claude')
 def test_benchmark_quality_metrics(self):
  with tempfile.TemporaryDirectory() as d:
   ledger=Path(d)/'l';ledger.write_text('\n'.join(json.dumps(x) for x in [{'task_id':'a','model':'claude','result':'passed','accepted':True,'elapsed_seconds':3,'validation_status':'passed'},{'task_id':'b','model':'codex','result':'accepted','accepted':True,'elapsed_seconds':2,'false_accept':0}]))
   data=json.loads(run('run-benchmark-suite.py','--cases',R/'benchmarks/cases','--ledger',ledger).stdout);self.assertEqual(data['metrics']['accepted_tasks'],2);self.assertEqual(data['metrics']['zero_codex_completion_rate'],.5);self.assertIn('first_pass_success_rate',data['metrics'])
if __name__=='__main__':unittest.main()
