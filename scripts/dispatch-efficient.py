#!/usr/bin/env python3
"""Budgeted dispatch front door. Preview by default; --execute invokes Claude once."""
import argparse, hashlib, json, re, subprocess, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
def rows(path): return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []
def main():
 p=argparse.ArgumentParser();p.add_argument("--plan",required=True);p.add_argument("--task-card",required=True);p.add_argument("--output-dir",required=True);p.add_argument("--ledger",default=".ai-workflow/run-ledger.jsonl");p.add_argument("--retry-state");p.add_argument("--current-context");p.add_argument("--failure-log");p.add_argument("--execute",action="store_true");a=p.parse_args();plan=json.loads(Path(a.plan).read_text());card=Path(a.task_card);out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True);ledger=Path(a.ledger);calls=[x for x in rows(ledger) if x.get("task_id")==plan["task_id"] and x.get("model")=="claude"]
 if len(calls)>=plan["budget"]["claude_calls"]: print("Claude call budget exhausted");return 2
 if calls and not a.retry_state: print("Retry requires --retry-state and new evidence");return 2
 if calls:
  old=json.loads(Path(a.retry_state).read_text());now={"task_card":hashlib.sha256(card.read_bytes()).hexdigest(),"context":hashlib.sha256(Path(a.current_context).read_bytes()).hexdigest() if a.current_context else None,"failure_log":hashlib.sha256(Path(a.failure_log).read_bytes()).hexdigest() if a.failure_log else None}
  if not any(v and v!=old.get(k) for k,v in now.items()): print("no-new-evidence retry blocked");return 2
 dispatch_card=card
 if plan["execution"].get("single_pass_allowed"):
  text=card.read_text(encoding="utf-8");text=re.sub(r"(?im)^\|\s*Mode\s*\|\s*builder\s*\|","| Mode | mixed-exception |",text,count=1);text+="\n## Mixed Exception\nExpress Lane authorizes implementation plus exact narrow validation only.\n";dispatch_card=out/"single-pass-task-card.md";dispatch_card.write_text(text,encoding="utf-8")
 preview={"task_id":plan["task_id"],"lane":plan["lane"],"dispatch_card":str(dispatch_card),"single_pass":plan["execution"].get("single_pass_allowed",False),"call_index":len(calls)+1,"execute":a.execute};(out/"dispatch-preview.json").write_text(json.dumps(preview,sort_keys=True,indent=2)+"\n");print(json.dumps(preview,sort_keys=True,indent=2))
 if not a.execute:return 0
 start=time.time();result=subprocess.run(["bash",str(HERE/"dispatch-to-claude.sh"),str(dispatch_card)],text=True,capture_output=True);entry={"schema_version":1,"timestamp":int(time.time()),"run_id":out.name,"task_id":plan["task_id"],"stage":"single-pass" if preview["single_pass"] else "builder","model":"claude","call_index":len(calls)+1,"input_hash":hashlib.sha256(dispatch_card.read_bytes()).hexdigest(),"evidence_hash":hashlib.sha256((a.failure_log or "initial").encode()).hexdigest(),"elapsed_seconds":round(time.time()-start,3),"result":"dispatched" if result.returncode==0 else "dispatch-failed","next_action":"milestone-review"};ledger.parent.mkdir(parents=True,exist_ok=True)
 with ledger.open("a",encoding="utf-8") as f:f.write(json.dumps(entry,sort_keys=True)+"\n")
 (out/"dispatch.stdout").write_text(result.stdout);(out/"dispatch.stderr").write_text(result.stderr);return result.returncode
if __name__=="__main__":raise SystemExit(main())
