#!/usr/bin/env python3
"""Small append/query knowledge store for workflow lessons."""
import argparse,json,time
from pathlib import Path
def main():
 p=argparse.ArgumentParser(); p.add_argument("action",choices=["add","query"]); p.add_argument("--store",default=".aiwf/lessons.jsonl"); p.add_argument("--task"); p.add_argument("--lesson"); p.add_argument("--tag",action="append",default=[]); a=p.parse_args(); path=Path(a.store)
 if a.action=="add":
  if not a.task or not a.lesson: p.error("add requires --task and --lesson")
  path.parent.mkdir(parents=True,exist_ok=True); record={"version":1,"created_at":int(time.time()),"task":a.task,"lesson":a.lesson,"tags":a.tag,"trust":"generated-reviewed"}
  with path.open("a",encoding="utf-8") as f:f.write(json.dumps(record,ensure_ascii=False,sort_keys=True)+"\n")
 else:
  if not path.exists(): return
  for line in path.read_text(encoding="utf-8").splitlines():
   item=json.loads(line)
   if not a.tag or set(a.tag)&set(item.get("tags",[])): print(json.dumps(item,ensure_ascii=False,sort_keys=True))
if __name__=="__main__":main()
