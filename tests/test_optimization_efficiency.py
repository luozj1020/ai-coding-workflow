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
   root=Path(d);hints=root/'h.json';card=root/'task.md';out=root/'run';cache=root/'cache';card.write_text('| Mode | builder |\n');no={k:'no' for k in ('public_api','data_model','security','migration','permission','concurrency','cross_module','production_impact')};hints.write_text(json.dumps({'task_id':'T','goal':'g','target_files_count':1,'predicted_diff_lines':5,'exact_validation':True,'effective_risks':no,'target_files':['a.py'],'validation':['pytest -q'],'execution_owner':'claude-builder','claude_role':'execution-builder','task_role':'auxiliary','durable_output_required':True,'codex_review_scope':'bounded'}))
   run('efficiency-control.py','prepare','--facts',hints,'--task-card',card,'--output-dir',out,'--cache-dir',cache);plan=json.loads((out/'execution-plan.json').read_text());self.assertEqual(plan['lane'],'express');self.assertTrue(plan['execution']['single_pass_allowed']);self.assertFalse(plan['context']['cache_reused'])
   run('efficiency-control.py','prepare','--facts',hints,'--task-card',card,'--output-dir',out,'--cache-dir',cache);self.assertTrue(json.loads((out/'execution-plan.json').read_text())['context']['cache_reused'])
   preview=run('dispatch-efficient.py','--plan',out/'execution-plan.json','--task-card',card,'--output-dir',out/'dispatch');self.assertEqual(json.loads(preview.stdout)['execute'],False);self.assertIn('mixed-exception',(out/'dispatch/single-pass-task-card.md').read_text())
   evidence=root/'e.json';evidence.write_text(json.dumps({'allowed_paths':['a.py'],'changed_files':['a.py'],'validation_exit_code':0,'diff_lines':2}));run('efficiency-control.py','review','--plan',out/'execution-plan.json','--evidence',evidence,'--milestone','final-candidate','--output',out/'review.json');self.assertEqual(json.loads((out/'review.json').read_text())['review']['tier'],'L0-local')
 def test_codex_fast_path_stops_before_card_and_context_artifacts(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);facts=root/'facts.json';out=root/'run';no={k:'no' for k in ('public_api','data_model','security','migration','permission','concurrency','cross_module','production_impact')}
   facts.write_text(json.dumps({'task_id':'DIRECT','target_files_count':3,'predicted_diff_lines':180,'exact_validation':True,'effective_risks':no,'execution_owner':'codex-fast-path','deterministic_owner_decision':True,'delegation_value':False}))
   run('efficiency-control.py','prepare','--facts',facts,'--output-dir',out)
   plan=json.loads((out/'execution-plan.json').read_text());decision=json.loads((out/'dispatch-decision.json').read_text())
   self.assertEqual(plan['execution']['owner'],'codex-fast-path');self.assertTrue(plan['context']['skipped']);self.assertFalse(decision['task_card_required'])
   self.assertEqual(plan['execution']['economy_gate']['status'],'reject')
   self.assertTrue(plan['control_plane']['within_budget']);self.assertEqual(plan['control_plane']['combined_bytes'],0)
   self.assertFalse((out/'context-packet.json').exists());self.assertFalse((out/'retry-state.json').exists())
 def test_solution_planner_role_reaches_execution_plan_and_preview(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);facts=root/'facts.json';card=root/'task.md';out=root/'run';no={k:'no' for k in ('public_api','data_model','security','migration','permission','concurrency','cross_module','production_impact')}
   facts.write_text(json.dumps({'task_id':'PLAN','goal':'design feature','target_files_count':8,'predicted_diff_lines':600,'exact_validation':False,'effective_risks':no,'execution_owner':'claude-builder','claude_role':'solution-planner','goal_clarity':'high','implementation_path_clarity':'low','bounded_exploration_scope':True,'durable_structured_output':True,'expected_codex_work_reduction_ratio':.4,'multi_phase_task':True}))
   card.write_text('| Mode | builder |\n| Planning owner | Claude |\n')
   run('efficiency-control.py','prepare','--facts',facts,'--task-card',card,'--output-dir',out)
   plan=json.loads((out/'execution-plan.json').read_text());self.assertEqual(plan['execution']['claude_role'],'solution-planner');self.assertEqual(plan['execution']['builder_mode'],'solution-planning')
   preview=json.loads(run('dispatch-efficient.py','--plan',out/'execution-plan.json','--task-card',card,'--output-dir',out/'dispatch').stdout)
   self.assertEqual(preview['claude_role'],'solution-planner');self.assertEqual(preview['builder_mode'],'solution-planning')
 def test_batch_builder_role_reaches_execution_plan(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);facts=root/'facts.json';card=root/'task.md';out=root/'run';no={k:'no' for k in ('public_api','data_model','security','migration','permission','concurrency','cross_module','production_impact')}
   facts.write_text(json.dumps({'task_id':'BATCH','goal':'mechanical update','target_files_count':8,'predicted_diff_lines':400,'exact_validation':True,'effective_risks':no,'execution_owner':'claude-builder','claude_role':'batch-builder','mechanical_batch':True,'task_role':'auxiliary','independent_write_scopes':True,'durable_output_required':True,'codex_review_scope':'sampled'}))
   card.write_text('| Mode | builder |\n| Transformation rule | replace |\n| Independent write units | src/a, src/b |\n')
   run('efficiency-control.py','prepare','--facts',facts,'--task-card',card,'--output-dir',out)
   plan=json.loads((out/'execution-plan.json').read_text());self.assertEqual(plan['execution']['claude_role'],'batch-builder');self.assertEqual(plan['execution']['builder_mode'],'batch')
 def test_control_plane_budget_blocks_oversized_card_before_dispatch(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);facts=root/'facts.json';card=root/'task.md';out=root/'run';no={k:'no' for k in ('public_api','data_model','security','migration','permission','concurrency','cross_module','production_impact')}
   facts.write_text(json.dumps({'task_id':'BIG','target_files_count':4,'predicted_diff_lines':240,'exact_validation':True,'effective_risks':no,'execution_owner':'claude-builder','delegation_value':True,'control_plane_policy':{'max_task_card_bytes':64}}));card.write_text('| Mode | builder |\n'+'x'*256)
   result=run('efficiency-control.py','prepare','--facts',facts,'--task-card',card,'--output-dir',out,check=False)
   self.assertEqual(result.returncode,2);plan=json.loads((out/'execution-plan.json').read_text());decision=json.loads((out/'dispatch-decision.json').read_text())
   self.assertFalse(plan['control_plane']['within_budget']);self.assertIn('task-card-byte-budget-exceeded',plan['control_plane']['failures']);self.assertEqual(decision['action'],'recompose-before-dispatch');self.assertFalse((out/'context-packet.json').exists())
 def test_canary_model_failure_blocks_redispatch_and_requires_reroute(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);plan=root/'plan.json';card=root/'task.md';ledger=root/'ledger.jsonl';classification=root/'classification.json';out=root/'out'
   plan.write_text(json.dumps({'task_id':'CANARY','lane':'standard','budget':{'claude_calls':2},'control_plane':{'within_budget':True},'execution':{'delegation_mode':'canary'}}));card.write_text('| Mode | builder |\n');ledger.write_text(json.dumps({'task_id':'CANARY','model':'claude'})+'\n');classification.write_text(json.dumps({'failure_class':'model-no-progress','economic_stop_loss':True,'reroute_required':True,'same_worktree_retry_eligible':False}))
   out.mkdir();(out/'dispatch.stdout').write_text('Attempt Class: '+str(classification)+'\n')
   result=run('dispatch-efficient.py','--plan',plan,'--task-card',card,'--output-dir',out,'--ledger',ledger,check=False)
   self.assertEqual(result.returncode,2);decision=json.loads((out/'dispatch-decision.json').read_text());self.assertEqual(decision['action'],'reroute-before-redispatch');self.assertFalse(decision['takeover_authorized'])
 def test_recovery_and_remote_precheck(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip();manifest=root/'m.json';manifest.write_text(json.dumps({'schema_version':1,'repository':{'sha':sha},'validation':{'targets':['//a:a']}}));self.assertEqual(run('remote-precheck.py',manifest,'--repo',R).returncode,0)
   ingest=root/'i.json';ingest.write_text(json.dumps({'classification':'network'}));self.assertEqual(json.loads(run('route-recovery.py',ingest).stdout)['model'],None);ingest.write_text(json.dumps({'classification':'compile'}));self.assertEqual(json.loads(run('route-recovery.py',ingest).stdout)['model'],'claude')
 def test_benchmark_quality_metrics(self):
  with tempfile.TemporaryDirectory() as d:
   ledger=Path(d)/'l';ledger.write_text('\n'.join(json.dumps(x) for x in [{'task_id':'a','model':'claude','result':'passed','accepted':True,'elapsed_seconds':3,'validation_status':'passed'},{'task_id':'b','model':'codex','result':'accepted','accepted':True,'elapsed_seconds':2,'false_accept':0}]))
   data=json.loads(run('run-benchmark-suite.py','--cases',R/'benchmarks/cases','--ledger',ledger).stdout);self.assertEqual(data['metrics']['accepted_tasks'],2);self.assertEqual(data['metrics']['zero_codex_completion_rate'],.5);self.assertIn('first_pass_success_rate',data['metrics'])
if __name__=='__main__':unittest.main()
