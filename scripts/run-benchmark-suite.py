#!/usr/bin/env python3
import argparse,json,time
from pathlib import Path
def main():
 p=argparse.ArgumentParser(); p.add_argument("--cases",default="benchmarks/cases");p.add_argument("--ledger"); a=p.parse_args(); root=Path(a.cases); cases=sorted(x.name for x in root.iterdir() if x.is_dir()) if root.exists() else []
 rows=[json.loads(x) for x in Path(a.ledger).read_text().splitlines() if x.strip()] if a.ledger and Path(a.ledger).exists() else []
 models={m:sum(1 for x in rows if x.get("model")==m) for m in ("codex","claude","spark")};useful=sum(1 for x in rows if x.get("result") not in ("pending","no-progress","unused"));tasks={}
 for row in rows:tasks.setdefault(row.get("task_id","unknown"),[]).append(row)
 accepted={k:v for k,v in tasks.items() if any(x.get("accepted") is True or x.get("result") in ("accepted","passed") for x in v)};first_pass=sum(1 for v in accepted.values() if sum(1 for x in v if x.get("model")=="claude")<=1);zero_codex=sum(1 for v in accepted.values() if not any(x.get("model")=="codex" for x in v));retries=sum(1 for v in tasks.values() if sum(1 for x in v if x.get("model")=="claude")>1)
 metrics={"model_calls":models,"useful_call_rate":useful/len(rows) if rows else None,"elapsed_seconds":sum(float(x.get("elapsed_seconds",0)) for x in rows),"human_touches":sum(1 for x in rows if x.get("model")=="human"),"accepted_tasks":len(accepted),"codex_calls_per_accepted_task":models["codex"]/len(accepted) if accepted else None,"zero_codex_completion_rate":zero_codex/len(accepted) if accepted else None,"claude_retry_rate":retries/len(tasks) if tasks else None,"first_pass_success_rate":first_pass/len(accepted) if accepted else None,"iterations_per_accepted_task":sum(max((int(x.get("iteration",1)) for x in v),default=1) for v in accepted.values())/len(accepted) if accepted else None,"false_accepts":sum(int(x.get("false_accept",0)) for x in rows),"false_revises":sum(int(x.get("false_revise",0)) for x in rows),"scope_violations":sum(int(x.get("scope_violation",0)) for x in rows),"validation_pass_rate":sum(1 for x in rows if x.get("validation_status")=="passed")/sum(1 for x in rows if x.get("validation_status")) if any(x.get("validation_status") for x in rows) else None}
 print(json.dumps({"schema_version":1,"timestamp":int(time.time()),"cases":cases,"count":len(cases),"metrics":metrics},sort_keys=True))
if __name__=="__main__":main()
