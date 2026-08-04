#!/usr/bin/env python3
"""Write or uniquely replace content in one receipt-approved exact file."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys


RUNTIME_PROTOCOL = "aiwf-exact-write-v2"


class ApprovedWriteError(ValueError):
    pass


def _normalized_relative(raw: str) -> str:
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ApprovedWriteError("--path must be a normalized repository-relative path")
    return path.as_posix()


def _approved_staged_file(receipt_path: Path, relative_path: str) -> tuple[str, Path, dict[str, object]]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "ready":
        raise ApprovedWriteError("write-sandbox receipt is not ready")
    relative_path = _normalized_relative(relative_path)
    matches = [
        item for item in receipt.get("bindings", [])
        if isinstance(item, dict) and item.get("relative_path") == relative_path
    ]
    if len(matches) != 1 or matches[0].get("kind") != "file":
        raise ApprovedWriteError(f"path is not one exact approved file: {relative_path}")

    binding = matches[0]
    staged = Path(str(binding.get("source", "")))
    staging_root = Path(str(receipt.get("staging_root", ""))).resolve()
    try:
        staged.resolve(strict=True).relative_to(staging_root)
    except (OSError, ValueError) as exc:
        raise ApprovedWriteError("approved staged file escapes its staging root") from exc
    metadata = staged.lstat()
    if not stat.S_ISREG(metadata.st_mode) or staged.is_symlink() or metadata.st_nlink != 1:
        raise ApprovedWriteError("approved staged file is not a private regular file")

    return relative_path, staged, binding


def _open_private_file(staged: Path) -> int:
    flags = os.O_RDWR
    # os.open() file descriptors inherit text-mode translation on Windows
    # unless O_BINARY is requested.  The approved writer operates on bytes, so
    # translating an existing CRLF sequence would corrupt it into CRCRLF.
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(staged, flags)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        os.close(descriptor)
        raise ApprovedWriteError("approved staged file changed identity before write")
    return descriptor


def _replace_descriptor_content(descriptor: int, content: bytes) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    view = memoryview(content)
    try:
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        raise


def write_approved(receipt_path: Path, relative_path: str, content: bytes) -> dict[str, object]:
    relative_path, staged, binding = _approved_staged_file(receipt_path, relative_path)
    if not binding.get("complete_file_write_allowed"):
        raise ApprovedWriteError(
            "complete-file replacement is allowed only for new files or an explicit "
            "Full file replacement paths declaration; use unique-fragment-replacement"
        )
    descriptor = _open_private_file(staged)
    try:
        _replace_descriptor_content(descriptor, content)
    finally:
        os.close(descriptor)
    return {
        "status": "written",
        "operation": "complete-file",
        "relative_path": relative_path,
        "bytes": len(content),
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
    }


def replace_unique_approved(
    receipt_path: Path, relative_path: str, old: bytes, new: bytes,
    max_bytes: int = 16 * 1024 * 1024,
) -> dict[str, object]:
    if not old:
        raise ApprovedWriteError("unique replacement requires a non-empty old fragment")
    relative_path, staged, _binding = _approved_staged_file(receipt_path, relative_path)
    descriptor = _open_private_file(staged)
    try:
        size = os.fstat(descriptor).st_size
        if size > max_bytes:
            raise ApprovedWriteError("approved file exceeds --max-bytes")
        current = bytearray()
        while len(current) < size:
            chunk = os.read(descriptor, min(1024 * 1024, size - len(current)))
            if not chunk:
                break
            current.extend(chunk)
        current_bytes = bytes(current)
        matches = current_bytes.count(old)
        if matches != 1:
            raise ApprovedWriteError(
                f"old fragment must match exactly once; observed {matches} matches"
            )
        content = current_bytes.replace(old, new, 1)
        if len(content) > max_bytes:
            raise ApprovedWriteError("replacement result exceeds --max-bytes")
        _replace_descriptor_content(descriptor, content)
    finally:
        os.close(descriptor)
    return {
        "status": "written",
        "operation": "unique-fragment-replacement",
        "relative_path": relative_path,
        "matches": 1,
        "bytes": len(content),
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        "old_sha256": "sha256:" + hashlib.sha256(old).hexdigest(),
        "new_sha256": "sha256:" + hashlib.sha256(new).hexdigest(),
    }


def probe_approved(receipt_path: Path, relative_path: str) -> dict[str, object]:
    """Prove that the receipt-selected staging file is writable without changing it."""
    relative_path, staged, _binding = _approved_staged_file(receipt_path, relative_path)
    descriptor = _open_private_file(staged)
    try:
        size = os.fstat(descriptor).st_size
        if size:
            os.lseek(descriptor, size - 1, os.SEEK_SET)
            final_byte = os.read(descriptor, 1)
            os.lseek(descriptor, size - 1, os.SEEK_SET)
            os.write(descriptor, final_byte)
        else:
            os.write(descriptor, b"\0")
            os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {
        "status": "ready",
        "operation": "write-probe",
        "relative_path": relative_path,
        "bytes": size,
    }


def _decode_base64(label: str, value: str) -> bytes:
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ApprovedWriteError(f"--{label} must be canonical base64") from exc
    if base64.b64encode(decoded) != encoded:
        raise ApprovedWriteError(f"--{label} must be canonical base64")
    return decoded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-protocol",
        action="version",
        version=RUNTIME_PROTOCOL,
        help="print the exact-write runtime protocol and exit",
    )
    parser.add_argument(
        "--receipt", type=Path,
        help="write-scope receipt; defaults to AI_WORKFLOW_WRITE_SCOPE_RECEIPT",
    )
    parser.add_argument("--path", required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source", type=Path)
    source.add_argument("--stdin", action="store_true")
    source.add_argument("--content-base64")
    source.add_argument("--replace-old-source", type=Path)
    source.add_argument("--replace-old-base64")
    source.add_argument("--probe", action="store_true")
    parser.add_argument("--replace-new-source", type=Path)
    parser.add_argument("--replace-new-base64")
    parser.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    args = parser.parse_args()
    try:
        receipt_value = args.receipt or os.environ.get("AI_WORKFLOW_WRITE_SCOPE_RECEIPT")
        if not receipt_value:
            raise ApprovedWriteError(
                "--receipt or AI_WORKFLOW_WRITE_SCOPE_RECEIPT is required"
            )
        receipt = Path(receipt_value).resolve()
        if args.max_bytes <= 0:
            raise ApprovedWriteError("--max-bytes must be positive")
        if args.replace_new_source and not args.replace_old_source:
            raise ApprovedWriteError("--replace-new-source requires --replace-old-source")
        if args.replace_new_base64 is not None and args.replace_old_base64 is None:
            raise ApprovedWriteError(
                "--replace-new-base64 requires --replace-old-base64"
            )
        if args.replace_old_source and args.replace_new_base64 is not None:
            raise ApprovedWriteError("source and base64 replacement modes cannot be mixed")
        if args.replace_old_base64 is not None and args.replace_new_source:
            raise ApprovedWriteError("source and base64 replacement modes cannot be mixed")
        if args.replace_old_source:
            if not args.replace_new_source:
                raise ApprovedWriteError("--replace-old-source requires --replace-new-source")
            for label, path in (("old", args.replace_old_source), ("new", args.replace_new_source)):
                if path.is_symlink() or not path.is_file():
                    raise ApprovedWriteError(f"--replace-{label}-source must be a regular non-symlink file")
            old = args.replace_old_source.read_bytes()
            new = args.replace_new_source.read_bytes()
            if len(old) > args.max_bytes or len(new) > args.max_bytes:
                raise ApprovedWriteError("replacement fragment exceeds --max-bytes")
            result = replace_unique_approved(
                receipt, args.path, old, new, args.max_bytes
            )
        elif args.replace_old_base64 is not None:
            if args.replace_new_base64 is None:
                raise ApprovedWriteError(
                    "--replace-old-base64 requires --replace-new-base64"
                )
            old = _decode_base64("replace-old-base64", args.replace_old_base64)
            new = _decode_base64("replace-new-base64", args.replace_new_base64)
            if len(old) > args.max_bytes or len(new) > args.max_bytes:
                raise ApprovedWriteError("replacement fragment exceeds --max-bytes")
            result = replace_unique_approved(
                receipt, args.path, old, new, args.max_bytes
            )
        elif args.probe:
            result = probe_approved(receipt, args.path)
        elif args.source:
            if args.source.is_symlink() or not args.source.is_file():
                raise ApprovedWriteError("--source must be a regular non-symlink file")
            content = args.source.read_bytes()
        elif args.stdin:
            content = sys.stdin.buffer.read(args.max_bytes + 1)
        elif args.content_base64 is not None:
            content = _decode_base64("content-base64", args.content_base64)
        else:
            raise ApprovedWriteError(
                "one write mode is required"
            )
        if not args.replace_old_source and args.replace_old_base64 is None and not args.probe:
            if len(content) > args.max_bytes:
                raise ApprovedWriteError("replacement content exceeds --max-bytes")
            result = write_approved(receipt, args.path, content)
    except (ApprovedWriteError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"approved write: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
