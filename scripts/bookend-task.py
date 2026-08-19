#!/usr/bin/env python3
"""Durable Bookend task controller.

The submitting Codex episode freezes a Task JSON, starts this controller, and
returns immediately.  The controller owns the Claude execution lifetime and
emits a durable wake request only for DONE_CANDIDATE or SEMANTIC_BLOCKED.

The execution adapter is intentionally process-based.  The default adapter is
``run-workflow.py``; tests and future epoch-aware adapters may provide another
Python executable with the same CLI/result contract.  An adapter can request a
same-owner next epoch by returning ``bookend_state=epoch_expired`` together
with ``continuation_safe=true``.  Runtime, authority, and budget failures never
request a Codex semantic inference.

Product state continuity: when an executor returns ``product_worktree`` in its
result, the controller persists that path in the bookend state and passes it to
the next epoch via ``AIWF_BOOKEND_PRODUCT_WORKTREE``.  This allows the next
epoch to resume from the same working tree rather than starting fresh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SCHEMA_VERSION = 1
TERMINAL_STATES = {
    "review_ready",
    "semantic_blocked",
    "runtime_blocked",
    "authority_blocked",
    "budget_exhausted",
    "cancelled",
}
CODEX_WAKE_STATES = {"review_ready", "semantic_blocked"}
SEMANTIC_FAILURES = {
    "semantic-blocked",
    "semantic_blocked",
    "contract-conflict",
    "semantic-decision-required",
}


class BookendError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BookendError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BookendError(f"JSON object required: {path}")
    return value


def append_event(control_dir: Path, event: str, detail: Dict[str, Any]) -> None:
    state_path = control_dir / "bookend-state.json"
    state = load_json(state_path) if state_path.is_file() else {}
    value = {
        "schema_version": 2,
        "timestamp": utc_now(),
        "run_id": control_dir.name,
        "task_id": state.get("logical_task_id", ""),
        "iteration": state.get("epoch"),
        "phase": "review" if event == "codex_wakeup_requested" else "execute",
        "role": "bookend-control-plane",
        "event": event,
        "artifact_refs": [],
        "detail": detail,
    }
    with (control_dir / "bookend-events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def update_state(state_path: Path, state: str, **updates: Any) -> Dict[str, Any]:
    value = load_json(state_path)
    value.update(updates)
    value["state"] = state
    value["terminal"] = state in TERMINAL_STATES
    value["codex_wakeup_required"] = state in CODEX_WAKE_STATES
    value["updated_at"] = utc_now()
    atomic_json(state_path, value)
    append_event(
        state_path.parent,
        "state_changed",
        {
            "state": state,
            "epoch": value.get("epoch", 0),
            "codex_wakeup_required": value["codex_wakeup_required"],
        },
    )
    return value


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise BookendError(
            f"git {' '.join(args)} failed: {(proc.stderr or '').strip()}"
        )
    return proc.stdout.strip()


def runtime_repo_root(repo: Path) -> Path:
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    return common.parent if common.name == ".git" else repo.resolve()


def safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return normalized[:80] or "task"


def resolve_state(value: str) -> Path:
    path = Path(value).resolve()
    if path.is_dir():
        path = path / "bookend-state.json"
    if not path.is_file():
        raise BookendError(f"bookend state not found: {path}")
    return path


def load_run_workflow():
    import importlib.util

    path = HERE / "run-workflow.py"
    spec = importlib.util.spec_from_file_location("_aiwf_bookend_freeze", path)
    if spec is None or spec.loader is None:
        raise BookendError("run-workflow.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze_task(
    task_path: Path,
    control_dir: Path,
    repo: Path,
    profiles_dir: Optional[Path],
) -> Tuple[Path, Dict[str, Any]]:
    raw = task_path.read_bytes()
    try:
        task = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BookendError(f"invalid Task JSON: {exc}") from exc
    if not isinstance(task, dict) or not str(task.get("id", "")).strip():
        raise BookendError("Task JSON requires a non-empty id")

    frozen = control_dir / "frozen-task.json"
    frozen.write_bytes(raw)
    workflow = load_run_workflow()
    freeze_result = workflow.run_lifecycle(
        task_path=frozen,
        execute=False,
        profiles_dir=profiles_dir,
        repo=repo,
        run_dir_base=control_dir / "freeze",
        bookend_owned=True,
    )
    atomic_json(control_dir / "freeze-result.json", freeze_result)
    if freeze_result.get("status") != "routed":
        raise BookendError(
            "contract is not freezeable: "
            + str(freeze_result.get("error") or freeze_result)
        )
    if freeze_result.get("final_decision") != "claude-dispatch-ready":
        raise BookendError(
            "Bookend submit requires a frozen Claude-owned task; decision="
            + str(freeze_result.get("final_decision"))
        )
    return frozen, freeze_result


def create_task(args: argparse.Namespace) -> Tuple[Path, Dict[str, Any]]:
    task_path = Path(args.task).resolve()
    if not task_path.is_file():
        raise BookendError(f"task file not found: {task_path}")
    repo = (
        Path(args.repo).resolve()
        if args.repo
        else Path(git(task_path.parent, "rev-parse", "--show-toplevel"))
    )
    raw_task = load_json(task_path)
    logical_task_id = str(raw_task.get("id", "")).strip()
    if not logical_task_id:
        raise BookendError("Task JSON requires a non-empty id")
    root = (
        Path(args.run_dir_base).resolve()
        if args.run_dir_base
        else runtime_repo_root(repo) / ".worktrees"
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    control_dir = root / f"bookend-{safe_id(logical_task_id)}-{stamp}"
    control_dir.mkdir(parents=True, exist_ok=False)

    profiles_dir = Path(args.profiles_dir).resolve() if args.profiles_dir else None
    frozen, freeze_result = freeze_task(task_path, control_dir, repo, profiles_dir)
    state_path = control_dir / "bookend-state.json"
    value = {
        "schema_version": SCHEMA_VERSION,
        "logical_task_id": logical_task_id,
        "contract_hash": sha256_file(frozen),
        "base_sha": git(repo, "rev-parse", "HEAD"),
        "owner": "claude",
        "owner_lease_state": "assigned",
        "epoch_write_grant": "not-issued",
        "state": "submitted",
        "terminal": False,
        "codex_wakeup_required": False,
        "epoch": 0,
        "max_epochs": args.max_epochs,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "repo": str(repo),
        "control_dir": str(control_dir),
        "frozen_task": str(frozen),
        "freeze_result": str(control_dir / "freeze-result.json"),
        "freeze_run_dir": freeze_result.get("run_dir"),
        "profiles_dir": str(profiles_dir) if profiles_dir else None,
        "executor": (
            str(Path(args.executor).resolve())
            if args.executor
            else str((HERE / "run-workflow.py").resolve())
        ),
        "dispatcher": str(Path(args.dispatcher).resolve()) if args.dispatcher else None,
        "host_authority": bool(args.host_authority),
        "review_request": None,
        "last_result": None,
    }
    atomic_json(state_path, value)
    append_event(
        control_dir,
        "task_submitted",
        {
            "logical_task_id": logical_task_id,
            "contract_hash": value["contract_hash"],
            "base_sha": value["base_sha"],
        },
    )
    return state_path, value


def executor_command(state: Dict[str, Any], epoch_dir: Path) -> List[str]:
    executable = Path(str(state["executor"]))
    command = [
        sys.executable,
        str(executable),
        str(state["frozen_task"]),
        "--execute",
        "--json",
        "--bookend-owned",
    ]
    command.extend(["--repo", str(state["repo"]), "--run-dir-base", str(epoch_dir)])
    if state.get("profiles_dir"):
        command.extend(["--profiles-dir", str(state["profiles_dir"])])
    if state.get("dispatcher"):
        command.extend(["--dispatcher", str(state["dispatcher"])])
    if state.get("host_authority"):
        command.append("--host-authority")
    return command


def parse_json_stdout(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise BookendError("executor returned no JSON result")
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise BookendError(f"executor result is not one JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise BookendError("executor result must be a JSON object")
    return value


def run_epoch(state_path: Path, epoch: int) -> Tuple[int, Dict[str, Any]]:
    state = load_json(state_path)
    control_dir = state_path.parent
    epoch_dir = control_dir / "epochs" / f"epoch-{epoch:03d}"
    epoch_dir.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(
        {
            "AIWF_BOOKEND_LOGICAL_TASK_ID": str(state["logical_task_id"]),
            "AIWF_BOOKEND_CONTRACT_HASH": str(state["contract_hash"]),
            "AIWF_BOOKEND_EPOCH": str(epoch),
            "AIWF_BOOKEND_CONTROL_DIR": str(control_dir),
            "AIWF_BOOKEND_PREVIOUS_RESULT": str(state.get("last_result") or ""),
        }
    )
    product_worktree = state.get("product_worktree")
    if product_worktree:
        env["AIWF_BOOKEND_PRODUCT_WORKTREE"] = str(product_worktree)
    convergence_receipt = state.get("convergence_receipt")
    if convergence_receipt:
        env["AIWF_BOOKEND_CONVERGENCE_RECEIPT"] = str(convergence_receipt)
    command = executor_command(state, epoch_dir)
    atomic_json(
        epoch_dir / "epoch-request.json",
        {
            "schema_version": SCHEMA_VERSION,
            "epoch": epoch,
            "logical_task_id": state["logical_task_id"],
            "contract_hash": state["contract_hash"],
            "base_sha": state["base_sha"],
            "command": command,
        },
    )
    proc = subprocess.run(
        command,
        cwd=str(state["repo"]),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    (epoch_dir / "executor.stdout").write_text(proc.stdout, encoding="utf-8")
    (epoch_dir / "executor.stderr").write_text(proc.stderr, encoding="utf-8")
    try:
        result = parse_json_stdout(proc.stdout)
    except BookendError as exc:
        result = {
            "status": "failed",
            "bookend_state": "runtime_blocked",
            "error": str(exc),
            "executor_exit_code": proc.returncode,
        }
    result_path = epoch_dir / "epoch-result.json"
    atomic_json(result_path, result)
    append_event(
        control_dir,
        "epoch_finished",
        {
            "epoch": epoch,
            "exit_code": proc.returncode,
            "bookend_state": result.get("bookend_state"),
            "status": result.get("status"),
            "result": str(result_path),
        },
    )
    return proc.returncode, result


def artifact_ref(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None or not path.is_file():
        return None
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def validate_artifact_reference(value: Any, label: str) -> Path:
    if not isinstance(value, dict):
        raise BookendError(f"{label} artifact reference is missing")
    raw_path = str(value.get("path") or "").strip()
    path = Path(raw_path).resolve() if raw_path else Path()
    if not raw_path or not path.is_file() or path.is_symlink():
        raise BookendError(f"{label} artifact is unreadable")
    if value.get("sha256") != sha256_file(path):
        raise BookendError(f"{label} artifact hash mismatch")
    if value.get("bytes") != path.stat().st_size:
        raise BookendError(f"{label} artifact size mismatch")
    return path


def validate_projection_coverage(projection: Dict[str, Any], diff_size: int) -> None:
    coverage = projection.get("coverage")
    if not isinstance(coverage, list) or not coverage:
        raise BookendError("review projection coverage is empty")
    allowed = {"semantic-frontier", "mechanically-verified", "generated-derived"}
    cursor = 0
    for index, segment in enumerate(coverage):
        if not isinstance(segment, dict):
            raise BookendError(f"review projection segment {index} is invalid")
        start = segment.get("start_byte")
        end = segment.get("end_byte")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start != cursor
            or end <= start
            or segment.get("classification") not in allowed
        ):
            raise BookendError(
                f"review projection segment {index} breaks exact coverage"
            )
        cursor = end
    if cursor != diff_size:
        raise BookendError("review projection does not cover the complete diff")
    if projection.get("coverage_valid") is not True:
        raise BookendError("review projection is not marked coverage-valid")
    if projection.get("unclassified_byte_count") != 0:
        raise BookendError("review projection contains unclassified bytes")


def validate_review_request(state_path: Path, request: Dict[str, Any]) -> None:
    state = load_json(state_path)
    if request.get("logical_task_id") != state.get("logical_task_id"):
        raise BookendError("wake request logical task mismatch")
    if request.get("contract_hash") != state.get("contract_hash"):
        raise BookendError("wake request contract mismatch")
    frozen = validate_artifact_reference(request.get("frozen_task"), "frozen task")
    if sha256_file(frozen) != state.get("contract_hash"):
        raise BookendError("frozen task no longer matches the contract")
    validate_artifact_reference(request.get("execution_result"), "execution result")

    if request.get("kind") != "final-review":
        return
    projection_path = validate_artifact_reference(
        request.get("review_projection"), "review projection"
    )
    validate_artifact_reference(request.get("review_capsule"), "review capsule")
    diff_path = validate_artifact_reference(request.get("diff"), "review diff")
    projection = load_json(projection_path)
    if projection.get("logical_task_id") != state.get("logical_task_id"):
        raise BookendError("review projection logical task mismatch")
    if projection.get("contract_hash") != state.get("contract_hash"):
        raise BookendError("review projection contract mismatch")
    if projection.get("base_sha") != state.get("base_sha"):
        raise BookendError("review projection base mismatch")
    projection_diff = validate_artifact_reference(
        projection.get("diff"), "projected diff"
    )
    if projection_diff != diff_path:
        raise BookendError("wake request and projection name different diffs")
    validate_artifact_reference(projection.get("contract"), "projected contract")
    validate_projection_coverage(projection, diff_path.stat().st_size)


def label_path(dispatch_stdout: Path, label: str) -> Optional[Path]:
    if not dispatch_stdout.is_file():
        return None
    prefix = label + ":"
    for line in reversed(
        dispatch_stdout.read_text(encoding="utf-8", errors="replace").splitlines()
    ):
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            if value:
                path = Path(value)
                return path.resolve() if path.is_file() else None
    return None


def find_diff(result: Dict[str, Any]) -> Optional[Path]:
    run_dir_raw = result.get("run_dir")
    if not run_dir_raw:
        return None
    run_dir = Path(str(run_dir_raw))
    for label in ("Diff", "Recovered Diff", "Scoped Patch"):
        path = label_path(run_dir / "dispatch.stdout", label)
        if path is not None:
            return path
    for candidate in (
        run_dir / "handoff.diff",
        run_dir / "changes.diff",
        run_dir / "scoped.patch",
    ):
        if candidate.is_file():
            return candidate.resolve()
    return None


def build_projection(state_path: Path, result: Dict[str, Any]) -> Tuple[Path, Path]:
    control_dir = state_path.parent
    state = load_json(state_path)
    diff = find_diff(result)
    if diff is None:
        raise BookendError("DONE_CANDIDATE has no hash-bound diff artifact")
    raw = diff.read_bytes()
    if not raw:
        raise BookendError("DONE_CANDIDATE diff is empty")

    # The first implementation deliberately sends the entire diff as semantic
    # frontier.  This is not yet compressed, but it proves the projection
    # invariant: every byte belongs to exactly one coverage segment.  Future
    # deterministic classifiers may split this span without changing the
    # review contract.
    coverage = [
        {
            "start_byte": 0,
            "end_byte": len(raw),
            "classification": "semantic-frontier",
            "expanded": True,
        }
    ]
    run_dir = Path(str(result.get("run_dir")))
    evidence_paths = [
        run_dir / "result.json",
        run_dir / "evidence.json",
        run_dir / "acceptance-result.json",
        run_dir / "review-ladder-result.json",
        run_dir / "artifact-manifest.json",
    ]
    projection = {
        "schema_version": SCHEMA_VERSION,
        "kind": "coverage-preserving-review-projection",
        "logical_task_id": state["logical_task_id"],
        "contract": artifact_ref(Path(str(state["frozen_task"]))),
        "contract_hash": state["contract_hash"],
        "base_sha": state["base_sha"],
        "diff": artifact_ref(diff),
        "coverage": coverage,
        "coverage_valid": coverage[0]["start_byte"] == 0
        and coverage[0]["end_byte"] == len(raw),
        "unclassified_byte_count": 0,
        "classification_counts": {"semantic-frontier": 1},
        "machine_evidence": [
            {**ref, "evidence_type": "deterministic_fact"}
            for ref in (artifact_ref(path) for path in evidence_paths)
            if ref
        ],
        "model_claims": {
            "evidence_type": "model_claim",
            "source": "claude",
            "semantic_assumptions": [],
            "unresolved_risks": [],
        },
    }
    projection_path = control_dir / "review-projection.json"
    atomic_json(projection_path, projection)
    capsule = {
        "schema_version": SCHEMA_VERSION,
        "kind": "bookend-review-capsule",
        "logical_task_id": state["logical_task_id"],
        "contract_hash": state["contract_hash"],
        "base_sha": state["base_sha"],
        "projection": artifact_ref(projection_path),
        "diff": projection["diff"],
        "coverage_valid": True,
        "unclassified_byte_count": 0,
        "semantic_frontier_segments": 1,
        "review_instruction": "Review semantic implications only; expand the hash-bound projection selectively.",
    }
    capsule_path = control_dir / "review-capsule.json"
    atomic_json(capsule_path, capsule)
    return projection_path, capsule_path


def emit_wake_request(
    state_path: Path,
    kind: str,
    reason: str,
    result: Dict[str, Any],
    projection: Optional[Path] = None,
    capsule: Optional[Path] = None,
) -> Path:
    state = load_json(state_path)
    projected_diff = load_json(projection).get("diff") if projection else None
    request = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "logical_task_id": state["logical_task_id"],
        "contract_hash": state["contract_hash"],
        "reason": reason,
        "created_at": utc_now(),
        "frozen_task": artifact_ref(Path(str(state["frozen_task"]))),
        "execution_result": (
            artifact_ref(Path(str(state["last_result"])))
            if state.get("last_result")
            else None
        ),
        "review_projection": artifact_ref(projection),
        "review_capsule": artifact_ref(capsule),
        "diff": projected_diff,
        "codex_action": (
            "final-semantic-review"
            if kind == "final-review"
            else "bounded-contract-delta"
        ),
        "merge_authorized": False,
    }
    path = state_path.parent / "codex-wake-request.json"
    atomic_json(path, request)
    append_event(
        state_path.parent,
        "codex_wakeup_requested",
        {
            "kind": kind,
            "reason": reason,
            "request": str(path),
        },
    )
    return path


def classify_result(exit_code: int, result: Dict[str, Any]) -> str:
    explicit = str(result.get("bookend_state") or "").strip().lower().replace("-", "_")
    if explicit in {
        "done_candidate",
        "semantic_blocked",
        "epoch_expired",
        "runtime_blocked",
        "authority_blocked",
        "budget_exhausted",
    }:
        if explicit == "done_candidate" and not (
            result.get("status") == "completed"
            and result.get("acceptance_status") == "passed"
        ):
            return "runtime_blocked"
        return explicit
    failure = str(result.get("failure_status") or "").strip().lower()
    if failure in SEMANTIC_FAILURES:
        return "semantic_blocked"
    if exit_code == 75 or failure == "needs-host-execution":
        return "authority_blocked"
    if (
        result.get("status") == "completed"
        and result.get("acceptance_status") == "passed"
    ):
        return "done_candidate"
    return "runtime_blocked"


def validate_continuation_receipt(
    state_path: Path,
    result: Dict[str, Any],
    epoch: int,
) -> Tuple[bool, str]:
    raw_path = str(result.get("continuation_receipt") or "").strip()
    if not raw_path:
        return False, "epoch continuation receipt is missing"
    path = Path(raw_path).resolve()
    if not path.is_file() or path.is_symlink():
        return False, "epoch continuation receipt is unreadable"
    expected_hash = str(result.get("continuation_receipt_sha256") or "")
    if not expected_hash or sha256_file(path) != expected_hash:
        return False, "epoch continuation receipt hash mismatch"
    try:
        receipt = load_json(path)
    except BookendError as exc:
        return False, str(exc)
    state = load_json(state_path)
    exact = {
        "schema_version": SCHEMA_VERSION,
        "kind": "bookend-epoch-continuation",
        "logical_task_id": state["logical_task_id"],
        "contract_hash": state["contract_hash"],
        "epoch": epoch,
        "owner": "claude",
        "prior_write_grant_revoked": True,
        "no_active_writer": True,
    }
    for key, expected in exact.items():
        if receipt.get(key) != expected:
            return False, f"epoch continuation receipt mismatch: {key}"
    stable = str(receipt.get("stable_state_hash") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", stable):
        return False, "epoch continuation stable state hash is invalid"
    return True, "verified"


def validate_semantic_block_receipt(
    state_path: Path,
    result: Dict[str, Any],
    epoch: int,
) -> Tuple[bool, str]:
    raw_path = str(result.get("semantic_block_receipt") or "").strip()
    if not raw_path:
        return False, "semantic block receipt is missing"
    path = Path(raw_path).resolve()
    if not path.is_file() or path.is_symlink():
        return False, "semantic block receipt is unreadable"
    if sha256_file(path) != str(result.get("semantic_block_receipt_sha256") or ""):
        return False, "semantic block receipt hash mismatch"
    try:
        receipt = load_json(path)
    except BookendError as exc:
        return False, str(exc)
    state = load_json(state_path)
    exact = {
        "schema_version": SCHEMA_VERSION,
        "kind": "bookend-semantic-block",
        "logical_task_id": state["logical_task_id"],
        "contract_hash": state["contract_hash"],
        "epoch": epoch,
    }
    for key, expected in exact.items():
        if receipt.get(key) != expected:
            return False, f"semantic block receipt mismatch: {key}"
    if not str(receipt.get("decision_required") or "").strip():
        return False, "semantic block decision_required is empty"
    blocking = receipt.get("blocking_acceptance")
    if not isinstance(blocking, list) or not blocking:
        return False, "semantic block must name blocking acceptance"
    if receipt.get("execution_failure_only") is not False:
        return False, "execution failure cannot be a semantic block"
    return True, "verified"


def generate_convergence_receipt(
    state_path: Path,
    epoch: int,
    product_worktree: str,
) -> Path:
    """Generate a Bookend convergence continuation receipt.

    The receipt proves that the product worktree is under Bookend control,
    the prior epoch writer has been revoked, and the worktree state is
    stable.  Unlike a reviewed-continuation approval, it does not require
    a Codex semantic decision — the frozen contract is the authority.
    """
    from worktree_state_hash import compute_worktree_state_hash

    state = load_json(state_path)
    control_dir = state_path.parent
    wt = Path(product_worktree).resolve()

    if not wt.is_dir():
        raise BookendError(f"product worktree is not a directory: {wt}")
    if wt.is_symlink():
        raise BookendError(f"product worktree is a symlink: {wt}")

    # Verify the worktree is under the expected .worktrees boundary.
    repo = Path(str(state["repo"]))
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode == 0:
            common = Path(result.stdout.strip())
            if not common.is_absolute():
                common = (repo / common).resolve()
            runtime_root = (common.parent if common.name == ".git" else repo.resolve())
            expected_root = (runtime_root / ".worktrees").resolve()
            if not str(wt).startswith(str(expected_root) + os.sep):
                raise BookendError(
                    f"product worktree {wt} is outside .worktrees boundary {expected_root}"
                )
    except (OSError, FileNotFoundError):
        pass  # If git is unavailable, skip boundary check

    # Compute stable state hash of the dirty worktree.
    try:
        state_hash = compute_worktree_state_hash(str(wt))
    except Exception as exc:
        raise BookendError(f"cannot compute worktree state hash: {exc}")

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "bookend-convergence-continuation",
        "logical_task_id": state["logical_task_id"],
        "contract_hash": state["contract_hash"],
        "epoch": epoch,
        "owner": "claude",
        "product_worktree": str(wt),
        "worktree_state_hash": state_hash,
        "prior_write_grant_revoked": True,
        "no_active_writer": True,
    }
    receipt_path = control_dir / f"convergence-receipt-epoch-{epoch:03d}.json"
    atomic_json(receipt_path, receipt)
    return receipt_path


def acquire_supervisor(control_dir: Path) -> None:
    lock = control_dir / "supervisor.lock"
    try:
        fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BookendError("a supervisor already owns this task") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "created_at": utc_now()}) + "\n")


def supervise(state_path: Path) -> Dict[str, Any]:
    state = load_json(state_path)
    control_dir = state_path.parent
    acquire_supervisor(control_dir)
    frozen = Path(str(state["frozen_task"]))
    repo = Path(str(state["repo"]))
    if sha256_file(frozen) != state["contract_hash"]:
        return update_state(
            state_path, "runtime_blocked", blocker="frozen-contract-hash-mismatch"
        )
    if git(repo, "rev-parse", "HEAD") != state["base_sha"]:
        return update_state(
            state_path, "runtime_blocked", blocker="source-head-changed-after-freeze"
        )

    max_epochs = int(state.get("max_epochs", 1))
    for epoch in range(1, max_epochs + 1):
        update_state(
            state_path,
            "converging",
            epoch=epoch,
            epoch_write_grant=f"issued:{epoch}",
            owner_lease_state="active",
        )
        exit_code, result = run_epoch(state_path, epoch)
        result_path = (
            control_dir / "epochs" / f"epoch-{epoch:03d}" / "epoch-result.json"
        )
        # Persist the executor's product worktree so the next epoch can
        # resume from the same working tree rather than starting fresh.
        state_updates: Dict[str, Any] = {
            "last_result": str(result_path),
            "epoch_write_grant": f"revoked:{epoch}",
        }
        if result.get("product_worktree"):
            state_updates["product_worktree"] = str(result["product_worktree"])
        state = update_state(state_path, "classifying", **state_updates)
        outcome = classify_result(exit_code, result)

        if outcome == "epoch_expired" and result.get("continuation_safe") is True:
            continuation_valid, continuation_reason = validate_continuation_receipt(
                state_path,
                result,
                epoch,
            )
            if not continuation_valid:
                return update_state(
                    state_path,
                    "runtime_blocked",
                    blocker=continuation_reason,
                    owner_lease_state="suspended",
                )
            if epoch < max_epochs:
                update_state(
                    state_path,
                    "recovering",
                    recovery="same-owner-next-epoch",
                    continuation_receipt=result.get("continuation_receipt"),
                )
                continue
            return update_state(
                state_path,
                "budget_exhausted",
                blocker="maximum-execution-epochs-reached",
                owner_lease_state="suspended",
            )
        if outcome == "done_candidate":
            update_state(state_path, "projecting")
            try:
                projection, capsule = build_projection(state_path, result)
            except BookendError as exc:
                return update_state(
                    state_path,
                    "runtime_blocked",
                    blocker=str(exc),
                    owner_lease_state="suspended",
                )
            wake = emit_wake_request(
                state_path,
                "final-review",
                "done-candidate",
                result,
                projection,
                capsule,
            )
            return update_state(
                state_path,
                "review_ready",
                review_request=str(wake),
                review_projection=str(projection),
                review_capsule=str(capsule),
                owner_lease_state="released",
            )
        if outcome == "semantic_blocked":
            semantic_valid, semantic_reason = validate_semantic_block_receipt(
                state_path,
                result,
                epoch,
            )
            if not semantic_valid:
                return update_state(
                    state_path,
                    "runtime_blocked",
                    blocker=semantic_reason,
                    owner_lease_state="suspended",
                )
            wake = emit_wake_request(
                state_path,
                "semantic-escalation",
                "semantic-contract-decision-required",
                result,
            )
            return update_state(
                state_path,
                "semantic_blocked",
                review_request=str(wake),
                owner_lease_state="suspended",
            )
        if outcome == "authority_blocked":
            return update_state(
                state_path,
                "authority_blocked",
                blocker=result.get("error")
                or result.get("failure_status")
                or "authority-required",
                owner_lease_state="suspended",
            )
        if outcome == "budget_exhausted":
            return update_state(
                state_path, "budget_exhausted", owner_lease_state="suspended"
            )
        # Non-semantic failure: if more epochs remain, let Claude continue
        # converging under the same owner rather than stopping the supervisor.
        # Compile errors, test failures, incomplete acceptance, transport
        # failures, and executor crashes are all non-semantic — the frozen
        # contract may still be satisfiable in a subsequent epoch.
        if epoch < max_epochs:
            wt = state.get("product_worktree")
            receipt_path = None
            if wt:
                try:
                    receipt_path = generate_convergence_receipt(
                        state_path, epoch, str(wt),
                    )
                except BookendError:
                    pass  # Receipt generation failure is not fatal
            update_state(
                state_path,
                "recovering",
                recovery="convergence-continue",
                convergence_receipt=str(receipt_path) if receipt_path else None,
                blocker=result.get("error")
                or result.get("failure_status")
                or "executor-did-not-converge",
            )
            continue
        return update_state(
            state_path,
            "runtime_blocked",
            blocker=result.get("error")
            or result.get("failure_status")
            or "executor-did-not-converge",
            owner_lease_state="suspended",
        )
    return update_state(state_path, "budget_exhausted", owner_lease_state="suspended")


def spawn_supervisor(state_path: Path) -> int:
    control_dir = state_path.parent
    stdout = (control_dir / "supervisor.stdout").open("ab")
    stderr = (control_dir / "supervisor.stderr").open("ab")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "supervise",
        str(state_path),
        "--json",
    ]
    kwargs: Dict[str, Any] = {
        "cwd": str(load_json(state_path)["repo"]),
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": stderr,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **kwargs)
    stdout.close()
    stderr.close()
    atomic_json(
        control_dir / "supervisor.json",
        {
            "schema_version": SCHEMA_VERSION,
            "pid": proc.pid,
            "command": command,
            "started_at": utc_now(),
        },
    )
    return proc.pid


def cmd_submit(args: argparse.Namespace) -> int:
    state_path, _ = create_task(args)
    if args.foreground:
        final = supervise(state_path)
        output = final
    else:
        pid = spawn_supervisor(state_path)
        output = load_json(state_path)
        output["supervisor_pid"] = pid
    if args.json:
        print(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Bookend state: {state_path}")
        print(f"State: {output.get('state')}")
        print(
            "Codex may end this execution episode; the control plane now owns Claude."
        )
    if args.foreground and output.get("state") not in CODEX_WAKE_STATES:
        if output.get("state") == "authority_blocked":
            return 75
        return 1
    return 0


def cmd_supervise(args: argparse.Namespace) -> int:
    state_path = resolve_state(args.state)
    try:
        value = supervise(state_path)
    except BookendError as exc:
        value = update_state(state_path, "runtime_blocked", blocker=str(exc))
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(f"State: {value.get('state')}")
        print(f"Bookend state: {state_path}")
    if value.get("state") in CODEX_WAKE_STATES:
        return 0
    if value.get("state") == "authority_blocked":
        return 75
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    state_path = resolve_state(args.state)
    value = load_json(state_path)
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Task: {value.get('logical_task_id')}")
        print(f"State: {value.get('state')}")
        print(f"Epoch: {value.get('epoch')}/{value.get('max_epochs')}")
        print(
            f"Codex wakeup required: {str(bool(value.get('codex_wakeup_required'))).lower()}"
        )
        if value.get("review_request"):
            print(f"Wake request: {value['review_request']}")
        if value.get("blocker"):
            print(f"Blocker: {value['blocker']}")
    return 0


def cmd_review_input(args: argparse.Namespace) -> int:
    state_path = resolve_state(args.state)
    state = load_json(state_path)
    if state.get("state") not in CODEX_WAKE_STATES:
        raise BookendError(
            "Codex review is not scheduled for state=" + str(state.get("state"))
        )
    request = Path(str(state.get("review_request") or ""))
    if not request.is_file():
        raise BookendError("wake request is missing")
    value = load_json(request)
    try:
        validate_review_request(state_path, value)
    except BookendError as exc:
        update_state(
            state_path,
            "runtime_blocked",
            blocker=f"stale-review-input: {exc}",
            review_request=None,
            owner_lease_state="suspended",
        )
        raise
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aiwf bookend",
        description="Submit and supervise a Claude-owned task without keeping Codex active.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser(
        "submit",
        help="Freeze and submit a durable Bookend task.",
        description="Freeze and submit a durable Bookend task.",
    )
    submit.add_argument("task")
    submit.add_argument("--repo")
    submit.add_argument("--run-dir-base")
    submit.add_argument("--profiles-dir")
    submit.add_argument("--dispatcher")
    submit.add_argument(
        "--executor", help="Epoch executor implementing the run-workflow JSON contract."
    )
    submit.add_argument("--max-epochs", type=int, default=3)
    submit.add_argument("--host-authority", action="store_true")
    submit.add_argument(
        "--foreground",
        action="store_true",
        help="Run supervisor in this process for tests/diagnosis.",
    )
    submit.add_argument("--json", action="store_true")
    submit.set_defaults(func=cmd_submit)

    supervise_p = sub.add_parser(
        "supervise", help="Internal/control-plane supervisor entrypoint."
    )
    supervise_p.add_argument("state")
    supervise_p.add_argument("--json", action="store_true")
    supervise_p.set_defaults(func=cmd_supervise)

    status = sub.add_parser("status", help="Read one durable Bookend task state.")
    status.add_argument("state")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    review = sub.add_parser(
        "review-input", help="Return the hash-bound Codex wake request."
    )
    review.add_argument("state")
    review.set_defaults(func=cmd_review_input)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "max_epochs", 1) < 1:
        parser.error("--max-epochs must be positive")
    try:
        return int(args.func(args))
    except BookendError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
