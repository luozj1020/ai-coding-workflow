#!/usr/bin/env python3
"""Atomically transfer a stopped Claude worktree to bounded Codex ownership."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))


class TakeoverError(RuntimeError):
    pass


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise TakeoverError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROCESS_IDENTITY = _load_module("aiwf_process_identity", SCRIPT_DIR / "process-identity.py")
OWNER_LEASE = None
WORKTREE_HASH = None


def _load_takeover_modules() -> None:
    """Load takeover-only dependencies outside the lightweight stop command."""
    global OWNER_LEASE, WORKTREE_HASH
    if OWNER_LEASE is None:
        OWNER_LEASE = _load_module(
            "aiwf_owner_lease", SCRIPT_DIR / "owner_lease.py"
        )
    if WORKTREE_HASH is None:
        WORKTREE_HASH = _load_module(
            "aiwf_worktree_state_hash", SCRIPT_DIR / "worktree_state_hash.py"
        )


def load_json(path: Path, label: str) -> Dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise TakeoverError(f"{label} is missing, unsafe, or oversized")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TakeoverError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise TakeoverError(f"{label} must be a JSON object")
    return value


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _linux_descendants(pid: int) -> List[int]:
    children: Dict[int, List[int]] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            tail = stat[stat.rfind(")") + 2 :].split()
            parent = int(tail[1])
            child = int(entry.name)
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(parent, []).append(child)
    result: List[int] = []
    frontier = list(children.get(pid, []))
    while frontier:
        child = frontier.pop()
        result.append(child)
        frontier.extend(children.get(child, []))
    return result


def _snapshot_processes(pids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    result: Dict[int, Dict[str, Any]] = {}
    for pid in pids:
        current = PROCESS_IDENTITY._process(pid)
        if current is not None:
            result[pid] = current
    return result


def _linux_owned_process_groups(pids: Iterable[int]) -> List[int]:
    """Return descendant-created groups, excluding the takeover caller's group."""
    groups = set()
    caller_group = os.getpgrp()
    for pid in pids:
        try:
            stat = (Path("/proc") / str(pid) / "stat").read_text(
                encoding="utf-8", errors="replace",
            )
            tail = stat[stat.rfind(")") + 2 :].split()
            group = int(tail[2])
        except (OSError, ValueError, IndexError):
            continue
        if group != caller_group and group == pid:
            groups.add(group)
    return sorted(groups)


def _same_process(identity: Dict[str, Any]) -> bool:
    pid = int(identity["pid"])
    current = PROCESS_IDENTITY._process(pid)
    if current is None:
        return False
    if current.get("state") == "Z":
        return False
    return all(
        identity.get(field) == current.get(field)
        for field in ("pid", "start_time_ticks", "pid_namespace_inode", "cmdline_sha256")
    )


def _wait_windows_inactive(pid: int, timeout: float) -> bool:
    """Authoritatively wait for a Windows process handle to become signaled."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    process = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not process:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return True
        raise TakeoverError(
            f"cannot open Windows process {pid} for termination wait: error {error}"
        )
    try:
        result = kernel32.WaitForSingleObject(process, max(1, int(timeout * 1000)))
    finally:
        kernel32.CloseHandle(process)
    if result == 0:  # WAIT_OBJECT_0
        return True
    if result == 258:  # WAIT_TIMEOUT
        return False
    raise TakeoverError(f"Windows process termination wait failed: result {result}")


def terminate_identity(identity: Dict[str, Any], task_id: str, role: str, timeout: float) -> Dict[str, Any]:
    status, _ = PROCESS_IDENTITY.check(identity, task_id, role)
    if status in {"not-running", "pid-reused-or-foreign"}:
        return {"role": role, "initial_status": status, "terminated": False, "confirmed_inactive": True}
    if status != "running-same-process":
        raise TakeoverError(f"{role} process visibility is not authoritative: {status}")
    pid = int(identity["pid"])
    descendants = _linux_descendants(pid) if sys.platform != "win32" else []
    snapshots = _snapshot_processes([pid, *descendants])
    owned_groups = _linux_owned_process_groups(descendants) if sys.platform != "win32" else []
    windows_inactive = False
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode not in {0, 128}:
            raise TakeoverError(f"taskkill failed for {role}: {(result.stderr or result.stdout).strip()}")
        windows_inactive = _wait_windows_inactive(pid, timeout)
        if not windows_inactive:
            raise TakeoverError(f"{role} process tree did not terminate before timeout")
    else:
        # The broker launches the model in a new session/process group. Kill
        # that group first so a child created during takeover cannot escape by
        # reparenting after the wrapper is stopped.
        for group in owned_groups:
            leader = snapshots.get(group)
            if leader and _same_process(leader):
                try:
                    os.killpg(group, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for child in reversed(descendants):
            snapshot = snapshots.get(child)
            if snapshot and _same_process(snapshot):
                try:
                    os.kill(child, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if _same_process(identity):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_same_process(value) for value in snapshots.values()):
            break
        time.sleep(0.1)
    survivors = [process_id for process_id, value in snapshots.items() if _same_process(value)]
    if windows_inactive:
        survivors = []
    if survivors and sys.platform != "win32":
        for group in owned_groups:
            leader = snapshots.get(group)
            if leader and _same_process(leader):
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for process_id in survivors:
            try:
                os.kill(process_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + min(timeout, 2.0)
        while time.monotonic() < deadline and any(
            _same_process(snapshots[process_id]) for process_id in survivors
        ):
            time.sleep(0.1)
        survivors = [process_id for process_id in survivors if _same_process(snapshots[process_id])]
    if survivors:
        raise TakeoverError(f"{role} process tree did not terminate: {survivors}")
    final_status, _ = PROCESS_IDENTITY.check(identity, task_id, role)
    if windows_inactive:
        final_status = "not-running"
    if final_status not in {"not-running", "pid-reused-or-foreign"}:
        raise TakeoverError(f"{role} termination could not be confirmed: {final_status}")
    return {
        "role": role, "initial_status": status, "terminated": True,
        "confirmed_inactive": True, "descendant_count": len(descendants),
        "terminated_process_groups": owned_groups,
    }


def process_receipts(runtime: Dict[str, Any]) -> List[Tuple[str, Optional[Path], Optional[Path]]]:
    identities = runtime.get("process_identity_files") or {}
    pid_files = runtime.get("pid_files") or {}
    if not isinstance(identities, dict) or not isinstance(pid_files, dict):
        raise TakeoverError("runtime process_identity_files must be an object")
    result: List[Tuple[str, Optional[Path], Optional[Path]]] = []
    for role in ("checker", "claude", "dispatcher"):
        identity_raw = identities.get(role)
        pid_raw = pid_files.get(role)
        if identity_raw or pid_raw:
            result.append((
                role,
                Path(str(identity_raw)).resolve() if identity_raw else None,
                Path(str(pid_raw)).resolve() if pid_raw else None,
            ))
    if not result:
        raise TakeoverError("runtime has no process receipts")
    return result


def stable_hash(worktree: Path, samples: int, interval: float) -> Tuple[str, List[str]]:
    observed: List[str] = []
    for index in range(samples):
        observed.append(WORKTREE_HASH.compute_worktree_state_hash(worktree))
        if index + 1 < samples:
            time.sleep(interval)
    if len(set(observed)) != 1:
        raise TakeoverError("worktree changed during takeover stability sampling")
    return observed[-1], observed


def _runtime_attempt_binding(runtime: Dict[str, Any]) -> Dict[str, str]:
    worktree_text = str(runtime.get("worktree") or "")
    source_text = str(runtime.get("source_repository") or "")
    source_base = str(runtime.get("source_base_commit") or runtime.get("base_commit") or "")
    execution_base = str(
        runtime.get("execution_base_commit") or runtime.get("worktree_start_commit") or ""
    )
    lineage_root = str(runtime.get("lineage_root_task_id") or "")
    session_id = str(runtime.get("claude_session_id") or "")
    if not all((worktree_text, source_text, source_base, execution_base, lineage_root, session_id)):
        raise TakeoverError("runtime attempt lineage binding is incomplete")
    worktree = Path(worktree_text).resolve()
    card = worktree / "TASK_CARD_FULL.md"
    if not worktree.is_dir() or not card.is_file():
        raise TakeoverError("runtime attempt worktree/card is unavailable")
    return {
        "task_id": str(runtime.get("task_id") or ""),
        "lineage_root_task_id": lineage_root,
        "task_card_sha256": digest(card),
        "source_base_commit": source_base,
        "execution_base_commit": execution_base,
        "source_repository": str(Path(source_text).resolve()),
        "worktree": str(worktree),
        "claude_session_id": session_id,
    }


def _validate_candidate_attempt_lineage(
    candidate: Dict[str, Any], runtime_path: Path, runtime: Dict[str, Any], task_id: str,
) -> None:
    lineage = candidate.get("attempt_lineage")
    if not isinstance(lineage, dict) or lineage.get("schema") != "aiwf-takeover-attempt-lineage-v1":
        raise TakeoverError("takeover candidate lacks a bound attempt lineage")
    if lineage.get("relation") != "retry-in-place":
        raise TakeoverError("takeover candidate was not produced by retry-in-place")
    if lineage.get("current_task_id") != task_id:
        raise TakeoverError("current task does not match takeover attempt lineage")
    if lineage.get("current_runtime_receipt") != str(runtime_path):
        raise TakeoverError("current runtime path does not match takeover attempt lineage")
    if lineage.get("current_runtime_receipt_object") != digest(runtime_path):
        raise TakeoverError("current runtime changed after takeover candidate issuance")

    prior_task_id = str(lineage.get("prior_task_id") or "")
    prior_runtime_text = str(lineage.get("prior_runtime_receipt") or "")
    if not prior_task_id or not prior_runtime_text:
        raise TakeoverError("takeover candidate prior attempt lineage is incomplete")
    prior_runtime_path = Path(prior_runtime_text).resolve()
    if lineage.get("prior_runtime_receipt_object") != digest(prior_runtime_path):
        raise TakeoverError("prior runtime changed after takeover candidate issuance")
    prior_runtime = load_json(prior_runtime_path, "prior runtime receipt")
    if prior_runtime.get("task_id") != prior_task_id:
        raise TakeoverError("prior runtime task identity mismatch")

    current_binding = _runtime_attempt_binding(runtime)
    prior_binding = _runtime_attempt_binding(prior_runtime)
    if current_binding["task_id"] != task_id:
        raise TakeoverError("current runtime attempt binding task identity mismatch")
    binding = lineage.get("binding")
    if not isinstance(binding, dict):
        raise TakeoverError("takeover candidate attempt binding is unavailable")
    for key, expected in current_binding.items():
        if binding.get(key) != expected:
            raise TakeoverError(f"takeover candidate attempt binding mismatch: {key}")
    for key in (
        "lineage_root_task_id", "task_card_sha256", "source_base_commit",
        "execution_base_commit", "source_repository", "worktree", "claude_session_id",
    ):
        if prior_binding[key] != current_binding[key]:
            raise TakeoverError(f"prior/current takeover attempt mismatch: {key}")
    if candidate.get("lineage_root_task_id") != current_binding["lineage_root_task_id"]:
        raise TakeoverError("candidate lineage root does not match current runtime")
    if runtime.get("strategy") != "retry-in-place" or runtime.get("retry_of") != prior_task_id:
        raise TakeoverError("current runtime is not an explicit retry of the prior task")
    try:
        current_ordinal = int(runtime.get("retry_ordinal", -1))
        prior_ordinal = int(prior_runtime.get("retry_ordinal", -1))
    except (TypeError, ValueError) as exc:
        raise TakeoverError("takeover retry ordinal is invalid") from exc
    if current_ordinal != prior_ordinal + 1:
        raise TakeoverError("takeover retry ordinal is not consecutive")


def prepare(args: argparse.Namespace) -> Dict[str, Any]:
    _load_takeover_modules()
    candidate_path = args.receipt.resolve()
    runtime_path = args.runtime.resolve()
    candidate = load_json(candidate_path, "takeover candidate")
    runtime = load_json(runtime_path, "runtime receipt")
    if candidate.get("schema_version") != 3 or candidate.get("status") != "preparation-required":
        raise TakeoverError("receipt is not a schema-v3 takeover candidate")
    if candidate.get("authorization") != "codex-takeover-candidate":
        raise TakeoverError("receipt does not represent a Codex takeover candidate")
    if candidate.get("runtime_receipt") != str(runtime_path):
        raise TakeoverError("runtime path does not match takeover candidate")
    if candidate.get("runtime_receipt_object") != digest(runtime_path):
        raise TakeoverError("runtime receipt changed after candidate issuance")
    task_id = str(candidate.get("current_task_id") or "")
    if not task_id or runtime.get("task_id") != task_id:
        raise TakeoverError("runtime task identity mismatch")
    _validate_candidate_attempt_lineage(candidate, runtime_path, runtime, task_id)
    worktree = Path(str(runtime.get("worktree") or "")).resolve()
    if not worktree.is_dir():
        raise TakeoverError("runtime worktree is unavailable")

    lease_evidence: Dict[str, Any]
    if args.owner_lease:
        lease_path = args.owner_lease.resolve()
        lease = load_json(lease_path, "Owner Lease")
        if OWNER_LEASE.validate_lease(lease):
            raise TakeoverError("Owner Lease is invalid")
        if lease.get("status") in {"requested", "granted"}:
            revoked = OWNER_LEASE.transition_lease(
                lease, "revoked", "codex-takeover-single-writer-transfer",
            )
        elif lease.get("status") == "revoked":
            revoked = lease
        else:
            raise TakeoverError("Owner Lease must be active or already revoked")
        revoked_output = args.revoked_lease_output or args.output.with_suffix(".owner-lease.revoked.json")
        atomic_json(revoked_output, revoked)
        lease_evidence = {
            "status": "revoked", "input_object": digest(lease_path),
            "revoked_lease": str(revoked_output.resolve()),
            "revoked_lease_id": revoked["lease_id"],
        }
    else:
        lease_evidence = {"status": "explicitly-absent"}

    marker = runtime_path.parent / f"{task_id}.codex-write-owner.json"
    in_progress = {
        "schema_version": 1,
        "status": "takeover-in-progress",
        "authorization": "none",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "candidate_receipt": str(candidate_path),
        "candidate_receipt_object": digest(candidate_path),
        "runtime_receipt": str(runtime_path),
        "runtime_receipt_object": digest(runtime_path),
        "task_id": task_id,
        "worktree": str(worktree),
        "write_owner": "none",
        "single_writer_confirmed": False,
        "owner_lease": lease_evidence,
        "merge_authorized": False,
    }
    # Claim the worktree before stopping processes so a concurrent continuation
    # cannot pass its ownership check during the termination/stability window.
    # A later failure intentionally leaves this deny marker in place.
    atomic_json(marker, in_progress)

    process_evidence = []
    for role, identity_path, pid_path in process_receipts(runtime):
        if identity_path is not None and identity_path.is_file():
            identity = load_json(identity_path, f"{role} process identity")
            process_evidence.append(
                terminate_identity(identity, task_id, role, args.terminate_timeout)
            )
            continue
        if pid_path is not None and pid_path.is_file() and pid_path.read_text(
            encoding="utf-8", errors="replace",
        ).strip():
            raise TakeoverError(f"{role} has a PID receipt without authoritative process identity")
        process_evidence.append({
            "role": role,
            "initial_status": "not-started",
            "terminated": False,
            "confirmed_inactive": True,
        })

    baseline, samples = stable_hash(worktree, args.stability_samples, args.stability_interval)
    grant = {
        "schema_version": 1,
        "status": "authorized",
        "authorization": "codex-bounded-takeover",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "candidate_receipt": str(candidate_path),
        "candidate_receipt_object": digest(candidate_path),
        "runtime_receipt": str(runtime_path),
        "runtime_receipt_object": digest(runtime_path),
        "task_id": task_id,
        "lineage_root_task_id": candidate.get("lineage_root_task_id"),
        "worktree": str(worktree),
        "write_owner_marker": str(marker.resolve()),
        "write_owner": "codex",
        "single_writer_confirmed": True,
        "owner_lease": lease_evidence,
        "process_termination": process_evidence,
        "takeover_baseline_hash": baseline,
        "stability_samples": samples,
        "allowed_write_paths": candidate.get("allowed_write_paths", []),
        "forbidden_paths": candidate.get("forbidden_paths", []),
        "required_validation": candidate.get("required_validation"),
        "merge_authorized": False,
    }
    # Upgrade the deny marker only after every prior process is confirmed
    # inactive and the product content remains stable.
    atomic_json(marker, grant)
    atomic_json(args.output.resolve(), grant)
    return grant


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--receipt", type=Path, required=True)
    result.add_argument("--runtime", type=Path, required=True)
    lease = result.add_mutually_exclusive_group(required=True)
    lease.add_argument("--owner-lease", type=Path)
    lease.add_argument("--no-owner-lease", action="store_true")
    result.add_argument("--revoked-lease-output", type=Path)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--terminate-timeout", type=float, default=5.0)
    result.add_argument("--stability-samples", type=int, default=2)
    result.add_argument("--stability-interval", type=float, default=1.0)
    return result


def terminate_process_parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Identity-confirm and terminate one recorded workflow process tree.",
    )
    result.add_argument("--identity", type=Path, required=True)
    result.add_argument("--task-id", required=True)
    result.add_argument(
        "--role", choices=("dispatcher", "claude", "checker"), required=True,
    )
    result.add_argument("--terminate-timeout", type=float, default=5.0)
    result.add_argument("--reason", default="bounded-process-tree-stop")
    result.add_argument("--output", type=Path, required=True)
    return result


def terminate_process_command(args: argparse.Namespace) -> Dict[str, Any]:
    identity_path = args.identity.resolve()
    identity = load_json(identity_path, f"{args.role} process identity")
    evidence = terminate_identity(
        identity, args.task_id, args.role, args.terminate_timeout,
    )
    value = {
        "schema_version": 1,
        "status": "confirmed-inactive",
        "task_id": args.task_id,
        "role": args.role,
        "reason": args.reason,
        "identity_receipt": str(identity_path),
        "identity_receipt_object": digest(identity_path),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "process_termination": evidence,
    }
    atomic_json(args.output.resolve(), value)
    return value


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    if argv and argv[0] == "terminate-process":
        args = terminate_process_parser().parse_args(argv[1:])
        if args.terminate_timeout <= 0:
            print("Error: invalid termination timeout", file=sys.stderr)
            return 2
        try:
            value = terminate_process_command(args)
        except (OSError, ValueError, TypeError, KeyError, TakeoverError) as exc:
            print(f"process termination failed: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    args = parser().parse_args(argv)
    if args.terminate_timeout <= 0 or args.stability_samples < 2 or args.stability_interval < 0:
        print("Error: invalid timeout/stability settings", file=sys.stderr)
        return 2
    try:
        value = prepare(args)
    except (OSError, ValueError, TypeError, KeyError, TakeoverError) as exc:
        print(f"takeover preparation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
