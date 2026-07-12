#!/usr/bin/env python3
"""Route tasks by risk, expected completion efficiency, quota and latency."""
import argparse,json
HIGH={"public_api","data_model","migration","security","permission","concurrency","cross_module","production_impact"}
BUDGETS={"normal":{"express":[0,1,0],"standard":[1,2,1],"assured":[2,3,1],"recovery":[1,2,1]},"constrained":{"express":[0,1,0],"standard":[1,2,1],"assured":[2,3,1],"recovery":[1,2,1]},"critical":{"express":[0,1,0],"standard":[0,2,1],"assured":[2,3,0],"recovery":[0,2,1]}}
def route(h):
 risks={k for k,v in h.get("risks",{}).items() if v not in (False,"no",None,0)}
 recovery=bool(h.get("failure_type") or h.get("interrupted"))
 if risks&HIGH: lane="assured"
 elif recovery: lane="recovery"
 elif h.get("files",99)<=2 and h.get("diff_lines",999)<=100 and h.get("exact_validation") and not risks: lane="express"
 else: lane="standard"
 qm=h.get("quota_mode","normal");lm=h.get("latency_mode","interactive");c,cl,s=BUDGETS[qm][lane]
 split=lane=="assured" or bool(h.get("test_changes_large") or h.get("validation_affects_behavior"))
 return {"schema_version":1,"lane":lane,"reason":sorted(risks) or (["failure recovery"] if recovery else ["bounded deterministic scope" if lane=="express" else "ordinary scoped work"]),"budget":{"codex_calls":c,"claude_calls":cl,"spark_calls":s,"codex_reserved_for":["architecture-review","final-review"] if lane=="assured" else (["final-review"] if c else [])},"execution":{"builder_checker_split":split,"single_pass_preferred":not split,"remote_rounds":1},"estimated_efficiency":{"first_pass_confidence":h.get("first_pass_confidence","medium"),"context_cache_reusable":bool(h.get("context_cache"))},"quota_mode":qm,"latency_mode":lm}
def main():
 p=argparse.ArgumentParser();p.add_argument("input");a=p.parse_args();h=json.loads(open(a.input,encoding="utf-8").read());print(json.dumps(route(h),ensure_ascii=False,sort_keys=True,indent=2))
if __name__=="__main__":main()
