#!/usr/bin/env python3
"""Append-only model/run ledger with budget and duplicate-evidence guards."""
import argparse,hashlib,json,time
from pathlib import Path
def records(path):
 if not path.exists(): return []
 return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
def main():
 p=argparse.ArgumentParser();p.add_argument("action",choices=["check","record"]);p.add_argument("--ledger",default=".ai-workflow/run-ledger.jsonl");p.add_argument("--run-id",required=True);p.add_argument("--task-id",required=True);p.add_argument("--stage",required=True);p.add_argument("--model",required=True);p.add_argument("--max-calls",type=int,required=True);p.add_argument("--input",default="");p.add_argument("--evidence",default="");p.add_argument("--elapsed-seconds",type=float,default=0);p.add_argument("--result",default="pending");p.add_argument("--next-action",default="");a=p.parse_args(); path=Path(a.ledger); old=records(path); calls=[x for x in old if x.get("task_id")==a.task_id and x.get("model")==a.model]
 ih=hashlib.sha256(a.input.encode()).hexdigest();eh=hashlib.sha256(a.evidence.encode()).hexdigest();duplicate=any(x.get("input_hash")==ih and x.get("evidence_hash")==eh for x in calls)
 decision={"allowed":len(calls)<a.max_calls and not duplicate,"calls_used":len(calls),"max_calls":a.max_calls,"duplicate_evidence":duplicate}
 if a.action=="check": print(json.dumps(decision,sort_keys=True));return 0 if decision["allowed"] else 2
 if not decision["allowed"]: print(json.dumps(decision,sort_keys=True));return 2
 item={"schema_version":1,"timestamp":int(time.time()),"run_id":a.run_id,"task_id":a.task_id,"stage":a.stage,"model":a.model,"call_index":len(calls)+1,"input_hash":ih,"evidence_hash":eh,"elapsed_seconds":a.elapsed_seconds,"result":a.result,"next_action":a.next_action}
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open("a",encoding="utf-8") as f:f.write(json.dumps(item,sort_keys=True)+"\n")
 print(json.dumps(item,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
