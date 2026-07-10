#!/usr/bin/env python3
"""Manage optional indexed code-search backends for ai-coding-workflow.

The workflow does not require a search service. For very large repositories,
Zoekt can provide a fast local index and Sourcegraph can be used when an
organization already runs a service. This helper keeps setup explicit and
reversible.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request


ZOEKT_PACKAGES = [
    "github.com/sourcegraph/zoekt/cmd/zoekt-git-index@latest",
    "github.com/sourcegraph/zoekt/cmd/zoekt-index@latest",
    "github.com/sourcegraph/zoekt/cmd/zoekt-query@latest",
]


def run_command(args, cwd=None, timeout=None):
    started = time.monotonic()
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr, time.monotonic() - started
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or "timeout", time.monotonic() - started
    except FileNotFoundError as exc:
        return 127, "", str(exc), time.monotonic() - started


def default_zoekt_index():
    return os.environ.get(
        "AI_CODE_ZOEKT_INDEX",
        os.path.join(os.path.expanduser("~"), ".cache", "ai-coding-workflow", "zoekt"),
    )


def find_repo_root(start):
    rc, stdout, _, _ = run_command(["git", "-C", start, "rev-parse", "--show-toplevel"], start, 5)
    if rc == 0 and stdout.strip():
        return os.path.abspath(stdout.strip())
    return os.path.abspath(start)


def docker_compose_command():
    docker = shutil.which("docker")
    if not docker:
        return None
    rc, _, _, _ = run_command([docker, "compose", "version"], timeout=5)
    if rc == 0:
        return [docker, "compose"]
    legacy = shutil.which("docker-compose")
    if legacy:
        return [legacy]
    return None


def check_sourcegraph(url, token=None, timeout=5):
    if not url:
        return "not configured"
    api_url = url.rstrip("/") + "/.api/health"
    request = urllib.request.Request(api_url)
    if token:
        request.add_header("Authorization", "token {}".format(token))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return "reachable rc={}".format(response.status)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return "unreachable: {}".format(exc)


def command_doctor(args):
    print("=== Code Search Service Doctor ===")
    print("go: {}".format(shutil.which("go") or "MISSING"))
    print("docker: {}".format(shutil.which("docker") or "MISSING"))
    compose = docker_compose_command()
    print("docker compose: {}".format(" ".join(compose) if compose else "MISSING"))
    for binary in ["zoekt-git-index", "zoekt-index", "zoekt-query"]:
        print("{}: {}".format(binary, shutil.which(binary) or "MISSING"))
    print("Zoekt index: {}".format(os.path.abspath(args.zoekt_index)))
    print("Zoekt index exists: {}".format("yes" if os.path.isdir(args.zoekt_index) else "no"))
    sourcegraph_url = args.sourcegraph_url or os.environ.get("SOURCEGRAPH_URL", "")
    token = args.sourcegraph_token or os.environ.get("SOURCEGRAPH_TOKEN")
    print("Sourcegraph URL: {}".format(sourcegraph_url or "not configured"))
    print("Sourcegraph health: {}".format(check_sourcegraph(sourcegraph_url, token, args.timeout)))
    print("")
    print("Recommended locator order for large repositories:")
    print("  1. Zoekt if indexed and zoekt-query is available")
    print("  2. Sourcegraph if SOURCEGRAPH_URL is configured")
    print("  3. rg/git grep lexical fallback")
    print("  4. bounded CodeGraph only for concrete symbols")
    return 0


def command_install_zoekt(args):
    go = shutil.which("go")
    if not go:
        print("go is required to install Zoekt CLI tools.", file=sys.stderr)
        print("Install Go first, then rerun with --yes.", file=sys.stderr)
        return 1
    commands = [[go, "install", package] for package in ZOEKT_PACKAGES]
    print("Planned Zoekt install commands:")
    for command in commands:
        print("  {}".format(" ".join(command)))
    if not args.yes:
        print("Dry-run only. Re-run with --yes to install.")
        return 0
    for command in commands:
        print("Installing: {}".format(" ".join(command)))
        rc, stdout, stderr, elapsed = run_command(command, timeout=args.timeout)
        if stdout.strip():
            print(stdout.strip())
        if stderr.strip():
            print(stderr.strip(), file=sys.stderr)
        if rc != 0:
            print("command failed rc={} elapsed={:.1f}s".format(rc, elapsed), file=sys.stderr)
            return rc
    return 0


def command_index_zoekt(args):
    indexer = shutil.which("zoekt-git-index")
    if not indexer:
        print("zoekt-git-index not found. Run install-zoekt first.", file=sys.stderr)
        return 1
    repo_root = find_repo_root(args.repo)
    os.makedirs(args.zoekt_index, exist_ok=True)
    command = [indexer, "-index", os.path.abspath(args.zoekt_index), repo_root]
    print("Indexing repository with Zoekt:")
    print("  {}".format(" ".join(command)))
    if not args.yes:
        print("Dry-run only. Re-run with --yes to build/update the index.")
        return 0
    rc, stdout, stderr, elapsed = run_command(command, timeout=args.timeout)
    if stdout.strip():
        print(stdout.strip())
    if stderr.strip():
        print(stderr.strip(), file=sys.stderr)
    print("elapsed={:.1f}s".format(elapsed))
    return rc


def command_sourcegraph_plan(args):
    compose = docker_compose_command()
    print("=== Sourcegraph Docker Compose Plan ===")
    print("Docker Compose available: {}".format("yes" if compose else "no"))
    print("Deployment docs: https://sourcegraph.com/docs/self-hosted/deploy/docker-compose")
    print("Sourcegraph 7+ requires Docker Compose or Kubernetes; single-container mode was removed.")
    print("")
    print("High-level setup:")
    print("  1. Create/fork a private copy of sourcegraph/deploy-sourcegraph-docker.")
    print("  2. Check out the desired release branch/tag.")
    print("  3. Put local overrides in docker-compose.override.yaml.")
    print("  4. Start from the deployment repo's docker-compose directory:")
    print("     docker compose up -d")
    print("  5. Configure SOURCEGRAPH_URL and SOURCEGRAPH_TOKEN for locate-code.py.")
    print("")
    if not compose:
        print("Docker Compose is missing on this machine; Sourcegraph service was not started.")
    return 0


def command_sourcegraph_up(args):
    compose = docker_compose_command()
    if not compose:
        print("Docker Compose is required to start Sourcegraph.", file=sys.stderr)
        return 1
    if not os.path.isdir(args.deployment_dir):
        print("deployment directory not found: {}".format(args.deployment_dir), file=sys.stderr)
        return 1
    compose_file = os.path.join(args.deployment_dir, "docker-compose.yaml")
    if not os.path.isfile(compose_file):
        print("docker-compose.yaml not found in {}".format(args.deployment_dir), file=sys.stderr)
        return 1
    command = compose + ["up", "-d"]
    print("Planned Sourcegraph start command:")
    print("  cd {} && {}".format(args.deployment_dir, " ".join(command)))
    if not args.yes:
        print("Dry-run only. Re-run with --yes to start containers.")
        return 0
    rc, stdout, stderr, _ = run_command(command, cwd=args.deployment_dir, timeout=args.timeout)
    if stdout.strip():
        print(stdout.strip())
    if stderr.strip():
        print(stderr.strip(), file=sys.stderr)
    return rc


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--zoekt-index",
        default=default_zoekt_index(),
        help="Zoekt index directory. Default: AI_CODE_ZOEKT_INDEX or ~/.cache/ai-coding-workflow/zoekt.",
    )
    parser.add_argument("--timeout", type=float, default=600.0, help="Command timeout in seconds.")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check local code-search service readiness.")
    doctor.add_argument("--sourcegraph-url", default="", help="Sourcegraph base URL.")
    doctor.add_argument("--sourcegraph-token", default="", help="Sourcegraph access token.")
    doctor.set_defaults(func=command_doctor)

    install_zoekt = sub.add_parser("install-zoekt", help="Install Zoekt CLI tools with go install.")
    install_zoekt.add_argument("--yes", action="store_true", help="Actually run go install.")
    install_zoekt.set_defaults(func=command_install_zoekt)

    index_zoekt = sub.add_parser("index-zoekt", help="Build/update a Zoekt index for a repository.")
    index_zoekt.add_argument("--repo", default=".", help="Repository to index.")
    index_zoekt.add_argument("--yes", action="store_true", help="Actually run zoekt-git-index.")
    index_zoekt.set_defaults(func=command_index_zoekt)

    plan = sub.add_parser("sourcegraph-plan", help="Print Sourcegraph Docker Compose setup guidance.")
    plan.set_defaults(func=command_sourcegraph_plan)

    up = sub.add_parser("sourcegraph-up", help="Start an existing Sourcegraph Docker Compose deployment.")
    up.add_argument("--deployment-dir", required=True, help="Directory containing docker-compose.yaml.")
    up.add_argument("--yes", action="store_true", help="Actually run docker compose up -d.")
    up.set_defaults(func=command_sourcegraph_up)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
