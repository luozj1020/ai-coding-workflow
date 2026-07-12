#!/usr/bin/env python3
"""Local deterministic gate before a human performs remote validation."""
import argparse,json,subprocess
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument("manifest");p.add_argument("--repo",default=".");p.add_argument("--buildifier-status",choices=["passed","failed","not-run"],default="not-run");p.add_argument("--static-status",choices=["passed","failed","not-run"],default="not-run");a=p.parse_args();d=json.loads(Path(a.manifest).read_text());repo=Path(a.repo);checks={"manifest":d.get("schema_version")==1 and bool(d.get("validation",{}).get("targets")),"diff_check":subprocess.run(["git","diff","--check"],cwd=repo,capture_output=True).returncode==0,"sha":subprocess.run(["git","rev-parse","HEAD"],cwd=repo,text=True,capture_output=True).stdout.strip().startswith(d.get("repository",{}).get("sha","__missing__")),"buildifier":a.buildifier_status!="failed","static":a.static_status!="failed"};out={"schema_version":1,"ready":all(checks.values()),"checks":checks,"batched_targets":d.get("validation",{}).get("targets",[]),"remote_rounds":1};print(json.dumps(out,sort_keys=True,indent=2));return 0 if out["ready"] else 2
if __name__=="__main__":raise SystemExit(main())
