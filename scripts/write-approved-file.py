#!/usr/bin/env python3
"""Write or uniquely replace content in one receipt-approved exact file."""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import symtable
import sys
from typing import Optional

try:
    import tomllib
except ImportError:  # Python 3.9/3.10 compatibility; fail closed for TOML writes.
    tomllib = None


RUNTIME_PROTOCOL = "aiwf-exact-write-v3"
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
LARGE_FRAGMENT_MIN_BYTES = 4096
LARGE_FRAGMENT_MAX_FRACTION = 0.75
ABNORMAL_GROWTH_MIN_BASE_LINES = 20
ABNORMAL_GROWTH_ABSOLUTE_ALLOWANCE = 400
ABNORMAL_GROWTH_MULTIPLIER = 3


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


def _read_descriptor_content(descriptor: int, max_bytes: int) -> bytes:
    size = os.fstat(descriptor).st_size
    if size > max_bytes:
        raise ApprovedWriteError("approved file exceeds --max-bytes")
    os.lseek(descriptor, 0, os.SEEK_SET)
    current = bytearray()
    while len(current) < size:
        chunk = os.read(descriptor, min(1024 * 1024, size - len(current)))
        if not chunk:
            break
        current.extend(chunk)
    if len(current) != size:
        raise ApprovedWriteError("approved file changed size while preparing candidate")
    return bytes(current)


def _transactional_descriptor_write(
    descriptor: int, previous: bytes, candidate: bytes,
) -> None:
    try:
        _replace_descriptor_content(descriptor, candidate)
    except Exception as write_error:
        try:
            _replace_descriptor_content(descriptor, previous)
        except Exception as rollback_error:
            raise ApprovedWriteError(
                "candidate write failed and rollback could not restore the checkpoint: "
                f"{rollback_error}"
            ) from write_error
        raise ApprovedWriteError(
            "candidate write failed; the previous checkpoint was restored"
        ) from write_error


def _annotation_root_name(node: ast.expr) -> str:
    while isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _literal_true(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _literal_false(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _dataclass_decorator(node: ast.ClassDef) -> Optional[ast.expr]:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if _call_name(target) == "dataclass":
            return decorator
    return None


def _field_call_options(value: Optional[ast.expr]) -> tuple[bool, bool, bool]:
    """Return has_default, participates_in_init, keyword_only."""
    if not isinstance(value, ast.Call) or _call_name(value.func) != "field":
        return value is not None, True, False
    options = {item.arg: item.value for item in value.keywords if item.arg}
    has_default = "default" in options or "default_factory" in options
    participates = not (
        "init" in options and _literal_false(options["init"])
    )
    keyword_only = "kw_only" in options and _literal_true(options["kw_only"])
    return has_default, participates, keyword_only


def _validate_dataclass_field_order(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        decorator = _dataclass_decorator(node)
        if decorator is None:
            continue
        if isinstance(decorator, ast.Call):
            options = {item.arg: item.value for item in decorator.keywords if item.arg}
            if "kw_only" in options and _literal_true(options["kw_only"]):
                continue
        default_seen = False
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                continue
            if _annotation_root_name(statement.annotation) == "ClassVar":
                continue
            has_default, participates, keyword_only = _field_call_options(statement.value)
            if not participates or keyword_only:
                continue
            if default_seen and not has_default:
                raise ApprovedWriteError(
                    "candidate validation failed: dataclass "
                    f"{node.name!r} has non-default field {statement.target.id!r} "
                    "after a default field"
                )
            default_seen = default_seen or has_default


def _top_level_definitions(tree: ast.Module) -> Counter[str]:
    return Counter(
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )


def _top_level_imports(tree: ast.Module) -> Counter[tuple[object, ...]]:
    values: Counter[tuple[object, ...]] = Counter()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                values[("import", alias.name, alias.asname)] += 1
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                values[("from", node.level, node.module, alias.name, alias.asname)] += 1
    return values


def _main_guard_count(tree: ast.Module) -> int:
    count = 0
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.ops) != 1:
            continue
        if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
            continue
        if not isinstance(test.ops[0], ast.Eq) or len(test.comparators) != 1:
            continue
        value = test.comparators[0]
        if isinstance(value, ast.Constant) and value.value == "__main__":
            count += 1
    return count


def _secondary_module_string_count(tree: ast.Module) -> int:
    return sum(
        1 for index, node in enumerate(tree.body)
        if index > 0 and isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _validate_python_file_boundaries(
    previous_source: str, candidate_source: str,
    previous_tree: Optional[ast.Module], candidate_tree: ast.Module,
) -> list[str]:
    """Reject common whole-module concatenation signatures."""
    candidate_lines = candidate_source.splitlines()
    previous_lines = previous_source.splitlines()
    internal_headers = [
        index for index, line in enumerate(candidate_lines, 1)
        if (line.startswith("#!") and index != 1)
        or (
            re.search(r"^[ \t]*#.*coding[:=][ \t]*[-\w.]+", line)
            and index > 2
        )
    ]
    if internal_headers:
        raise ApprovedWriteError(
            "candidate validation failed: internal module header at lines "
            + ", ".join(str(value) for value in internal_headers[:8])
        )
    previous_main_guards = _main_guard_count(previous_tree) if previous_tree else 0
    candidate_main_guards = _main_guard_count(candidate_tree)
    if candidate_main_guards > 1 and candidate_main_guards > previous_main_guards:
        raise ApprovedWriteError(
            "candidate validation failed: newly duplicated module entry point"
        )
    previous_secondary_strings = (
        _secondary_module_string_count(previous_tree) if previous_tree else 0
    )
    candidate_secondary_strings = _secondary_module_string_count(candidate_tree)
    if (
        candidate_secondary_strings > previous_secondary_strings
        and candidate_secondary_strings > 0
    ):
        raise ApprovedWriteError(
            "candidate validation failed: secondary module-level string suggests "
            "a concatenated file boundary"
        )
    if len(previous_lines) >= ABNORMAL_GROWTH_MIN_BASE_LINES:
        maximum = max(
            len(previous_lines) + ABNORMAL_GROWTH_ABSOLUTE_ALLOWANCE,
            len(previous_lines) * ABNORMAL_GROWTH_MULTIPLIER,
        )
        if len(candidate_lines) > maximum:
            raise ApprovedWriteError(
                "candidate validation failed: abnormal line-count growth "
                f"({len(previous_lines)} -> {len(candidate_lines)}; maximum {maximum})"
            )
    return [
        "python-module-header-boundary",
        "python-single-module-entry-point",
        "python-no-secondary-module-string",
        "python-bounded-line-growth",
    ]


def _module_import_bindings(tree: ast.Module) -> set[str]:
    bindings: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    bindings.add(alias.asname or alias.name)
    return bindings


def _global_references(table: symtable.SymbolTable) -> set[str]:
    references = {
        symbol.get_name() for symbol in table.get_symbols()
        if symbol.is_referenced() and (
            table.get_type() == "module" or symbol.is_global()
        )
    }
    for child in table.get_children():
        references.update(_global_references(child))
    return references


def _reject_removed_used_imports(
    previous_tree: ast.Module, candidate_tree: ast.Module,
    candidate_source: str, relative_path: str,
) -> None:
    removed = _module_import_bindings(previous_tree).difference(
        _module_import_bindings(candidate_tree)
    )
    if not removed:
        return
    table = symtable.symtable(candidate_source, relative_path, "exec")
    global_references = _global_references(table)
    module_symbols = {symbol.get_name(): symbol for symbol in table.get_symbols()}
    missing: list[str] = []
    for name in sorted(removed.intersection(global_references)):
        symbol = module_symbols.get(name)
        if symbol is not None and (
            symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
        ):
            continue
        missing.append(name)
    if missing:
        raise ApprovedWriteError(
            "candidate validation failed: removed imports remain globally referenced: "
            + ", ".join(missing)
        )


def _reject_new_duplicates(
    label: str, previous: Counter[object], candidate: Counter[object],
) -> None:
    introduced = [
        value for value, count in candidate.items()
        if count > 1 and count > previous.get(value, 0)
    ]
    if introduced:
        rendered = ", ".join(repr(value) for value in sorted(introduced, key=repr))
        raise ApprovedWriteError(
            f"candidate validation failed: newly duplicated top-level {label}: {rendered}"
        )


def _validate_candidate(
    relative_path: str, previous: bytes, candidate: bytes,
) -> dict[str, object]:
    suffix = PurePosixPath(relative_path).suffix.lower()
    checks: list[str] = []
    if suffix in {".py", ".pyi"}:
        try:
            source = candidate.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ApprovedWriteError(
                "candidate validation failed: Python source must be UTF-8"
            ) from exc
        try:
            candidate_tree = ast.parse(source, filename=relative_path)
            compile(candidate_tree, relative_path, "exec", dont_inherit=True)
        except SyntaxError as exc:
            location = f"line {exc.lineno}" if exc.lineno else "unknown line"
            raise ApprovedWriteError(
                f"candidate validation failed: Python syntax error at {location}: {exc.msg}"
            ) from exc
        checks.extend(("python-ast", "python-compile"))
        _validate_dataclass_field_order(candidate_tree)
        checks.append("python-dataclass-field-order")
        try:
            previous_tree = ast.parse(previous.decode("utf-8"), filename=relative_path)
        except (SyntaxError, UnicodeDecodeError):
            previous_tree = None
        try:
            previous_source = previous.decode("utf-8")
        except UnicodeDecodeError:
            previous_source = ""
        checks.extend(_validate_python_file_boundaries(
            previous_source, source, previous_tree, candidate_tree,
        ))
        if previous_tree is not None:
            _reject_new_duplicates(
                "definitions",
                _top_level_definitions(previous_tree),
                _top_level_definitions(candidate_tree),
            )
            _reject_new_duplicates(
                "imports",
                _top_level_imports(previous_tree),
                _top_level_imports(candidate_tree),
            )
            _reject_removed_used_imports(
                previous_tree, candidate_tree, source, relative_path
            )
            checks.extend((
                "python-no-new-duplicate-definitions",
                "python-no-new-duplicate-imports",
                "python-no-removed-used-imports",
            ))
    elif suffix == ".json":
        try:
            json.loads(candidate.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApprovedWriteError(
                f"candidate validation failed: invalid JSON: {exc}"
            ) from exc
        checks.append("json-parse")
    elif suffix == ".toml":
        if tomllib is None:
            raise ApprovedWriteError(
                "candidate validation failed: TOML parser unavailable; use Python 3.11+"
            )
        try:
            tomllib.loads(candidate.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ApprovedWriteError(
                f"candidate validation failed: invalid TOML: {exc}"
            ) from exc
        checks.append("toml-parse")
    return {
        "status": "passed",
        "checks": checks,
        "candidate_sha256": "sha256:" + hashlib.sha256(candidate).hexdigest(),
    }


def write_approved(
    receipt_path: Path, relative_path: str, content: bytes,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, object]:
    relative_path, staged, binding = _approved_staged_file(receipt_path, relative_path)
    if not binding.get("complete_file_write_allowed"):
        raise ApprovedWriteError(
            "complete-file replacement is allowed only for new files or an explicit "
            "Full file replacement paths declaration; use unique-fragment-replacement"
        )
    descriptor = _open_private_file(staged)
    try:
        previous = _read_descriptor_content(descriptor, max_bytes)
        validation = _validate_candidate(relative_path, previous, content)
        _transactional_descriptor_write(descriptor, previous, content)
    finally:
        os.close(descriptor)
    return {
        "status": "written",
        "operation": "complete-file",
        "relative_path": relative_path,
        "bytes": len(content),
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        "previous_sha256": "sha256:" + hashlib.sha256(previous).hexdigest(),
        "candidate_validation": validation,
    }


def replace_unique_approved(
    receipt_path: Path, relative_path: str, old: bytes, new: bytes,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, object]:
    if not old:
        raise ApprovedWriteError("unique replacement requires a non-empty old fragment")
    relative_path, staged, binding = _approved_staged_file(receipt_path, relative_path)
    descriptor = _open_private_file(staged)
    try:
        current_bytes = _read_descriptor_content(descriptor, max_bytes)
        matches = current_bytes.count(old)
        if matches != 1:
            raise ApprovedWriteError(
                f"old fragment must match exactly once; observed {matches} matches"
            )
        content = current_bytes.replace(old, new, 1)
        if len(content) > max_bytes:
            raise ApprovedWriteError("replacement result exceeds --max-bytes")
        if (
            len(current_bytes) >= LARGE_FRAGMENT_MIN_BYTES
            and len(old) / len(current_bytes) > LARGE_FRAGMENT_MAX_FRACTION
            and not binding.get("complete_file_write_allowed")
        ):
            raise ApprovedWriteError(
                "replacement fragment covers more than 75% of an existing file; "
                "split the edit or explicitly declare a full-file replacement"
            )
        validation = _validate_candidate(relative_path, current_bytes, content)
        _transactional_descriptor_write(descriptor, current_bytes, content)
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
        "previous_sha256": "sha256:" + hashlib.sha256(current_bytes).hexdigest(),
        "candidate_validation": validation,
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
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
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
            result = write_approved(receipt, args.path, content, args.max_bytes)
    except (ApprovedWriteError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"approved write: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
