#!/usr/bin/env python3
"""Render a bounded Claude execution capsule from a full Markdown task card.

The full card remains the audit source.  This helper copies only executable
sections and never summarizes or invents requirements.  ``delta`` mode is for
hash-bound same-lineage continuations and deliberately omits repeated routing,
ownership, and planning policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Optional


BOOTSTRAP_SECTIONS = {
    "ID", "Task Mode", "Goal", "Scope", "Context", "Claude Context Packet",
    "Builder Contract", "Batch Builder Gate", "Exploratory Builder Contract",
    "Handoff Contract", "Revision Delta", "Dependency Summary", "Required Revisions", "Required Changes", "Acceptance Criteria",
    "Spec Gate", "Root Cause Gate", "Test-First / TDD Contract",
    "Post-Implementation Contract", "Required Invariants",
    "Testing Responsibility", "Validation Contract", "Temporary File Policy",
    "Stop Conditions", "Files / Modules", "Required Report",
}

DELTA_SECTIONS = {
    "ID", "Task Mode", "Goal", "Scope", "Claude Context Packet", "Revision Delta", "Dependency Summary",
    "Handoff Contract", "Required Revisions", "Required Changes",
    "Acceptance Criteria", "Testing Responsibility", "Validation Contract",
    "Temporary File Policy", "Stop Conditions", "Required Report",
    "Post-Implementation Contract", "Required Invariants",
}

MAX_CHECKPOINT_BYTES = 32 * 1024
MAX_COMPILED_CONTEXT_BYTES = 12 * 1024
MAX_RECOVERY_DELTA_BYTES = 12 * 1024
MAX_REVIEWED_CONTINUATION_BYTES = 64 * 1024
SAFE_COMPILED_CONTEXT_KINDS = frozenset(
    ("procedure", "retrieval", "validation", "output-contract")
)
SAFE_COMPILED_CONTEXT_POLARITIES = frozenset(("positive", "negative"))
RECOVERY_DELTA_SCHEMA = "aiwf-recovery-delta-v1"

# These sections carry constraints that must stay byte-identical when a full
# audit card is compacted into an execution capsule.  They are checked by
# category so an intentionally absent optional section does not make a legacy
# card unusable, while any present hard-contract section can never disappear.
HARD_CONTRACT_GROUPS = {
    "write_boundary": (
        "Scope", "Handoff Contract", "Revision Delta", "Required Changes", "Required Revisions",
    ),
    "acceptance": ("Acceptance Criteria",),
    "validation": ("Validation Contract",),
    "stop_conditions": ("Stop Conditions",),
    "report": ("Required Report",),
}


class CapsuleError(RuntimeError):
    pass


def _sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(re.finditer(r"(?m)^##[ \t]+([^\n]+?)\s*$", text))
    preamble = text[: matches[0].start()] if matches else text
    result: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append((match.group(1).strip(), text[match.start():end].rstrip()))
    return preamble.rstrip(), result


def _atomic_write(path: Path, content: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _hard_contract_coverage(
    source_text: str, capsule_text: str, *, require_complete: bool,
) -> dict[str, object]:
    """Prove that every present hard-contract section survived compaction."""
    _, source_sections = _sections(source_text)
    _, capsule_sections = _sections(capsule_text)
    capsule_bodies: dict[str, list[str]] = {}
    for name, body in capsule_sections:
        capsule_bodies.setdefault(name, []).append(body)

    groups: list[dict[str, object]] = []
    missing_source_categories: list[str] = []
    dropped_sections: list[str] = []
    for category, names in HARD_CONTRACT_GROUPS.items():
        present = [(name, body) for name, body in source_sections if name in names]
        covered: list[str] = []
        dropped: list[str] = []
        for name, body in present:
            if body in capsule_bodies.get(name, []):
                covered.append(name)
            else:
                dropped.append(name)
                dropped_sections.append(name)
        if not present:
            missing_source_categories.append(category)
        groups.append({
            "category": category,
            "source_sections": [name for name, _ in present],
            "covered_sections": covered,
            "dropped_sections": dropped,
            "status": "covered" if present and not dropped else (
                "missing-source" if not present else "dropped"
            ),
        })
    if dropped_sections:
        raise CapsuleError(
            "execution capsule dropped hard-contract sections: {}".format(
                ", ".join(sorted(set(dropped_sections)))
            )
        )
    if require_complete and missing_source_categories:
        raise CapsuleError(
            "task card lacks required hard-contract categories: {}".format(
                ", ".join(missing_source_categories)
            )
        )
    return {
        "status": "complete" if not missing_source_categories else "source-incomplete",
        "require_complete": require_complete,
        "groups": groups,
        "missing_source_categories": missing_source_categories,
        "dropped_sections": [],
    }


def _checkpoint(
    path: Optional[Path], receipt_path: Optional[Path], task_card_sha256: str,
) -> tuple[str, Optional[dict[str, object]]]:
    if path is None:
        if receipt_path is not None:
            raise CapsuleError("rehydration receipt requires a checkpoint")
        return "", None
    raw = path.read_bytes()
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise CapsuleError(
            f"rehydration checkpoint exceeds {MAX_CHECKPOINT_BYTES} bytes"
        )
    text = raw.decode("utf-8", errors="replace").strip()
    if receipt_path is None:
        # Legacy caller-supplied checkpoints remain compatible, but they are
        # explicitly marked unbound in the capsule receipt.
        return text, {"path": str(path.resolve()), "binding": "legacy-unbound"}
    if not text.startswith("<!-- aiwf-context-checkpoint-v1 -->\n## Accepted Context Checkpoint"):
        raise CapsuleError("rehydration checkpoint has an unsupported schema marker")
    if f"Current task-card digest: `{task_card_sha256}`" not in text:
        raise CapsuleError("rehydration checkpoint is not bound to this task card")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CapsuleError("cannot read rehydration checkpoint receipt: {}".format(exc)) from exc
    if not isinstance(receipt, dict):
        raise CapsuleError("rehydration checkpoint receipt must contain an object")
    if receipt.get("schema") != "aiwf-context-checkpoint-v1" or receipt.get("status") != "created":
        raise CapsuleError("rehydration checkpoint receipt has an unsupported schema or status")
    raw_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    if receipt.get("next_task_card_sha256") != task_card_sha256:
        raise CapsuleError("rehydration checkpoint receipt is not bound to this task card")
    if receipt.get("checkpoint_sha256") != raw_sha256 or receipt.get("model_generated") is not False:
        raise CapsuleError("rehydration checkpoint receipt does not match deterministic checkpoint bytes")
    return text, {
        "path": str(path.resolve()),
        "binding": "receipt-bound",
        "sha256": raw_sha256,
        "bytes": len(raw),
        "receipt": str(receipt_path.resolve()),
        "receipt_sha256": "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
    }


def _compiled_context(
    path: Optional[Path], receipt_path: Optional[Path], task_card_sha256: str
) -> tuple[str, Optional[dict[str, object]]]:
    """Load a deterministic compiler packet and prove it belongs to this card."""
    if path is None:
        if receipt_path is not None:
            raise CapsuleError("compiled-context receipt requires compiled context")
        return "", None
    if receipt_path is None:
        raise CapsuleError("compiled context requires its compilation receipt")
    raw = path.read_bytes()
    if len(raw) > MAX_COMPILED_CONTEXT_BYTES:
        raise CapsuleError(
            f"compiled context exceeds {MAX_COMPILED_CONTEXT_BYTES} bytes"
        )
    text = raw.decode("utf-8", errors="replace").strip()
    if "<!-- aiwf-compiled-context-packet-v1 -->" not in text:
        raise CapsuleError("compiled context has an unsupported schema marker")
    if not text.startswith("<!-- aiwf-compiled-context-packet-v1 -->\n## Compiled Execution Guidance"):
        raise CapsuleError("compiled context has an invalid execution guidance header")
    if f"Task-card digest: `{task_card_sha256}`" not in text:
        raise CapsuleError("compiled context is not bound to this task card")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CapsuleError("cannot read compiled-context receipt: {}".format(exc)) from exc
    if not isinstance(receipt, dict):
        raise CapsuleError("compiled-context receipt must contain an object")
    if receipt.get("schema") != "aiwf-context-compilation-v1" or receipt.get("status") != "compiled":
        raise CapsuleError("compiled-context receipt has an unsupported schema or status")
    raw_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    if receipt.get("task_card_sha256") != task_card_sha256:
        raise CapsuleError("compiled-context receipt is not bound to this task card")
    if receipt.get("packet_sha256") != raw_sha256:
        raise CapsuleError("compiled-context receipt does not match packet bytes")
    if receipt.get("hard_contracts_trimmed") is not False or receipt.get("model_generated") is not False:
        raise CapsuleError("compiled-context receipt does not preserve deterministic hard contracts")
    if receipt.get("conflict_free") is not True:
        raise CapsuleError("compiled-context receipt does not prove rule conflicts were checked")
    for field in ("selected", "rescued"):
        entries = receipt.get(field, [])
        if not isinstance(entries, list):
            raise CapsuleError("compiled-context receipt has invalid {} entries".format(field))
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("kind") not in SAFE_COMPILED_CONTEXT_KINDS:
                raise CapsuleError("compiled-context receipt contains an unsafe cue kind")
            if entry.get("polarity", "positive") not in SAFE_COMPILED_CONTEXT_POLARITIES:
                raise CapsuleError("compiled-context receipt contains an unsafe cue polarity")
    return text, {
        "path": str(path.resolve()),
        # Bind the original compiler artifact bytes, not the display-normalized
        # copy that is appended to the capsule.
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "receipt": str(receipt_path.resolve()),
        "receipt_sha256": "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
    }


def _recovery_delta(
    path: Optional[Path], receipt_path: Optional[Path], task_card_sha256: str,
) -> tuple[str, Optional[dict[str, object]]]:
    """Load only a deterministic, classification-bound recovery delta."""
    if path is None:
        if receipt_path is not None:
            raise CapsuleError("recovery-delta receipt requires a recovery delta")
        return "", None
    if receipt_path is None:
        raise CapsuleError("recovery delta requires its receipt")
    raw = path.read_bytes()
    if len(raw) > MAX_RECOVERY_DELTA_BYTES:
        raise CapsuleError("recovery delta exceeds {} bytes".format(MAX_RECOVERY_DELTA_BYTES))
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CapsuleError("cannot read recovery-delta receipt: {}".format(exc)) from exc
    if not isinstance(receipt, dict):
        raise CapsuleError("recovery-delta receipt must contain an object")
    raw_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    if (
        receipt.get("schema") != RECOVERY_DELTA_SCHEMA
        or receipt.get("status") != "created"
        or receipt.get("task_card_sha256") != task_card_sha256
        or receipt.get("delta_sha256") != raw_sha256
        or receipt.get("model_generated") is not False
    ):
        raise CapsuleError("recovery-delta receipt is invalid or does not match its bytes")
    text = raw.decode("utf-8", errors="replace").strip()
    marker = "<!-- {} -->\n## Bounded Recovery Delta".format(RECOVERY_DELTA_SCHEMA)
    if not text.startswith(marker):
        raise CapsuleError("recovery delta has an unsupported schema marker")
    if "Current task-card digest: `{}`".format(task_card_sha256) not in text:
        raise CapsuleError("recovery delta is not bound to this task card")
    failure_class = receipt.get("failure_class")
    if failure_class not in {"model-no-progress", "acknowledgement-only", "report-evidence-mismatch"}:
        raise CapsuleError("recovery-delta receipt has an unsupported failure class")
    return text, {
        "path": str(path.resolve()),
        "sha256": raw_sha256,
        "bytes": len(raw),
        "receipt": str(receipt_path.resolve()),
        "receipt_sha256": "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "failure_class": failure_class,
    }


def _reviewed_continuation(
    path: Optional[Path], task_card_sha256: str,
) -> tuple[str, Optional[dict[str, object]]]:
    """Render only accepted state summaries and unresolved review delta."""
    if path is None:
        return "", None
    raw = path.read_bytes()
    if len(raw) > MAX_REVIEWED_CONTINUATION_BYTES:
        raise CapsuleError("reviewed continuation approval is oversized")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise CapsuleError("reviewed continuation approval is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise CapsuleError("reviewed continuation approval has an unsupported schema")
    if value.get("status") != "available" or value.get("decision") != "accepted-direction":
        raise CapsuleError("reviewed continuation approval is not available")
    expected = task_card_sha256.split(":", 1)[-1]
    if value.get("next_task_card_sha256") != expected:
        raise CapsuleError("reviewed continuation is not bound to this task card")
    delta = value.get("delta_continuation")
    state = value.get("accepted_path_state")
    if not isinstance(delta, dict) or not isinstance(state, dict):
        raise CapsuleError("reviewed continuation lacks accepted delta state")
    if delta.get("full_prior_task_card_repeated") is not False:
        raise CapsuleError("reviewed continuation repeats the prior task card")

    def clean(item: object) -> str:
        return re.sub(r"\s+", " ", str(item)).strip()

    lines = [
        "<!-- aiwf-reviewed-continuation-delta-v1 -->",
        "## Accepted Continuation Context",
        "- Prior task: `{}`".format(clean(value.get("prior_task_id", "unknown"))),
        "- Accepted baseline: `{}`".format(
            clean(delta.get("baseline_worktree_state_hash", "unknown"))
        ),
        "- Reuse the accepted file state below; do not re-explore it unless a hash mismatch is observed.",
    ]
    for name in sorted(state):
        item = state[name] if isinstance(state[name], dict) else {}
        evidence = item.get("sha256") or item.get("kind") or "unknown"
        lines.append("  - `{}`: `{}`".format(clean(name), clean(evidence)))
    findings = delta.get("unresolved_findings")
    findings = findings if isinstance(findings, list) else []
    lines.extend(["", "## Unresolved Review Findings"])
    lines.extend("- {}".format(clean(item)) for item in findings)
    if not findings:
        lines.append("- None recorded; execute only the current task-card delta.")
    refs = delta.get("new_validation_refs")
    refs = refs if isinstance(refs, list) else []
    lines.extend(["", "## New Validation Evidence"])
    lines.extend("- `{}`".format(clean(item)) for item in refs)
    if not refs:
        lines.append("- None supplied.")
    text = "\n".join(lines)
    return text, {
        "path": str(path.resolve()),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "accepted_path_count": len(state),
        "unresolved_finding_count": len(findings),
        "new_validation_ref_count": len(refs),
        "full_prior_task_card_repeated": False,
    }


def render(
    text: str,
    *,
    mode: str,
    continuation_kind: str,
    checkpoint: str,
    compiled_context: str = "",
    recovery_delta: str = "",
    reviewed_continuation: str = "",
) -> str:
    preamble, sections = _sections(text)
    keep = DELTA_SECTIONS if mode == "delta" else BOOTSTRAP_SECTIONS
    selected = [body for name, body in sections if name in keep]
    if not selected:
        raise CapsuleError("task card has no executable sections")
    if not any(name in {"Goal", "Handoff Contract", "Required Changes", "Required Revisions"}
               for name, _ in sections if name in keep):
        raise CapsuleError("capsule lacks Goal/Handoff/Required Changes")

    header = [
        "<!-- AIWF execution capsule v1; TASK_CARD_FULL.md remains the audit source. -->",
        f"<!-- mode={mode}; continuation_kind={continuation_kind} -->",
        "<!-- Bounded execution view: executable contract sections only. -->",
        "",
    ]
    if preamble:
        # Keep only the title/non-section identity, never arbitrary long preamble.
        title_lines = [line for line in preamble.splitlines() if line.strip()][:8]
        header.extend(title_lines)
        header.append("")
    body = "\n\n".join(selected)
    if checkpoint:
        # Deterministic checkpoints already carry their schema marker and
        # heading.  Do not wrap them again: duplicate headings both waste
        # prompt bytes and obscure the receipt-bound boundary.  Legacy
        # caller-supplied text remains accepted as an explicitly unbound
        # compatibility path.
        body += "\n\n" + checkpoint
    if recovery_delta:
        body += "\n\n" + recovery_delta
    if reviewed_continuation:
        body += "\n\n" + reviewed_continuation
    if compiled_context:
        body += "\n\n" + compiled_context
    return "\n".join(header) + body.rstrip() + "\n"


def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--task-card", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--mode", choices=("bootstrap", "delta"), default="bootstrap")
    result.add_argument(
        "--continuation-kind",
        choices=("initial", "next-slice", "revision", "checker-followup"),
        default="initial",
    )
    result.add_argument("--rehydrate-from", type=Path)
    result.add_argument("--rehydrate-receipt", type=Path)
    result.add_argument("--compiled-context", type=Path)
    result.add_argument("--compiled-context-receipt", type=Path)
    result.add_argument("--recovery-delta", type=Path)
    result.add_argument("--recovery-delta-receipt", type=Path)
    result.add_argument("--reviewed-continuation", type=Path)
    result.add_argument(
        "--require-complete-contract", action="store_true",
        help="fail if any hard-contract category is absent from the source card",
    )
    result.add_argument("--receipt", type=Path)
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        source = args.task_card.resolve()
        if not source.is_file():
            raise CapsuleError(f"task card not found: {source}")
        task_card_sha256 = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        checkpoint, checkpoint_receipt = _checkpoint(
            args.rehydrate_from, args.rehydrate_receipt, task_card_sha256
        )
        compiled_context, compiled_context_receipt = _compiled_context(
            args.compiled_context, args.compiled_context_receipt, task_card_sha256
        )
        recovery_delta, recovery_delta_receipt = _recovery_delta(
            args.recovery_delta, args.recovery_delta_receipt, task_card_sha256
        )
        reviewed_continuation, reviewed_continuation_receipt = _reviewed_continuation(
            args.reviewed_continuation, task_card_sha256
        )
        source_text = source.read_text(encoding="utf-8", errors="replace")
        contract_content = render(
            source_text,
            mode=args.mode,
            continuation_kind=args.continuation_kind,
            checkpoint="",
            compiled_context="",
            recovery_delta="",
            reviewed_continuation="",
        )
        content = render(
            source_text,
            mode=args.mode,
            continuation_kind=args.continuation_kind,
            checkpoint=checkpoint,
            compiled_context=compiled_context,
            recovery_delta=recovery_delta,
            reviewed_continuation=reviewed_continuation,
        )
        hard_contract_coverage = _hard_contract_coverage(
            source_text, contract_content, require_complete=args.require_complete_contract,
        )
        _atomic_write(args.output, content)
        receipt = {
            "schema": "aiwf-execution-capsule-v1",
            "mode": args.mode,
            "continuation_kind": args.continuation_kind,
            "task_card": str(source),
            "task_card_sha256": task_card_sha256,
            "checkpoint": str(args.rehydrate_from.resolve()) if args.rehydrate_from else None,
            "checkpoint_binding": checkpoint_receipt,
            "compiled_context": compiled_context_receipt,
            "recovery_delta": recovery_delta_receipt,
            "reviewed_continuation": reviewed_continuation_receipt,
            "hard_contract_coverage": hard_contract_coverage,
            "output": str(args.output.resolve()),
            "output_sha256": _sha256(content),
            "output_bytes": len(content.encode("utf-8")),
        }
        if args.receipt:
            _atomic_write(args.receipt, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (CapsuleError, OSError, UnicodeError) as exc:
        print(f"execution-capsule: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
