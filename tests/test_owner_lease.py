import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from owner_lease import OwnerLeaseError, select_owner, transition_lease, validate_lease  # noqa: E402


STATE = "sha256:" + "1" * 64
EVIDENCE = "sha256:" + "2" * 64


def request(**overrides):
    value = {
        "schema_version": 1, "task_id": "T-7", "state_id": STATE,
        "operation": "semantic-revision", "original_builder_id": "claude-session-1",
        "current_builder_id": "claude-session-1", "current_session_id": "session-1",
        "resume_status": "succeeded", "new_evidence_refs": [], "semantic_blockers": [],
        "explicit_owner_id": None, "switch_reason": None,
        "last_handoff_event_id": None, "lease_ttl_seconds": 1800,
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize("operation", ["mechanical-revision", "test-fix"])
def test_mechanical_and_test_revision_return_to_original_builder(operation):
    lease = select_owner(request(
        operation=operation, original_builder_id="claude-original",
        current_builder_id="checker-current", resume_status="not-attempted",
    ))
    assert lease["selected_owner_id"] == "claude-original"
    assert lease["selected_model"] == "claude-builder"
    assert lease["model_switch"] == {
        "required": True, "from_owner": "checker-current", "to_owner": "claude-original",
        "reason": "return-to-original-builder",
    }
    assert lease["handoff_count"] == 1


def test_no_semantic_blocker_skips_advisor_and_no_evidence_skips_reviewer():
    lease = select_owner(request())
    assert lease["advisor"] == {"action": "skip", "reason": "no-semantic-blocker"}
    assert lease["reviewer"]["action"] == "skip"
    assert lease["reviewer"]["reason"] == "no-new-evidence"


def test_new_evidence_and_semantic_blocker_enable_bounded_calls():
    lease = select_owner(request(new_evidence_refs=[EVIDENCE], semantic_blockers=["public contract unclear"]))
    assert lease["advisor"]["action"] == "invoke"
    assert lease["reviewer"]["action"] == "invoke"
    assert lease["reviewer"]["new_evidence_refs"] == [EVIDENCE]


def test_new_session_requires_resume_failure_for_same_owner():
    pending = select_owner(request(resume_status="not-attempted"))
    assert pending["status"] == "requested"
    assert pending["session"]["mode"] == "resume-required"
    failed = select_owner(request(resume_status="failed"))
    assert failed["status"] == "granted"
    assert failed["session"]["mode"] == "new-session"
    resumed = select_owner(request(resume_status="succeeded"))
    assert resumed["session"]["mode"] == "resumed-session"


def test_model_switch_always_has_reason_and_explicit_owner_wins():
    lease = select_owner(request(explicit_owner_id="codex", switch_reason="human-requested-owner"))
    assert lease["selected_owner_id"] == "codex"
    assert lease["owner_source"] == "explicit-human-owner"
    assert lease["model_switch"]["reason"] == "human-requested-owner"
    assert validate_lease(lease) == []


def test_reason_is_rejected_when_no_switch_occurs():
    with pytest.raises(OwnerLeaseError, match="forbidden"):
        select_owner(request(switch_reason="not-a-switch"))


def test_previous_lease_preserves_chain_and_counts_renewal():
    first = select_owner(request())
    second = select_owner(request(), first)
    assert second["previous_lease_id"] == first["lease_id"]
    assert second["lease_generation"] == 2
    assert second["renewal_count"] == 1
    assert second["handoff_count"] == 0


def test_expire_and_revoke_are_hash_chained_terminal_transitions():
    first = select_owner(request())
    expired = transition_lease(first, "expired")
    assert expired["previous_lease_id"] == first["lease_id"]
    assert expired["transition_reason"] == "ttl-expired"
    assert validate_lease(expired) == []
    revoked = transition_lease(first, "revoked", "human-cancelled-task")
    assert revoked["transition_reason"] == "human-cancelled-task"
    with pytest.raises(OwnerLeaseError, match="terminal"):
        transition_lease(revoked, "expired")


def test_cli_schema_and_installer_registration(tmp_path):
    request_path = tmp_path / "request.json"
    output = tmp_path / "OWNER_LEASE.json"
    request_path.write_text(json.dumps(request()), encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts/select-continuation-owner.py"),
        "--request", str(request_path), "-o", str(output),
    ], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert validate_lease(json.loads(output.read_text(encoding="utf-8"))) == []
    schema = json.loads((ROOT / "schemas/owner-lease.schema.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    installer = (ROOT / "scripts/install_workflow.py").read_text(encoding="utf-8")
    assert "select-continuation-owner.py" in installer
    assert "owner-lease.schema.json" in installer
