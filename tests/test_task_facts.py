import importlib.util,json,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];SCRIPTS=ROOT/'scripts';PROFILES=ROOT/'profiles'
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
facts_mod=load('task_facts',SCRIPTS/'collect-task-facts.py');schema=load('facts_schema',SCRIPTS/'task_schema.py');router=load('facts_router',SCRIPTS/'route-task.py')
RISK_KEYS=('public_api','data_model','security','migration','permission','concurrency','cross_module','production_impact')
def task(risk=None,profiles=None):
 return {'schema_version':1,'id':'T-1','mode':'builder','goal':'g','profiles':profiles or ['base'],'scope':{'write_paths':['README.md'],'read_paths':['scripts/*.py']},'acceptance':[{'id':'AC-1','description':'ok','validation_id':'v'}],'risk':risk or {k:'no' for k in RISK_KEYS},'handoff':{'must_do':['report']},'validation':[{'id':'v','command':['python','-V']}],'stop_conditions':['stop'],'extensions':{}}
class TaskFactsTests(unittest.TestCase):
 def write(self,root,data,name='task.json'):
  path=Path(root)/name;path.write_text(json.dumps(data),encoding='utf-8');return path
 def test_five_profiles_compose_and_validate(self):
  data=task(profiles=['base','bugfix','cpp-bazel','manual-remote-validation','quota-efficient-balanced']);composed=schema.compose_profiles(data['profiles'],PROFILES,data);self.assertEqual(schema.validate_task(composed),[]);self.assertIn('cpp_bazel',composed['extensions']);self.assertIn('remote_validation',composed['extensions'])
 def test_auto_repository_facts_write_scope_and_hint_authority(self):
  with tempfile.TemporaryDirectory() as d:
   risks={k:'no' for k in RISK_KEYS};risks['security']='yes';task_path=self.write(d,task(risks));hints=self.write(d,{'risks':{'security':'no'},'commit':'fake','repository_size':'fake','repository_markers':['fake'],'predicted_diff_lines':10},'hints.json');value=facts_mod.collect_facts(task_path,hints,PROFILES,ROOT);head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();self.assertEqual(value['commit'],head);self.assertEqual(value['effective_risks']['security'],'yes');self.assertEqual(value['target_files'],['README.md']);self.assertGreater(value['repository']['tracked_files'],0);self.assertNotEqual(value['repository_size'],'fake');self.assertNotIn('fake',value['repository_markers']);self.assertEqual(router.route(value)['lane'],'assured')
 def test_delegation_card_facts_survive_collection(self):
  with tempfile.TemporaryDirectory() as d:
   data=task();task_path=self.write(d,data);hint_data={'ownership_profile':'economy-first','multi_phase_task':True,'symbols':['render_plan'],'constraints':['preserve schema'],'root_cause_evidence':'planner is missing','source_of_truth_example':'scripts/example.py:render','transformation_rule':'replace legacy calls'};hints=self.write(d,hint_data,'hints.json');value=facts_mod.collect_facts(task_path,hints,PROFILES,ROOT)
   for key,expected in hint_data.items():self.assertEqual(value[key],expected)
 def test_missing_risks_unknown_and_hash_stable(self):
  with tempfile.TemporaryDirectory() as d:
   data=task({'security':'no'});p=self.write(d,data);first=facts_mod.collect_facts(p,None,PROFILES,ROOT);second=facts_mod.collect_facts(p,None,PROFILES,ROOT);self.assertEqual(first['declared_risks']['public_api'],'unknown');self.assertEqual(first['routing_facts_hash'],second['routing_facts_hash']);self.assertFalse(router.route(first)['execution']['single_pass_allowed'])
 def test_router_is_only_single_pass_gate(self):
  no={k:'no' for k in RISK_KEYS};express={'effective_risks':no,'target_files_count':2,'predicted_diff_lines':100,'exact_validation':True};decision=router.route(express);self.assertEqual(decision['lane'],'express');self.assertTrue(decision['execution']['single_pass_allowed']);self.assertEqual(decision['execution']['single_pass_reason'],'express-lane-exact-validation')
  for changed in ({**express,'target_files_count':3},{**express,'predicted_diff_lines':101},{**express,'exact_validation':False},{**express,'effective_risks':{**no,'security':'unknown'}},{**express,'failure_type':'compile'}):self.assertFalse(router.route(changed)['execution']['single_pass_allowed'])
 def test_claude_first_owner_and_checker_dispatch_are_value_gated(self):
  no={k:'no' for k in RISK_KEYS};base={'effective_risks':no,'target_files_count':4,'predicted_diff_lines':200,'exact_validation':True}
  local=router.route(base);self.assertEqual(local['execution']['owner'],'claude-builder');self.assertEqual(local['execution']['owner_source'],'claude-first-default');self.assertEqual(local['execution']['ownership_profile'],'claude-first');self.assertFalse(local['execution']['builder_checker_split']);self.assertEqual(local['execution']['checker_skip_reason'],'checker skipped: deterministic evidence sufficient')
  checker=router.route({**base,'test_writing_required':True});self.assertTrue(checker['execution']['checker_model_dispatch']);self.assertEqual(checker['execution']['checker_value_reasons'],['assigned-test-writing'])
  direct=router.route({**base,'delegation_value':False,'effective_risks':{**no,'security':'yes'}});self.assertEqual(direct['execution']['owner'],'codex-fast-path')
 def test_economy_first_retains_strict_gate_and_claude_first_ignores_latency(self):
  no={k:'no' for k in RISK_KEYS};base={'ownership_profile':'economy-first','effective_risks':no,'target_files_count':4,'predicted_diff_lines':240,'exact_validation':True,'claude_role':'execution-builder','task_role':'auxiliary','durable_output_required':True,'codex_review_scope':'bounded'}
  good=router.route({**base,'delegation_value':True,'expected_delegated_cost_ratio':.8,'expected_active_elapsed_ratio':1.2,'expected_codex_work_reduction_ratio':.4})
  self.assertEqual(good['execution']['economy_gate']['status'],'pass');self.assertEqual(good['execution']['owner'],'claude-builder');self.assertEqual(good['execution']['builder_mode'],'execution-only')
  costly=router.route({**base,'expected_delegated_cost_ratio':.9,'expected_active_elapsed_ratio':1.1,'expected_codex_work_reduction_ratio':.5})
  self.assertEqual(costly['execution']['economy_gate']['status'],'reject');self.assertEqual(costly['execution']['owner'],'codex-fast-path');self.assertEqual(costly['execution']['owner_source'],'economy-gate');self.assertEqual(costly['precard_estimator']['reason_code'],'deterministic-economy-gate')
  tolerated=router.route({**base,'delegation_value':True,'expected_delegated_cost_ratio':.8,'expected_active_elapsed_ratio':1.8,'expected_codex_work_reduction_ratio':.5})
  self.assertEqual(tolerated['execution']['economy_gate']['status'],'pass');self.assertEqual(tolerated['execution']['owner'],'claude-builder')
  slow=router.route({**base,'expected_delegated_cost_ratio':.8,'expected_active_elapsed_ratio':2.1,'expected_codex_work_reduction_ratio':.5})
  self.assertEqual(slow['execution']['economy_gate']['reason'],'expected-active-time-too-high')
  unknown=router.route(base);self.assertEqual(unknown['execution']['economy_gate']['status'],'unknown');self.assertEqual(unknown['execution']['owner'],'codex-fast-path');self.assertEqual(unknown['precard_estimator']['spark_action'],'skip')
  canary=router.route({**base,'claude_role':'batch-builder','mechanical_batch':True,'independent_write_scopes':True,'delegation_value':True})
  self.assertEqual(canary['execution']['delegation_mode'],'canary');self.assertEqual(canary['execution']['model_failure_limit'],1);self.assertFalse(canary['execution']['parallel_release_allowed']);self.assertEqual(canary['precard_estimator']['reason_code'],'deterministic-economy-gate')
  portfolio=router.route({**base,'ownership_profile':'claude-first','expected_delegated_cost_ratio':.8,'expected_active_elapsed_ratio':9.0,'expected_codex_work_reduction_ratio':.5});self.assertEqual(portfolio['execution']['owner'],'claude-builder');self.assertNotEqual(portfolio['execution']['economy_gate']['reason'],'expected-active-time-too-high')
 def test_exploratory_builder_owns_bounded_open_implementation(self):
  no={k:'no' for k in RISK_KEYS};base={'effective_risks':no,'target_files_count':6,'predicted_diff_lines':500,'exact_validation':False,'delegation_value':True,'expected_delegated_cost_ratio':.8,'expected_active_elapsed_ratio':1.2,'expected_codex_work_reduction_ratio':.4}
  exploratory=router.route({**base,'claude_role':'exploratory-builder','goal_clarity':'high','implementation_path_clarity':'low','bounded_exploration_scope':True,'durable_output_required':True})
  self.assertEqual(exploratory['execution']['owner'],'claude-builder');self.assertEqual(exploratory['execution']['claude_role'],'exploratory-builder');self.assertEqual(exploratory['execution']['builder_mode'],'exploratory')
 def test_mechanical_batch_is_the_default_claude_execution_shape(self):
  no={k:'no' for k in RISK_KEYS};base={'effective_risks':no,'target_files_count':8,'predicted_diff_lines':500,'exact_validation':True,'delegation_value':True,'expected_delegated_cost_ratio':.7,'expected_active_elapsed_ratio':1.5,'expected_codex_work_reduction_ratio':.4,'claude_role':'batch-builder','mechanical_batch':True,'task_role':'auxiliary','independent_write_scopes':True,'durable_output_required':True,'codex_review_scope':'sampled'}
  decision=router.route(base);self.assertEqual(decision['execution']['owner'],'claude-builder');self.assertEqual(decision['execution']['claude_role'],'batch-builder');self.assertEqual(decision['execution']['builder_mode'],'batch')
  for changed in ({**base,'independent_write_scopes':False},{**base,'codex_review_scope':'full'},{**base,'task_role':'core-semantic'}):self.assertEqual(router.route(changed)['execution']['owner'],'claude-builder')
 def test_solution_planner_converges_then_freezes_once(self):
  no={k:'no' for k in RISK_KEYS};base={'effective_risks':no,'target_files_count':8,'predicted_diff_lines':600,'exact_validation':False,'delegation_value':True,'expected_delegated_cost_ratio':.7,'expected_active_elapsed_ratio':1.5,'expected_codex_work_reduction_ratio':.4,'claude_role':'solution-planner','goal_clarity':'medium','implementation_path_clarity':'low','bounded_exploration_scope':True,'durable_structured_output':True,'repository_size':'large'}
  decision=router.route(base);self.assertEqual(decision['execution']['owner'],'claude-builder');self.assertEqual(decision['execution']['claude_role'],'solution-planner');self.assertEqual(decision['execution']['builder_mode'],'solution-planning');self.assertEqual(decision['planning']['strategy'],'claude-converge-codex-freeze');self.assertEqual(decision['planning']['max_adversarial_review_rounds'],1);self.assertFalse(decision['planning']['implementation_replan_allowed_after_freeze'])
  low_reduction=router.route({**base,'expected_codex_work_reduction_ratio':.2});self.assertEqual(low_reduction['execution']['claude_role'],'solution-planner')
  for changed in ({**base,'durable_structured_output':False},{**base,'bounded_exploration_scope':False},{**base,'goal_clarity':'low'}):
   rejected=router.route(changed);self.assertEqual(rejected['execution']['owner'],'claude-builder');self.assertNotEqual(rejected['execution']['claude_role'],'solution-planner')
 def test_readonly_claude_requires_durable_structured_value(self):
  no={k:'no' for k in RISK_KEYS};base={'effective_risks':no,'target_files_count':20,'predicted_diff_lines':0,'exact_validation':False,'delegation_value':True,'read_only_task':True}
  summary=router.route(base);self.assertEqual(summary['execution']['owner'],'codex-fast-path');self.assertEqual(summary['execution']['owner_source'],'readonly-without-durable-value');self.assertFalse(summary['execution']['read_only_delegation_allowed'])
  structured=router.route({**base,'durable_structured_output':True,'expected_codex_work_reduction_ratio':.4,'expected_delegated_cost_ratio':.8,'expected_active_elapsed_ratio':1.2});self.assertEqual(structured['execution']['owner'],'codex-fast-path');self.assertTrue(structured['execution']['read_only_delegation_allowed'])
 def test_precard_spark_skip_requires_complete_deterministic_evidence(self):
  no={k:'no' for k in RISK_KEYS};base={'effective_risks':no,'target_files_count':2,'predicted_diff_lines':80,'exact_validation':True,'delegation_value':False,'solution_clarity':'high','context_scope':'local','codex_review_scope':'full'}
  decision=router.route(base);self.assertEqual(decision['precard_estimator']['spark_action'],'skip');self.assertEqual(decision['precard_estimator']['reason_code'],'sized-tiny-fastpath')
  for changed in ({**base,'solution_clarity':'medium'},{**base,'predicted_diff_lines':101},{**base,'context_scope':'broad'}):self.assertEqual(router.route(changed)['precard_estimator']['reason_code'],'deterministic-economy-gate')
  explicit=router.route({**base,'predicted_diff_lines':500,'recommended_owner':'codex-fast-path','deterministic_owner_decision':True});self.assertEqual(explicit['precard_estimator']['reason_code'],'explicit-deterministic-owner')
  recovery=router.route({**base,'recommended_owner':'codex-fast-path','deterministic_owner_decision':True,'failure_type':'transport'});self.assertEqual(recovery['precard_estimator']['spark_action'],'skip')
  candidate=router.route({'effective_risks':no,'target_files_count':8,'predicted_diff_lines':500,'exact_validation':False,'claude_role':'solution-planner','goal_clarity':'high','implementation_path_clarity':'low','bounded_exploration_scope':True,'durable_structured_output':True,'expected_codex_work_reduction_ratio':.4,'multi_phase_task':True,'spark_route_requested':True})
  self.assertEqual(candidate['precard_estimator']['spark_action'],'estimate')
 def test_registration_and_downstream_field(self):
  installer=(SCRIPTS/'install_workflow.py').read_text();self.assertIn('collect-task-facts.py',installer);self.assertIn('routing-facts-v1.schema.json',installer);self.assertEqual(json.loads((ROOT/'schemas/routing-facts-v1.schema.json').read_text())['properties']['schema_version']['const'],1);self.assertIn('"facts":"collect-task-facts.py"',(SCRIPTS/'aiwf.py').read_text());self.assertNotIn('single_pass_builder_checker',(SCRIPTS/'efficiency-control.py').read_text());self.assertNotIn('single_pass_builder_checker',(SCRIPTS/'dispatch-efficient.py').read_text())
if __name__=='__main__':unittest.main()
