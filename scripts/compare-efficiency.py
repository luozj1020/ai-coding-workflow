#!/usr/bin/env python3
import argparse,json
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument("baseline");p.add_argument("candidate");a=p.parse_args();b=json.loads(Path(a.baseline).read_text())["metrics"];c=json.loads(Path(a.candidate).read_text())["metrics"];bc=b["model_calls"].get("codex",0);cc=c["model_calls"].get("codex",0);reduction=(bc-cc)/bc if bc else None;delta=(c["elapsed_seconds"]-b["elapsed_seconds"])/b["elapsed_seconds"] if b["elapsed_seconds"] else None
 print(json.dumps({"codex_call_reduction":reduction,"elapsed_delta":delta,"quota_gate_pass":reduction is not None and reduction>=.30,"latency_gate_pass":delta is None or delta<=.15,"pareto_candidate":bool(reduction is not None and reduction>0 and (delta is None or delta<=.15))},sort_keys=True,indent=2))
if __name__=="__main__":main()
