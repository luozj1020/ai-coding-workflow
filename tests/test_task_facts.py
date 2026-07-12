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
 def test_missing_risks_unknown_and_hash_stable(self):
  with tempfile.TemporaryDirectory() as d:
   data=task({'security':'no'});p=self.write(d,data);first=facts_mod.collect_facts(p,None,PROFILES,ROOT);second=facts_mod.collect_facts(p,None,PROFILES,ROOT);self.assertEqual(first['declared_risks']['public_api'],'unknown');self.assertEqual(first['routing_facts_hash'],second['routing_facts_hash']);self.assertFalse(router.route(first)['execution']['single_pass_allowed'])
 def test_router_is_only_single_pass_gate(self):
  no={k:'no' for k in RISK_KEYS};express={'effective_risks':no,'target_files_count':2,'predicted_diff_lines':100,'exact_validation':True};decision=router.route(express);self.assertEqual(decision['lane'],'express');self.assertTrue(decision['execution']['single_pass_allowed']);self.assertEqual(decision['execution']['single_pass_reason'],'express-lane-exact-validation')
  for changed in ({**express,'target_files_count':3},{**express,'predicted_diff_lines':101},{**express,'exact_validation':False},{**express,'effective_risks':{**no,'security':'unknown'}},{**express,'failure_type':'compile'}):self.assertFalse(router.route(changed)['execution']['single_pass_allowed'])
 def test_registration_and_downstream_field(self):
  installer=(SCRIPTS/'install_workflow.py').read_text();self.assertIn('collect-task-facts.py',installer);self.assertIn('routing-facts-v1.schema.json',installer);self.assertEqual(json.loads((ROOT/'schemas/routing-facts-v1.schema.json').read_text())['properties']['schema_version']['const'],1);self.assertIn('"facts":"collect-task-facts.py"',(SCRIPTS/'aiwf.py').read_text());self.assertNotIn('single_pass_builder_checker',(SCRIPTS/'efficiency-control.py').read_text());self.assertNotIn('single_pass_builder_checker',(SCRIPTS/'dispatch-efficient.py').read_text())
if __name__=='__main__':unittest.main()
