"""Shared bounded evidence-capsule helpers.

Full evidence remains file-backed.  Capsules contain only stable identities,
counts, bounded selectors, and an optional advisory Spark tool request.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


CAPSULE_SCHEMA_VERSION = 1
DEFAULT_CAPSULE_MAX_BYTES = 4096
DEFAULT_SELECTOR_ITEMS = 12
MIN_SPARK_SAVED_BYTES = 8192


def compact_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def artifact_ref(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size if resolved.is_file() else None,
        "sha256": sha256_file(resolved),
    }


def repository_head(start: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "HEAD"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def bounded_selector(values: Iterable[Any], limit: int = DEFAULT_SELECTOR_ITEMS) -> dict[str, Any]:
    items = list(values)
    encoded = compact_bytes({"items": items})
    return {
        "items": items[:limit],
        "total_count": len(items),
        "omitted_count": max(0, len(items) - limit),
        "sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }


def bounded_text(value: Any, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _compression_route(
    *, kind: str, full_bytes: int, reason_codes: list[str], capsule_target_bytes: int,
) -> dict[str, Any]:
    saved = max(0, full_bytes - capsule_target_bytes)
    recommended = bool(reason_codes) and saved >= MIN_SPARK_SAVED_BYTES
    return {
        "strategy": "spark-optional" if recommended else "local-capsule",
        "spark_recommended": recommended,
        "spark_mode": "postflight-bundle" if recommended else None,
        "reason_codes": reason_codes,
        "estimated_full_bytes": full_bytes,
        "estimated_capsule_bytes": capsule_target_bytes,
        "estimated_codex_bytes_saved": saved,
        "minimum_savings_for_spark": MIN_SPARK_SAVED_BYTES,
        "evidence_kind": kind,
        "advisory_only": True,
        "codex_semantic_review_required": True,
        "spark_can_authorize_acceptance": False,
    }


def acceptance_compression_route(
    value: dict[str, Any], full_bytes: int,
    capsule_target_bytes: int = DEFAULT_CAPSULE_MAX_BYTES,
) -> dict[str, Any]:
    reasons: list[str] = []
    if len(value.get("changed_paths", [])) >= 8:
        reasons.append("many-changed-paths")
    if len(value.get("acceptance_index", [])) >= 10:
        reasons.append("many-acceptance-items")
    if len(value.get("review_selection", {}).get("expanded_acceptance_ids", [])) >= 6:
        reasons.append("many-expanded-acceptance-items")
    if len(value.get("unresolved_risks", [])) >= 6:
        reasons.append("many-unresolved-risks")
    if len(value.get("frozen_invariants", [])) >= 12:
        reasons.append("many-invariants")
    if len(value.get("changed_symbols", [])) >= 20:
        reasons.append("many-changed-symbols")
    return _compression_route(
        kind="acceptance-bundle", full_bytes=full_bytes, reason_codes=reasons,
        capsule_target_bytes=capsule_target_bytes,
    )


def review_compression_route(
    packet: dict[str, Any], full_bytes: int,
    capsule_target_bytes: int = DEFAULT_CAPSULE_MAX_BYTES,
) -> dict[str, Any]:
    reasons: list[str] = []
    if packet.get("prompt_bytes", 0) >= 12_288:
        reasons.append("large-review-prompt")
    if len(packet.get("diff_hunks", [])) >= 12:
        reasons.append("many-diff-hunks")
    if len(packet.get("changed_files", [])) >= 8:
        reasons.append("many-changed-files")
    if len(packet.get("omitted_evidence", [])) >= 10:
        reasons.append("many-omitted-artifacts")
    if len(packet.get("failures", [])) >= 8:
        reasons.append("many-failure-signals")
    return _compression_route(
        kind="review-packet", full_bytes=full_bytes, reason_codes=reasons,
        capsule_target_bytes=capsule_target_bytes,
    )


def spark_tool_request(task_card: Path, artifact: Path) -> dict[str, Any]:
    task_ref = artifact_ref(task_card)
    input_ref = artifact_ref(artifact)
    return {
        "argv": [
            "bash", "ai/run-codex-spark.sh", str(task_card.resolve()),
            "--mode", "postflight-bundle",
            "--artifact", str(artifact.resolve()),
            "--result-mode", "direct",
            "--execution-env", "host",
        ],
        "task_card": task_ref,
        "input_artifact": input_ref,
        "result_contract": "advisory-summary-only",
    }


def finalize_capsule(
    value: dict[str, Any], max_bytes: int = DEFAULT_CAPSULE_MAX_BYTES,
) -> dict[str, Any]:
    value = dict(value)
    metrics = dict(value.get("transfer_metrics", {}))
    value["transfer_metrics"] = metrics
    metrics["capsule_max_bytes"] = max_bytes
    metrics["within_limit"] = True
    for _ in range(8):
        measured = len(compact_bytes(value))
        if metrics.get("capsule_bytes") == measured:
            break
        metrics["capsule_bytes"] = measured
    if metrics["capsule_bytes"] > max_bytes:
        raise ValueError(
            "evidence capsule exceeds hard limit: "
            f"{metrics['capsule_bytes']} > {max_bytes} bytes"
        )
    return value
