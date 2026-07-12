#!/usr/bin/env python3
import argparse,json
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument("baseline");p.add_argument("candidate");a=p.parse_args();b=json.loads(Path(a.baseline).read_text())["metrics"];c=json.loads(Path(a.candidate).read_text())["metrics"];bc=b["model_calls"].get("codex",0);cc=c["model_calls"].get("codex",0);reduction=(bc-cc)/bc if bc else None;delta=(c["elapsed_seconds"]-b["elapsed_seconds"])/b["elapsed_seconds"] if b["elapsed_seconds"] else None
 first_pass_delta=(c.get("first_pass_success_rate") or 0)-(b.get("first_pass_success_rate") or 0);quality=(c.get("false_accepts",0)<=b.get("false_accepts",0) and c.get("scope_violations",0)<=b.get("scope_violations",0));human=c.get("human_touches",0)<=b.get("human_touches",0)
 print(json.dumps({"codex_call_reduction":reduction,"elapsed_delta":delta,"first_pass_delta":first_pass_delta,"quota_gate_pass":reduction is not None and reduction>=.30,"latency_gate_pass":delta is None or delta<=.15,"first_pass_gate_pass":first_pass_delta>=-.05,"quality_gate_pass":quality,"human_touch_gate_pass":human,"pareto_candidate":bool(reduction is not None and reduction>0 and (delta is None or delta<=.15) and first_pass_delta>=-.05 and quality and human)},sort_keys=True,indent=2))
if __name__=="__main__":main()
