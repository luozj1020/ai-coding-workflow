import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "context-broker.py"
sys.path.insert(0, str(ROOT / "scripts"))
from workflow_state import state_id_for  # noqa: E402


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    optimizer = repo / "src" / "optimizer.py"
    optimizer.write_text(
        "def helper(value):\n"
        "    return value + 1\n\n"
        "def optimize(value):\n"
        "    result = helper(value)\n"
        "    return result\n\n"
        "def run_pipeline(value):\n"
        "    return optimize(value)\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_optimizer.py").write_text(
        "from src.optimizer import optimize\n\n"
        "def test_optimize():\n"
        "    assert optimize(1) == 2\n",
        encoding="utf-8",
    )
    (repo / "BUILD.bazel").write_text(
        "py_library(\n"
        "    name = \"optimizer\",\n"
        "    srcs = [\"src/optimizer.py\"],\n"
        ")\n",
        encoding="utf-8",
    )
    return repo


def make_state(tmp_path, allowed_paths=None, phase="implementation"):
    state = {
        "schema_version": 1,
        "state_id": "",
        "parent_state_id": None,
        "revision": 0,
        "task_id": "T-PHASE5",
        "phase": phase,
        "repository_state_hash": "sha256:fixture-worktree-v1",
        "goal": {"id": "G-1", "statement": "Optimize graph", "acceptance_ids": ["AC-1"]},
        "constraints": [],
        "accepted_decisions": [],
        "rejected_hypotheses": [],
        "open_questions": [],
        "evidence_refs": [],
        "acceptance_status": {
            "AC-1": {"description": "Optimizer remains callable", "status": "pending", "evidence_refs": []},
        },
        "next_action": {
            "owner": "claude-builder",
            "operation": "implement",
            "allowed_paths": allowed_paths or [],
        },
    }
    state["state_id"] = state_id_for(state)
    return write_json(tmp_path / "state.json", state), state


def make_query(tmp_path, state_id, *, symbols=None, include=None, max_bytes=12000, **extra):
    query = {
        "state_id": state_id,
        "requester": "claude-session-28",
        "query": {
            "intent": "locate-optimizer-contract",
            "symbols": symbols or ["optimize"],
            "include": include or ["definition", "callers", "callees", "tests", "build-rules"],
            "max_bytes": max_bytes,
        },
    }
    query["query"].update(extra)
    return write_json(tmp_path / "query.json", query)


def run_request(repo, state_path, query_path, output, store=None):
    args = [
        sys.executable, str(SCRIPT), "request", "--repo", str(repo),
        "--state", str(state_path), "--query", str(query_path),
        "--output", str(output),
    ]
    if store is not None:
        args.extend(["--store", str(store)])
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )


def load_response(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_generates_five_context_types_as_reference_only_objects(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path)
    query_path = make_query(tmp_path, state["state_id"])
    output = tmp_path / "response.json"
    store = tmp_path / "objects"
    result = run_request(repo, state_path, query_path, output, store)
    assert result.returncode == 0, result.stderr
    response = load_response(output)
    assert response["state_id"] == state["state_id"]
    assert response["context_id"].startswith("sha256:")
    assert response["cache"] == {"requested": 5, "hits": 0, "generated": 5}
    assert {item["query_type"] for item in response["objects"]} == {
        "definition", "callers", "callees", "tests", "build-rules",
    }
    assert all("content" not in item for item in response["objects"])
    assert all("evidence_quality" in item for item in response["objects"])
    assert next(
        item for item in response["objects"] if item["query_type"] == "callees"
    )["evidence_quality"] == "bounded-lexical-candidate"
    assert response["budget"]["used_bytes"] <= response["budget"]["max_bytes"]


def test_second_identical_request_reuses_objects_without_generation(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path)
    query_path = make_query(tmp_path, state["state_id"])
    store = tmp_path / "objects"
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"
    assert run_request(repo, state_path, query_path, first_output, store).returncode == 0
    object_paths_before = sorted(store.glob("*/*.json"))
    result = run_request(repo, state_path, query_path, second_output, store)
    assert result.returncode == 0, result.stderr
    response = load_response(second_output)
    assert response["cache"] == {"requested": 5, "hits": 5, "generated": 0}
    assert all(item["cache_outcome"] == "hit" for item in response["objects"])
    assert response["context_id"] == load_response(first_output)["context_id"]
    assert sorted(store.glob("*/*.json")) == object_paths_before


def test_query_and_response_are_bound_to_exact_state_hash(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path)
    query_path = make_query(tmp_path, "sha256:" + "0" * 64)
    output = tmp_path / "response.json"
    result = run_request(repo, state_path, query_path, output, tmp_path / "objects")
    assert result.returncode == 1
    assert "state_id does not match" in result.stderr
    assert not output.exists()
    assert state["state_id"] not in result.stdout


def test_content_budget_omits_whole_object_instead_of_truncating_it(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path)
    query_path = make_query(
        tmp_path, state["state_id"], include=["definition"], max_bytes=1,
    )
    output = tmp_path / "response.json"
    result = run_request(repo, state_path, query_path, output, tmp_path / "objects")
    assert result.returncode == 0, result.stderr
    response = load_response(output)
    assert response["objects"] == []
    assert response["budget"]["used_bytes"] == 0
    assert response["unresolved"][0]["status"] == "budget-exceeded"


def test_not_found_is_explicit(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path)
    query_path = make_query(
        tmp_path, state["state_id"], symbols=["MissingSymbol"], include=["definition"],
    )
    output = tmp_path / "response.json"
    assert run_request(repo, state_path, query_path, output, tmp_path / "objects").returncode == 0
    unresolved = load_response(output)["unresolved"]
    assert unresolved == [{
        "symbol": "MissingSymbol", "query_type": "definition", "status": "not-found",
        "detail": "no bounded repository evidence matched",
    }]


def test_stale_cached_match_is_distinct_from_not_found(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path)
    query_path = make_query(tmp_path, state["state_id"], include=["definition"])
    store = tmp_path / "objects"
    first = tmp_path / "first.json"
    assert run_request(repo, state_path, query_path, first, store).returncode == 0
    object_id = load_response(first)["objects"][0]["object_id"]
    digest = object_id.split(":", 1)[1]
    object_path = store / digest[:2] / (digest[2:] + ".json")
    validity = {
        "schema_version": 1,
        "object_id": object_id,
        "status": "stale",
        "checked_at": "2026-07-21T00:00:00+00:00",
        "current_context_hash": "sha256:" + "1" * 64,
        "reasons": [{
            "dependency": "file_hash", "status": "stale",
            "expected": "sha256:old", "actual": "sha256:new",
        }],
    }
    write_json(object_path.with_suffix(".validity.json"), validity)
    second = tmp_path / "second.json"
    result = run_request(repo, state_path, query_path, second, store)
    assert result.returncode == 0, result.stderr
    assert load_response(second)["unresolved"][0]["status"] == "stale"


def test_requested_path_outside_state_scope_is_permission_denied(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path, allowed_paths=["tests"])
    query_path = make_query(
        tmp_path, state["state_id"], include=["definition"], paths=["src"],
    )
    output = tmp_path / "response.json"
    result = run_request(repo, state_path, query_path, output, tmp_path / "objects")
    assert result.returncode == 0, result.stderr
    response = load_response(output)
    assert response["objects"] == []
    assert response["unresolved"][0]["status"] == "permission-denied"


def test_role_and_phase_order_prioritize_reviewer_tests(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path, phase="review")
    query_path = make_query(
        tmp_path, state["state_id"], include=["definition", "tests"], role="reviewer",
    )
    output = tmp_path / "response.json"
    assert run_request(repo, state_path, query_path, output, tmp_path / "objects").returncode == 0
    assert load_response(output)["objects"][0]["query_type"] == "tests"


def test_schemas_are_strict_and_cover_terminal_statuses():
    query_schema = json.loads((ROOT / "schemas" / "context-query.schema.json").read_text())
    response_schema = json.loads((ROOT / "schemas" / "context-response.schema.json").read_text())
    assert query_schema["additionalProperties"] is False
    assert query_schema["properties"]["query"]["additionalProperties"] is False
    statuses = response_schema["$defs"]["unresolved"]["properties"]["status"]["enum"]
    assert set(statuses) == {"not-found", "stale", "permission-denied", "budget-exceeded"}


def test_malformed_query_fails_without_traceback_or_partial_output(tmp_path):
    repo = make_repo(tmp_path)
    state_path, _ = make_state(tmp_path)
    query_path = tmp_path / "query.json"
    query_path.write_text("{not-json", encoding="utf-8")
    output = tmp_path / "response.json"
    result = run_request(repo, state_path, query_path, output, tmp_path / "objects")
    assert result.returncode == 1
    assert "cannot read" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_cache_hit_must_match_current_repository_file_hash(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_repo = make_repo(first_root)
    second_repo = make_repo(second_root)
    (second_repo / "src" / "optimizer.py").write_text(
        "def optimize(value):\n    return 99\n", encoding="utf-8",
    )
    first_state, first_value = make_state(first_root)
    second_state, second_value = make_state(second_root)
    first_query = make_query(first_root, first_value["state_id"], include=["definition"])
    second_query = make_query(second_root, second_value["state_id"], include=["definition"])
    store = tmp_path / "shared-objects"
    first_output = first_root / "response.json"
    second_output = second_root / "response.json"
    assert run_request(first_repo, first_state, first_query, first_output, store).returncode == 0
    result = run_request(second_repo, second_state, second_query, second_output, store)
    assert result.returncode == 0, result.stderr
    response = load_response(second_output)
    assert response["cache"] == {"requested": 1, "hits": 0, "generated": 1}
    object_id = response["objects"][0]["object_id"]
    digest = object_id.split(":", 1)[1]
    obj = json.loads((store / digest[:2] / (digest[2:] + ".json")).read_text())
    assert "return 99" in obj["content"]["value"]["text"]


def test_query_cardinality_and_document_size_are_bounded(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path)
    too_many = make_query(
        tmp_path, state["state_id"], symbols=[f"Symbol{index}" for index in range(17)],
        include=["definition"],
    )
    output = tmp_path / "too-many-response.json"
    result = run_request(repo, state_path, too_many, output, tmp_path / "objects")
    assert result.returncode == 1
    assert "query.symbols exceeds 16 item limit" in result.stderr
    oversized = tmp_path / "oversized-query.json"
    oversized.write_text(" " * (1024 * 1024 + 1), encoding="utf-8")
    output = tmp_path / "oversized-response.json"
    result = run_request(repo, state_path, oversized, output, tmp_path / "objects")
    assert result.returncode == 1
    assert "Context Query exceeds" in result.stderr
    assert not output.exists()


def test_windows_absolute_query_path_is_rejected_before_repository_scan(tmp_path):
    repo = make_repo(tmp_path)
    state_path, state = make_state(tmp_path, allowed_paths=["src"])
    query_path = make_query(
        tmp_path, state["state_id"], include=["definition"],
        paths=["C:\\secrets\\token.py"],
    )
    output = tmp_path / "response.json"
    result = run_request(repo, state_path, query_path, output, tmp_path / "objects")
    assert result.returncode == 1
    assert "repository-relative" in result.stderr
    assert not output.exists()


def test_cpp_bazel_canary_returns_bounded_candidates(tmp_path):
    repo = tmp_path / "cpp-repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "optimizer.cc").write_text(
        "int Helper(int value) { return value + 1; }\n"
        "int GraphOptimizer::Optimize(int value) { return Helper(value); }\n"
        "int RunPipeline(int value) { return GraphOptimizer::Optimize(value); }\n",
        encoding="utf-8",
    )
    (repo / "tests" / "optimizer_test.cc").write_text(
        "TEST(OptimizerTest, Runs) { GraphOptimizer::Optimize(1); }\n",
        encoding="utf-8",
    )
    (repo / "BUILD.bazel").write_text(
        "cc_library(name = \"optimizer\", srcs = [\"src/optimizer.cc\"])\n",
        encoding="utf-8",
    )
    state_path, state = make_state(tmp_path)
    query_path = make_query(
        tmp_path, state["state_id"], symbols=["GraphOptimizer::Optimize"],
    )
    output = tmp_path / "cpp-response.json"
    result = run_request(repo, state_path, query_path, output, tmp_path / "cpp-objects")
    assert result.returncode == 0, result.stderr
    response = load_response(output)
    assert response["cache"] == {"requested": 5, "hits": 0, "generated": 5}
    assert {item["query_type"] for item in response["objects"]} == {
        "definition", "callers", "callees", "tests", "build-rules",
    }
    assert response["budget"]["used_bytes"] <= response["budget"]["max_bytes"]


def test_current_repository_python_canary_locates_real_symbol(tmp_path):
    state_path, state = make_state(tmp_path, allowed_paths=["scripts", "tests"])
    query_path = make_query(
        tmp_path, state["state_id"], symbols=["state_id_for"],
        include=["definition", "callers", "tests"], max_bytes=32000,
    )
    output = tmp_path / "real-repo-response.json"
    result = run_request(ROOT, state_path, query_path, output, tmp_path / "real-objects")
    assert result.returncode == 0, result.stderr
    response = load_response(output)
    definitions = [item for item in response["objects"] if item["query_type"] == "definition"]
    assert definitions
    assert definitions[0]["repository"]["path"] == "scripts/workflow_state.py"
    assert all(item["content_bytes"] <= response["budget"]["max_bytes"] for item in response["objects"])
