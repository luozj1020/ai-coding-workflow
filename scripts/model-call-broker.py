#!/usr/bin/env python3
"""Atomic model-call broker with cross-process quota enforcement.

Single entry point for Claude, Spark, and Codex calls. Prevents overspending
quota, duplicate evidence triggering another call, and reserved review stages
being consumed early.

Usage:
    python scripts/model-call-broker.py --role claude --stage builder \
        --plan execution-plan.json --input claude-prompt.md -- claude -p ...

    python scripts/model-call-broker.py --role codex --stage final-review \
        --plan execution-plan.json --evidence review-packet.json -- codex exec --json ...

    # Legacy compatibility (no plan):
    python scripts/model-call-broker.py --role claude --stage builder \
        --max-calls 3 --input claude-prompt.md -- claude -p ...

Python 3.9+ compatible. No third-party dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Cross-process locking
# ---------------------------------------------------------------------------

try:
    import msvcrt  # type: ignore[import-untyped]
except ImportError:
    msvcrt = None  # type: ignore[assignment]

try:
    import fcntl  # type: ignore[import-untyped]
except ImportError:
    fcntl = None  # type: ignore[assignment]


@dataclass
class LedgerLock:
    """Cross-process file lock. Use as a context manager.

    Windows: msvcrt byte-range locking on a .lock sidecar file.
    Unix: fcntl.flock(LOCK_EX) on a .lock sidecar file.
    Fallback: directory-based lock (works everywhere, stale-lock risk on crash).
    """

    lock_path: Path
    _fh: Any = None
    _dir_fallback: bool = False

    def acquire(self, timeout: float = 30.0) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if msvcrt is not None:
            # Open in r+b/w+b mode to allow read+write; ensure at least one
            # byte exists before msvcrt.locking (Windows requires non-empty).
            if self.lock_path.exists():
                self._fh = open(self.lock_path, "r+b")  # noqa: SIM115
            else:
                self._fh = open(self.lock_path, "w+b")  # noqa: SIM115
            # Ensure lock file contains at least one byte
            size = self._fh.seek(0, 2)  # seek to end, returns position
            if size == 0:
                self._fh.write(b"\x00")
                self._fh.flush()
            self._fh.seek(0)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                    return
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Could not acquire ledger lock {self.lock_path} within {timeout}s"
                        )
                    time.sleep(0.05)
        elif fcntl is not None:
            self._fh = open(self.lock_path, "w")  # noqa: SIM115
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        else:
            # Directory-based fallback
            self._dir_fallback = True
            deadline = time.monotonic() + timeout
            lock_dir = self.lock_path.with_suffix(".lockdir")
            while True:
                try:
                    lock_dir.mkdir()
                    self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
                    return
                except FileExistsError:
                    # Check for stale lock (>60s)
                    try:
                        age = time.time() - lock_dir.stat().st_mtime
                        if age > 60:
                            lock_dir.rmdir()
                            continue
                    except OSError:
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Could not acquire ledger lock {self.lock_path} within {timeout}s"
                        )
                    time.sleep(0.05)

    def release(self) -> None:
        if self._fh is not None:
            if msvcrt is not None:
                try:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            elif fcntl is not None:
                try:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            self._fh.close()
            self._fh = None
        elif self._dir_fallback:
            lock_dir = self.lock_path.with_suffix(".lockdir")
            try:
                lock_dir.rmdir()
            except OSError:
                pass
            self._dir_fallback = False

    def __enter__(self) -> "LedgerLock":
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BrokerError(Exception):
    """Raised when the broker denies or fails a reservation."""
    pass


class PlanError(BrokerError):
    """Raised when the execution plan is structurally invalid.

    Subclass of BrokerError so callers that catch BrokerError still see it,
    but the CLI maps it to exit code 3 (error) rather than exit code 2
    (denied).
    """
    pass


# ---------------------------------------------------------------------------
# Plan loading
# ---------------------------------------------------------------------------

REQUIRED_BUDGET_KEYS = ("claude_calls", "spark_calls", "codex_calls")


def load_plan(path: Path) -> Dict[str, Any]:
    """Load and validate an execution plan from JSON."""
    if not path.exists():
        raise PlanError(f"Execution plan not found: {path}")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PlanError(f"Cannot read execution plan {path}: {exc}") from exc
    if not isinstance(plan, dict):
        raise PlanError("Execution plan must be a JSON object")
    if "budget" not in plan:
        raise PlanError("Execution plan missing 'budget' section")
    budget = plan["budget"]
    for key in REQUIRED_BUDGET_KEYS:
        if key not in budget:
            raise PlanError(f"Execution plan budget missing '{key}'")
    return plan


def make_compatibility_plan(
    role: str, max_calls: Optional[int], task_id: str
) -> Dict[str, Any]:
    """Build a conservative default plan for legacy callers.

    Does not silently grant unlimited calls. Each role defaults to 1 call
    unless overridden by --max-calls.
    """
    budgets = {"claude_calls": 1, "spark_calls": 1, "codex_calls": 1}
    budget_key = f"{role}_calls"
    if budget_key in budgets and max_calls is not None:
        budgets[budget_key] = max(max_calls, 0)
    return {
        "schema_version": 1,
        "task_id": task_id,
        "lane": "standard",
        "budget": budgets,
        "review": {"reserved_for": [], "milestones": []},
        "compatibility_mode": True,
    }


# ---------------------------------------------------------------------------
# Ledger I/O (append-only JSONL, cross-process safe under lock)
# ---------------------------------------------------------------------------

VALID_STATES = ("reserved", "running", "succeeded", "failed", "cancelled")

# Legal state transitions (append-only audit trail).
VALID_TRANSITIONS: Dict[str, set] = {
    "reserved": {"running"},
    "running": {"succeeded", "failed", "cancelled"},
}


def load_ledger(path: Path) -> List[Dict[str, Any]]:
    """Load all records from the JSONL ledger.

    Fails closed on malformed JSON, missing fields, or invalid states
    rather than silently returning an empty ledger that restores quota.
    """
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BrokerError(f"Cannot read ledger {path}: {exc}") from exc

    records: List[Dict[str, Any]] = []
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BrokerError(
                f"Malformed JSON at ledger line {lineno}: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise BrokerError(
                f"Ledger line {lineno} is not a JSON object"
            )
        if "state" not in record:
            raise BrokerError(
                f"Ledger line {lineno} missing 'state' field"
            )
        state = record["state"]
        if state not in VALID_STATES:
            raise BrokerError(
                f"Ledger line {lineno} has invalid state: {state!r}"
            )
        records.append(record)
    return records


def append_ledger(path: Path, record: Dict[str, Any]) -> None:
    """Append a single JSONL record to the ledger. Caller must hold lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Ledger folding and validation
# ---------------------------------------------------------------------------


def fold_by_reservation(
    records: List[Dict[str, Any]], task_id: str, role: str
) -> Dict[str, Dict[str, Any]]:
    """Fold ledger records by reservation_id, keeping latest state per reservation.

    Because the ledger is transition-based (reserved -> running -> succeeded),
    a single reservation may have multiple records.  This returns one entry per
    reservation_id with the most recent (highest timestamp) record.
    """
    latest: Dict[str, Dict[str, Any]] = {}
    for r in records:
        if r.get("task_id") != task_id or r.get("role") != role:
            continue
        rid = r.get("reservation_id")
        if rid is None:
            continue
        existing = latest.get(rid)
        if existing is None or r.get("timestamp", 0) >= existing.get("timestamp", 0):
            latest[rid] = r
    return latest


def validate_ledger_history(records: List[Dict[str, Any]]) -> None:
    """Validate that every reservation in the ledger has legal transitions.

    Walks each reservation's full history (sorted by timestamp) and checks
    every consecutive transition against VALID_TRANSITIONS.  Preserves
    append-only audit: raises on the first malformed history found.
    """
    by_rid: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        rid = r.get("reservation_id")
        if rid is not None:
            by_rid.setdefault(rid, []).append(r)

    for rid, res_records in by_rid.items():
        sorted_records = sorted(res_records, key=lambda x: x.get("timestamp", 0))
        states = [r["state"] for r in sorted_records]

        if not states or states[0] != "reserved":
            raise BrokerError(
                f"Reservation {rid} doesn't start with 'reserved', "
                f"starts with '{states[0] if states else '<empty>'}'"
            )

        for i in range(1, len(states)):
            prev, curr = states[i - 1], states[i]
            allowed = VALID_TRANSITIONS.get(prev, set())
            if curr not in allowed:
                raise BrokerError(
                    f"Reservation {rid}: illegal transition {prev} -> {curr}"
                )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def compute_hash(path: Optional[Path], fallback: bytes = b"") -> str:
    """SHA-256 of file contents, or of fallback bytes if no path."""
    if path is not None:
        data = path.read_bytes()
    else:
        data = fallback
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Budget accounting (transition-aware, static reserved capacity)
# ---------------------------------------------------------------------------


def budget_consuming(record: Dict[str, Any]) -> bool:
    """States that count against budget: reserved, running, succeeded."""
    return record.get("state") in ("reserved", "running", "succeeded")


def count_role_calls(
    records: List[Dict[str, Any]], task_id: str, role: str
) -> int:
    """Count budget-consuming reservations for a task+role.

    Folds records by reservation_id so that a single reservation
    (reserved -> running -> succeeded) counts as one call, not three.
    """
    folded = fold_by_reservation(records, task_id, role)
    return sum(1 for r in folded.values() if budget_consuming(r))


def check_budget(
    records: List[Dict[str, Any]],
    plan: Dict[str, Any],
    role: str,
    stage: str,
) -> None:
    """Validate role/stage against plan budget and review.reserved_for.

    Reserved capacity is static: ``review.reserved_for`` lists stages whose
    capacity is pre-allocated before any call occurs.  Each reserved stage
    gets exactly one slot from the total budget.  Non-reserved stages may
    use only the unreserved remainder (``max_calls - len(reserved_for)``).
    """
    budget = plan["budget"]
    budget_key = f"{role}_calls"
    max_calls = budget.get(budget_key, 0)
    if max_calls <= 0:
        raise BrokerError(
            f"Role '{role}' has zero budget in execution plan"
        )

    folded = fold_by_reservation(records, plan["task_id"], role)
    total_consumed = sum(1 for r in folded.values() if budget_consuming(r))

    reserved_for = plan.get("review", {}).get("reserved_for", [])

    if reserved_for:
        reserved_stages = set(reserved_for)
        num_reserved = len(reserved_stages)
        unreserved_budget = max(0, max_calls - num_reserved)

        if stage in reserved_stages:
            used_reserved_stages = {
                r.get("stage")
                for r in folded.values()
                if budget_consuming(r) and r.get("stage") in reserved_stages
            }
            protected_other_slots = len(
                (reserved_stages - used_reserved_stages) - {stage}
            )
            usable_limit = max_calls - protected_other_slots
            # A repeated call at one reserved stage may use spare unreserved
            # capacity, but can never steal another stage's protected slot.
            if total_consumed >= usable_limit:
                raise BrokerError(
                    f"Stage '{stage}' cannot consume capacity reserved for "
                    f"other stages: {total_consumed}/{usable_limit} usable calls used"
                )
        else:
            # Non-reserved stage (e.g. implementation): may only use the
            # unreserved portion of the budget.
            non_reserved_consumed = sum(
                1
                for r in folded.values()
                if budget_consuming(r) and r.get("stage") not in reserved_for
            )
            if non_reserved_consumed >= unreserved_budget:
                raise BrokerError(
                    f"Stage '{stage}' cannot consume reserved budget for "
                    f"{reserved_for}: unreserved quota exhausted "
                    f"({non_reserved_consumed}/{unreserved_budget})"
                )
    else:
        if total_consumed >= max_calls:
            raise BrokerError(
                f"Budget exhausted for role '{role}': "
                f"{total_consumed}/{max_calls} calls used"
            )


def check_duplicate(
    records: List[Dict[str, Any]],
    task_id: str,
    role: str,
    input_hash: str,
    evidence_hash: str,
    retry_failed: bool,
) -> None:
    """Reject duplicate evidence unless retry_failed authorises a retry.

    Folds records by reservation_id so each reservation is checked once
    (by its latest state).  Default is fail-closed: identical evidence is
    rejected even if the prior reservation failed or was cancelled.
    ``--retry-failed`` may retry only a reservation whose latest state is
    ``failed`` or ``cancelled``; it still creates a new reservation and
    obeys total budget policy.
    """
    folded = fold_by_reservation(records, task_id, role)

    for rid, rec in folded.items():
        if (
            rec.get("input_hash") != input_hash
            or rec.get("evidence_hash") != evidence_hash
        ):
            continue

        latest_state = rec.get("state")
        if latest_state in ("reserved", "running", "succeeded"):
            # Active reservation with same evidence: always deny.
            raise BrokerError(
                f"Duplicate evidence rejected: reservation {rid} "
                f"already exists with same input/evidence hash"
            )
        elif latest_state in ("failed", "cancelled"):
            if not retry_failed:
                # Fail-closed default: deny even when prior attempt failed.
                raise BrokerError(
                    f"Duplicate evidence rejected: reservation {rid} "
                    f"has same input/evidence hash (fail-closed default)"
                )
            # --retry-failed set and latest state is terminal: allow retry.
            return


# ---------------------------------------------------------------------------
# Reservation allocation
# ---------------------------------------------------------------------------


def allocate_reservation(
    records: List[Dict[str, Any]],
    plan: Dict[str, Any],
    role: str,
    stage: str,
    input_hash: str,
    evidence_hash: str,
    run_id: str,
    reservation_id: str,
) -> Dict[str, Any]:
    """Create a 'reserved' record for the ledger. Caller must append it."""
    call_index = count_role_calls(records, plan["task_id"], role) + 1
    record = {
        "schema_version": 1,
        "timestamp": int(time.time()),
        "run_id": run_id,
        "reservation_id": reservation_id,
        "task_id": plan["task_id"],
        "stage": stage,
        "role": role,
        "state": "reserved",
        "call_index": call_index,
        "input_hash": input_hash,
        "evidence_hash": evidence_hash,
    }
    return record


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def run_command(
    command: Sequence[str],
    input_data: Optional[bytes],
    output_path: Optional[Path],
    stderr_path: Optional[Path],
) -> int:
    """Execute the model command directly, without a command shell."""
    out_fh = open(output_path, "wb") if output_path else None
    err_fh = open(stderr_path, "wb") if stderr_path else None

    try:
        result = subprocess.run(
            command,
            input=input_data,
            stdout=out_fh,
            stderr=err_fh,
            shell=False,
        )
        return result.returncode
    finally:
        if output_path and out_fh is not None:
            out_fh.close()
        if stderr_path and err_fh is not None:
            err_fh.close()


# ---------------------------------------------------------------------------
# Main lifecycle
# ---------------------------------------------------------------------------


def execute(
    role: str,
    stage: str,
    plan_path: Optional[Path],
    input_path: Optional[Path],
    evidence_path: Optional[Path],
    output_path: Optional[Path],
    stderr_path: Optional[Path],
    ledger_path: Path,
    run_id: str,
    reservation_id: str,
    dry_run: bool,
    retry_failed: bool,
    max_calls: Optional[int],
    compatibility_task_id: str,
    command: Sequence[str],
) -> int:
    """Full broker lifecycle. Returns child exit code."""
    # 1. Load or generate plan
    if plan_path is not None:
        plan = load_plan(plan_path)
    else:
        plan = make_compatibility_plan(role, max_calls, compatibility_task_id)

    # 2. Hash input and evidence
    input_hash = compute_hash(input_path)
    evidence_hash = compute_hash(evidence_path)

    # 3. Acquire lock and validate
    lock = LedgerLock(ledger_path.with_suffix(".lock"))

    with lock:
        records = load_ledger(ledger_path)
        validate_ledger_history(records)
        check_budget(records, plan, role, stage)
        check_duplicate(
            records, plan["task_id"], role, input_hash, evidence_hash, retry_failed
        )

        if dry_run:
            budget_key = f"{role}_calls"
            folded = fold_by_reservation(records, plan["task_id"], role)
            used = sum(1 for r in folded.values() if budget_consuming(r))
            print(json.dumps({
                "dry_run": True,
                "role": role,
                "stage": stage,
                "task_id": plan["task_id"],
                "input_hash": input_hash,
                "evidence_hash": evidence_hash,
                "budget_used": used,
                "budget_max": plan["budget"].get(budget_key, 0),
                "command": command,
            }, sort_keys=True, indent=2))
            return 0

        # 4. Allocate reservation (append 'reserved' transition)
        reserved_record = allocate_reservation(
            records, plan, role, stage, input_hash, evidence_hash,
            run_id, reservation_id,
        )
        append_ledger(ledger_path, reserved_record)

        # 5. Append 'running' transition
        running_record = {
            **reserved_record,
            "state": "running",
            "timestamp": int(time.time()),
        }
        append_ledger(ledger_path, running_record)

    # 6. Execute command (lock released)
    input_data = input_path.read_bytes() if input_path is not None else None
    start = time.monotonic()

    try:
        exit_code = run_command(command, input_data, output_path, stderr_path)
    except Exception as exc:
        # Record failure
        elapsed = time.monotonic() - start
        with lock:
            fail_record = {
                **running_record,
                "state": "failed",
                "timestamp": int(time.time()),
                "elapsed_seconds": round(elapsed, 3),
                "exit_code": -1,
                "error": str(exc),
            }
            append_ledger(ledger_path, fail_record)
        raise

    elapsed = time.monotonic() - start

    # 7. Record final state
    final_state = "succeeded" if exit_code == 0 else "failed"
    with lock:
        final_record = {
            **running_record,
            "state": final_state,
            "timestamp": int(time.time()),
            "elapsed_seconds": round(elapsed, 3),
            "exit_code": exit_code,
            "output_path": str(output_path) if output_path else None,
            "stderr_path": str(stderr_path) if stderr_path else None,
        }
        append_ledger(ledger_path, final_record)

    return exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="aiwf model-call",
        description="Atomic model-call broker with cross-process quota enforcement",
    )

    # Required
    parser.add_argument(
        "--role", required=True, choices=["claude", "spark", "codex"],
        help="Model role (claude, spark, codex)",
    )
    parser.add_argument("--stage", required=True, help="Execution stage (e.g. builder, final-review)")

    # Plan (optional for compatibility mode)
    parser.add_argument("--plan", type=Path, help="Execution plan JSON (optional)")

    # Input/evidence
    parser.add_argument("--input", type=Path, help="Input file (hashed and passed as stdin)")
    parser.add_argument("--evidence", type=Path, help="Evidence file (hashed for dedup)")

    # Output
    parser.add_argument("--output", type=Path, help="Redirect child stdout to file")
    parser.add_argument("--stderr", type=Path, help="Redirect child stderr to file")

    # Ledger
    parser.add_argument("--ledger", type=Path, default=Path(".ai-workflow/run-ledger.jsonl"),
                        help="Ledger JSONL path (default: .ai-workflow/run-ledger.jsonl)")

    # Run ID
    parser.add_argument("--run-id", help="Run identifier (auto-generated if omitted)")
    parser.add_argument("--reservation-id", help="Reservation identifier (auto-generated if omitted)")
    parser.add_argument(
        "--task-id",
        default="compat-default",
        help="Stable task identifier used only when --plan is omitted",
    )

    # Behavior
    parser.add_argument("--dry-run", action="store_true", help="Validate and inspect without executing")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Allow retry of failed reservation with same evidence (fail-closed by default)")

    # Legacy compatibility
    parser.add_argument("--max-calls", type=int, help="Budget override for compatibility mode (no --plan)")

    # Command
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="Command to execute (after -- separator)")

    args = parser.parse_args()

    # Validate command
    if not args.command or args.command[0] != "--":
        parser.error("Command required after '--' separator")
    command = args.command[1:]
    if not command:
        parser.error("No command specified after '--'")

    # Validate --max-calls requires no --plan
    if args.max_calls is not None and args.plan is not None:
        parser.error("--max-calls cannot be used with --plan")

    # Generate IDs
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:12]}"
    reservation_id = args.reservation_id or f"res-{uuid.uuid4().hex[:12]}"

    try:
        return execute(
            role=args.role,
            stage=args.stage,
            plan_path=args.plan,
            input_path=args.input,
            evidence_path=args.evidence,
            output_path=args.output,
            stderr_path=args.stderr,
            ledger_path=args.ledger,
            run_id=run_id,
            reservation_id=reservation_id,
            dry_run=args.dry_run,
            retry_failed=args.retry_failed,
            max_calls=args.max_calls,
            compatibility_task_id=args.task_id,
            command=command,
        )
    except PlanError as exc:
        print(f"broker: error: {exc}", file=sys.stderr)
        return 3
    except BrokerError as exc:
        print(f"broker: denied: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"broker: error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
