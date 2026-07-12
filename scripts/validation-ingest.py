#!/usr/bin/env python3
"""Compress remote validation logs into machine-readable evidence."""
import argparse,json,re
from pathlib import Path
def classify(text):
 rules=[("permission",r"permission denied|unauthorized"),("network",r"network|connection|proxy|timed out"),("dependency",r"not found|no such package|download failed"),("compile",r"error:|compilation failed"),("test",r"FAILED|AssertionError|test.*fail"),("timeout",r"timeout|timed out")]
 for kind,pattern in rules:
  if re.search(pattern,text,re.I):return kind
 return "unknown"
def main():
 p=argparse.ArgumentParser();p.add_argument("log");p.add_argument("--expected-sha");p.add_argument("--exit-code",type=int);a=p.parse_args();text=Path(a.log).read_text(encoding="utf-8",errors="replace");sha=re.search(r"\b[0-9a-f]{40,64}\b",text,re.I);failed=sorted(set(re.findall(r"(?m)^(?://[^\s:]+:[^\s]+).*?(?:FAILED|FAIL)",text)));locations=re.findall(r"(?:[A-Za-z]:)?[^\s:]+:\d+(?::\d+)?",text)[:20]
 out={"schema_version":1,"commit_sha":sha.group(0) if sha else None,"sha_matches":not a.expected_sha or bool(sha and sha.group(0).startswith(a.expected_sha)),"exit_code":a.exit_code,"classification":classify(text),"failed_targets":failed,"locations":locations,"key_lines":[x for x in text.splitlines() if re.search(r"error|fail|timeout|denied",x,re.I)][:30]};print(json.dumps(out,ensure_ascii=False,sort_keys=True,indent=2))
if __name__=="__main__":main()
