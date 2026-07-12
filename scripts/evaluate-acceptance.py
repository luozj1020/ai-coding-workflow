#!/usr/bin/env python3
"""L0 deterministic acceptance evaluator; never performs semantic approval."""
import argparse,json
from pathlib import Path,PurePosixPath
def evaluate(d):
 allowed=d.get("allowed_paths",[]);changed=d.get("changed_files",[])
 outside=[x for x in changed if not any(x==y or PurePosixPath(y) in PurePosixPath(x).parents for y in allowed)]
 checks={"scope":not outside,"validation":d.get("validation_exit_code")==0,"artifacts":not d.get("missing_artifacts"),"sha":d.get("sha_matches",True),"untracked":not d.get("unexpected_untracked"),"diff_budget":d.get("diff_lines",0)<=d.get("max_diff_lines",100)}
 matrix=[{"id":k,"status":"satisfied" if v else "failed","evidence":[]} for k,v in checks.items()]
 out={"schema_version":1,"status":"passed" if all(checks.values()) else "failed","acceptance_matrix":matrix,"scope_violations":outside,"codex_required":bool(d.get("semantic_uncertainty") or d.get("evidence_conflict")),"review_triggers":[k for k in ("semantic_uncertainty","evidence_conflict","design_uncertain","cross_module_risk_discovered") if d.get(k)]}
 return out
def main():
 p=argparse.ArgumentParser();p.add_argument("input");a=p.parse_args();out=evaluate(json.loads(Path(a.input).read_text(encoding="utf-8")))
 print(json.dumps(out,sort_keys=True,indent=2));return 0 if out["status"]=="passed" else 2
if __name__=="__main__":raise SystemExit(main())
