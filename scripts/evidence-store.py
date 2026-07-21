#!/usr/bin/env python3
"""Store, read, reference, and measure content-addressed Evidence Objects."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_store import (  # noqa: E402
    EvidenceStoreError, build_object, build_reference_packet,
    MAX_METRICS_BYTES, default_metrics_path, empty_metrics, load_object,
    record_receiver_access, read_json_bounded, reference_for, store_object,
    validate_metrics,
)
from workflow_state import atomic_write_json  # noqa: E402


DEFAULT_MAX_OBJECT_BYTES = 4 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_REFS_FILE_BYTES = 4 * 1024 * 1024
MAX_PACKET_REFS = 4096


def receipt(args, object_id, metrics_path, *, readable=True):
    if not args.receiver:
        return None
    access = record_receiver_access(
        metrics_path, args.receiver, object_id, readable=readable,
    )
    value = {
        "schema_version": 1,
        "receiver": args.receiver,
        "object_id": object_id,
        "cache_outcome": access["outcome"],
        "metrics_path": str(metrics_path),
    }
    if getattr(args, "receipt_output", None):
        atomic_write_json(args.receipt_output, value)
    return value


def put_command(args):
    if args.content.stat().st_size > args.max_bytes:
        raise EvidenceStoreError(f"content exceeds {args.max_bytes} byte limit")
    raw = args.content.read_bytes()
    if len(raw) > args.max_bytes:
        raise EvidenceStoreError(f"content exceeds {args.max_bytes} byte limit")
    metadata = read_json_bounded(args.metadata, MAX_METADATA_BYTES, "Evidence metadata")
    obj = build_object(metadata, raw, args.encoding)
    path, cache_hit = store_object(args.store, obj)
    print(json.dumps({
        "status": "reused" if cache_hit else "stored",
        "object_id": obj["object_id"],
        "object_path": str(path),
        "content_hash": obj["content_hash"],
        "content_bytes": obj["content_bytes"],
        "store_cache_hit": cache_hit,
    }, sort_keys=True))
    return 0


def read_command(args):
    metrics_path = args.metrics or default_metrics_path(args.store)
    try:
        obj = load_object(args.store, args.object_id)
    except EvidenceStoreError:
        receipt(args, args.object_id, metrics_path, readable=False)
        raise
    access_receipt = receipt(args, args.object_id, metrics_path)
    if args.format == "object":
        output = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    elif args.format == "reference":
        output = json.dumps(reference_for(obj), ensure_ascii=False, sort_keys=True) + "\n"
    else:
        encoding = obj["content"]["encoding"]
        if encoding == "utf-8":
            output = obj["content"]["value"]
        elif encoding == "json":
            output = json.dumps(obj["content"]["value"], ensure_ascii=False, sort_keys=True) + "\n"
        else:
            output = obj["content"]["value"] + "\n"
    sys.stdout.write(output)
    if access_receipt and not args.receipt_output:
        print(json.dumps(access_receipt, sort_keys=True), file=sys.stderr)
    return 0


def packet_command(args):
    object_ids = list(args.object_id)
    if args.refs_file:
        value = read_json_bounded(args.refs_file, MAX_REFS_FILE_BYTES, "reference file")
        if isinstance(value, list):
            object_ids.extend(value)
        elif isinstance(value, dict) and isinstance(value.get("object_refs"), list):
            for index, item in enumerate(value["object_refs"]):
                if isinstance(item, dict):
                    object_id = item.get("object_id")
                    if not isinstance(object_id, str):
                        raise EvidenceStoreError(
                            f"object_refs[{index}].object_id must be a string"
                        )
                    object_ids.append(object_id)
                else:
                    object_ids.append(item)
        else:
            raise EvidenceStoreError("refs file must be an object_id array or reference packet")
    if not all(isinstance(item, str) for item in object_ids):
        raise EvidenceStoreError("all object references must be strings")
    if len(object_ids) > MAX_PACKET_REFS:
        raise EvidenceStoreError(f"reference packet exceeds {MAX_PACKET_REFS} object limit")
    packet = build_reference_packet(args.store, object_ids)
    atomic_write_json(args.output, packet)
    print(json.dumps({
        "status": "built", "output": str(args.output),
        "object_count": len(packet["object_refs"]),
        "inline_content": False,
        "total_content_bytes": packet["total_content_bytes"],
    }, sort_keys=True))
    return 0


def stats_command(args):
    metrics_path = args.metrics or default_metrics_path(args.store)
    if metrics_path.exists() and metrics_path.is_symlink():
        raise EvidenceStoreError("receiver metrics path must not be a symlink")
    if metrics_path.exists():
        metrics = read_json_bounded(metrics_path, MAX_METRICS_BYTES, "receiver metrics")
    else:
        metrics = empty_metrics()
    errors = validate_metrics(metrics)
    if errors:
        raise EvidenceStoreError("; ".join(errors))
    known_reads = metrics["total_hits"] + metrics["total_misses"]
    metrics["cache_hit_rate"] = (
        metrics["total_hits"] / known_reads if known_reads else None
    )
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    put = sub.add_parser("put")
    put.add_argument("--store", type=Path, default=Path(".ai-workflow/objects"))
    put.add_argument("--metadata", type=Path, required=True)
    put.add_argument("--content", type=Path, required=True)
    put.add_argument("--encoding", choices=("utf-8", "base64", "json"), default="utf-8")
    put.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_OBJECT_BYTES)
    put.set_defaults(handler=put_command)

    read = sub.add_parser("read")
    read.add_argument("--store", type=Path, default=Path(".ai-workflow/objects"))
    read.add_argument("--object-id", required=True)
    read.add_argument("--format", choices=("content", "object", "reference"), default="content")
    read.add_argument("--receiver")
    read.add_argument("--metrics", type=Path)
    read.add_argument("--receipt-output", type=Path)
    read.set_defaults(handler=read_command)

    packet = sub.add_parser("packet")
    packet.add_argument("--store", type=Path, default=Path(".ai-workflow/objects"))
    packet.add_argument("--object-id", action="append", default=[])
    packet.add_argument("--refs-file", type=Path)
    packet.add_argument("--output", type=Path, required=True)
    packet.set_defaults(handler=packet_command)

    stats = sub.add_parser("stats")
    stats.add_argument("--store", type=Path, default=Path(".ai-workflow/objects"))
    stats.add_argument("--metrics", type=Path)
    stats.set_defaults(handler=stats_command)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if getattr(args, "max_bytes", 1) < 1:
            raise EvidenceStoreError("--max-bytes must be positive")
        return args.handler(args)
    except (OSError, json.JSONDecodeError, EvidenceStoreError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
