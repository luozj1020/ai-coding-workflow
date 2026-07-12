#!/usr/bin/env python3
"""Map ingested failures to the cheapest safe recovery owner."""
import argparse,json
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument("ingest");p.add_argument("--assured",action="store_true");p.add_argument("--failure-count",type=int,default=1);a=p.parse_args();d=json.loads(Path(a.ingest).read_text());kind=d.get("classification","unknown")
 if kind in {"environment","dependency","permission","network","timeout"}:owner,model="local-or-human",None
 elif kind in {"compile","test"} and a.failure_count<2:owner,model="claude-revision","claude"
 elif a.assured:owner,model="codex","codex"
 else:owner,model="spark-triage","spark"
 print(json.dumps({"schema_version":1,"classification":kind,"owner":owner,"model":model,"preserve_artifacts":True,"restart_observe":False},sort_keys=True))
if __name__=="__main__":main()
