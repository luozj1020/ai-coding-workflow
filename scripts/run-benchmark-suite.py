#!/usr/bin/env python3
import argparse,json,time
from pathlib import Path
def main():
 p=argparse.ArgumentParser(); p.add_argument("--cases",default="benchmarks/cases"); a=p.parse_args(); root=Path(a.cases); cases=sorted(x.name for x in root.iterdir() if x.is_dir()) if root.exists() else []
 print(json.dumps({"schema_version":1,"timestamp":int(time.time()),"cases":cases,"count":len(cases)},sort_keys=True))
if __name__=="__main__":main()
