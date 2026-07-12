#!/usr/bin/env python3
"""Select L0 local, L1 Spark, or L2 Codex review without invoking a model."""
import argparse,json
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument("input");a=p.parse_args();d=json.loads(Path(a.input).read_text(encoding="utf-8"));lane=d.get("lane","standard")
 if lane=="assured" or d.get("codex_required") or d.get("review_triggers"): tier="L2-codex"
 elif d.get("status")!="passed" or d.get("semantic_uncertainty"): tier="L1-spark"
 else: tier="L0-local"
 if d.get("codex_available") is False and tier=="L2-codex": action="stop" if lane=="assured" else "human-review"
 else: action={"L0-local":"human-review","L1-spark":"spark-review","L2-codex":"codex-review"}[tier]
 print(json.dumps({"schema_version":1,"tier":tier,"action":action,"final_merge_authorized":False},sort_keys=True))
if __name__=="__main__":main()
