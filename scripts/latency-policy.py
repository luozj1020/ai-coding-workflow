#!/usr/bin/env python3
"""Deterministic latency-budget policies."""
import argparse,json
POLICIES={"interactive":{"max_iterations":2,"max_context_rebuilds":1,"max_remote_validation_rounds":1,"prefer_single_pass":True},"balanced":{"max_iterations":3,"max_context_rebuilds":1,"max_remote_validation_rounds":1,"prefer_single_pass":True},"batch":{"max_iterations":4,"max_context_rebuilds":2,"max_remote_validation_rounds":2,"prefer_single_pass":False}}
def main():
 p=argparse.ArgumentParser();p.add_argument("mode",choices=POLICIES);a=p.parse_args();print(json.dumps({"mode":a.mode,**POLICIES[a.mode]},sort_keys=True))
if __name__=="__main__":main()
