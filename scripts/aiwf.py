#!/usr/bin/env python3
"""Unified, dependency-free entry point for the workflow helpers."""
import argparse,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
COMMANDS={"setup":"install_workflow.py","doctor":"doctor_workflow.py","task-lint":"lint-task-card.py","review":"review-with-codex.sh","loop":"run-loop.sh","resume":"resume-run.py","handoff":"generate-handoff.py","benchmark":"run-benchmark-suite.py","learn":"learn-store.py"}
def main():
 p=argparse.ArgumentParser(prog="aiwf"); p.add_argument("command",choices=[*COMMANDS,"migrate"]); p.add_argument("args",nargs=argparse.REMAINDER); a=p.parse_args()
 if a.command=="migrate":
  mode="apply" if "--apply" in a.args else "preview"; print(f"migration_mode={mode}"); print("No destructive migration is performed; run setup/update explicitly."); return
 target=ROOT/COMMANDS[a.command]; cmd=(["bash",str(target)] if target.suffix==".sh" else [sys.executable,str(target)])+a.args
 raise SystemExit(subprocess.call(cmd))
if __name__=="__main__":main()
