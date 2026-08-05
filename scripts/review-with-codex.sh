#!/usr/bin/env bash
# review-with-codex.sh  -  Send execution evidence to Codex/GPT for review.
#
# Usage: bash ai/review-with-codex.sh <task-card> <result-json> <diff-file>
#        [--spark-compression auto|off|required] [extra-evidence ...]
#
# This script:
#   1. Validates that codex CLI exists.
#   2. Validates that input files exist.
#   3. Optionally reads extra evidence files (usage summary, source status, report, etc.).
#   4. Invokes codex exec for structured review.
#   5. Persists review output, Codex JSON events, and Codex usage summary when available.

set -euo pipefail

# Git for Windows can be launched through bin/bash.exe without the usual Unix tool PATH.
# Prepending these paths is harmless on Unix and makes helper scripts stable on Windows.
PATH="/usr/bin:/bin:/mingw64/bin:${PATH}"
export PATH

if [ $# -lt 3 ]; then
    echo "Usage: $0 <task-card> <result-json> <diff-file> [extra-evidence ...]" >&2
    echo "" >&2
    echo "Optional extra evidence files (any combination):" >&2
    echo "  usage.txt           - Claude token/cost usage summary" >&2
    echo "  checker-report.md   - Checker-only validation report" >&2
    echo "  source-status.txt   - Source repo state before dispatch" >&2
    echo "  worktree-status.txt - Worktree state after execution" >&2
    echo "  untracked.txt       - Untracked files listing" >&2
    echo "  report.md           - Claude modification report" >&2
    echo "  progress.log        - Claude dispatch heartbeat/progress log" >&2
    echo "  pid                 - Claude subprocess PID artifact" >&2
    echo "  --spark-compression - auto, off, or required (default: auto)" >&2
    exit 1
fi

TASK_CARD="$1"
RESULT_JSON="$2"
DIFF_FILE="$3"
shift 3
SPARK_COMPRESSION_MODE="${AI_WORKFLOW_SPARK_COMPRESSION:-auto}"
EXTRA_FILES=()
while [ $# -gt 0 ]; do
    case "$1" in
        --spark-compression)
            [ $# -ge 2 ] || { echo "Error: --spark-compression requires a value." >&2; exit 1; }
            SPARK_COMPRESSION_MODE="$2"
            shift 2
            ;;
        *)
            EXTRA_FILES+=("$1")
            shift
            ;;
    esac
done

for f in "$TASK_CARD" "$RESULT_JSON" "$DIFF_FILE"; do
    if [ ! -f "$f" ]; then
        echo "Error: File not found: $f" >&2
        exit 1
    fi
done

for f in "${EXTRA_FILES[@]+"${EXTRA_FILES[@]}"}"; do
    if [ -n "$f" ] && [ ! -f "$f" ]; then
        echo "Warning: Extra evidence file not found, skipping: $f" >&2
    fi
done

if ! command -v codex &>/dev/null; then
    echo "Error: codex CLI is not installed or not in PATH." >&2
    exit 1
fi

REVIEW_PREFIX="${DIFF_FILE%.diff}"
REVIEW_OUTPUT_FILE="${REVIEW_PREFIX}.review.txt"
CODEX_EVENTS_FILE="${REVIEW_PREFIX}.codex-events.jsonl"
CODEX_USAGE_FILE="${REVIEW_PREFIX}.codex-usage.txt"
REVIEW_PACKET_FILE="${REVIEW_PREFIX}.review-packet.json"
REVIEW_CAPSULE_FILE="${REVIEW_PREFIX}.review-capsule.json"
REVIEW_PROMPT_FILE="${REVIEW_PREFIX}.review-prompt.txt"
SPARK_COMPRESSION_OUTPUT_FILE="${REVIEW_PREFIX}.spark-compression.stdout"
SPARK_COMPRESSION_CAPSULE_FILE="${REVIEW_PREFIX}.spark-compression-capsule.json"

# Build review packet if build-review-packet.py is available
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_PACKET_SCRIPT="${SCRIPT_DIR}/build-review-packet.py"

PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
fi

# Find the run directory (parent of the diff file's directory)
RUN_DIR="$(dirname "$DIFF_FILE")"
# If diff is in a dispatch-N subdirectory, go up one level
if [[ "$RUN_DIR" == */dispatch-* ]]; then
    RUN_DIR="$(dirname "$RUN_DIR")"
fi

# Build review packet from run directory
if [ -n "$PYTHON_CMD" ] && [ -f "$BUILD_PACKET_SCRIPT" ]; then
    # Collect supplemental files
    SUPPLEMENTAL_ARGS=()
    for f in "${EXTRA_FILES[@]+"${EXTRA_FILES[@]}"}"; do
        if [ -n "$f" ] && [ -f "$f" ]; then
            SUPPLEMENTAL_ARGS+=("$f")
        fi
    done

    set +e
    "$PYTHON_CMD" "$BUILD_PACKET_SCRIPT" "$RUN_DIR" \
        --output "$REVIEW_PACKET_FILE" \
        --capsule-output "$REVIEW_CAPSULE_FILE" \
        --prompt-output "$REVIEW_PROMPT_FILE" \
        --prompt-mode capsule \
        --task-card "$TASK_CARD" \
        --diff-file "$DIFF_FILE" \
        --supplemental "${SUPPLEMENTAL_ARGS[@]}" \
        --stdout-mode off \
        >/dev/null 2>&1
    PACKET_STATUS=$?
    set -e

    if [ "$PACKET_STATUS" -ne 0 ]; then
        echo "Warning: Review packet build failed (exit $PACKET_STATUS). Falling back to direct evidence." >&2
    fi
fi

case "$SPARK_COMPRESSION_MODE" in
    auto|off|required) ;;
    *)
        echo "Error: AI_WORKFLOW_SPARK_COMPRESSION must be auto, off, or required." >&2
        exit 1
        ;;
esac

# Complex evidence may receive one advisory Spark compression pass. The full
# response remains file-backed; Codex receives only the bounded summary capsule.
SPARK_COMPRESSION_RECOMMENDED="no"
if [ -n "$PYTHON_CMD" ] && [ -s "$REVIEW_CAPSULE_FILE" ]; then
    SPARK_COMPRESSION_RECOMMENDED="$("$PYTHON_CMD" - "$REVIEW_CAPSULE_FILE" <<'PYEOF'
import json, sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print("yes" if value.get("compression_route", {}).get("spark_recommended") else "no")
PYEOF
)"
fi

if [ "$SPARK_COMPRESSION_MODE" != "off" ] && [ "$SPARK_COMPRESSION_RECOMMENDED" = "yes" ] && \
   [ -f "${SCRIPT_DIR}/run-codex-spark.sh" ]; then
    set +e
    bash "${SCRIPT_DIR}/run-codex-spark.sh" "$TASK_CARD" \
        --mode postflight-bundle \
        --artifact "$REVIEW_PACKET_FILE" \
        --result-mode direct \
        --execution-env host \
        >"$SPARK_COMPRESSION_OUTPUT_FILE" 2>"${SPARK_COMPRESSION_OUTPUT_FILE}.stderr"
    SPARK_COMPRESSION_STATUS=$?
    set -e
    if [ -n "$PYTHON_CMD" ] && [ -s "$SPARK_COMPRESSION_OUTPUT_FILE" ] && \
       [ -f "${SCRIPT_DIR}/build-spark-summary-capsule.py" ]; then
        "$PYTHON_CMD" "${SCRIPT_DIR}/build-spark-summary-capsule.py" \
            "$SPARK_COMPRESSION_OUTPUT_FILE" \
            --output "$SPARK_COMPRESSION_CAPSULE_FILE" \
            --stdout-mode off || true
    fi
    if [ "$SPARK_COMPRESSION_STATUS" -ne 0 ] && [ "$SPARK_COMPRESSION_MODE" = "required" ]; then
        echo "Error: required Spark compression failed with exit ${SPARK_COMPRESSION_STATUS}." >&2
        exit "$SPARK_COMPRESSION_STATUS"
    fi
fi

if [ -s "$SPARK_COMPRESSION_CAPSULE_FILE" ] && [ -s "$REVIEW_PROMPT_FILE" ]; then
    {
        echo ""
        echo "Spark advisory capsule: ${SPARK_COMPRESSION_CAPSULE_FILE}"
        echo "Read it as a bounded advisory index only; verify cited original evidence before deciding."
    } >> "$REVIEW_PROMPT_FILE"
fi

# Use the capsule prompt when available. If packet generation failed, keep the
# fallback tool-backed too; never paste task, diff, result, or log bodies.
if [ ! -f "$REVIEW_PROMPT_FILE" ]; then
    REVIEW_PROMPT=$(cat <<_REVIEW_EOF_
# Tool-backed review fallback

The deterministic review-packet builder was unavailable. Do not implement or merge.
Use local read-only tools and inspect these exact files selectively:
- Task card: $TASK_CARD
- Result receipt: $RESULT_JSON
- Diff: $DIFF_FILE

Do not cat whole files or logs. Start with file sizes, hashes, diffstat, changed paths,
and failed gates; then read only semantic hotspots. Treat unreadable or stale evidence
as needs-review. Return exactly one JSON object with schema_version=1;
decision=accept|revise|split|reject; scope=phase|whole-task; reasoning;
direction.status; acceptance; validation; next_task; lessons.
_REVIEW_EOF_
)
fi

# PYTHON_CMD is set earlier (before review packet build).
# Remove the duplicate definition that was here.

write_codex_usage() {
    if [ -z "$PYTHON_CMD" ]; then
        {
            echo "# Codex Token / Cost Usage Summary"
            echo ""
            echo "Skipped: neither python3 nor python found in PATH."
            echo "Raw review output: $REVIEW_OUTPUT_FILE"
            echo "Codex events: $CODEX_EVENTS_FILE"
        } > "$CODEX_USAGE_FILE"
        return 0
    fi

    "$PYTHON_CMD" - "$CODEX_EVENTS_FILE" "$REVIEW_OUTPUT_FILE" "$CODEX_USAGE_FILE" \
        "$REVIEW_PROMPT_FILE" "$REVIEW_CAPSULE_FILE" "$SPARK_COMPRESSION_CAPSULE_FILE" <<'PYEOF'
import json
import re
import sys
from pathlib import Path

events_path = Path(sys.argv[1])
text_path = Path(sys.argv[2])
usage_path = Path(sys.argv[3])
prompt_path = Path(sys.argv[4])
capsule_path = Path(sys.argv[5])
spark_capsule_path = Path(sys.argv[6])

keys = {
    "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens", "reasoning_tokens",
    "total_cost_usd", "cost_usd", "duration_ms"
}
found = []

def walk(value, prefix=""):
    if isinstance(value, dict):
        for k, v in value.items():
            name = f"{prefix}.{k}" if prefix else k
            if k in keys and isinstance(v, (int, float)):
                found.append((name, v))
            walk(v, name)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            walk(item, f"{prefix}[{i}]")

if events_path.exists() and events_path.stat().st_size:
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            continue

if not found and text_path.exists():
    text = text_path.read_text(encoding="utf-8", errors="replace")
    patterns = [
        ("input_tokens", r"input[_ ]tokens[:=]\s*([0-9]+)"),
        ("output_tokens", r"output[_ ]tokens[:=]\s*([0-9]+)"),
        ("total_tokens", r"total[_ ]tokens[:=]\s*([0-9]+)"),
        ("total_cost_usd", r"total[_ ]cost[_ ]usd[:=]\s*([0-9.]+)"),
        ("cost_usd", r"cost[_ ]usd[:=]\s*([0-9.]+)"),
    ]
    for name, pat in patterns:
        for m in re.finditer(pat, text, re.I):
            raw = m.group(1)
            found.append((name, float(raw) if "." in raw else int(raw)))

lines = ["# Codex Token / Cost Usage Summary", ""]
if found:
    lines.append("Detected usage fields from Codex review output/events:")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    for name, value in found:
        lines.append(f"| {name} | {value} |")
else:
    lines.append("Usage unavailable: no recognizable token/cost fields were found in Codex output.")
lines.append("")
lines.append("## Evidence transfer")
lines.append("")
lines.append(f"- Codex prompt bytes: {prompt_path.stat().st_size if prompt_path.is_file() else 'unavailable'}")
lines.append(f"- Review capsule bytes: {capsule_path.stat().st_size if capsule_path.is_file() else 'unavailable'}")
lines.append(f"- Spark capsule bytes: {spark_capsule_path.stat().st_size if spark_capsule_path.is_file() else 0}")
if capsule_path.is_file():
    try:
        capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
        transfer = capsule.get("transfer_metrics", {})
        lines.append(f"- Legacy full prompt bytes avoided: {capsule.get('legacy_full_prompt_bytes', 'unknown')}")
        lines.append(f"- Estimated Codex bytes saved: {capsule.get('compression_route', {}).get('estimated_codex_bytes_saved', 'unknown')}")
        lines.append(f"- Capsule hard limit respected: {transfer.get('within_limit', False)}")
    except (OSError, ValueError):
        lines.append("- Capsule metrics unavailable: invalid capsule")
lines.append("")
lines.append(f"Review output: {text_path}")
lines.append(f"Codex events: {events_path}")
usage_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PYEOF
}

echo "Invoking Codex for review..."
if [ ${#EXTRA_FILES[@]} -gt 0 ]; then
    echo "Including ${#EXTRA_FILES[@]} extra evidence file(s)."
fi
echo "Review Output: $REVIEW_OUTPUT_FILE"
echo "Codex Events:  $CODEX_EVENTS_FILE"
echo "Codex Usage:   $CODEX_USAGE_FILE"
if [ -f "$REVIEW_PACKET_FILE" ]; then
    echo "Review Packet: $REVIEW_PACKET_FILE"
fi
if [ -f "$REVIEW_CAPSULE_FILE" ]; then
    echo "Review Capsule: $REVIEW_CAPSULE_FILE"
fi
if [ -f "$SPARK_COMPRESSION_CAPSULE_FILE" ]; then
    echo "Spark Compression Capsule: $SPARK_COMPRESSION_CAPSULE_FILE"
fi
echo ""

# Pass prompt via stdin or file to avoid huge command-line arguments
CODEX_CALL_STARTED_EPOCH="$(date +%s)"
set +e
if [ "${AI_CODING_WORKFLOW_BYPASS_BROKER:-0}" = "1" ]; then
    # Internal bypass for tests/bootstrap to avoid broker recursion.
    if [ -f "$REVIEW_PROMPT_FILE" ]; then
        codex exec --json < "$REVIEW_PROMPT_FILE" > "$CODEX_EVENTS_FILE" 2>"${REVIEW_OUTPUT_FILE}.stderr"
    else
        TEMP_PROMPT="$(mktemp "${REVIEW_PREFIX}.prompt.XXXXXX")"
        printf '%s' "$REVIEW_PROMPT" > "$TEMP_PROMPT"
        codex exec --json < "$TEMP_PROMPT" > "$CODEX_EVENTS_FILE" 2>"${REVIEW_OUTPUT_FILE}.stderr"
        rm -f "$TEMP_PROMPT"
    fi
else
    # Broker-mediated execution for quota enforcement and audit.
    BROKER_REPO_ROOT="$(git -C "$RUN_DIR" rev-parse --show-toplevel 2>/dev/null || pwd)"
    broker_args=(
        --role codex --stage final-review
        --task-id "review-$(basename "$REVIEW_PREFIX")"
        --ledger "${BROKER_REPO_ROOT}/.ai-workflow/model-calls.jsonl"
        --output "$CODEX_EVENTS_FILE" --stderr "${REVIEW_OUTPUT_FILE}.stderr"
    )
    if [ -f "execution-plan.json" ]; then
        broker_args+=(--plan "execution-plan.json")
    fi
    if [ -f "$REVIEW_PROMPT_FILE" ]; then
        broker_args+=(--input "$REVIEW_PROMPT_FILE")
        python3 "${SCRIPT_DIR}/model-call-broker.py" "${broker_args[@]}" -- \
            codex exec --json
    else
        TEMP_PROMPT="$(mktemp "${REVIEW_PREFIX}.prompt.XXXXXX")"
        printf '%s' "$REVIEW_PROMPT" > "$TEMP_PROMPT"
        broker_args+=(--input "$TEMP_PROMPT")
        python3 "${SCRIPT_DIR}/model-call-broker.py" "${broker_args[@]}" -- \
            codex exec --json
        rm -f "$TEMP_PROMPT"
    fi
fi
CODEX_STATUS=$?
set -e
CODEX_CALL_WALL_MS="$(( ($(date +%s) - CODEX_CALL_STARTED_EPOCH) * 1000 ))"

if [ -s "$CODEX_EVENTS_FILE" ] && [ -n "$PYTHON_CMD" ]; then
    "$PYTHON_CMD" - "$CODEX_EVENTS_FILE" "$REVIEW_OUTPUT_FILE" <<'PYEOF'
import json
import sys
from pathlib import Path

events = Path(sys.argv[1])
out = Path(sys.argv[2])
messages = []
for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    text = None
    for key in ("message", "text", "content", "delta"):
        value = event.get(key) if isinstance(event, dict) else None
        if isinstance(value, str):
            text = value
            break
    if text:
        messages.append(text)
if messages:
    out.write_text("\n".join(messages) + "\n", encoding="utf-8")
else:
    out.write_text(events.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
PYEOF
else
    cat "${REVIEW_OUTPUT_FILE}.stderr" > "$REVIEW_OUTPUT_FILE" 2>/dev/null || true
fi

write_codex_usage

# Also persist a canonical machine-readable record. The legacy Markdown file
# remains available to old summaries and humans.
if [ -n "$PYTHON_CMD" ] && [ -f "${SCRIPT_DIR}/model-usage.py" ] && [ -f "$CODEX_EVENTS_FILE" ]; then
    REVIEW_REPO_ROOT="$(git -C "$RUN_DIR" rev-parse --show-toplevel 2>/dev/null || pwd)"
    MODEL_USAGE_LEDGER="${AI_WORKFLOW_MODEL_USAGE_LEDGER:-${REVIEW_REPO_ROOT}/.ai-workflow/model-usage.jsonl}"
    CODEX_REVIEW_TASK_ID="${AI_WORKFLOW_TASK_ID:-review-$(basename "$REVIEW_PREFIX")}"
    CODEX_USAGE_ARGS=(
        --source codex --input "$CODEX_EVENTS_FILE" --ledger "$MODEL_USAGE_LEDGER"
        --task-id "$CODEX_REVIEW_TASK_ID"
        --call-id "review-$(basename "$REVIEW_PREFIX")"
        --role codex --stage final-review --result "$CODEX_STATUS"
        --wall-time-ms "$CODEX_CALL_WALL_MS"
    )
    if [ -n "${AI_WORKFLOW_RUN_ID:-}" ]; then
        CODEX_USAGE_ARGS+=(--run-id "$AI_WORKFLOW_RUN_ID")
    fi
    if [ -n "${AI_WORKFLOW_EXPERIMENT_ARM:-}" ]; then
        CODEX_USAGE_ARGS+=(--experiment-arm "$AI_WORKFLOW_EXPERIMENT_ARM")
    fi
    "$PYTHON_CMD" "${SCRIPT_DIR}/model-usage.py" capture "${CODEX_USAGE_ARGS[@]}" \
        >/dev/null 2>>"${REVIEW_OUTPUT_FILE}.stderr" || \
        echo "Warning: canonical Codex usage capture failed; legacy usage evidence was preserved." >&2
fi

# Parse the structured review decision from the review text
REVIEW_DECISION_FILE="${REVIEW_PREFIX}.review-decision.json"
NEXT_TASK_DRAFT_FILE="${REVIEW_PREFIX}.next-task-draft.json"
PARSE_STATUS=0

if [ -s "$REVIEW_OUTPUT_FILE" ] && [ -n "$PYTHON_CMD" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    PARSE_SCRIPT="${SCRIPT_DIR}/parse-review-decision.py"
    if [ -f "$PARSE_SCRIPT" ]; then
        set +e
        PARSE_OUTPUT="$("$PYTHON_CMD" "$PARSE_SCRIPT" "$REVIEW_OUTPUT_FILE" \
            --output "$REVIEW_DECISION_FILE" \
            --next-task-draft "$NEXT_TASK_DRAFT_FILE" 2>&1)"
        PARSE_STATUS=$?
        set -e

        if [ "$PARSE_STATUS" -ne 0 ]; then
            echo "Error: Review decision parsing failed (exit $PARSE_STATUS)." >&2
            echo "$PARSE_OUTPUT" >&2
            echo "The review output did not contain a valid structured decision." >&2
            echo "Review text cannot override the JSON decision protocol." >&2
        else
            echo "$PARSE_OUTPUT"
        fi
    else
        echo "Warning: parse-review-decision.py not found at $PARSE_SCRIPT" >&2
        PARSE_STATUS=1
    fi
elif [ -s "$REVIEW_OUTPUT_FILE" ]; then
    echo "Warning: Python not available; cannot parse structured review decision." >&2
    PARSE_STATUS=1
fi

if [ -s "$REVIEW_OUTPUT_FILE" ]; then
    cat "$REVIEW_OUTPUT_FILE"
fi
if [ "$CODEX_STATUS" -ne 0 ]; then
    echo "Warning: codex exited with non-zero status. Check $REVIEW_OUTPUT_FILE and ${REVIEW_OUTPUT_FILE}.stderr" >&2
    exit "$CODEX_STATUS"
fi

if [ "$PARSE_STATUS" -ne 0 ]; then
    echo "Error: Review decision parsing failed. Structured decision required." >&2
    exit "$PARSE_STATUS"
fi

echo ""
echo "=== Review Complete ==="
echo "Review Output: $REVIEW_OUTPUT_FILE"
echo "Review Decision: $REVIEW_DECISION_FILE"
if [ -f "$NEXT_TASK_DRAFT_FILE" ] && [ -s "$NEXT_TASK_DRAFT_FILE" ]; then
    echo "Next Task Draft: $NEXT_TASK_DRAFT_FILE"
fi
echo "Codex Events:  $CODEX_EVENTS_FILE"
echo "Codex Usage:   $CODEX_USAGE_FILE"
