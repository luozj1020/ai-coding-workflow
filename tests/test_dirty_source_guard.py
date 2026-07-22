import os
import json
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "scripts" / "dispatch-to-claude.sh"
CHECK_WORKTREE = ROOT / "scripts" / "check-worktree.sh"
CLASSIFY_ATTEMPT = ROOT / "scripts" / "classify-claude-attempt.py"
CLAUDE_HEALTHCHECK = ROOT / "scripts" / "claude-healthcheck.py"
DISPATCH_PREFLIGHT = ROOT / "scripts" / "dispatch-preflight.py"
PROCESS_IDENTITY = ROOT / "scripts" / "process-identity.py"
CODEGRAPH_WORKTREE_GUARD = ROOT / "scripts" / "codegraph-worktree-guard.py"
CLAUDE_API_AVAILABILITY = ROOT / "scripts" / "claude-api-availability.py"
ARCHIVE_CONTROL_FILES = ROOT / "scripts" / "archive-control-files.py"
BUILD_TAKEOVER_RECEIPT = ROOT / "scripts" / "build-takeover-receipt.py"
CREATE_DIRTY_SNAPSHOT = ROOT / "scripts" / "create-dirty-snapshot.py"
ENFORCE_CHECKER_CONTRACT = ROOT / "scripts" / "enforce-checker-contract.py"
VALIDATE_ADVISOR_REQUEST = ROOT / "scripts" / "validate-advisor-request.py"
VALIDATE_ADVISOR_RESPONSE = ROOT / "scripts" / "validate-advisor-response.py"
WORKTREE_STATE_HASH = ROOT / "scripts" / "worktree_state_hash.py"
PREPARE_WORKTREE_CONTINUATION = ROOT / "scripts" / "prepare-worktree-continuation.py"
MODEL_USAGE = ROOT / "scripts" / "model-usage.py"
TEMP_ROOT = ROOT / ".worktrees" / "dirty-source-guard-tests"

def find_bash():
    if sys.platform == "win32":
        for candidate in [
            pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
            pathlib.Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ]:
            if candidate.exists():
                return str(candidate)
    return "bash"


BASH = find_bash()


def start_bash_sleeper():
    proc = subprocess.Popen(
        [BASH, "-c", "echo $$; exec sleep 60"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    return proc, proc.stdout.readline().strip()


def remove_tree(path):
    def make_writable(func, failing_path, _exc_info):
        try:
            os.chmod(failing_path, stat.S_IWRITE)
            func(failing_path)
        except OSError:
            pass

    shutil.rmtree(path, onerror=make_writable)


class DirtySourceGuardBehaviorTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.case_root = pathlib.Path(tempfile.mkdtemp(prefix="case-", dir=str(TEMP_ROOT)))
        self.repo = self.case_root / "repo"
        self.fake_bin = self.case_root / "fake-bin"
        self.repo.mkdir()
        self.fake_bin.mkdir()
        self._write_fake_claude()
        self._run(["git", "init"], cwd=self.repo)
        self._run(["git", "config", "user.email", "test@example.com"], cwd=self.repo)
        self._run(["git", "config", "user.name", "Test User"], cwd=self.repo)
        (self.repo / "README.md").write_text("# fixture\n", encoding="utf-8")
        (self.repo / "scripts").mkdir()
        shutil.copy2(DISPATCH, self.repo / "scripts" / "dispatch-to-claude.sh")
        shutil.copy2(CLASSIFY_ATTEMPT, self.repo / "scripts" / "classify-claude-attempt.py")
        shutil.copy2(CLAUDE_HEALTHCHECK, self.repo / "scripts" / "claude-healthcheck.py")
        shutil.copy2(DISPATCH_PREFLIGHT, self.repo / "scripts" / "dispatch-preflight.py")
        shutil.copy2(PROCESS_IDENTITY, self.repo / "scripts" / "process-identity.py")
        shutil.copy2(CODEGRAPH_WORKTREE_GUARD, self.repo / "scripts" / "codegraph-worktree-guard.py")
        shutil.copy2(CLAUDE_API_AVAILABILITY, self.repo / "scripts" / "claude-api-availability.py")
        shutil.copy2(ARCHIVE_CONTROL_FILES, self.repo / "scripts" / "archive-control-files.py")
        shutil.copy2(BUILD_TAKEOVER_RECEIPT, self.repo / "scripts" / "build-takeover-receipt.py")
        shutil.copy2(CREATE_DIRTY_SNAPSHOT, self.repo / "scripts" / "create-dirty-snapshot.py")
        shutil.copy2(ENFORCE_CHECKER_CONTRACT, self.repo / "scripts" / "enforce-checker-contract.py")
        shutil.copy2(VALIDATE_ADVISOR_REQUEST, self.repo / "scripts" / "validate-advisor-request.py")
        shutil.copy2(VALIDATE_ADVISOR_RESPONSE, self.repo / "scripts" / "validate-advisor-response.py")
        shutil.copy2(WORKTREE_STATE_HASH, self.repo / "scripts" / "worktree_state_hash.py")
        shutil.copy2(PREPARE_WORKTREE_CONTINUATION, self.repo / "scripts" / "prepare-worktree-continuation.py")
        shutil.copy2(MODEL_USAGE, self.repo / "scripts" / "model-usage.py")
        self._run(["git", "add", "README.md", "scripts/dispatch-to-claude.sh",
                   "scripts/classify-claude-attempt.py", "scripts/claude-healthcheck.py",
                   "scripts/dispatch-preflight.py", "scripts/process-identity.py",
                   "scripts/codegraph-worktree-guard.py",
                   "scripts/claude-api-availability.py",
                   "scripts/archive-control-files.py", "scripts/build-takeover-receipt.py",
                   "scripts/create-dirty-snapshot.py", "scripts/enforce-checker-contract.py",
                   "scripts/validate-advisor-request.py", "scripts/validate-advisor-response.py",
                   "scripts/worktree_state_hash.py", "scripts/prepare-worktree-continuation.py"], cwd=self.repo)
        self._run(["git", "add", "scripts/model-usage.py"], cwd=self.repo)
        self._run(["git", "commit", "-m", "init"], cwd=self.repo)

    def tearDown(self):
        if getattr(self, "repo", None) and self.repo.exists():
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(self.repo),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
        if self.case_root.exists():
            remove_tree(self.case_root)
        try:
            TEMP_ROOT.rmdir()
        except OSError:
            pass

    def _write_fake_claude(self):
        fake = self.fake_bin / "claude"
        with open(fake, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "#!/usr/bin/env bash\n"
                "if [[ \"$*\" == *\"--help\"* ]]; then\n"
                "  echo 'Usage: claude [options]'\n"
                "  if [ -n \"${FAKE_CLAUDE_HELP_TOOLS_FLAG:-}\" ]; then\n"
                "    echo '  --tools          Specify allowed tools'\n"
                "  fi\n"
                "  if [ -n \"${FAKE_CLAUDE_HELP_ALLOWED_FLAG:-}\" ]; then\n"
                "    echo \"  ${FAKE_CLAUDE_HELP_ALLOWED_FLAG}   Specify allowed tool patterns\"\n"
                "  fi\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$*\" == *\"你好\"* ]]; then\n"
                "  if [ \"${FAKE_CLAUDE_HEALTHCHECK_TRUST:-0}\" = 1 ]; then echo 'this workspace has not been trusted' >&2; exit 42; fi\n"
                "  if [ \"${FAKE_CLAUDE_HEALTHCHECK_FAIL:-0}\" = 1 ]; then exit 42; fi\n"
                "  printf '你好！\\n'\n"
                "  exit 0\n"
                "fi\n"
                "if [ -n \"${FAKE_CLAUDE_INVOCATION_LOG:-}\" ]; then printf 'invoke\\n' >> \"${FAKE_CLAUDE_INVOCATION_LOG}\"; fi\n"
                "if [ -n \"${FAKE_CLAUDE_ARGV_LOG:-}\" ]; then printf '%s\\n' \"$*\" >> \"${FAKE_CLAUDE_ARGV_LOG}\"; fi\n"
                "if [ -n \"${FAKE_CLAUDE_ARGV_NUL_LOG:-}\" ]; then printf '%s\\0' \"$@\" > \"${FAKE_CLAUDE_ARGV_NUL_LOG}\"; fi\n"
                "if [ -n \"${FAKE_CLAUDE_PROMPT_CAPTURE:-}\" ]; then\n"
                "  cat > \"${FAKE_CLAUDE_PROMPT_CAPTURE}\"\n"
                "else\n"
                "  cat >/dev/null\n"
                "fi\n"
                "case \"${FAKE_CLAUDE_MODE:-success}\" in\n"
                "  fail-empty)\n"
                "    exit 42\n"
                "    ;;\n"
                "  stage-change)\n"
                "    printf '# staged by claude\\n' > README.md\n"
                "    git add README.md\n"
                "    ;;\n"
                "  approval-blocked|approval-incomplete|approval-unrelated)\n"
                "    mkdir -p tests\n"
                "    printf '# checker edit\\n' > tests/test_fixture.py\n"
                "    if [ \"${FAKE_CLAUDE_MODE}\" = approval-incomplete ]; then\n"
                "      printf '# incomplete report\\n' > CLAUDE_REPORT.md\n"
                "    else\n"
                "      cat > CLAUDE_REPORT.md <<'REPORT_EOF'\n"
                "# Claude Modification Report\n\n"
                "## Requirements Summary\nChecker validation.\n\n"
                "## Files Changed\n- tests/test_fixture.py\n\n"
                "## Acceptance Criteria Mapping\n- test edit complete\n\n"
                "## Out-of-Scope Confirmation\nNo out-of-scope changes.\n\n"
                "## Plan Match\nfull\n\n"
                "## Checks Run\n- python -m pytest: blocked by approval\n\n"
                "Implementation and test edits complete.\n"
                "REPORT_EOF\n"
                "    fi\n"
                "    if [ \"${FAKE_CLAUDE_MODE}\" = approval-unrelated ]; then\n"
                "      sed -i 's/python -m pytest: blocked by approval/no validation command assigned/' CLAUDE_REPORT.md\n"
                "      printf 'Deployment approval required. Test edits complete.\\n' > CLAUDE_PROGRESS.md\n"
                "      echo 'deployment approval required' >&2\n"
                "    else\n"
                "      printf 'Test edits complete. Validation command blocked by approval.\\n' > CLAUDE_PROGRESS.md\n"
                "      echo 'validation command requires permission approval' >&2\n"
                "    fi\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-4}\"\n"
                "    ;;\n"
                "  seed-only)\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  worktree-change)\n"
                "    printf '# worktree change\\n' > NEW_FILE.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  progress-update)\n"
                "    printf 'Real progress update.\\n' > CLAUDE_PROGRESS.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  builder-editing-phase)\n"
                "    printf '%s\\n' 'Current Phase: implementation' 'Context Acquisition Complete: yes' 'Planned First Write: src/example.py add implementation' > CLAUDE_PROGRESS.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-4}\"\n"
                "    ;;\n"
                "  worktree-change-validation)\n"
                "    printf '# worktree change\\n' > NEW_FILE.md\n"
                "    printf '%s\\n' 'Current Phase: validation' 'Validation command started' > CLAUDE_PROGRESS.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-4}\"\n"
                "    ;;\n"
                "  checker-validation-start)\n"
                "    printf '%s\\n' 'Current Phase: validation' 'Validation command started' > CLAUDE_PROGRESS.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-4}\"\n"
                "    ;;\n"
                "  valid-report)\n"
                "    cat > CLAUDE_REPORT.md <<'REPORT_EOF'\n"
                "# Claude Report\n\n"
                "## Requirements Summary\nDone.\n\n"
                "## Files Changed\n- README.md\n\n"
                "## Acceptance Criteria Mapping\n- complete\n\n"
                "## Out-of-Scope Confirmation\nNone.\n\n"
                "## Plan Match\nfull\n\n"
                "## Checks Run\n- passed\n\n"
                "Implementation complete.\n"
                "REPORT_EOF\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  blocker-recorded)\n"
                "    printf 'Dispatcher-created draft. Permission blocker encountered.\\n' > CLAUDE_REPORT.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  api-error-no-diff)\n"
                "    printf '%s\\n' '{\"is_error\":true,\"result\":\"API Error: Connection closed mid-response\",\"total_cost_usd\":0}'\n"
                "    exit 0\n"
                "    ;;\n"
                "  api-error-with-diff)\n"
                "    printf '# api error work\\n' > README.md\n"
                "    printf '%s\\n' '{\"is_error\":true,\"result\":\"API Error: Connection closed mid-response\",\"total_cost_usd\":0}'\n"
                "    exit 0\n"
                "    ;;\n"
                "  diff-without-report)\n"
                "    printf '# diff work\\n' > README.md\n"
                "    ;;\n"
                "  planner-contract)\n"
                "    printf '%s\\n' '{\"schema_version\":1,\"task_id\":\"fixture\",\"goal\":\"g\",\"end_state\":\"done\",\"invariants\":[],\"non_goals\":[],\"unknowns\":[],\"acceptance\":[],\"slices\":[]}' > solution-contract.draft.json\n"
                "    ;;\n"
                "  empty-file)\n"
                "    : > EMPTY_PLACEHOLDER.py\n"
                "    ;;\n"
                "  delayed-diff)\n"
                "    sleep \"${FAKE_CLAUDE_PRE_DIFF_SLEEP:-1}\"\n"
                "    printf '# delayed worktree change\\n' > NEW_FILE.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-10}\"\n"
                "    ;;\n"
                "  incremental-progress)\n"
                "    printf '# incremental work\\n' > NEW_FILE.md\n"
                "    sleep \"${FAKE_CLAUDE_SLEEP_SECONDS:-4}\"\n"
                "    printf 'Progress update during extension.\\n' > CLAUDE_PROGRESS.md\n"
                "    sleep \"${FAKE_CLAUDE_POST_PROGRESS_SLEEP:-4}\"\n"
                "    ;;\n"
                "  clock-only-progress)\n"
                "    for i in 1 2 3 4 5; do\n"
                "      printf '%s\\n' 'Current Phase: implementation' 'Substantive progress: yes' \"Last Update: ${i}\" > CLAUDE_PROGRESS.md\n"
                "      sleep 1\n"
                "    done\n"
                "    ;;\n"
                "  exit-adjacent-file)\n"
                "    nohup bash -c 'sleep 0.2; printf late > LATE_FILE.py' >/dev/null 2>&1 &\n"
                "    ;;\n"
                "  advisor-request-valid)\n"
                "    printf '# advisor request work\\n' > README.md\n"
                "    _TASK_ID=$(python3 -c \"\n"
                "import re, sys\n"
                "text = open('CLAUDE_TASK_CARD.md', encoding='utf-8').read()\n"
                "m = re.search(r'\\\"task_id\\\":\\s*\\\"([^\\\"]+)\\\"', text)\n"
                "print(m.group(1) if m else 'unknown')\n"
                "\" 2>/dev/null || echo unknown)\n"
                "    cat > ADVISOR_REQUEST.json <<REQ_EOF\n"
                "{\n"
                "  \"schema_version\": 1,\n"
                "  \"task_id\": \"${_TASK_ID}\",\n"
                "  \"direction\": \"on-plan\",\n"
                "  \"blocker\": {\n"
                "    \"kind\": \"semantic\",\n"
                "    \"question\": \"How should I handle this edge case?\",\n"
                "    \"blocking\": true\n"
                "  },\n"
                "  \"completed_work\": \"Implemented main feature\",\n"
                "  \"advisor_used\": false\n"
                "}\n"
                "REQ_EOF\n"
                "    ;;\n"
                "  advisor-request-malformed)\n"
                "    printf '{not valid json' > ADVISOR_REQUEST.json\n"
                "    ;;\n"
                "  advisor-request-mismatch)\n"
                "    cat > ADVISOR_REQUEST.json <<'REQ_EOF'\n"
                "{\n"
                "  \"schema_version\": 1,\n"
                "  \"task_id\": \"different-dispatch-task\",\n"
                "  \"direction\": \"on-plan\",\n"
                "  \"blocker\": {\n"
                "    \"kind\": \"semantic\",\n"
                "    \"question\": \"How should I proceed?\",\n"
                "    \"blocking\": true\n"
                "  },\n"
                "  \"completed_work\": \"Implemented the main feature\",\n"
                "  \"advisor_used\": false\n"
                "}\n"
                "REQ_EOF\n"
                "    ;;\n"
                "  advisor-request-only)\n"
                "    _TASK_ID=$(python3 -c \"\n"
                "import re, sys\n"
                "text = open('CLAUDE_TASK_CARD.md', encoding='utf-8').read()\n"
                "m = re.search(r'\\\"task_id\\\":\\s*\\\"([^\\\"]+)\\\"', text)\n"
                "print(m.group(1) if m else 'unknown')\n"
                "\" 2>/dev/null || echo unknown)\n"
                "    cat > ADVISOR_REQUEST.json <<REQ_EOF\n"
                "{\n"
                "  \"schema_version\": 1,\n"
                "  \"task_id\": \"${_TASK_ID}\",\n"
                "  \"direction\": \"on-plan\",\n"
                "  \"blocker\": {\n"
                "    \"kind\": \"semantic\",\n"
                "    \"question\": \"How should I proceed?\",\n"
                "    \"blocking\": true\n"
                "  },\n"
                "  \"completed_work\": \"No implementation yet\",\n"
                "  \"advisor_used\": false\n"
                "}\n"
                "REQ_EOF\n"
                "    ;;\n"
                "  checker-valid)\n"
                "    mkdir -p tests\n"
                "    printf 'def test_ok():\\n    assert True\\n' > tests/test_fixture.py\n"
                "    cat > CLAUDE_REPORT.md <<'REPORT_EOF'\n"
                "# Claude Modification Report\n\n"
                "## Requirements Summary\nChecker test completed.\n\n"
                "## Files Changed\n- tests/test_fixture.py\n\n"
                "## Acceptance Criteria Mapping\n- test complete\n\n"
                "## Out-of-Scope Confirmation\nNone.\n\n"
                "## Plan Match\nfull\n\n"
                "## Checks Run\n- single-file pytest passed\n\n"
                "Implementation complete.\n"
                "REPORT_EOF\n"
                "    ;;\n"
                "  success)\n"
                "    cat > CLAUDE_REPORT.md <<'REPORT_EOF'\n"
                "# Claude Modification Report\n\n"
                "## Requirements Summary\nDispatch completed.\n\n"
                "## Files Changed\n- README.md\n\n"
                "## Acceptance Criteria Mapping\n- task complete\n\n"
                "## Out-of-Scope Confirmation\nNone.\n\n"
                "## Plan Match\nfull\n\n"
                "## Checks Run\n- bash -n scripts/dispatch-to-claude.sh: passed\n\n"
                "Implementation complete.\n"
                "REPORT_EOF\n"
                "    ;;\n"
                "esac\n"
                "printf '%s\\n' '{\"total_cost_usd\":0,\"usage\":{\"input_tokens\":0,\"output_tokens\":0}}'\n"
            )
        os.chmod(fake, 0o755)

    def _run(self, args, cwd=None, env=None, timeout=60):
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            args,
            cwd=str(cwd or self.repo),
            env=merged_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=True,
        )

    def _write_task_card(self):
        task = self.repo / "task-cards" / "PROJ.md"
        task.parent.mkdir(exist_ok=True)
        task.write_text("# Task\n\nNo-op dispatch fixture.\n", encoding="utf-8")
        return task

    def _write_low_risk_checker_card(self, omit=None, duplicate=None):
        rows = [
            "Public API risk", "Data model risk", "Security risk", "Migration risk",
            "Permission risk", "Concurrency risk", "Cross-module risk", "Production impact",
        ]
        lines = [
            "# Checker", "", "## Task Mode", "", "| Field | Value |", "|---|---|",
            "| Mode | checker-test |", "", "## Checker Reuse Risk Gate", "",
            "| Field | Value |", "|---|---|",
        ]
        for field in rows:
            if field != omit:
                lines.append("| {} | no |".format(field))
        if duplicate:
            lines.append("| {} | no |".format(duplicate))
        task = self.repo / "task-cards" / "CHECKER.md"
        task.parent.mkdir(exist_ok=True)
        task.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return task

    def _write_builder_task_card(self):
        task = self.repo / "task-cards" / "BUILDER.md"
        task.parent.mkdir(exist_ok=True)
        task.write_text(
            "# Task Card\n\n"
            "## Goal\n\nImplement visible work.\n\n"
            "## Task Mode\n\n"
            "| Field | Value |\n|---|---|\n| Mode | builder |\n\n"
            "## Claude Context Packet\n\n"
            "| Field | Value |\n|---|---|\n| Target files/modules | README.md |\n\n"
            "## Handoff Contract\n\nEdit README.\n\n"
            "## Acceptance Criteria\n\n- README changed\n\n"
            "## Testing Responsibility\n\nBuilder runs tests.\n\n"
            "## Validation Contract\n\n"
            "```validation\ntrue\n```\n\n"
            "## Required Report\n\nReport files changed.\n\n"
            "## Implementation Notes\n\nSome implementation notes.\n\n"
            "## Review Checklist\n\n- [ ] Code reviewed\n\n",
            encoding="utf-8",
        )
        return task

    def test_low_risk_checker_defaults_to_reuse_managed(self):
        self._write_low_risk_checker_card()
        result = self._dispatch("task-cards/CHECKER.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Worktree Strategy: reuse-managed", result.stdout)
        self.assertNotIn("Updating files:", result.stdout + result.stderr)

    def test_test_writing_checker_gets_120_second_durable_output_deadline(self):
        task = self._write_low_risk_checker_card()
        with task.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Testing Responsibility\n\n| Responsibility | Owner |\n|---|---|\n"
                "| Test writing | Claude |\n| Narrow validation | Claude |\n"
                "\n## Scope\n\n- Write paths: tests/test_fixture.py\n"
            )
        result = self._dispatch("task-cards/CHECKER.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        runtime = json.loads(self._artifact_path(result.stdout, "Runtime Identity").read_text())
        self.assertEqual(runtime["first_progress_timeout_seconds"], 120)
        self.assertEqual(runtime["first_progress_action"], "stop")
        self.assertFalse(str(runtime["task_tmpdir"]).startswith(str(self.repo)))
        prompt = (self._artifact_path(result.stdout, "Worktree") / "CLAUDE_PROMPT.md").read_text(encoding="utf-8")
        self.assertIn("After each test-file write", prompt)
        self.assertIn("$TMPDIR", prompt)

    def test_test_writing_checker_runtime_enforces_each_file(self):
        task = self._write_low_risk_checker_card()
        with task.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Testing Responsibility\n\n| Responsibility | Owner |\n|---|---|\n"
                "| Test writing | Claude |\n| Narrow validation | Claude |\n"
                "\n## Scope\n\n- Write paths: tests/test_fixture.py\n"
                "\n| Runtime field | Value |\n|---|---|\n"
                "| Per-file validation command | python -m py_compile {path} |\n"
            )
        result = self._dispatch(
            "task-cards/CHECKER.md", {"FAKE_CLAUDE_MODE": "checker-valid"}
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        receipt_path = next((self.repo / ".worktrees").glob("claude-*.checker-contract.json"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertTrue(receipt["enforcement_passed"])
        self.assertEqual(len(receipt["validations"]), 2)

    def test_missing_risk_cannot_be_replaced_by_duplicate(self):
        self._write_low_risk_checker_card(
            omit="Cross-module risk", duplicate="Security risk"
        )
        result = self._dispatch("task-cards/CHECKER.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Worktree Strategy: fresh", result.stdout)

    def test_explicit_fresh_overrides_checker_reuse(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"CLAUDE_CODE_WORKTREE_STRATEGY": "fresh"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Worktree Strategy: fresh", result.stdout)

    def test_approval_blocked_checker_converges_before_fake_exit(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"FAKE_CLAUDE_MODE": "approval-blocked", "FAKE_CLAUDE_SLEEP_SECONDS": "8",
             "CLAUDE_CODE_APPROVAL_CONVERGENCE_HEARTBEATS": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("approval-blocked early convergence", progress.lower())
        self.assertIn("approval_blocked_early_convergence", status)

    def test_approval_convergence_requires_complete_report(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"FAKE_CLAUDE_MODE": "approval-incomplete", "FAKE_CLAUDE_SLEEP_SECONDS": "3"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("approval-blocked early convergence", progress.lower())

    def test_approval_convergence_ignores_unrelated_approval(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"FAKE_CLAUDE_MODE": "approval-unrelated", "FAKE_CLAUDE_SLEEP_SECONDS": "3"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("approval-blocked early convergence", progress.lower())

    def _dispatch(self, task_arg="task-cards/PROJ.md", extra_env=None):
        env = {
            "PATH": str(self.fake_bin) + os.pathsep + os.environ.get("PATH", ""),
            "CLAUDE_CODE_TIMEOUT_SECONDS": "30",
            "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
            "CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS": "0",
            "CLAUDE_CODE_TERMINAL_DRAIN_SECONDS": "0",
            # Most dispatcher tests exercise timing rather than connectivity.
            # Probe-specific tests opt back into startup preflight explicitly.
            "CLAUDE_CODE_API_PROBE_MODE": "failure-only",
            "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "0",
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [BASH, "scripts/dispatch-to-claude.sh", task_arg],
            cwd=str(self.repo),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
        )

    def _artifact_path(self, stdout, label):
        prefix = label + ":"
        for line in stdout.splitlines():
            if line.startswith(prefix):
                return pathlib.Path(line.split(":", 1)[1].strip())
        self.fail(f"missing artifact label {label!r} in output:\n{stdout}")

    def _prepare_advisor_continuation(self):
        task_id, request_id, reservation_id = "prior-advisor", "request-1", "reservation-1"
        root = self.repo / ".worktrees"
        wt, advisor_dir = root / task_id, root / (task_id + ".advisor-request")
        root.mkdir(exist_ok=True)
        base = self._run(["git", "rev-parse", "HEAD"]).stdout.strip()
        self._run(["git", "worktree", "add", "-b", "prior-advisor-branch", str(wt), base])
        (wt / "README.md").write_text("# prior implementation\n", encoding="utf-8")
        diff_hash = self._run(
            [sys.executable, "scripts/worktree_state_hash.py", "--worktree", str(wt)]
        ).stdout.strip()
        response = {
            "schema_version": 1, "request_id": request_id, "advisor": "spark",
            "reservation_id": reservation_id, "evidence_hash": "a" * 64,
            "decision": "continue", "answer": "Finish the bounded change.",
            "allowed_changes": ["README.md"], "forbidden_changes": ["forbidden/"],
            "new_validation": [], "risk_changed": False, "resume_allowed": True,
        }
        packet = {
            "task_id": task_id, "request_id": request_id, "base_commit": base,
            "diff_hash": diff_hash, "evidence_hash": "a" * 64,
            "allowed_changes": ["README.md"], "forbidden_paths": ["forbidden/"],
        }
        (wt / "advisor-packet.json").write_text(json.dumps(packet), encoding="utf-8")
        advisor_dir.mkdir()
        (advisor_dir / "advisor-response-validated.json").write_text(json.dumps(response), encoding="utf-8")
        result = dict(response=response, ok=True, task_id=task_id, request_id=request_id,
                      advisor="spark", reservation_id=reservation_id, evidence_hash="a" * 64,
                      decision="continue", resume_eligible=True)
        result_path = advisor_dir / "advisor-call-result.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        (root / (task_id + ".runtime.json")).write_text(json.dumps({
            "worktree": str(wt), "source_repository": str(self.repo),
            "base_commit": base, "branch": "prior-advisor-branch",
        }), encoding="utf-8")
        self._write_task_card()
        return task_id, result_path, result, root / (task_id + ".advisor-continue-consumed")

    def test_advisor_continuation_binds_broker_result_and_runs_once(self):
        task_id, result_path, result_data, marker = self._prepare_advisor_continuation()
        invocation_log = self.case_root / "claude-invocations.log"
        env = {"CLAUDE_CODE_ADVISOR_CONTINUE_TASK_ID": task_id,
               "CLAUDE_CODE_ALLOW_DIRTY_SOURCE": "1",
               "FAKE_CLAUDE_INVOCATION_LOG": str(invocation_log)}
        result_data["reservation_id"] = "mismatched"
        result_path.write_text(json.dumps(result_data), encoding="utf-8")
        rejected = self._dispatch(extra_env=env)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(invocation_log.exists())
        self.assertFalse(marker.exists())

        result_data["reservation_id"] = "reservation-1"
        result_path.write_text(json.dumps(result_data), encoding="utf-8")
        accepted = self._dispatch(extra_env=env)
        self.assertEqual(accepted.returncode, 0, accepted.stderr + accepted.stdout)
        self.assertEqual(invocation_log.read_text(encoding="utf-8").splitlines(), ["invoke"])
        consumed = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual((consumed["request_id"], consumed["reservation_id"]),
                         ("request-1", "reservation-1"))
        duplicate = self._dispatch(extra_env=env)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(invocation_log.read_text(encoding="utf-8").splitlines(), ["invoke"])

    def test_clean_repo_with_tracked_task_card_succeeds(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Complete", result.stdout)
        self.assertIn("Checker Report:", result.stdout)
        self.assertTrue(list((self.repo / ".worktrees").glob("claude-*.checker-report.md")))
        event_logs = list((self.repo / ".worktrees").glob("claude-*.monitor-events.log"))
        self.assertEqual(len(event_logs), 1)
        events = event_logs[0].read_text(encoding="utf-8").splitlines()
        self.assertIn("event=started", events[0])
        self.assertIn("event=terminal", events[-1])
        self.assertIn("terminal=yes", events[-1])
        rows = (self.repo / ".ai-workflow" / "model-usage.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["role"], "claude")

    def test_untracked_task_card_only_succeeds(self):
        self._write_task_card()

        result = self._dispatch("task-cards/PROJ.md")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Complete", result.stdout)

    def test_untracked_task_card_with_dot_slash_succeeds(self):
        self._write_task_card()

        result = self._dispatch("./task-cards/PROJ.md")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Complete", result.stdout)

    def test_dispatch_prompts_claude_with_execution_card_projection(self):
        task = self._write_task_card()
        task.write_text(
            "\n".join(
                [
                    "# Task Card",
                    "",
                    "## Goal",
                    "",
                    "Implement visible work.",
                    "",
                    "## Goal Loop Contract",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Success signal | README changed |",
                    "",
                    "## Task Mode",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Mode | builder |",
                    "",
                    "## Direction / Boundary Acknowledgement",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Required before editing? | yes |",
                    "| Blocking Codex approval required? | yes |",
                    "| Maximum acknowledgement rounds | 1 |",
                    "",
                    "## Handoff Contract",
                    "",
                    "| Field | Items |",
                    "|-------|-------|",
                    "| Must do | Edit README |",
                    "",
                    "## Codex Context Budget",
                    "",
                    "| Metric | Target |",
                    "|--------|--------|",
                    "| Max Codex context tokens | 1000 |",
                    "",
                    "## High-Token Delegation Gate",
                    "",
                    "- [ ] Full repository scan",
                    "",
                    "## Delegation Continuity Gate",
                    "",
                    "| Check | Value |",
                    "|-------|-------|",
                    "| Remaining implementation/test-writing phases | phase B |",
                    "",
                    "## Direction Review Gate",
                    "",
                    "| Check | Value |",
                    "|-------|-------|",
                    "| Builder diff matches planned direction? | yes/no/partial |",
                    "",
                    "## Acceptance Criteria",
                    "",
                    "- [ ] README changed",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        capture = self.case_root / "captured-prompt.md"

        result = self._dispatch(extra_env={"FAKE_CLAUDE_PROMPT_CAPTURE": str(capture)})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        prompt = capture.read_text(encoding="utf-8")
        self.assertIn("--- CLAUDE EXECUTION CARD ---", prompt)
        self.assertIn("Core rules:", prompt)
        self.assertIn("Implement visible work.", prompt)
        self.assertIn("## Task Mode", prompt)
        self.assertIn("## Direction / Boundary Acknowledgement", prompt)
        self.assertIn("Maximum acknowledgement rounds", prompt)
        self.assertIn("## Handoff Contract", prompt)
        self.assertIn("## Acceptance Criteria", prompt)
        self.assertNotIn("## Codex Context Budget", prompt)
        self.assertNotIn("Max Codex context tokens", prompt)
        self.assertNotIn("## High-Token Delegation Gate", prompt)
        self.assertNotIn("## Delegation Continuity Gate", prompt)
        self.assertNotIn("## Direction Review Gate", prompt)
        self.assertNotIn("## Goal Loop Contract", prompt)
        self.assertNotIn("Phase-gate requirements:", prompt)

        worktree = self._artifact_path(result.stdout, "Worktree")
        full_card = (worktree / "TASK_CARD_FULL.md").read_text(encoding="utf-8")
        claude_card = (worktree / "CLAUDE_TASK_CARD.md").read_text(encoding="utf-8")
        self.assertIn("## Codex Context Budget", full_card)
        self.assertIn("## Goal Loop Contract", full_card)
        self.assertNotIn("## Codex Context Budget", claude_card)
        self.assertNotIn("## Direction Review Gate", claude_card)
        self.assertNotIn("## Goal Loop Contract", claude_card)
        self.assertIn("## Task Mode", claude_card)
        self.assertIn("## Direction / Boundary Acknowledgement", claude_card)
        self.assertIn("## Acceptance Criteria", claude_card)

    def test_safe_execution_profile_restores_standard_prompt_and_execution_view(self):
        task = self._write_task_card()
        task.write_text(
            "\n".join(
                [
                    "# Task Card",
                    "",
                    "## Goal",
                    "",
                    "Implement visible work.",
                    "",
                    "## Goal Loop Contract",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Success signal | README changed |",
                    "",
                    "## Task Mode",
                    "",
                    "| Field | Value |",
                    "|-------|-------|",
                    "| Mode | builder |",
                    "",
                    "## Acceptance Criteria",
                    "",
                    "- [ ] README changed",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        capture = self.case_root / "safe-profile-prompt.md"

        result = self._dispatch(
            extra_env={
                "CLAUDE_CODE_EXECUTION_PROFILE": "safe",
                "FAKE_CLAUDE_PROMPT_CAPTURE": str(capture),
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        prompt = capture.read_text(encoding="utf-8")
        self.assertIn("Phase-gate requirements:", prompt)
        self.assertIn("## Goal Loop Contract", prompt)
        self.assertIn("Execution Profile: safe", result.stdout)
        self.assertIn("Prompt Profile:  standard", result.stdout)

    def test_fast_large_repo_profile_uses_summary_evidence(self):
        self._write_task_card()

        result = self._dispatch(
            extra_env={
                "CLAUDE_CODE_EXECUTION_PROFILE": "fast-large-repo",
                "FAKE_CLAUDE_MODE": "stage-change",
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Execution Profile: fast-large-repo", result.stdout)
        self.assertIn("Worktree Strategy: reuse-managed", result.stdout)
        self.assertIn("Large Repo Mode: 1", result.stdout)
        self.assertIn("Evidence Mode:   summary", result.stdout)
        runtime = json.loads(self._artifact_path(result.stdout, "Runtime Identity").read_text(encoding="utf-8"))
        self.assertEqual(runtime["context_acquisition_timeout_seconds"], 420)
        worktree = self._artifact_path(result.stdout, "Worktree")
        self.assertEqual(worktree, self.repo / ".worktrees" / "reuse" / "claude-managed")
        diff = self._artifact_path(result.stdout, "Diff").read_text(encoding="utf-8")
        self.assertIn("Evidence mode: summary", diff)
        self.assertIn("Full patch generation was skipped", diff)
        self.assertIn("README.md", diff)
        self.assertNotIn("# staged by claude", diff)

        second = self._dispatch(extra_env={"CLAUDE_CODE_EXECUTION_PROFILE": "fast-large-repo"})
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("reusable managed worktree already exists", second.stderr)
        self.assertIn("CLAUDE_CODE_REUSE_WORKTREE_RESET=1", second.stderr)

    def test_checker_progress_distinguishes_validation_skipped_by_policy(self):
        shutil.copy2(CHECK_WORKTREE, self.repo / "scripts" / "check-worktree.sh")
        self._run(["git", "add", "scripts/check-worktree.sh"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add checker"], cwd=self.repo)
        task = self._write_task_card()
        task.write_text(
            "# Task\n\n"
            "## Validation Contract\n\n"
            "| Check | Command | Required? | Notes |\n"
            "|-------|---------|-----------|-------|\n"
            "| Local validation allowed? | no | required | commands only |\n\n"
            "```bash validation\n"
            "false\n"
            "```\n",
            encoding="utf-8",
        )

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        checker = self._artifact_path(result.stdout, "Checker Report").read_text(encoding="utf-8")
        self.assertIn("artifact collection OK; validation skipped by policy", progress)
        self.assertIn("SKIPPED by policy", checker)
        self.assertNotIn("Checker helper completed: ALL GREEN", progress)

    def test_unrelated_untracked_file_blocks(self):
        self._write_task_card()
        (self.repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")

        result = self._dispatch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale HEAD", result.stderr)
        self.assertIn("scratch.txt", result.stderr)
        worktrees = self.repo / ".worktrees"
        artifacts = sorted(p.name for p in worktrees.glob("claude-*")) if worktrees.exists() else []
        self.assertEqual([], artifacts)

    def test_tracked_diff_blocks(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)
        (self.repo / "README.md").write_text("# changed\n", encoding="utf-8")

        result = self._dispatch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tracked changes", result.stderr)
        self.assertIn("README.md", result.stderr)

    def test_staged_diff_blocks(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)
        (self.repo / "README.md").write_text("# staged\n", encoding="utf-8")
        self._run(["git", "add", "README.md"], cwd=self.repo)

        result = self._dispatch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Staged changes", result.stderr)
        self.assertIn("README.md", result.stderr)

    def test_untracked_root_control_is_archived_and_does_not_block(self):
        self._write_task_card()
        (self.repo / "CLAUDE_PROGRESS.md").write_text("historical control\n", encoding="utf-8")

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        receipts = list((self.repo / ".worktrees").glob("claude-*.control-archive.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["archived_paths"], ["CLAUDE_PROGRESS.md"])
        self.assertTrue((self.repo / "CLAUDE_PROGRESS.md").is_file())

    def test_allow_dirty_override_succeeds_with_warning(self):
        self._write_task_card()
        (self.repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")

        result = self._dispatch(extra_env={"CLAUDE_CODE_ALLOW_DIRTY_SOURCE": "1"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("CLAUDE_CODE_ALLOW_DIRTY_SOURCE=1", result.stderr)
        self.assertIn("Dispatch Complete", result.stdout)
        self.assertIn("Source status saved to:", result.stdout)
        self.assertTrue(list((self.repo / ".worktrees").glob("claude-*.source-status.txt")))

    def test_allow_dirty_blocks_task_relevant_untracked_module_missing_from_fresh_worktree(self):
        card = self._write_task_card()
        card.write_text(card.read_text(encoding="utf-8") + "\nTarget: `src/headless/`\n", encoding="utf-8")
        (self.repo / "src/headless").mkdir(parents=True)
        (self.repo / "src/headless/main.ts").write_text("new module\n", encoding="utf-8")

        result = self._dispatch(extra_env={"CLAUDE_CODE_ALLOW_DIRTY_SOURCE": "1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dispatch preflight blocked", result.stderr)
        evidence = list((self.repo / ".worktrees").glob("claude-*.dispatch-preflight.json"))
        self.assertEqual(len(evidence), 1)
        payload = json.loads(evidence[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["blocked_paths"], ["src/headless/main.ts"])

    def test_dirty_snapshot_places_uncommitted_module_in_fresh_isolated_worktree(self):
        card = self._write_task_card()
        card.write_text(card.read_text(encoding="utf-8") + "\nTarget: `src/headless/`\n", encoding="utf-8")
        (self.repo / "src/headless").mkdir(parents=True)
        (self.repo / "src/headless/main.ts").write_text("new module\n", encoding="utf-8")
        source_head = self._run(["git", "rev-parse", "HEAD"]).stdout.strip()

        result = self._dispatch(extra_env={"CLAUDE_CODE_DIRTY_SOURCE_MODE": "snapshot"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        receipt_path = next((self.repo / ".worktrees").glob("claude-*.dirty-snapshot.json"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        runtime_path = next((self.repo / ".worktrees").glob("claude-*.runtime.json"))
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        worktree = pathlib.Path(runtime["worktree"])
        self.assertEqual((worktree / "src/headless/main.ts").read_text(encoding="utf-8"), "new module\n")
        self.assertEqual(runtime["base_commit"], source_head)
        self.assertEqual(runtime["worktree_start_commit"], receipt["snapshot_commit"])
        self.assertEqual(self._run(["git", "rev-parse", "HEAD"]).stdout.strip(), source_head)

    def test_workspace_trust_preflight_stops_before_builder_window(self):
        self._write_task_card()
        result = self._dispatch(extra_env={
            "CLAUDE_CODE_API_PROBE_MODE": "always",
            "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "1",
            "FAKE_CLAUDE_HEALTHCHECK_TRUST": "1",
        })
        self.assertEqual(result.returncode, 75)
        self.assertIn("workspace-not-trusted", result.stderr)
        result_file = next((self.repo / ".worktrees").glob("claude-*.result.json"))
        payload = json.loads(result_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["dispatch_outcome"], "preflight-blocked")
        self.assertFalse(payload["builder_started"])

    def test_reuse_managed_worktree_requires_explicit_reset_when_existing(self):
        self._write_task_card()

        first = self._dispatch(extra_env={"CLAUDE_CODE_WORKTREE_STRATEGY": "reuse-managed"})

        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        worktree = self._artifact_path(first.stdout, "Worktree")
        self.assertEqual(worktree, self.repo / ".worktrees" / "reuse" / "claude-managed")
        self.assertIn("Worktree Strategy: reuse-managed", first.stdout)

        second = self._dispatch(extra_env={"CLAUDE_CODE_WORKTREE_STRATEGY": "reuse-managed"})

        self.assertNotEqual(second.returncode, 0)
        self.assertIn("reusable managed worktree already exists", second.stderr)
        self.assertIn("CLAUDE_CODE_REUSE_WORKTREE_RESET=1", second.stderr)

        third = self._dispatch(
            extra_env={
                "CLAUDE_CODE_WORKTREE_STRATEGY": "reuse-managed",
                "CLAUDE_CODE_REUSE_WORKTREE_RESET": "1",
            }
        )

        self.assertEqual(third.returncode, 0, third.stderr + third.stdout)
        self.assertEqual(self._artifact_path(third.stdout, "Worktree"), worktree)
        source_status = self._artifact_path(third.stdout, "Source Status").read_text(encoding="utf-8")
        self.assertIn("- Strategy: reuse-managed", source_status)
        self.assertIn("- Reuse reset allowed: 1", source_status)

    def test_large_repo_mode_skips_expensive_untracked_evidence(self):
        self._write_task_card()
        (self.repo / "scratch.txt").write_text("dirty but intentionally skipped in large mode\n", encoding="utf-8")

        result = self._dispatch(extra_env={"CLAUDE_CODE_LARGE_REPO_MODE": "1"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Large Repo Mode: 1", result.stdout)
        source_status = self._artifact_path(result.stdout, "Source Status").read_text(encoding="utf-8")
        diffstat = self._artifact_path(result.stdout, "Diffstat").read_text(encoding="utf-8")
        untracked = self._artifact_path(result.stdout, "Untracked Files").read_text(encoding="utf-8")
        self.assertIn("- Large repo mode: 1", source_status)
        self.assertIn("skipped: CLAUDE_CODE_LARGE_REPO_MODE=1", source_status)
        self.assertIn("skipped: CLAUDE_CODE_LARGE_REPO_MODE=1", diffstat)
        self.assertIn("skipped: CLAUDE_CODE_LARGE_REPO_MODE=1", untracked)

    def test_claude_early_exit_without_result_gets_fallback_artifacts(self):
        self._write_task_card()

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "fail-empty"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("claude exited with non-zero status 42", result.stderr)
        result_file = self._artifact_path(result.stdout, "Result")
        report_file = self._artifact_path(result.stdout, "Report")
        progress_file = self._artifact_path(result.stdout, "Claude Progress")
        raw_result_file = self._artifact_path(result.stdout, "Raw Result")
        data = json.loads(result_file.read_text(encoding="utf-8"))
        self.assertTrue(data["fallback"])
        self.assertEqual(data["claude_exit_status"], 42)
        self.assertTrue(raw_result_file.exists())
        report = report_file.read_text(encoding="utf-8")
        self.assertIn("AI-CODING-WORKFLOW:DISPATCH-FALLBACK-REPORT", report)
        self.assertIn("This fallback report is not a valid Claude report.", report)
        self.assertIn("Evidence classification: seeded report only", report)
        self.assertIn("Implementation changes: 0", report)
        self.assertIn("Claude exit status: 42", report)
        self.assertIn("Fallback result generated: yes", report)
        progress = progress_file.read_text(encoding="utf-8")
        self.assertIn("dispatch-started", progress)
        self.assertIn("- [ ] Context gathered", progress)

    def test_network_monitor_writes_metadata_log_when_enabled(self):
        self._write_task_card()

        result = self._dispatch(extra_env={"CLAUDE_CODE_NETWORK_MONITOR": "1"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        network_file = self._artifact_path(result.stdout, "Network Log")
        progress_file = self._artifact_path(result.stdout, "Progress Log")
        network = network_file.read_text(encoding="utf-8")
        progress = progress_file.read_text(encoding="utf-8")
        self.assertIn("Claude Network Diagnostics", network)
        self.assertIn("Network monitoring is metadata-only", network)
        self.assertIn("CLAUDE_CODE_NETWORK_MONITOR: 1", network)
        self.assertIn("CLAUDE_CODE_PROXY_MODE:", network)
        self.assertIn("Socket Snapshots", network)
        self.assertIn("Final network snapshot:", progress)

    def test_staged_claude_changes_are_in_combined_diff(self):
        self._write_task_card()

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "stage-change"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        diff_file = self._artifact_path(result.stdout, "Diff")
        diffstat_file = self._artifact_path(result.stdout, "Diffstat")
        diff = diff_file.read_text(encoding="utf-8")
        diffstat = diffstat_file.read_text(encoding="utf-8")
        self.assertIn("## Staged Diff", diff)
        self.assertIn("+# staged by claude", diff)
        self.assertIn("## Staged Changes", diffstat)
        self.assertIn("README.md", diffstat)

    # --- Runtime identity and retry-in-place tests ---

    def _do_fresh_dispatch(self):
        """Run a fresh dispatch and return (result, worktree_path, runtime_dict)."""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)
        result = self._dispatch()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        worktree = self._artifact_path(result.stdout, "Worktree")
        runtime_path = self._artifact_path(result.stdout, "Runtime Identity")
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        return result, worktree, runtime

    def test_fresh_dispatch_writes_valid_runtime_json(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        runtime_path = self._artifact_path(result.stdout, "Runtime Identity")
        self.assertTrue(runtime_path.exists())
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(runtime["schema_version"], 1)
        self.assertIn("task_id", runtime)
        self.assertIn("worktree", runtime)
        self.assertIn("base_commit", runtime)
        self.assertIn("source_repository", runtime)
        self.assertIn("branch", runtime)
        self.assertIn("strategy", runtime)
        self.assertIn("pid_files", runtime)
        self.assertIn("dispatcher", runtime["pid_files"])
        self.assertIn("claude", runtime["pid_files"])
        self.assertIn("checker", runtime["pid_files"])
        self.assertIn("pid", runtime["pid_files"])
        worktree = self._artifact_path(result.stdout, "Worktree")
        self.assertEqual(pathlib.Path(runtime["worktree"]), worktree)
        self.assertEqual(pathlib.Path(runtime["source_repository"]), self.repo)
        self.assertEqual(len(runtime["base_commit"]), 40)
        self.assertEqual(runtime["strategy"], "fresh")
        self.assertEqual(runtime["retry_ordinal"], 0)
        self.assertEqual(runtime["lineage_root_task_id"], runtime["task_id"])
        self.assertEqual(runtime["claude_session_mode"], "new")
        self.assertEqual(len(runtime["claude_session_id"]), 36)
        self.assertEqual(runtime["codegraph_policy"], "fallback")
        self.assertEqual(runtime["codegraph_execution_status"], "not-requested")
        self.assertFalse(runtime["codegraph_safe_to_use"])
        codegraph_receipt = pathlib.Path(runtime["codegraph_worktree_receipt"])
        self.assertTrue(codegraph_receipt.is_file())
        receipt = json.loads(codegraph_receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt["reason"], "source-not-indexed")
        self.assertIn("process_identity_files", runtime)
        self.assertNotIn("retry_of", runtime)

    def test_retry_in_place_reuses_prior_worktree_with_new_task_id(self):
        _, first_worktree, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]

        second = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        second_worktree = self._artifact_path(second.stdout, "Worktree")
        second_runtime_path = self._artifact_path(second.stdout, "Runtime Identity")
        second_runtime = json.loads(second_runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(first_worktree, second_worktree)
        self.assertNotEqual(first_runtime["task_id"], second_runtime["task_id"])
        self.assertEqual(second_runtime["retry_of"], prior_task_id)
        self.assertEqual(second_runtime["strategy"], "retry-in-place")
        self.assertEqual(second_runtime["retry_ordinal"], 1)
        self.assertEqual(second_runtime["lineage_root_task_id"], first_runtime["task_id"])
        self.assertEqual(second_runtime["claude_session_mode"], "resume")
        self.assertEqual(second_runtime["claude_session_resume_status"], "prior-runtime")
        self.assertEqual(second_runtime["claude_session_id"], first_runtime["claude_session_id"])
        self.assertIn("retry-in-place", second.stdout)

        third = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": second_runtime["task_id"]}
        )
        self.assertNotEqual(third.returncode, 0)
        self.assertIn("retry budget exhausted", third.stderr)

    def test_two_linked_execution_timeouts_issue_bounded_takeover_receipt(self):
        task = self._write_builder_task_card()
        with task.open("a", encoding="utf-8") as handle:
            handle.write("\n## Scope\n\n- Write paths: README.md\n- Forbidden paths: deploy/\n")
        timeout_env = {
            "CLAUDE_CODE_BUILDER_MODE": "execution-only",
            "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "1",
            "CLAUDE_CODE_FIRST_PROGRESS_ACTION": "stop",
            "CLAUDE_CODE_API_PROBE_MODE": "adaptive",
            "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "1",
            "FAKE_CLAUDE_MODE": "seed-only",
        }
        first = self._dispatch("task-cards/BUILDER.md", timeout_env)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        first_runtime_path = self._artifact_path(first.stdout, "Runtime Identity")
        first_task_id = json.loads(first_runtime_path.read_text(encoding="utf-8"))["task_id"]
        first_attempt = json.loads(self._artifact_path(first.stdout, "Attempt Class").read_text(encoding="utf-8"))
        self.assertEqual(first_attempt["failure_class"], "model-no-progress")

        second = self._dispatch(
            "task-cards/BUILDER.md",
            {**timeout_env, "CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": first_task_id},
        )
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        receipt_path = self._artifact_path(second.stdout, "Takeover Receipt")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["authorization"], "codex-bounded-takeover")
        self.assertEqual(receipt["allowed_write_paths"], ["README.md"])
        self.assertFalse(receipt["merge_authorized"])

    def test_retry_in_place_rejects_missing_runtime_json(self):
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": "nonexistent-task-id"}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prior runtime.json not found", result.stderr)

    def test_retry_in_place_rejects_unknown_untracked_files(self):
        _, first_worktree, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        (first_worktree / "scratch.txt").write_text("dirty\n", encoding="utf-8")

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown untracked files", result.stderr)
        self.assertIn("scratch.txt", result.stderr)

    def test_retry_in_place_accepts_known_control_files(self):
        _, first_worktree, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        for name in ["CLAUDE_REPORT.md", "CLAUDE_PROGRESS.md", "TASK_CARD.md",
                      "TASK_CARD_FULL.md", "CLAUDE_TASK_CARD.md", "CLAUDE_PROMPT.md"]:
            (first_worktree / name).write_text(f"# {name}\n", encoding="utf-8")

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("retry-in-place", result.stdout)

    def test_retry_in_place_rejects_managed_prior_strategy(self):
        self._write_task_card()
        first = self._dispatch(
            extra_env={"CLAUDE_CODE_WORKTREE_STRATEGY": "reuse-managed"}
        )
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        first_runtime = json.loads(
            self._artifact_path(first.stdout, "Runtime Identity").read_text(encoding="utf-8")
        )
        prior_task_id = first_runtime["task_id"]

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reuse-managed", result.stderr)

    def test_retry_in_place_rejects_live_role_pid(self):
        _, _, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        pid_file = self.repo / ".worktrees" / f"{prior_task_id}.claude.pid"
        sleeper, bash_pid = start_bash_sleeper()
        try:
            pid_file.write_text(bash_pid, encoding="utf-8")
            identity_file = self.repo / ".worktrees" / f"{prior_task_id}.claude.process.json"
            subprocess.run(
                [sys.executable, str(self.repo / "scripts" / "process-identity.py"),
                 "capture", "--pid", bash_pid, "--task-id", prior_task_id,
                 "--role", "claude", "--output", str(identity_file)],
                check=True, capture_output=True, text=True,
            )
            result = self._dispatch(
                extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
            )
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=10)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("still running", result.stderr)

    def test_retry_in_place_rejects_competing_reservation(self):
        _, _, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        reservation = self.repo / ".worktrees" / f".retry-lock-{prior_task_id}"
        reservation.mkdir()

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reservation already exists", result.stderr)

    def test_retry_in_place_rejects_source_diff_in_worktree(self):
        _, first_worktree, first_runtime = self._do_fresh_dispatch()
        prior_task_id = first_runtime["task_id"]
        (first_worktree / "README.md").write_text("# modified\n", encoding="utf-8")

        result = self._dispatch(
            extra_env={"CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID": prior_task_id}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tracked changes", result.stderr)

    def test_reviewed_continuation_reuses_dirty_fresh_worktree_once(self):
        first_card = self._write_task_card()
        first_card.write_text(
            "# Builder\n\n| Field | Value |\n|---|---|\n| Mode | builder |\n",
            encoding="utf-8",
        )
        next_card = self.repo / "task-cards" / "NEXT.md"
        next_card.write_text(
            "# Revision Builder\n\n| Field | Value |\n|---|---|\n| Mode | builder |\n",
            encoding="utf-8",
        )
        self._run(["git", "add", "task-cards/PROJ.md", "task-cards/NEXT.md"])
        self._run(["git", "commit", "-m", "add continuation cards"])
        first = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "diff-without-report"})
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        prior_runtime_path = self._artifact_path(first.stdout, "Runtime Identity")
        prior_runtime = json.loads(prior_runtime_path.read_text(encoding="utf-8"))
        prior_worktree = pathlib.Path(prior_runtime["worktree"])
        self.assertEqual(prior_runtime["strategy"], "fresh")
        self.assertTrue(
            (prior_worktree / "README.md").read_text(encoding="utf-8").startswith("# diff work")
        )

        approval = self.repo / ".worktrees" / "reviewed-approval.json"
        prepared = self._run([
            sys.executable, "scripts/prepare-worktree-continuation.py", "prepare",
            "--prior-task-id", prior_runtime["task_id"],
            "--next-task-card", str(next_card),
            "--next-role", "builder",
            "--decision", "accepted-direction",
            "--accepted-existing-path", "README.md",
            "--allow-new-write-path", "README.md",
            "--output", str(approval),
        ])
        self.assertEqual(json.loads(prepared.stdout)["status"], "available")

        second = self._dispatch(
            "task-cards/NEXT.md",
            {
                "CLAUDE_CODE_REVIEWED_CONTINUATION": str(approval),
                "FAKE_CLAUDE_MODE": "diff-without-report",
            },
        )
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertIn("Worktree Strategy: reviewed-continuation", second.stdout)
        self.assertEqual(self._artifact_path(second.stdout, "Worktree"), prior_worktree)
        second_runtime = json.loads(
            self._artifact_path(second.stdout, "Runtime Identity").read_text(encoding="utf-8")
        )
        self.assertEqual(second_runtime["strategy"], "reviewed-continuation")
        self.assertEqual(second_runtime["reviewed_continuation_of"], prior_runtime["task_id"])
        self.assertIsNone(second_runtime["worktree_setup_seconds"])

        replay = self._dispatch(
            "task-cards/NEXT.md",
            {"CLAUDE_CODE_REVIEWED_CONTINUATION": str(approval)},
        )
        self.assertNotEqual(replay.returncode, 0)
        self.assertIn("already consumed", replay.stderr)

    def test_zero_byte_untracked_placeholder_is_not_implementation_progress(self):
        self._write_task_card()
        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "empty-file"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("[dispatch] Implementation changes: 0", status)
        self.assertIn("[dispatch] Evidence classification: seeded report only", status)
        self.assertIn("[dispatch] Dispatch outcome: no_useful_progress", status)

    def test_progress_log_includes_child_exit_transition(self):
        self._write_task_card()

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("Claude child exited:", progress)
        self.assertIn("entering bounded terminal drain", progress)

    # --- Execution-only builder mode and first-progress timeout tests ---

    def test_invalid_builder_mode_fails_before_worktree_creation(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"CLAUDE_CODE_BUILDER_MODE": "invalid"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLAUDE_CODE_BUILDER_MODE must be", result.stderr)
        worktrees = self.repo / ".worktrees"
        artifacts = sorted(p.name for p in worktrees.glob("claude-*")) if worktrees.exists() else []
        self.assertEqual([], artifacts)

    def test_execution_only_non_builder_fails_before_worktree_creation(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"CLAUDE_CODE_BUILDER_MODE": "execution-only"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("execution-only requires task mode 'builder'", result.stderr)

    def test_standard_defaults_to_timeout_zero_and_preserves_headings(self):
        self._write_builder_task_card()
        result = self._dispatch("task-cards/BUILDER.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("First Progress:  0s observation", result.stdout)
        worktree = self._artifact_path(result.stdout, "Worktree")
        claude_card = (worktree / "CLAUDE_TASK_CARD.md").read_text(encoding="utf-8")
        self.assertIn("## Task Mode", claude_card)
        self.assertIn("## Goal", claude_card)
        self.assertIn("## Acceptance Criteria", claude_card)
        self.assertNotIn("execution-only view", claude_card.lower())

    def test_execution_only_defaults_to_120_second_stop_and_compact_prompt(self):
        self._write_builder_task_card()
        capture = self.case_root / "execution-only-prompt.md"
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "FAKE_CLAUDE_PROMPT_CAPTURE": str(capture),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("First Progress:  120s observation", result.stdout)
        self.assertIn("Builder Mode:    execution-only", result.stdout)
        runtime = json.loads(self._artifact_path(result.stdout, "Runtime Identity").read_text())
        self.assertEqual(runtime["first_progress_timeout_seconds"], 120)
        self.assertEqual(runtime["first_progress_action"], "stop")
        worktree = self._artifact_path(result.stdout, "Worktree")
        claude_card = (worktree / "CLAUDE_TASK_CARD.md").read_text(encoding="utf-8")
        self.assertIn("## Goal", claude_card)
        self.assertIn("## Task Mode", claude_card)
        self.assertIn("## Handoff Contract", claude_card)
        self.assertIn("## Acceptance Criteria", claude_card)
        self.assertIn("## Validation Contract", claude_card)
        self.assertNotIn("## Implementation Notes", claude_card)
        self.assertNotIn("## Review Checklist", claude_card)
        self.assertIn("execution-only view", claude_card.lower())
        prompt = capture.read_text(encoding="utf-8")
        self.assertIn("execution-only Builder mode", prompt)
        self.assertIn("Do NOT restate or redesign the plan", prompt)
        self.assertIn("--- CLAUDE EXECUTION CARD ---", prompt)

    def test_exploratory_card_auto_selects_exploratory_prompt_and_locator_tools(self):
        task = self._write_builder_task_card()
        text = task.read_text(encoding="utf-8").replace(
            "| Mode | builder |", "| Mode | builder |\n| Builder mode | exploratory |"
        )
        task.write_text(text, encoding="utf-8")
        capture = self.case_root / "exploratory-prompt.md"
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"FAKE_CLAUDE_PROMPT_CAPTURE": str(capture), "CLAUDE_CODE_TOOL_PROFILE": "auto"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Builder Mode:    exploratory", result.stdout)
        self.assertIn("Tool Profile:    locator-builder", result.stdout)
        prompt = capture.read_text(encoding="utf-8")
        self.assertIn("exploratory executor", prompt)
        self.assertIn("Do not finish with only a repository summary", prompt)
        self.assertIn("Produce at least one durable assigned output", prompt)

    def test_solution_planner_card_auto_selects_planning_prompt_and_locator_tools(self):
        task = self._write_builder_task_card()
        with task.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Claude Solution Planner Contract\n\n"
                "| Field | Value |\n|---|---|\n"
                "| Planning owner | Claude |\n"
                "| Required durable output | `solution-contract.draft.json` |\n"
                "\n## Solution Contract Inputs\n\n- Goal: fixture\n"
                "\n## Required Draft Shape\n\nValidate the JSON draft.\n"
            )
        capture = self.case_root / "solution-planner-prompt.md"
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"FAKE_CLAUDE_PROMPT_CAPTURE": str(capture), "CLAUDE_CODE_TOOL_PROFILE": "auto"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Builder Mode:    solution-planning", result.stdout)
        self.assertIn("Tool Profile:    locator-builder", result.stdout)
        prompt = capture.read_text(encoding="utf-8")
        self.assertIn("solution planner in a Codex/Claude Code workflow", prompt)
        self.assertIn("Do not edit product source", prompt)
        self.assertIn("Claude Solution Planner Contract", prompt)
        self.assertIn("contract-validation", prompt)
        worktree = self._artifact_path(result.stdout, "Worktree")
        progress = (worktree / "CLAUDE_PROGRESS.md").read_text(encoding="utf-8")
        self.assertIn("Execution Phase: planning", progress)

    def test_batch_card_auto_selects_batch_prompt_and_minimal_tools(self):
        task = self._write_builder_task_card()
        with task.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Batch Builder Gate\n\n"
                "| Field | Value |\n|---|---|\n"
                "| Transformation rule | deterministic replacement |\n"
                "| Independent write units | src/a.py, src/b.py |\n"
            )
        capture = self.case_root / "batch-prompt.md"
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"FAKE_CLAUDE_PROMPT_CAPTURE": str(capture), "CLAUDE_CODE_TOOL_PROFILE": "auto"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Builder Mode:    batch", result.stdout)
        self.assertIn("Tool Profile:    minimal-builder", result.stdout)
        prompt = capture.read_text(encoding="utf-8")
        self.assertIn("batch executor in a Codex/Claude Code workflow", prompt)
        self.assertIn("Never broaden the batch", prompt)
        self.assertIn("Batch Builder Gate", prompt)

    def test_solution_planner_allows_only_structured_contract_artifact(self):
        task = self._write_builder_task_card()
        with task.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Claude Solution Planner Contract\n\n"
                "| Field | Value |\n|---|---|\n| Planning owner | Claude |\n"
            )
        result = self._dispatch(
            "task-cards/BUILDER.md", {"FAKE_CLAUDE_MODE": "planner-contract"}
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Outcome:success", result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("Solution-planner output scope PASSED", progress)

    def test_solution_planner_source_edit_is_scope_violation(self):
        task = self._write_builder_task_card()
        with task.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Claude Solution Planner Contract\n\n"
                "| Field | Value |\n|---|---|\n| Planning owner | Claude |\n"
            )
        result = self._dispatch(
            "task-cards/BUILDER.md", {"FAKE_CLAUDE_MODE": "diff-without-report"}
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Outcome:scope_violation", result.stdout)
        self.assertIn("solution-planner changed paths", result.stderr)

    def test_first_progress_timeout_legacy_alias_is_honored(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT": "2",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("First Progress:  2s observation", result.stdout)
        runtime = json.loads(self._artifact_path(result.stdout, "Runtime Identity").read_text())
        self.assertEqual(runtime["first_progress_timeout_seconds"], 2)
        self.assertEqual(runtime["first_progress_timeout_source"], "alias(CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT)")

    def test_auto_builder_mode_uses_execution_only_only_with_explicit_gates(self):
        task = self._write_builder_task_card()
        with task.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Claude Context Packet\n\n| Field | Value |\n|---|---|\n"
                "| Context is sufficient for execution? | yes |\n"
                "| Execution-only eligible? | yes |\n"
            )
        self._run(["git", "add", "task-cards/BUILDER.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add auto builder task"], cwd=self.repo)
        result = self._dispatch("task-cards/BUILDER.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Builder Mode:    execution-only", result.stdout)
        self.assertIn("First Progress:  120s observation", result.stdout)

    def test_seed_only_stopped_at_short_deadline(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_FIRST_PROGRESS_ACTION": "stop",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("first_progress_timeout", progress.lower())
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("First-progress timed out: yes", status)

    def test_zero_output_runs_fixed_api_probe_before_attribution(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        probe_path = self._artifact_path(result.stdout, "API Probe")
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        self.assertEqual(probe["interaction_conclusion"], "available")
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("fixed prompt", progress)
        self.assertIn("conclusion=available", progress)

    def test_failed_zero_output_probe_does_not_count_toward_takeover(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "0",
                "FAKE_CLAUDE_MODE": "seed-only",
                "FAKE_CLAUDE_HEALTHCHECK_FAIL": "1",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Dispatch outcome: network_error", status)
        self.assertIn("Counts toward takeover: false", status)
        probe = json.loads(self._artifact_path(result.stdout, "API Probe").read_text(encoding="utf-8"))
        self.assertEqual(probe["interaction_conclusion"], "unavailable-in-current-environment")

    def test_useful_result_skips_zero_output_probe(self):
        self._write_builder_task_card()
        result = self._dispatch("task-cards/BUILDER.md", {"FAKE_CLAUDE_MODE": "success"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self._artifact_path(result.stdout, "API Probe").stat().st_size, 0)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("conclusion=not-run", progress)

    def test_retry_broker_arguments_preserve_logical_task_and_plan_budget(self):
        dispatch = DISPATCH.read_text(encoding="utf-8")
        self.assertIn('--task-id "${_RETRY_TASK_ID:-$TASK_ID}"', dispatch)
        self.assertIn('broker_args+=(--max-calls 2 --retry-failed)', dispatch)
        self.assertIn('if [ -f "execution-plan.json" ]', dispatch)
        self.assertIn('elif [ -n "${CLAUDE_CODE_RETRY_IN_PLACE_TASK_ID:-}" ]', dispatch)
        self.assertIn('if [ "$ZERO_OUTPUT_PROBE_CONCLUSION" != "not-run" ]', dispatch)
        self.assertIn('The diagnostic probe never updates learned route preference', dispatch)

    def test_source_diff_prevents_first_progress_timeout(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "worktree-change",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("first_progress_detected=1", progress)
        self.assertIn("signal=builder_worktree_change", progress)

    def test_generic_progress_update_does_not_refresh_execution_window(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_FIRST_PROGRESS_ACTION": "observe",
                "CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "progress-update",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("First substantive progress detected", progress)
        self.assertIn("context acquisition timeout", progress)

    def test_valid_report_prevents_first_progress_timeout(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "valid-report",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("first_progress_detected=1", progress)
        self.assertIn("signal=valid_report", progress)

    def test_builder_editing_readiness_does_not_count_as_durable_progress(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_TIMEOUT_SECONDS": "5",
                "CLAUDE_CODE_HARD_TIMEOUT_SECONDS": "10",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_EDIT_READY_GRACE_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "builder-editing-phase",
                "FAKE_CLAUDE_SLEEP_SECONDS": "8",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        metrics = json.loads(self._artifact_path(result.stdout, "Phase Metrics").read_text(encoding="utf-8"))
        self.assertIn("Claude editing readiness declared", progress)
        self.assertNotIn("signal=builder_editing_started", progress)
        self.assertIn("editing readiness produced no durable product write", progress)
        self.assertTrue(metrics["edit_ready_observed"])
        self.assertTrue(metrics["edit_ready_grace_expired"])
        self.assertEqual(metrics["first_progress_signal"], "none")

    def test_product_edit_idle_requires_two_confirmations_before_stop(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS": "2",
                "FAKE_CLAUDE_MODE": "worktree-change",
                "FAKE_CLAUDE_SLEEP_SECONDS": "10",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        metrics = json.loads(self._artifact_path(result.stdout, "Phase Metrics").read_text(encoding="utf-8"))
        self.assertIn("confirmation=1/2", progress)
        self.assertIn("confirmation=2/2", progress)
        self.assertTrue(metrics["product_idle_stopped"])
        self.assertEqual(metrics["final_execution_activity_state"], "implementation-idle")

    def test_active_validation_exempts_product_idle_stop(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_PRODUCT_IDLE_TIMEOUT_SECONDS": "1",
                "CLAUDE_CODE_PRODUCT_IDLE_CONFIRMATIONS": "1",
                "FAKE_CLAUDE_MODE": "worktree-change-validation",
                "FAKE_CLAUDE_SLEEP_SECONDS": "3",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        metrics = json.loads(self._artifact_path(result.stdout, "Phase Metrics").read_text(encoding="utf-8"))
        self.assertFalse(metrics["product_idle_stopped"])
        self.assertEqual(metrics["final_execution_activity_state"], "validation")

    def test_checker_validation_start_refreshes_active_window(self):
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {
                "CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_TIMEOUT_SECONDS": "5",
                "CLAUDE_CODE_HARD_TIMEOUT_SECONDS": "10",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "FAKE_CLAUDE_MODE": "checker-validation-start",
                "FAKE_CLAUDE_SLEEP_SECONDS": "3",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("signal=checker_validation_started", progress)
        self.assertIn("active_window_refreshed=yes", progress)

    def test_blocker_recorded_does_not_refresh_execution_window(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_FIRST_PROGRESS_ACTION": "observe",
                "CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS": "2",
                "FAKE_CLAUDE_MODE": "blocker-recorded",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("First substantive progress detected", progress)
        self.assertIn("context acquisition timeout", progress)

    def test_fallback_evidence_records_first_progress_timeout_no_acceptance(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_FIRST_PROGRESS_ACTION": "stop",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        result_file = self._artifact_path(result.stdout, "Result")
        data = json.loads(result_file.read_text(encoding="utf-8"))
        self.assertTrue(data.get("first_progress_timeout"))
        self.assertEqual(data.get("builder_mode"), "execution-only")
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("First-progress timed out: yes", status)
        self.assertIn("Counts toward takeover: false", status)
        self.assertNotIn("acceptance", status.lower())
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("acceptance", progress.lower())


    # --- Semantic result error detection and dispatch outcome tests ---

    def test_semantic_api_error_detected_with_no_diff(self):
        """exit 0 + is_error=true + API Error + no diff → api_error_without_diff"""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "api-error-no-diff"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Semantic result error: yes", status)
        self.assertIn("api_error:", status)
        self.assertIn("Dispatch outcome: api_error_without_diff", status)
        self.assertIn("Implementation changes: 0", status)
        # Raw result must not be discarded
        result_file = self._artifact_path(result.stdout, "Result")
        data = json.loads(result_file.read_text(encoding="utf-8"))
        self.assertTrue(data.get("is_error"))
        self.assertIn("API Error:", data.get("result", ""))

    def test_semantic_api_error_detected_with_diff(self):
        """exit 0 + is_error=true + API Error + with diff → api_error_with_diff"""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "api-error-with-diff"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Semantic result error: yes", status)
        self.assertIn("Dispatch outcome: api_error_with_diff", status)
        self.assertIn("Implementation changes: 1", status)
        # Evidence classification should still reflect diff presence
        self.assertIn("Evidence classification: diff without report", status)
        self.assertIn("Attempt failure class: recoverable-evidence", status)
        self.assertIn("Counts toward takeover: false", status)

    def test_normal_success_result_remains_success(self):
        """exit 0 + normal result → dispatch_outcome=success"""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertNotIn("Semantic result error: yes", status)
        self.assertIn("Dispatch outcome: success", status)
        self.assertIn("Attempt failure class: none", status)
        self.assertIn("Counts toward takeover: false", status)
        attempt = self._artifact_path(result.stdout, "Attempt Class")
        self.assertTrue(attempt.exists())

    def test_diff_without_valid_report_remains_recoverable_diff_evidence(self):
        """exit 0 + normal result + diff + no valid report → diff without report"""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)

        result = self._dispatch(extra_env={"FAKE_CLAUDE_MODE": "diff-without-report"})

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Evidence classification: diff without report", status)
        self.assertIn("Implementation changes: 1", status)
        self.assertIn("Dispatch outcome: success", status)
        self.assertNotIn("Dispatch outcome: no_useful_progress", status)

    def test_approval_blocked_not_classified_as_no_progress(self):
        """approval-blocked with test-only diff is not no_useful_progress"""
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"FAKE_CLAUDE_MODE": "approval-blocked", "FAKE_CLAUDE_SLEEP_SECONDS": "8"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Dispatch outcome: approval_blocked", status)
        self.assertNotIn("Dispatch outcome: no_useful_progress", status)


    # --- Progress-aware timeout extension tests ---

    def test_active_diff_growth_past_base_deadline_survives(self):
        """Worktree diff growth detected at base deadline extends the run."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "3",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "8",
                "FAKE_CLAUDE_MODE": "delayed-diff",
                "FAKE_CLAUDE_PRE_DIFF_SLEEP": "1",
                "FAKE_CLAUDE_SLEEP_SECONDS": "6",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        # Extension should have kicked in (base timeout at 3s, diff created at 1s)
        self.assertIn("Single growth extension started", progress)
        # Claude finishes at 7s (1+6) before extension deadline at 11s (3+8)
        self.assertNotIn("extension expired", progress)
        self.assertIn("Dispatch outcome: success", status)

    def test_first_diff_refreshes_one_complete_active_window(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS": "3",
                "CLAUDE_CODE_TIMEOUT_SECONDS": "3",
                "CLAUDE_CODE_HARD_TIMEOUT_SECONDS": "12",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "0",
                "FAKE_CLAUDE_MODE": "delayed-diff",
                "FAKE_CLAUDE_PRE_DIFF_SLEEP": "2",
                "FAKE_CLAUDE_SLEEP_SECONDS": "2",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("active_window_refreshed=yes", progress)
        self.assertNotIn("active execution timeout", progress)
        self.assertNotIn("Single growth extension started", progress)

    def test_hard_timeout_caps_refreshed_window(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS": "3",
                "CLAUDE_CODE_TIMEOUT_SECONDS": "10",
                "CLAUDE_CODE_HARD_TIMEOUT_SECONDS": "4",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "10",
                "FAKE_CLAUDE_MODE": "delayed-diff",
                "FAKE_CLAUDE_PRE_DIFF_SLEEP": "1",
                "FAKE_CLAUDE_SLEEP_SECONDS": "8",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("hard runtime timeout", progress)
        self.assertNotIn("Single growth extension started", progress)

    def test_progress_report_growth_survives_extension(self):
        """Progress/report file growth during extension keeps run alive."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "8",
                "FAKE_CLAUDE_MODE": "incremental-progress",
                "FAKE_CLAUDE_SLEEP_SECONDS": "4",
                "FAKE_CLAUDE_POST_PROGRESS_SLEEP": "4",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        # Extension should have kicked in due to worktree change
        self.assertIn("Single growth extension started", progress)
        # Claude finishes at 8s (4+4) before extension deadline at 10s (2+8)
        self.assertNotIn("extension expired", progress)
        self.assertIn("Dispatch outcome: success", status)

    def test_clock_only_progress_rewrites_do_not_extend_window(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_CONTEXT_ACQUISITION_TIMEOUT_SECONDS": "4",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "5",
                "CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS": "0",
                "FAKE_CLAUDE_MODE": "clock-only-progress",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("Single growth extension started", progress)

    def test_terminal_drain_captures_exit_adjacent_file(self):
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "FAKE_CLAUDE_MODE": "exit-adjacent-file",
                "CLAUDE_CODE_TERMINAL_DRAIN_SECONDS": "1",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("Terminal drain complete", progress)
        untracked = self._artifact_path(result.stdout, "Untracked Files").read_text(encoding="utf-8")
        self.assertIn("LATE_FILE.py", untracked)

    def test_seeded_only_no_growth_still_times_out(self):
        """No progress growth at base deadline → hard timeout, no extension."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "10",
                "FAKE_CLAUDE_MODE": "seed-only",
                "FAKE_CLAUDE_SLEEP_SECONDS": "10",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("context acquisition timeout", progress)
        self.assertNotIn("timeout_extension_started", progress)
        self.assertNotIn("extension_active=1", progress)

    def test_extension_cap_prevents_infinite_wait(self):
        """Extension deadline stops even if progress was growing."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "5",
                "CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS": "0",
                "FAKE_CLAUDE_MODE": "incremental-progress",
                "FAKE_CLAUDE_SLEEP_SECONDS": "3",
                "FAKE_CLAUDE_POST_PROGRESS_SLEEP": "8",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("extension_active=1", progress)
        self.assertIn("extension expired", progress)
        self.assertIn("Progress extension used: yes", status)
        self.assertIn("Extension seconds: 5", status)

    def test_direction_stop_unaffected_by_extension(self):
        """Direction/manual stop terminates regardless of extension."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "10",
                "FAKE_CLAUDE_MODE": "worktree-change",
                "FAKE_CLAUDE_SLEEP_SECONDS": "1",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Dispatch outcome: success", status)
        self.assertNotIn("Dispatch outcome: timeout", status)

    def test_runtime_json_exposes_extension_fields(self):
        """Runtime JSON includes base_timeout_seconds and progress_extension_seconds."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "30",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "60",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        runtime_path = self._artifact_path(result.stdout, "Runtime Identity")
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(runtime["base_timeout_seconds"], 30)
        self.assertEqual(runtime["context_acquisition_timeout_seconds"], 30)
        self.assertEqual(runtime["hard_timeout_seconds"], 1500)
        self.assertEqual(runtime["active_window_refresh_limit"], 1)
        self.assertEqual(runtime["growth_extension_limit"], 1)
        self.assertEqual(runtime["progress_extension_seconds"], 60)

    def test_fallback_result_includes_extension_fields(self):
        """Fallback result JSON includes timeout extension fields."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "10",
                "FAKE_CLAUDE_MODE": "seed-only",
                "FAKE_CLAUDE_SLEEP_SECONDS": "10",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        result_file = self._artifact_path(result.stdout, "Result")
        data = json.loads(result_file.read_text(encoding="utf-8"))
        self.assertIn("timeout_extension_used", data)
        self.assertIn("timeout_extension_seconds", data)
        self.assertIn("timeout_extension_reason", data)
        self.assertFalse(data["timeout_extension_used"])
        self.assertEqual(data["timeout_extension_seconds"], 0)
        self.assertIsNone(data["timeout_extension_reason"])

    # --- ADVISOR_REQUEST.json dispatcher integration tests ---

    def test_valid_advisor_request_with_implementation_makes_advisor_eligible(self):
        """Fake Claude writes implementation diff + valid on-plan semantic request
        using the real generated task ID -> attempt classification is advisor eligible."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"FAKE_CLAUDE_MODE": "advisor-request-valid"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Advisor request valid: yes", status)
        self.assertIn("Advisor direction: on-plan", status)
        self.assertIn("Advisor blocker kind: semantic", status)
        self.assertIn("Advisor used: false", status)
        attempt = json.loads(
            self._artifact_path(result.stdout, "Attempt Class").read_text(encoding="utf-8")
        )
        self.assertTrue(attempt["advisor_continuation_eligible"])
        self.assertIsNone(attempt["advisor_rejection_reason"])

    def test_malformed_advisor_request_not_eligible_diagnostic_visible(self):
        """Malformed ADVISOR_REQUEST.json -> not eligible, defaults used, diagnostic archived."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"FAKE_CLAUDE_MODE": "advisor-request-malformed"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Advisor request valid: no", status)
        self.assertIn("Advisor direction: unknown", status)
        self.assertIn("Advisor blocker kind: none", status)
        # Check diagnostic was archived
        worktree = self._artifact_path(result.stdout, "Worktree")
        # The archive is a sibling of the run worktree under .worktrees/.
        advisor_archives = list(worktree.parent.glob("*.advisor-request"))
        self.assertTrue(len(advisor_archives) > 0, "Expected advisor request archive directory")

    def test_task_mismatched_advisor_request_rejected(self):
        """Task-mismatched request -> not eligible, defaults used."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"FAKE_CLAUDE_MODE": "advisor-request-mismatch"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Advisor request valid: no", status)
        self.assertIn("Advisor direction: unknown", status)
        worktree = self._artifact_path(result.stdout, "Worktree")
        archives = list(worktree.parent.glob("*.advisor-request/invalid.json"))
        self.assertTrue(archives, "Expected invalid advisor request archive")
        diagnostic = json.loads(archives[-1].read_text(encoding="utf-8"))["diagnostic"]
        self.assertEqual(diagnostic["reason"], "task-id-mismatch")

    def test_advisor_request_only_does_not_count_as_implementation_progress(self):
        """Request-only output -> does not count as implementation progress."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"FAKE_CLAUDE_MODE": "advisor-request-only"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Implementation changes: 0", status)
        self.assertIn("Advisor request valid: yes", status)
        # Should be classified as no_useful_progress since there's no diff or report
        self.assertIn("Dispatch outcome: no_useful_progress", status)

    def test_no_advisor_request_file_uses_defaults(self):
        """No ADVISOR_REQUEST.json -> direction=unknown, blocker_kind=none, advisor_used=false."""
        self._write_builder_task_card()
        result = self._dispatch("task-cards/BUILDER.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("Advisor request valid: no", status)
        self.assertIn("Advisor direction: unknown", status)
        self.assertIn("Advisor blocker kind: none", status)
        self.assertIn("Advisor used: false", status)

    # --- Tool profile and validation allowlist tests ---

    def _write_checker_card_with_validation(self, fences=None, mode="checker-test"):
        """Write a checker task card with configurable validation fence blocks.

        fences: list of (info_string, body) tuples for fenced code blocks.
        """
        if fences is None:
            fences = [("validation", "true\n")]

        fence_blocks = ""
        for info, body in fences:
            fence_blocks += f"```{info}\n{body}```\n\n"

        task = self.repo / "task-cards" / "CHECKER_TP.md"
        task.parent.mkdir(exist_ok=True)
        task.write_text(
            "# Checker Tool Profile Test\n\n"
            "## Task Mode\n\n"
            "| Field | Value |\n|---|---|\n| Mode | {} |\n\n"
            "## Validation Contract\n\n"
            "| Check | Value |\n|---|---|\n| Local validation allowed? | yes |\n\n"
            "{}".format(mode, fence_blocks),
            encoding="utf-8",
        )
        return task

    def _write_builder_task_card_with_claude_context(self):
        """Write a builder task card with execution-only eligible gates."""
        task = self.repo / "task-cards" / "BUILDER_TP.md"
        task.parent.mkdir(exist_ok=True)
        task.write_text(
            "# Builder Tool Profile Test\n\n"
            "## Task Mode\n\n"
            "| Field | Value |\n|---|---|\n| Mode | builder |\n\n"
            "## Claude Context Packet\n\n"
            "| Field | Value |\n|---|---|\n"
            "| Target files | README.md |\n"
            "| Execution-only eligible? | yes |\n"
            "| Context is sufficient for execution? | yes |\n\n"
            "## Handoff Contract\n\nEdit README.\n\n"
            "## Acceptance Criteria\n\n- README changed\n\n"
            "## Validation Contract\n\n"
            "```validation\ntrue\n```\n\n"
            "## Required Report\n\nReport files changed.\n",
            encoding="utf-8",
        )
        return task

    def test_invalid_tool_profile_fails_before_dispatch(self):
        """Invalid CLAUDE_CODE_TOOL_PROFILE exits before dispatch."""
        self._write_task_card()
        result = self._dispatch(
            "task-cards/PROJ.md",
            {"CLAUDE_CODE_TOOL_PROFILE": "nonexistent"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLAUDE_CODE_TOOL_PROFILE must be", result.stderr)
        worktrees = self.repo / ".worktrees"
        artifacts = sorted(p.name for p in worktrees.glob("claude-*")) if worktrees.exists() else []
        self.assertEqual([], artifacts)

    def test_invalid_validation_allowlist_fails_before_dispatch(self):
        """Invalid CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST exits before dispatch."""
        self._write_task_card()
        result = self._dispatch(
            "task-cards/PROJ.md",
            {"CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST": "invalid"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST must be", result.stderr)
        worktrees = self.repo / ".worktrees"
        artifacts = sorted(p.name for p in worktrees.glob("claude-*")) if worktrees.exists() else []
        self.assertEqual([], artifacts)

    def test_auto_execution_only_builder_resolves_minimal_builder(self):
        """Auto tool profile with execution-only Builder resolves minimal-builder."""
        task = self._write_builder_task_card_with_claude_context()
        self._run(["git", "add", "task-cards/BUILDER_TP.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add auto builder tp task"], cwd=self.repo)
        result = self._dispatch(
            "task-cards/BUILDER_TP.md",
            {"CLAUDE_CODE_TOOL_PROFILE": "auto"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Tool Profile:    minimal-builder", result.stdout)

    def test_standard_builder_resolves_locator_builder(self):
        """Auto tool profile with standard Builder resolves locator-builder."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"CLAUDE_CODE_TOOL_PROFILE": "auto", "CLAUDE_CODE_BUILDER_MODE": "standard"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Tool Profile:    locator-builder", result.stdout)

    def test_checker_test_resolves_checker(self):
        """Auto tool profile with checker-test resolves checker."""
        self._write_low_risk_checker_card()
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"CLAUDE_CODE_TOOL_PROFILE": "auto"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Tool Profile:    checker", result.stdout)

    def test_explicit_default_passes_no_tools_flags(self):
        """Explicit default profile passes no --tools or --allowedTools."""
        self._write_task_card()
        argv_log = self.case_root / "argv-default.log"
        result = self._dispatch(
            "task-cards/PROJ.md",
            {
                "CLAUDE_CODE_TOOL_PROFILE": "default",
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Tool Profile:    default", result.stdout)
        # Verify no --tools or --allowedTools in claude argv
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertNotIn("--tools", argv_content)
            self.assertNotIn("--allowedTools", argv_content)

    def test_old_cli_neither_flag_degrades_to_legacy(self):
        """Old CLI advertising neither --tools nor --allowedTools degrades to legacy."""
        self._write_low_risk_checker_card()
        argv_log = self.case_root / "argv-old-cli.log"
        # FAKE_CLAUDE_HELP_TOOLS_FLAG and FAKE_CLAUDE_HELP_ALLOWED_FLAG unset
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("tool_profile_supported=no", result.stdout)
        # Verify no --tools or --allowedTools in claude argv
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertNotIn("--tools", argv_content)
            self.assertNotIn("--allowedTools", argv_content)

    def test_partial_cli_only_tools_degrades_to_legacy(self):
        """CLI advertising only --tools but not --allowedTools degrades to legacy."""
        self._write_low_risk_checker_card()
        argv_log = self.case_root / "argv-partial-cli.log"
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                # No FAKE_CLAUDE_HELP_ALLOWED_FLAG
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("tool_profile_supported=no", result.stdout)
        # Verify no --tools or --allowedTools in claude argv
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertNotIn("--tools", argv_content)
            self.assertNotIn("--allowedTools", argv_content)

    def test_full_cli_advertising_both_flags_enables_profile(self):
        """CLI advertising both --tools and --allowedTools enables tool profile."""
        self._write_low_risk_checker_card()
        argv_log = self.case_root / "argv-full-cli.log"
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("tool_profile_supported=yes", result.stdout)
        self.assertIn("Tool Profile:    checker", result.stdout)
        # Verify --tools appears in claude argv
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertIn("--tools", argv_content)
            self.assertIn("--allowedTools", argv_content)

    def test_full_cli_advertising_allowed_tools_hyphen_enables_profile(self):
        """CLI advertising --tools and --allowed-tools (hyphenated) enables profile."""
        self._write_low_risk_checker_card()
        argv_log = self.case_root / "argv-hyphen-cli.log"
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowed-tools",
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("tool_profile_supported=yes", result.stdout)

    def test_direct_and_broker_paths_receive_same_profile_flags(self):
        """Direct/bypass and broker paths receive the same profile flags."""
        self._write_low_risk_checker_card()
        direct_argv_log = self.case_root / "argv-direct.log"
        broker_argv_log = self.case_root / "argv-broker.log"
        base_env = {
            "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
            "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
        }
        # Direct/bypass path
        result_direct = self._dispatch(
            "task-cards/CHECKER.md",
            {**base_env, "AI_CODING_WORKFLOW_BYPASS_BROKER": "1",
             "FAKE_CLAUDE_ARGV_LOG": str(direct_argv_log)},
        )
        self.assertEqual(result_direct.returncode, 0, result_direct.stderr + result_direct.stdout)
        # Broker path (default when model-call-broker.py exists)
        # Re-dispatch a fresh checker card
        self._write_low_risk_checker_card()
        result_broker = self._dispatch(
            "task-cards/CHECKER.md",
            {**base_env, "FAKE_CLAUDE_ARGV_LOG": str(broker_argv_log),
             "CLAUDE_CODE_REUSE_WORKTREE_RESET": "1"},
        )
        self.assertEqual(result_broker.returncode, 0, result_broker.stderr + result_broker.stdout)
        # Compare profile flags in both argv logs
        if direct_argv_log.exists() and broker_argv_log.exists():
            direct_argv = direct_argv_log.read_text(encoding="utf-8")
            broker_argv = broker_argv_log.read_text(encoding="utf-8")
            self.assertIn("--tools", direct_argv)
            self.assertIn("--tools", broker_argv)
            self.assertIn("--allowedTools", direct_argv)
            self.assertIn("--allowedTools", broker_argv)

    def test_checker_accepts_exact_commands_from_validation_fence(self):
        """Checker accepts simple exact commands from validation fences."""
        self._write_checker_card_with_validation([
            ("validation", "python -m pytest -q\ngit diff --check\n"),
        ])
        argv_log = self.case_root / "argv-checker-validation.log"
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("allowlist_accepted=2", result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertIn("Bash(python -m pytest -q)", argv_content)
            self.assertIn("Bash(git diff --check)", argv_content)

    def test_checker_accepts_commands_from_check_fence(self):
        """Checker accepts commands from fences with 'check' in info string."""
        self._write_checker_card_with_validation([
            ("bash check", "bash -n scripts/dispatch-to-claude.sh\n"),
        ])
        argv_log = self.case_root / "argv-check-fence.log"
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("allowlist_accepted=1", result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertIn("Bash(bash -n scripts/dispatch-to-claude.sh)", argv_content)

    def test_comments_and_blank_lines_ignored_in_validation(self):
        """Comments and blank lines in validation fences are ignored."""
        self._write_checker_card_with_validation([
            ("validation",
             "# This is a comment\n"
             "\n"
             "   \n"
             "python -m pytest -q\n"
             "# Another comment\n"
             "true\n"),
        ])
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("allowlist_accepted=2", result.stdout)

    def test_unsafe_operators_rejected_in_validation(self):
        """Shell operators ;, &&, ||, pipe, backticks, redirection are rejected."""
        unsafe_commands = [
            "echo hello; rm -rf /",
            "true && false",
            "false || true",
            "cat file | grep pattern",
            "echo `whoami`",
            "echo hello > /tmp/out",
            "echo hello < /tmp/in",
        ]
        fence_body = "\n".join(unsafe_commands) + "\ntrue\n"
        self._write_checker_card_with_validation([("validation", fence_body)])
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        # Only "true" should be accepted
        self.assertIn("allowlist_accepted=1", result.stdout)
        self.assertIn("allowlist_unsafe=7", result.stdout)

    def test_oversized_commands_rejected_in_validation(self):
        """Commands over 500 characters are rejected and counted."""
        long_cmd = "echo " + "x" * 500  # 505 chars total
        self._write_checker_card_with_validation([
            ("validation", long_cmd + "\ntrue\n"),
        ])
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("allowlist_accepted=1", result.stdout)
        self.assertIn("allowlist_oversized=1", result.stdout)

    def test_accepted_commands_beyond_12_overflow(self):
        """More than 12 accepted commands count as overflow."""
        cmds = "\n".join(f"cmd_{i}" for i in range(15)) + "\n"
        self._write_checker_card_with_validation([("validation", cmds)])
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("allowlist_accepted=12", result.stdout)
        self.assertIn("allowlist_overflow=3", result.stdout)

    def test_logs_contain_only_aggregate_counts_not_command_bodies(self):
        """Progress logs contain aggregate counts, never rejected command bodies."""
        self._write_checker_card_with_validation([
            ("validation",
             "echo secret_password_12345\n"
             "echo hello; rm -rf /\n"
             "true\n"),
        ])
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        # Aggregate counts should appear
        self.assertIn("allowlist_accepted=2", progress)
        self.assertIn("allowlist_unsafe=1", progress)
        # Rejected command bodies should NOT appear in progress
        self.assertNotIn("secret_password_12345", progress)
        self.assertNotIn("rm -rf", progress)

    def test_no_wildcard_bash_or_permission_bypass_in_argv(self):
        """No wildcard Bash(*) or permission bypass flags appear in constructed argv."""
        self._write_checker_card_with_validation([
            ("validation", "true\n"),
        ])
        argv_log = self.case_root / "argv-no-wildcard.log"
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertNotIn("Bash(*)", argv_content)
            self.assertNotIn("--dangerously-skip-permissions", argv_content)
            # Should have specific Bash(true) not wildcard
            self.assertIn("Bash(true)", argv_content)

    def test_default_profile_preserves_legacy_invocation(self):
        """Default/unsupported profile preserves legacy invocation with no profile flags."""
        self._write_task_card()
        argv_log = self.case_root / "argv-legacy.log"
        result = self._dispatch(
            "task-cards/PROJ.md",
            {
                "CLAUDE_CODE_TOOL_PROFILE": "default",
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Tool Profile:    default", result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            # Default profile should not add --tools or --allowedTools
            self.assertNotIn("--tools", argv_content)
            self.assertNotIn("--allowedTools", argv_content)

    def test_unsupported_cli_preserves_legacy_invocation(self):
        """Unsupported CLI (missing flags) preserves legacy invocation."""
        self._write_low_risk_checker_card()
        argv_log = self.case_root / "argv-unsupported.log"
        result = self._dispatch(
            "task-cards/CHECKER.md",
            {
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
                # No FAKE_CLAUDE_HELP_TOOLS_FLAG or FAKE_CLAUDE_HELP_ALLOWED_FLAG
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("tool_profile_supported=no", result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertNotIn("--tools", argv_content)
            self.assertNotIn("--allowedTools", argv_content)

    def test_checker_validation_mixed_fence_info_strings(self):
        """Both 'validation' and 'check' info strings are recognized."""
        self._write_checker_card_with_validation([
            ("validation", "python -m pytest -q\n"),
            ("bash check", "bash -n scripts/dispatch-to-claude.sh\n"),
        ])
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("allowlist_accepted=2", result.stdout)

    def test_validation_allowlist_disabled_passes_no_bash_allowed(self):
        """With allowlist disabled, no Bash() entries appear in allowedTools."""
        self._write_checker_card_with_validation([
            ("validation", "true\n"),
        ])
        argv_log = self.case_root / "argv-allowlist-disabled.log"
        result = self._dispatch(
            "task-cards/CHECKER_TP.md",
            {
                "CLAUDE_CODE_TASK_VALIDATION_ALLOWLIST": "0",
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
                "FAKE_CLAUDE_ARGV_LOG": str(argv_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("allowlist_enabled=no", result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertNotIn("Bash(", argv_content)

    def test_empty_arrays_work_under_strict_mode(self):
        """Empty tool profile arrays work correctly under set -u."""
        self._write_task_card()
        # Default profile with supported CLI → arrays stay empty
        result = self._dispatch(
            "task-cards/PROJ.md",
            {
                "CLAUDE_CODE_TOOL_PROFILE": "default",
                "FAKE_CLAUDE_HELP_TOOLS_FLAG": "1",
                "FAKE_CLAUDE_HELP_ALLOWED_FLAG": "--allowedTools",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Complete", result.stdout)

    # --- Unified API probe mode tests ---

    def test_api_probe_mode_always_runs_startup_probe(self):
        """Explicit always mode runs a startup interaction probe."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_API_PROBE_MODE": "always",
                "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "1",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("Startup interaction probe: conclusion=", progress)

    def test_adaptive_probe_reuses_recent_context_bound_success(self):
        self._write_builder_task_card()
        env = {
            "CLAUDE_CODE_API_PROBE_MODE": "adaptive",
            "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "1",
            "FAKE_CLAUDE_MODE": "success",
        }
        first = self._dispatch("task-cards/BUILDER.md", env)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        first_progress = self._artifact_path(first.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("Startup interaction probe: conclusion=available", first_progress)

        second = self._dispatch("task-cards/BUILDER.md", env)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        second_progress = self._artifact_path(second.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("Startup API availability reused", second_progress)
        self.assertNotIn("Interaction probe (startup): checking", second_progress)
        receipt = json.loads(
            self._artifact_path(second.stdout, "Startup Probe").read_text(encoding="utf-8")
        )
        self.assertTrue(receipt["cache_valid"])
        self.assertFalse(receipt["live_probe"])

    def test_api_probe_mode_failure_only_skips_startup_probe_when_preflight_overridden(self):
        """The diagnostic override preserves legacy failure-only probe behavior."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_API_PROBE_MODE": "failure-only",
                "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "0",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("Startup interaction probe:", progress)
        # Zero-output probe should still run.
        probe_path = self._artifact_path(result.stdout, "API Probe")
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        self.assertIn("interaction_conclusion", probe)

    def test_api_probe_mode_off_skips_all_probes_when_preflight_overridden(self):
        """The diagnostic override lets off mode skip all API probes."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_API_PROBE_MODE": "off",
                "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "0",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        probe_path = self._artifact_path(result.stdout, "API Probe")
        self.assertEqual(probe_path.stat().st_size, 0)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertNotIn("Startup interaction probe:", progress)
        self.assertNotIn("Interaction probe (zero-output):", progress)

    def test_successful_startup_probe_skips_redundant_observation_probe(self):
        """A successful startup probe is enough while Claude is still running;
        zero-output finalization may probe current availability once."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_FIRST_PROGRESS_ACTION": "observe",
                "CLAUDE_CODE_API_PROBE_MODE": "always",
                "CLAUDE_CODE_STARTUP_PREFLIGHT_REQUIRED": "1",
                "FAKE_CLAUDE_MODE": "seed-only",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        self.assertIn("First-progress observation probe skipped: startup probe already confirmed availability", progress)
        self.assertNotIn("First-progress observation probe: conclusion=", progress)
        self.assertIn("Interaction probe (zero-output):", progress)

    # --- First-progress observe vs stop tests ---

    def test_first_progress_observe_does_not_stop_at_threshold(self):
        """observe mode continues past the first-progress threshold; Claude
        runs until the base timeout kills it, not the first-progress timer."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_TIMEOUT_SECONDS": "5",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_FIRST_PROGRESS_ACTION": "observe",
                "FAKE_CLAUDE_MODE": "seed-only",
                "FAKE_CLAUDE_SLEEP_SECONDS": "10",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        # Observe mode logs the event but does not stop.
        self.assertIn("First-progress observation:", progress)
        # The process was NOT killed by first-progress; it ran until base timeout.
        self.assertIn("First-progress timed out: no", status)

    def test_first_progress_stop_preserves_legacy_termination(self):
        """stop mode kills Claude at the first-progress threshold."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_BUILDER_MODE": "execution-only",
                "CLAUDE_CODE_TIMEOUT_SECONDS": "30",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_FIRST_PROGRESS_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_FIRST_PROGRESS_ACTION": "stop",
                "FAKE_CLAUDE_MODE": "seed-only",
                "FAKE_CLAUDE_SLEEP_SECONDS": "10",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        self.assertIn("first_progress_timeout", progress.lower())
        self.assertIn("First-progress timed out: yes", status)

    # --- Single growth-extension tests ---

    def test_growth_during_extension_never_starts_another_round(self):
        """Growth may earn one extension, but cannot roll or start a second."""
        self._write_builder_task_card()
        t0 = time.monotonic()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "3",
                "CLAUDE_CODE_GROWING_PROGRESS_EXTENSION_SECONDS": "5",
                "FAKE_CLAUDE_MODE": "incremental-progress",
                "FAKE_CLAUDE_SLEEP_SECONDS": "4",
                "FAKE_CLAUDE_POST_PROGRESS_SLEEP": "10",
            },
        )
        wall = time.monotonic() - t0
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        status = self._artifact_path(result.stdout, "Status").read_text(encoding="utf-8")
        data = json.loads(self._artifact_path(result.stdout, "Result").read_text(encoding="utf-8"))
        self.assertEqual(progress.count("Single growth extension started:"), 1)
        self.assertNotIn("Second extension started:", progress)
        self.assertIn("single growth extension expired", progress)
        self.assertIn("Second extension used: no", status)
        self.assertFalse(data["second_extension_used"])
        self.assertEqual(data["growth_extension_limit"], 1)
        self.assertLess(wall, 25, "Wall-clock exceeded hard bounded single-extension behavior")

    # --- Recent activity window validation and stale-progress tests ---

    def test_recent_activity_window_invalid_value_fails(self):
        """Non-integer CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS exits with validation error."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {"CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS": "abc"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS must be a non-negative integer",
            result.stderr,
        )

    def test_stale_progress_at_base_deadline_does_not_extend(self):
        """Progress older than the recent-activity window is stale at the base
        deadline; the run terminates without starting the first extension."""
        self._write_builder_task_card()
        t0 = time.monotonic()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "4",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "5",
                "CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS": "1",
                "CLAUDE_CODE_TIMEOUT_DRAIN_SECONDS": "0",
                "CLAUDE_CODE_API_PROBE_MODE": "off",
                "FAKE_CLAUDE_MODE": "worktree-change",
                "FAKE_CLAUDE_SLEEP_SECONDS": "10",
            },
        )
        wall = time.monotonic() - t0
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        # Progress is stale (last activity >1s ago at the 4s base deadline) → no extension.
        self.assertNotIn("Single growth extension started", progress)
        self.assertIn("active execution timeout", progress)
        self.assertLess(wall, 25, "Wall-clock exceeded 25s; run should terminate at base deadline")

    def test_recent_progress_at_base_deadline_extends(self):
        """Progress within the recent-activity window at the base deadline
        triggers the first extension."""
        self._write_builder_task_card()
        result = self._dispatch(
            "task-cards/BUILDER.md",
            {
                "CLAUDE_CODE_TIMEOUT_SECONDS": "2",
                "CLAUDE_CODE_HEARTBEAT_SECONDS": "1",
                "CLAUDE_CODE_ACTIVE_PROGRESS_EXTENSION_SECONDS": "5",
                "CLAUDE_CODE_RECENT_ACTIVITY_WINDOW_SECONDS": "10",
                "FAKE_CLAUDE_MODE": "worktree-change",
                "FAKE_CLAUDE_SLEEP_SECONDS": "10",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        progress = self._artifact_path(result.stdout, "Progress Log").read_text(encoding="utf-8")
        # Progress is recent (last activity ≤10s ago at the 2s base deadline) → extension granted.
        self.assertIn("Single growth extension started", progress)

    # --- Dirty-source guard: dispatcher-owned file exemption tests ---

    def test_dirty_source_guard_ignores_dispatcher_owned_files(self):
        """The dirty-source guard allows the exact dispatcher-owned runtime files."""
        self._write_task_card()
        wf_dir = self.repo / ".ai-workflow"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "model-calls.jsonl").write_text("", encoding="utf-8")
        (wf_dir / "model-calls.lock").write_text("", encoding="utf-8")
        (wf_dir / "model-usage.jsonl").write_text("", encoding="utf-8")
        (wf_dir / "run-ledger.lock").write_text("", encoding="utf-8")

        result = self._dispatch()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Dispatch Complete", result.stdout)

    def test_dirty_source_guard_rejects_other_untracked_ai_workflow_file(self):
        """Other untracked .ai-workflow files remain dirty source."""
        self._write_task_card()
        wf_dir = self.repo / ".ai-workflow"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "other-file.json").write_text("{}", encoding="utf-8")

        result = self._dispatch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale HEAD", result.stderr)
        self.assertIn(".ai-workflow/other-file.json", result.stderr)

    # --- External integration gate tests ---

    def _write_external_integration_card(
        self, allowed="no", mcp_paths="none", plugin_paths="none", strict="yes"
    ):
        """Write a task card with a Claude External Integration Gate section."""
        task = self.repo / "task-cards" / "EXTINT.md"
        task.parent.mkdir(exist_ok=True)
        task.write_text(
            "# External Integration Test\n\n"
            "## Task Mode\n\n"
            "| Field | Value |\n|---|---|\n| Mode | builder |\n\n"
            "## Claude External Integration Gate\n\n"
            "| Field | Value |\n|---|---|\n"
            "| External integrations allowed? | {} |\n"
            "| MCP config paths | {} |\n"
            "| Plugin paths | {} |\n"
            "| Strict MCP isolation? | {} |\n\n"
            "## Acceptance Criteria\n\n- done\n".format(
                allowed, mcp_paths, plugin_paths, strict
            ),
            encoding="utf-8",
        )
        return task

    def _commit_external_integration_fixtures(self, *paths):
        self._run(["git", "add", *paths], cwd=self.repo)
        self._run(["git", "commit", "-m", "add external integration fixtures"], cwd=self.repo)

    def test_external_integration_default_no_gate_appends_bare_only(self):
        """Missing gate section → --bare only, no --mcp-config or --plugin-dir."""
        self._write_task_card()
        argv_log = self.case_root / "argv-ext-default.log"
        result = self._dispatch(
            "task-cards/PROJ.md",
            {"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("External Integrations: no", result.stdout)
        self.assertIn("MCP Config Paths: none", result.stdout)
        self.assertIn("Plugin Paths: none", result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertIn("--bare", argv_content)
            self.assertNotIn("--strict-mcp-config", argv_content)
            self.assertNotIn("--mcp-config", argv_content)
            self.assertNotIn("--plugin-dir", argv_content)

    def test_external_integration_explicit_no_appends_bare_only(self):
        """Explicit 'no' → --bare only, no --mcp-config or --plugin-dir."""
        self._write_external_integration_card(allowed="no")
        argv_log = self.case_root / "argv-ext-no.log"
        result = self._dispatch(
            "task-cards/EXTINT.md",
            {"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("External Integrations: no", result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertIn("--bare", argv_content)
            self.assertNotIn("--strict-mcp-config", argv_content)
            self.assertNotIn("--mcp-config", argv_content)
            self.assertNotIn("--plugin-dir", argv_content)

    def test_external_integration_yes_with_valid_paths_appends_strict_and_paths(self):
        """Valid repo-local paths → --bare --strict-mcp-config --mcp-config --plugin-dir."""
        mcp = self.repo / "mcp-config.json"
        mcp.write_text('{"mcpServers": {}}', encoding="utf-8")
        plugin = self.repo / "my-plugin"
        plugin.mkdir()
        (plugin / "index.js").write_text("module.exports = {};", encoding="utf-8")
        self._commit_external_integration_fixtures("mcp-config.json", "my-plugin")
        self._write_external_integration_card(
            allowed="yes", mcp_paths="mcp-config.json", plugin_paths="my-plugin"
        )
        argv_log = self.case_root / "argv-ext-valid.log"
        result = self._dispatch(
            "task-cards/EXTINT.md",
            {"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("External Integrations: yes", result.stdout)
        self.assertIn("MCP Config Paths: mcp-config.json", result.stdout)
        self.assertIn("Plugin Paths: my-plugin", result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertIn("--bare", argv_content)
            self.assertIn("--strict-mcp-config", argv_content)
            self.assertIn("--mcp-config", argv_content)
            self.assertIn("mcp-config.json", argv_content)
            self.assertIn("--plugin-dir", argv_content)
            self.assertIn("my-plugin", argv_content)

    def test_external_integration_absolute_mcp_path_fails_closed(self):
        """Absolute MCP path → fail before Claude is invoked."""
        self._write_external_integration_card(
            allowed="yes", mcp_paths="/etc/config.json"
        )
        result = self._dispatch("task-cards/EXTINT.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute", result.stderr.lower())
        runtime_files = sorted((self.repo / ".worktrees").glob("claude-*.runtime.json"))
        self.assertEqual(1, len(runtime_files))
        runtime = json.loads(runtime_files[0].read_text(encoding="utf-8"))
        self.assertFalse(runtime["external_integration_valid"])
        self.assertEqual(runtime["external_integration_rejection"], "absolute_mcp_path")

    def test_external_integration_traversal_mcp_path_fails_closed(self):
        """MCP path with '..' traversal → fail before Claude is invoked."""
        self._write_external_integration_card(
            allowed="yes", mcp_paths="../outside.json"
        )
        result = self._dispatch("task-cards/EXTINT.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("traversal", result.stderr.lower())

    def test_external_integration_missing_mcp_file_fails_closed(self):
        """MCP path referencing non-existent file → fail before Claude is invoked."""
        self._write_external_integration_card(
            allowed="yes", mcp_paths="nonexistent.json"
        )
        result = self._dispatch("task-cards/EXTINT.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr.lower())

    def test_external_integration_wrong_extension_mcp_fails_closed(self):
        """MCP config path without .json extension → fail before Claude is invoked."""
        self._write_external_integration_card(
            allowed="yes", mcp_paths="config.yaml"
        )
        result = self._dispatch("task-cards/EXTINT.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".json", result.stderr)

    def test_external_integration_wrong_type_plugin_fails_closed(self):
        """Plugin path that is a regular non-.zip file → fail before Claude is invoked."""
        (self.repo / "plugin.txt").write_text("not a plugin", encoding="utf-8")
        self._commit_external_integration_fixtures("plugin.txt")
        self._write_external_integration_card(
            allowed="yes", plugin_paths="plugin.txt"
        )
        result = self._dispatch("task-cards/EXTINT.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".zip", result.stderr)

    def test_external_integration_yes_with_no_declared_integrations_fails_closed(self):
        """'yes' with no declared integrations → fail before Claude is invoked."""
        self._write_external_integration_card(
            allowed="yes", mcp_paths="none", plugin_paths="none"
        )
        result = self._dispatch("task-cards/EXTINT.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no valid", result.stderr.lower())

    def test_external_integration_valid_zip_plugin_accepted(self):
        """Valid .zip plugin file is accepted."""
        plugin_zip = self.repo / "my-plugin.zip"
        plugin_zip.write_bytes(b"PK\x03\x04fake-zip")
        self._commit_external_integration_fixtures("my-plugin.zip")
        self._write_external_integration_card(
            allowed="yes", plugin_paths="my-plugin.zip"
        )
        argv_log = self.case_root / "argv-ext-zip.log"
        result = self._dispatch(
            "task-cards/EXTINT.md",
            {"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Plugin Paths: my-plugin.zip", result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertIn("--plugin-dir", argv_content)
            self.assertIn("my-plugin.zip", argv_content)

    def test_external_integration_multiple_mcp_config_paths(self):
        """Multiple comma-separated MCP config paths are all passed."""
        (self.repo / "a.json").write_text("{}", encoding="utf-8")
        (self.repo / "b.json").write_text("{}", encoding="utf-8")
        self._commit_external_integration_fixtures("a.json", "b.json")
        self._write_external_integration_card(
            allowed="yes", mcp_paths="a.json,b.json"
        )
        argv_log = self.case_root / "argv-ext-multi.log"
        result = self._dispatch(
            "task-cards/EXTINT.md",
            {"FAKE_CLAUDE_ARGV_LOG": str(argv_log)},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        if argv_log.exists():
            argv_content = argv_log.read_text(encoding="utf-8")
            self.assertIn("a.json", argv_content)
            self.assertIn("b.json", argv_content)

    def test_external_integration_evidence_in_runtime_json(self):
        """Runtime JSON includes external integration fields."""
        mcp = self.repo / "mcp.json"
        mcp.write_text("{}", encoding="utf-8")
        plugin = self.repo / "plug"
        plugin.mkdir()
        (plugin / "plugin.json").write_text("{}", encoding="utf-8")
        self._write_external_integration_card(
            allowed="yes", mcp_paths="mcp.json", plugin_paths="plug"
        )
        self._run(["git", "add", "task-cards/EXTINT.md", "mcp.json", "plug"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add ext task"], cwd=self.repo)
        result = self._dispatch("task-cards/EXTINT.md")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        runtime_path = self._artifact_path(result.stdout, "Runtime Identity")
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(runtime["external_integrations_allowed"], "yes")
        self.assertEqual(runtime["strict_mcp_isolation"], "yes")
        self.assertEqual(runtime["mcp_config_paths"], "mcp.json")
        self.assertEqual(runtime["plugin_paths"], "plug")
        self.assertEqual(runtime["external_integration_rejection"], "none")

    def test_external_integration_default_gate_runtime_json_shows_no(self):
        """Runtime JSON shows external_integrations_allowed=no when no gate."""
        self._write_task_card()
        self._run(["git", "add", "task-cards/PROJ.md"], cwd=self.repo)
        self._run(["git", "commit", "-m", "add task"], cwd=self.repo)
        result = self._dispatch()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        runtime_path = self._artifact_path(result.stdout, "Runtime Identity")
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(runtime["external_integrations_allowed"], "no")
        self.assertEqual(runtime["mcp_config_paths"], "none")
        self.assertEqual(runtime["plugin_paths"], "none")
        self.assertEqual(runtime["external_integration_rejection"], "none")

    def test_external_integration_preserves_case_and_spaces_as_single_arguments(self):
        config_dir = self.repo / "Configs"
        config_dir.mkdir()
        (config_dir / "My MCP.json").write_text("{}", encoding="utf-8")
        plugin_dir = self.repo / "Plugins" / "My Plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text("{}", encoding="utf-8")
        self._commit_external_integration_fixtures("Configs", "Plugins")
        self._write_external_integration_card(
            allowed="yes",
            mcp_paths="Configs/My MCP.json",
            plugin_paths="Plugins/My Plugin",
        )
        argv_log = self.case_root / "argv-ext-case-space.nul"
        result = self._dispatch(
            "task-cards/EXTINT.md",
            {"FAKE_CLAUDE_ARGV_NUL_LOG": str(argv_log)},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        argv = argv_log.read_bytes().decode("utf-8").rstrip("\0").split("\0")
        self.assertIn("Configs/My MCP.json", argv)
        self.assertIn("Plugins/My Plugin", argv)

    def test_external_integration_rejects_non_strict_authorization(self):
        self._write_external_integration_card(allowed="yes", strict="no")
        result = self._dispatch("task-cards/EXTINT.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("strict mcp isolation", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
