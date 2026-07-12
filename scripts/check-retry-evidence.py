#!/usr/bin/env python3
"""Fail closed when a retry has no changed task/context/failure/environment evidence."""
import argparse,hashlib,json
from pathlib import Path
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest() if p and Path(p).exists() else None
def main():
 p=argparse.ArgumentParser();p.add_argument("--previous",required=True);p.add_argument("--task-card");p.add_argument("--context");p.add_argument("--failure-log");p.add_argument("--environment");a=p.parse_args();prev=json.loads(Path(a.previous).read_text());now={k:digest(v) for k,v in {"task_card":a.task_card,"context":a.context,"failure_log":a.failure_log,"environment":a.environment}.items()};changed=[k for k,v in now.items() if v and v!=prev.get(k)];print(json.dumps({"allowed":bool(changed),"new_evidence":changed,"hashes":now},sort_keys=True));return 0 if changed else 2
if __name__=="__main__":raise SystemExit(main())
