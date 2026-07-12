#!/usr/bin/env python3
"""Generate a preview-only manual publish/remote validation handoff."""
import argparse, json, shlex
from pathlib import Path

def safe_id(value):
    if not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in value): raise ValueError("task-id contains unsafe characters")
    return value
def main():
    p=argparse.ArgumentParser(); p.add_argument("task_id", type=safe_id); p.add_argument("--output-dir", required=True); p.add_argument("--repo-url", required=True); p.add_argument("--branch", required=True); p.add_argument("--sha", required=True); p.add_argument("--target", action="append", default=[]); p.add_argument("--changed-file",action="append",default=[]); p.add_argument("--server-repo-path",default="<server-repo-path>");p.add_argument("--log-path",default="validation.log"); p.add_argument("--conda-env"); p.add_argument("--validation-state", choices=["local-runnable","remote-required","skipped-by-policy"], default="remote-required"); a=p.parse_args()
    if not (7 <= len(a.sha) <= 64 and all(c in "0123456789abcdefABCDEF" for c in a.sha)): p.error("--sha must be a git SHA")
    out=Path(a.output_dir).resolve(); out.mkdir(parents=True, exist_ok=True)
    commands=["bazel test " + " ".join(shlex.quote(x) for x in a.target) + " --test_output=errors"] if a.target else []
    manifest={"schema_version":1,"task_id":a.task_id,"repository":{"url":a.repo_url,"branch":a.branch,"sha":a.sha},"validation":{"state":a.validation_state,"targets":a.target,"commands":commands},"artifacts":["handoff.md","local-publish.sh","remote-update.sh","remote-validate.sh","manifest.json"]}
    q=shlex.quote
    add="git add "+(" ".join(q(x) for x in a.changed_file) if a.changed_file else "<reviewed-files>")
    local=f'''#!/usr/bin/env bash\nset -euo pipefail\necho {q(add)}\necho {q("git commit -m '<message>'")}\necho {q("git push origin " + a.branch)}\n'''
    update=["#!/usr/bin/env bash","set -euo pipefail",f"echo {q('cd '+a.server_repo_path)}",f"echo {q('git fetch origin')}",f"echo {q('git checkout '+a.branch)}",f"echo {q('git merge --ff-only origin/'+a.branch)}",f"echo {q('test $(git rev-parse HEAD) = '+a.sha)}"]
    remote=["#!/usr/bin/env bash","set -euo pipefail",f"echo {q('cd '+a.server_repo_path)}"]
    if a.conda_env: remote.append(f"echo {q('conda activate '+a.conda_env)}")
    remote += [f"echo {q(c+' 2>&1 | tee '+a.log_path)}" for c in commands]
    md=f"# Remote validation handoff: {a.task_id}\n\nRepository: `{a.repo_url}`\nBranch: `{a.branch}`\nExpected SHA: `{a.sha}`\nValidation state: `{a.validation_state}`\nChanged files: {', '.join(a.changed_file) or '(record before publish)'}\n\nScripts print commands only. Review and run them manually. Ingest returned logs with `python ai/validation-ingest.py {a.log_path} --expected-sha {a.sha}`.\n"
    for name,content in {"handoff.md":md,"local-publish.sh":local,"remote-update.sh":"\n".join(update)+"\n","remote-validate.sh":"\n".join(remote)+"\n","manifest.json":json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n"}.items(): (out/name).write_text(content,encoding="utf-8")
    print(out)
if __name__=="__main__": main()
