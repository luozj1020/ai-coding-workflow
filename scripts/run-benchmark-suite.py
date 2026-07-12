#!/usr/bin/env python3
import argparse,json,time
from pathlib import Path
def main():
 p=argparse.ArgumentParser(); p.add_argument("--cases",default="benchmarks/cases");p.add_argument("--ledger"); a=p.parse_args(); root=Path(a.cases); cases=sorted(x.name for x in root.iterdir() if x.is_dir()) if root.exists() else []
 rows=[json.loads(x) for x in Path(a.ledger).read_text().splitlines() if x.strip()] if a.ledger and Path(a.ledger).exists() else []
 models={m:sum(1 for x in rows if x.get("model")==m) for m in ("codex","claude","spark")};useful=sum(1 for x in rows if x.get("result") not in ("pending","no-progress","unused"))
 print(json.dumps({"schema_version":1,"timestamp":int(time.time()),"cases":cases,"count":len(cases),"metrics":{"model_calls":models,"useful_call_rate":useful/len(rows) if rows else None,"elapsed_seconds":sum(float(x.get("elapsed_seconds",0)) for x in rows),"human_touches":sum(1 for x in rows if x.get("model")=="human")}},sort_keys=True))
if __name__=="__main__":main()
