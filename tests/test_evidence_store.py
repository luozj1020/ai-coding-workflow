import json
import subprocess
import sys
from pathlib import Path

from tests._unittest_compat import load_function_tests, skip


ROOT = Path(__file__).resolve().parents[1]
STORE_SCRIPT = ROOT / "scripts" / "evidence-store.py"
INVALIDATE_SCRIPT = ROOT / "scripts" / "evidence-invalidate.py"


def run(script, *args):
    return subprocess.run(
        [sys.executable, str(script), *map(str, args)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )


def metadata(path="src/optimizer.cc", symbol="GraphOptimizer::Optimize", **deps):
    dependency_hashes = {
        "file_hash": "sha256:file-v1" if path else None,
        "symbol_hash": "sha256:symbol-v1" if symbol else None,
        "build_configuration_hash": None,
        "validation_command_hash": None,
        "worktree_state_hash": None,
    }
    dependency_hashes.update(deps)
    return {
        "schema_version": 1,
        "kind": "symbol-slice" if symbol else "file-slice",
        "repository": {"commit": "commit-v1", "path": path},
        "selector": {"symbol": symbol, "start_line": 10 if path else None, "end_line": 20 if path else None},
        "producer": {"tool": "lsp", "version": "1.0"},
        "dependency_hashes": dependency_hashes,
    }


def current_context(path="src/optimizer.cc", file_hash="sha256:file-v1", symbol_hash="sha256:symbol-v1", **values):
    result = {
        "schema_version": 1,
        "repository_commit": "commit-v1",
        "worktree_state_hash": None,
        "build_configuration_hash": None,
        "validation_command_hash": None,
        "paths": {},
    }
    if path:
        result["paths"][path] = {
            "file_hash": file_hash,
            "symbol_hashes": {"GraphOptimizer::Optimize": symbol_hash} if symbol_hash else {},
        }
    result.update(values)
    return result


def write_json(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def put(tmp_path, store, content="optimizer evidence", meta=None, encoding="utf-8", name="content.txt"):
    metadata_path = write_json(tmp_path, name + ".meta.json", meta or metadata())
    content_path = tmp_path / name
    if isinstance(content, bytes):
        content_path.write_bytes(content)
    else:
        content_path.write_text(content, encoding="utf-8")
    result = run(
        STORE_SCRIPT, "put", "--store", store, "--metadata", metadata_path,
        "--content", content_path, "--encoding", encoding,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def invalidate(tmp_path, store, object_id, context, apply=False):
    context_path = write_json(tmp_path, "current.json", context)
    args = [
        "--store", store, "--current", context_path, "--object-id", object_id,
    ]
    if apply:
        args.append("--apply")
    return run(INVALIDATE_SCRIPT, *args)


def test_identical_object_reuses_same_id_and_path(tmp_path):
    store = tmp_path / "objects"
    first = put(tmp_path, store)
    second = put(tmp_path, store)
    assert first["object_id"] == second["object_id"]
    assert first["object_path"] == second["object_path"]
    assert first["store_cache_hit"] is False
    assert second["store_cache_hit"] is True
    object_path = Path(first["object_path"])
    assert object_path.parent.name == first["object_id"][7:9]
    assert object_path.name == first["object_id"][9:] + ".json"


def test_provenance_is_part_of_object_identity(tmp_path):
    store = tmp_path / "objects"
    first = put(tmp_path, store)
    changed = metadata()
    changed["repository"]["commit"] = "commit-v2"
    second = put(tmp_path, store, meta=changed, name="second.txt")
    assert first["content_hash"] == second["content_hash"]
    assert first["object_id"] != second["object_id"]


def test_single_object_can_be_read_as_content_object_or_reference(tmp_path):
    store = tmp_path / "objects"
    stored = put(tmp_path, store, content="hello evidence")
    content = run(STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"])
    assert content.returncode == 0
    assert content.stdout == "hello evidence"
    full = run(
        STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"],
        "--format", "object",
    )
    assert json.loads(full.stdout)["content"]["value"] == "hello evidence"
    reference = run(
        STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"],
        "--format", "reference",
    )
    assert "content" not in json.loads(reference.stdout)


def test_missing_reference_fails_explicitly_and_records_unreadable(tmp_path):
    store = tmp_path / "objects"
    metrics = tmp_path / "metrics.json"
    missing = "sha256:" + "0" * 64
    result = run(
        STORE_SCRIPT, "read", "--store", store, "--object-id", missing,
        "--receiver", "claude-1", "--metrics", metrics,
    )
    assert result.returncode == 1
    assert "reference is unreadable" in result.stderr
    values = json.loads(metrics.read_text(encoding="utf-8"))
    assert values["total_unreadable"] == 1


def test_tampered_content_or_object_id_fails_closed(tmp_path):
    store = tmp_path / "objects"
    stored = put(tmp_path, store)
    path = Path(stored["object_path"])
    obj = json.loads(path.read_text(encoding="utf-8"))
    obj["content"]["value"] = "tampered"
    path.write_text(json.dumps(obj), encoding="utf-8")
    result = run(STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"])
    assert result.returncode == 1
    assert "content_hash does not match" in result.stderr


def test_reference_packet_never_inlines_content_and_resolves_every_ref(tmp_path):
    store = tmp_path / "objects"
    first = put(tmp_path, store, content="one")
    second = put(tmp_path, store, content="two", name="two.txt")
    packet_path = tmp_path / "packet.json"
    result = run(
        STORE_SCRIPT, "packet", "--store", store,
        "--object-id", first["object_id"], "--object-id", second["object_id"],
        "--output", packet_path,
    )
    assert result.returncode == 0, result.stderr
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["inline_content"] is False
    assert all("content" not in ref for ref in packet["object_refs"])
    assert "one" not in packet_path.read_text(encoding="utf-8")
    missing = "sha256:" + "f" * 64
    failed = run(
        STORE_SCRIPT, "packet", "--store", store,
        "--object-id", first["object_id"], "--object-id", missing,
        "--output", tmp_path / "bad-packet.json",
    )
    assert failed.returncode == 1
    assert not (tmp_path / "bad-packet.json").exists()


def test_receiver_cache_hit_is_counted_after_first_read(tmp_path):
    store = tmp_path / "objects"
    stored = put(tmp_path, store)
    metrics = tmp_path / "metrics.json"
    receipt_one = tmp_path / "receipt-one.json"
    receipt_two = tmp_path / "receipt-two.json"
    for receipt in (receipt_one, receipt_two):
        result = run(
            STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"],
            "--receiver", "claude-1", "--metrics", metrics,
            "--receipt-output", receipt,
        )
        assert result.returncode == 0
    assert json.loads(receipt_one.read_text())["cache_outcome"] == "miss"
    assert json.loads(receipt_two.read_text())["cache_outcome"] == "hit"
    stats = run(STORE_SCRIPT, "stats", "--store", store, "--metrics", metrics)
    values = json.loads(stats.stdout)
    assert values["total_hits"] == 1
    assert values["total_misses"] == 1
    assert values["cache_hit_rate"] == 0.5


def test_tampered_receiver_metric_totals_fail_closed(tmp_path):
    store = tmp_path / "objects"
    stored = put(tmp_path, store)
    metrics = tmp_path / "metrics.json"
    result = run(
        STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"],
        "--receiver", "claude-1", "--metrics", metrics,
    )
    assert result.returncode == 0
    value = json.loads(metrics.read_text())
    value["total_hits"] = 99
    metrics.write_text(json.dumps(value), encoding="utf-8")
    stats = run(STORE_SCRIPT, "stats", "--store", store, "--metrics", metrics)
    assert stats.returncode == 1
    assert "does not equal" in stats.stderr


def test_dependency_changes_invalidate_individual_object(tmp_path):
    cases = [
        ({}, {"repository_commit": "commit-v2"}, "repository.commit"),
        ({}, {"file_hash": "sha256:file-v2"}, "file_hash"),
        ({}, {"symbol_hash": "sha256:symbol-v2"}, "symbol_hash"),
        ({"build_configuration_hash": "build-v1"}, {"build_configuration_hash": "build-v2"}, "build_configuration_hash"),
        ({"validation_command_hash": "validation-v1"}, {"validation_command_hash": "validation-v2"}, "validation_command_hash"),
        ({"worktree_state_hash": "worktree-v1"}, {"worktree_state_hash": "worktree-v2"}, "worktree_state_hash"),
    ]
    for index, (metadata_changes, current_changes, dependency) in enumerate(cases):
        case_path = tmp_path / str(index)
        case_path.mkdir()
        store = case_path / "objects"
        stored = put(case_path, store, meta=metadata(**metadata_changes))
        context_kwargs = dict(current_changes)
        file_hash = context_kwargs.pop("file_hash", "sha256:file-v1")
        symbol_hash = context_kwargs.pop("symbol_hash", "sha256:symbol-v1")
        context = current_context(file_hash=file_hash, symbol_hash=symbol_hash, **context_kwargs)
        result = invalidate(case_path, store, stored["object_id"], context, apply=True)
        assert result.returncode == 2
        record = json.loads(result.stdout)["objects"][0]
        assert record["status"] == "stale"
        assert dependency in [reason["dependency"] for reason in record["reasons"]]
        read = run(STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"])
        assert read.returncode == 1
        assert "is stale" in read.stderr


def test_unrelated_file_change_does_not_invalidate_object(tmp_path):
    store = tmp_path / "objects"
    stored = put(tmp_path, store)
    context = current_context()
    context["paths"]["src/unrelated.cc"] = {
        "file_hash": "sha256:changed", "symbol_hashes": {},
    }
    result = invalidate(tmp_path, store, stored["object_id"], context, apply=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["objects"][0]["status"] == "valid"
    read = run(STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"])
    assert read.returncode == 0


def test_unknown_dependency_is_explicit_and_blocks_read_after_apply(tmp_path):
    store = tmp_path / "objects"
    stored = put(tmp_path, store)
    result = invalidate(tmp_path, store, stored["object_id"], current_context(path=None), apply=True)
    assert result.returncode == 3
    record = json.loads(result.stdout)["objects"][0]
    assert record["status"] == "unknown"
    assert {reason["dependency"] for reason in record["reasons"]} == {"file_hash", "symbol_hash"}
    read = run(STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"])
    assert read.returncode == 1
    assert "is unknown" in read.stderr


def test_revalidation_can_restore_reuse_without_rewriting_object(tmp_path):
    store = tmp_path / "objects"
    stored = put(tmp_path, store)
    object_bytes = Path(stored["object_path"]).read_bytes()
    assert invalidate(
        tmp_path, store, stored["object_id"],
        current_context(file_hash="sha256:file-v2"), apply=True,
    ).returncode == 2
    restored = invalidate(tmp_path, store, stored["object_id"], current_context(), apply=True)
    assert restored.returncode == 0
    assert Path(stored["object_path"]).read_bytes() == object_bytes
    assert run(STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"]).returncode == 0


def test_binary_and_canonical_json_content_are_supported(tmp_path):
    store = tmp_path / "objects"
    binary = put(tmp_path, store, content=b"\x00\xff\x10", encoding="base64", name="binary.bin")
    binary_read = run(
        STORE_SCRIPT, "read", "--store", store, "--object-id", binary["object_id"],
        "--format", "object",
    )
    assert json.loads(binary_read.stdout)["content"]["encoding"] == "base64"
    first = put(tmp_path, store, content='{"b": 2, "a": 1}', encoding="json", name="first.json")
    second = put(tmp_path, store, content='{"a":1,"b":2}', encoding="json", name="second.json")
    assert first["object_id"] == second["object_id"]


def test_repository_path_traversal_is_rejected(tmp_path):
    store = tmp_path / "objects"
    value = metadata(path="../secret.txt", symbol=None)
    metadata_path = write_json(tmp_path, "bad-meta.json", value)
    content = tmp_path / "content.txt"
    content.write_text("secret", encoding="utf-8")
    result = run(
        STORE_SCRIPT, "put", "--store", store, "--metadata", metadata_path,
        "--content", content,
    )
    assert result.returncode == 1
    assert "repository-relative" in result.stderr


def test_windows_absolute_repository_path_is_rejected(tmp_path):
    store = tmp_path / "objects"
    value = metadata(path="C:\\secrets\\token.txt", symbol=None)
    metadata_path = write_json(tmp_path, "windows-meta.json", value)
    content = tmp_path / "content.txt"
    content.write_text("secret", encoding="utf-8")
    result = run(
        STORE_SCRIPT, "put", "--store", store, "--metadata", metadata_path,
        "--content", content,
    )
    assert result.returncode == 1
    assert "repository-relative" in result.stderr


def test_oversized_content_is_rejected_before_object_creation(tmp_path):
    store = tmp_path / "objects"
    metadata_path = write_json(tmp_path, "meta.json", metadata())
    content = tmp_path / "oversized.bin"
    with content.open("wb") as handle:
        handle.truncate(4 * 1024 * 1024 + 1)
    result = run(
        STORE_SCRIPT, "put", "--store", store, "--metadata", metadata_path,
        "--content", content,
    )
    assert result.returncode == 1
    assert "byte limit" in result.stderr
    assert not store.exists()


def test_symlinked_object_shard_is_not_followed(tmp_path):
    store = tmp_path / "objects"
    outside = tmp_path / "outside"
    outside.mkdir()
    store.mkdir()
    try:
        (store / "ab").symlink_to(outside, target_is_directory=True)
    except OSError:
        skip("directory symlinks are unavailable")
    object_id = "sha256:ab" + "0" * 62
    result = run(STORE_SCRIPT, "read", "--store", store, "--object-id", object_id)
    assert result.returncode == 1
    assert "shard must not be a symlink" in result.stderr


def test_malformed_nested_metadata_fails_without_traceback(tmp_path):
    store = tmp_path / "objects"
    value = metadata()
    value["selector"] = []
    metadata_path = write_json(tmp_path, "bad-nested-meta.json", value)
    content = tmp_path / "content.txt"
    content.write_text("evidence", encoding="utf-8")
    result = run(
        STORE_SCRIPT, "put", "--store", store, "--metadata", metadata_path,
        "--content", content,
    )
    assert result.returncode == 1
    assert "selector must contain exactly" in result.stderr
    assert "Traceback" not in result.stderr


def test_malformed_validity_sidecar_fails_closed(tmp_path):
    store = tmp_path / "objects"
    stored = put(tmp_path, store)
    object_path = Path(stored["object_path"])
    validity_path = object_path.with_suffix(".validity.json")
    validity_path.write_text(json.dumps({
        "schema_version": 1,
        "object_id": stored["object_id"],
        "status": "stale",
        "checked_at": "now",
        "current_context_hash": "sha256:" + "0" * 64,
        "reasons": ["malformed"],
    }), encoding="utf-8")
    result = run(STORE_SCRIPT, "read", "--store", store, "--object-id", stored["object_id"])
    assert result.returncode == 1
    assert "invalid validity record" in result.stderr
    assert "Traceback" not in result.stderr


def test_malformed_reference_packet_input_fails_without_partial_output(tmp_path):
    store = tmp_path / "objects"
    refs = write_json(tmp_path, "bad-refs.json", {"object_refs": [{"kind": "file-slice"}]})
    output = tmp_path / "packet.json"
    result = run(
        STORE_SCRIPT, "packet", "--store", store, "--refs-file", refs,
        "--output", output,
    )
    assert result.returncode == 1
    assert "object_refs[0].object_id must be a string" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_schema_is_strict_and_content_addressed():
    schema = json.loads((ROOT / "schemas" / "evidence-object.schema.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["object_id"]["$ref"] == "#/$defs/hash"
    assert schema["properties"]["dependency_hashes"]["additionalProperties"] is False


def load_tests(loader, tests, pattern):
    return load_function_tests(globals())
