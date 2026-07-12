#!/usr/bin/env python3
"""Content-addressed bounded context cache for locator/LSP/graph evidence."""
import argparse,hashlib,json,time
from pathlib import Path
def key(meta): return hashlib.sha256(json.dumps(meta,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("action",choices=["get","put"]);p.add_argument("--cache",default=".ai-workflow/cache/context");p.add_argument("--meta",required=True);p.add_argument("--content");p.add_argument("--max-bytes",type=int,default=32768);a=p.parse_args();meta=json.loads(Path(a.meta).read_text(encoding="utf-8"));root=Path(a.cache);dest=root/(key(meta)+".json")
 if a.action=="get":
  if not dest.exists(): return 2
  print(dest.read_text(encoding="utf-8"));return 0
 if not a.content:p.error("put requires --content")
 content=Path(a.content).read_text(encoding="utf-8",errors="replace"); encoded=content.encode()[:a.max_bytes];content=encoded.decode("utf-8",errors="ignore")
 root.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps({"schema_version":1,"key":key(meta),"meta":meta,"created_at":int(time.time()),"content":content},ensure_ascii=False,sort_keys=True),encoding="utf-8");print(dest);return 0
if __name__=="__main__":raise SystemExit(main())
