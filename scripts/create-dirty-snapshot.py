#!/usr/bin/env python3
"""Create a hash-bound Git snapshot commit without changing the source index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def run(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def normalize_exclusions(repo: Path, values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(repo.resolve())
            except ValueError as exc:
                raise ValueError(f"excluded path is outside repository: {value}") from exc
        text = candidate.as_posix().lstrip("./")
        if text and text != "." and ".." not in Path(text).parts:
            result.append(text)
    return sorted(set(result))


def create_snapshot(repo: Path, output: Path, exclusions: list[str]) -> dict[str, object]:
    repo = repo.resolve()
    base = run(repo, "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory(prefix="aiwf-snapshot-") as temp_dir:
        index = Path(temp_dir) / "index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(index)
        run(repo, "read-tree", base, env=env)
        run(repo, "add", "-A", "--", ".", env=env)

        # Exempt only untracked control/input artifacts. A tracked path always
        # keeps its current working-tree state in the snapshot.
        applied_exclusions: list[str] = []
        for path in exclusions:
            tracked = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
            if not tracked:
                subprocess.run(
                    ["git", "-C", str(repo), "update-index", "--force-remove", "--", path],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                applied_exclusions.append(path)

        tree = run(repo, "write-tree", env=env)
        commit_env = dict(env)
        commit_env.update(
            {
                "GIT_AUTHOR_NAME": "AI Workflow Snapshot",
                "GIT_AUTHOR_EMAIL": "ai-workflow-snapshot@local.invalid",
                "GIT_COMMITTER_NAME": "AI Workflow Snapshot",
                "GIT_COMMITTER_EMAIL": "ai-workflow-snapshot@local.invalid",
            }
        )
        snapshot = run(
            repo,
            "commit-tree",
            tree,
            "-p",
            base,
            "-m",
            "AI workflow ephemeral dirty snapshot",
            env=commit_env,
        )

    changed = run(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", snapshot).splitlines()
    paths = []
    for rel in sorted(set(changed)):
        source = repo / rel
        paths.append(
            {
                "path": rel,
                "state": "file" if source.is_file() else "deleted-or-nonfile",
                "sha256": sha256_file(source) if source.is_file() else None,
            }
        )
    receipt: dict[str, object] = {
        "schema_version": 1,
        "mode": "dirty-snapshot",
        "source_repository": str(repo),
        "base_commit": base,
        "snapshot_commit": snapshot,
        "snapshot_tree": tree,
        "changed_paths": paths,
        "excluded_untracked_paths": applied_exclusions,
        "source_index_unchanged_by_design": True,
        "merge_authorized": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(f"{output.name}.tmp.{os.getpid()}")
    temp_output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_output, output)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()
    try:
        receipt = create_snapshot(
            args.repo, args.output, normalize_exclusions(args.repo, args.exclude)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(receipt["snapshot_commit"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
