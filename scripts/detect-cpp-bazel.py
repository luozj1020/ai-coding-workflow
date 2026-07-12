#!/usr/bin/env python3
"""Emit a stable, non-mutating C++/Bazel environment fingerprint."""
import argparse, json, platform, shutil, subprocess
from pathlib import Path

def version(command):
    exe = shutil.which(command[0])
    if not exe: return {"available": False, "version": None}
    try:
        out = subprocess.run([exe, *command[1:]], text=True, capture_output=True, timeout=5)
        line = (out.stdout or out.stderr).splitlines()
        return {"available": out.returncode == 0, "version": line[0] if line else "unknown"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "version": None, "error": type(exc).__name__}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--repo", default="."); ap.add_argument("--validation-policy", choices=["auto","remote","skip"], default="auto"); a=ap.parse_args()
    root=Path(a.repo).resolve(); bazel=version(["bazel","--version"])
    markers={p: (root/p).exists() for p in ["WORKSPACE","WORKSPACE.bazel","MODULE.bazel","BUILD","BUILD.bazel"]}
    state="skipped-by-policy" if a.validation_policy=="skip" else ("remote-required" if a.validation_policy=="remote" or not bazel["available"] else "local-runnable")
    data={"schema_version":1,"repo":str(root),"cpp_bazel":any(markers.values()),"markers":markers,"validation_state":state,"host":{"hostname":platform.node(),"system":platform.platform(),"architecture":platform.machine()},"tools":{"bazel":bazel,"java":version(["java","-version"]),"gcc":version(["gcc","--version"]),"clang":version(["clang","--version"]),"python":version(["python3","--version"])}}
    print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2))
if __name__=="__main__": main()
