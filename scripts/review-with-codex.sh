#!/usr/bin/env bash
# review-with-codex.sh  -  Send execution evidence to Codex/GPT for review.
#
# Usage: bash ai/review-with-codex.sh <task-card> <result-json> <diff-patch>
#
# This script:
#   1. Validates that codex CLI exists.
#   2. Validates that input files exist.
#   3. Invokes codex exec to review the evidence.
#   4. Asks Codex to review, not implement.
#   5. Asks for a decision: accept / revise / split / reject.

set -euo pipefail

# --- Argument validation ---
if [ $# -lt 3 ]; then
    echo "Usage: $0 <task-card> <result-json> <diff-patch>" >&2
    exit 1
fi

TASK_CARD="$1"
RESULT_JSON="$2"
DIFF_PATCH="$3"

for f in "$TASK_CARD" "$RESULT_JSON" "$DIFF_PATCH"; do
    if [ ! -f "$f" ]; then
        echo "Error: File not found: $f" >&2
        exit 1
    fi
done

# --- Check required tools ---
if ! command -v codex &>/dev/null; then
    echo "Error: codex CLI is not installed or not in PATH." >&2
    exit 1
fi

# --- Read evidence ---
TASK_CONTENT="$(cat "$TASK_CARD")"
RESULT_CONTENT="$(cat "$RESULT_JSON")"
DIFF_CONTENT="$(cat "$DIFF_PATCH")"

# --- Build review prompt ---
REVIEW_PROMPT="You are a code reviewer. Review the following execution evidence and make a decision.

## Your Role
- You are reviewing, NOT implementing.
- Evaluate whether the implementation matches the task card intent.
- Assess regression risk and design coherence.
- Check for security implications.

## Task Card
${TASK_CONTENT}

## Execution Result
${RESULT_CONTENT}

## Diff
\`\`\`
${DIFF_CONTENT}
\`\`\`

## Required Output
Respond with exactly one of these decisions, followed by your reasoning:

- **ACCEPT**  -  implementation is correct and complete.
- **REVISE**  -  implementation needs changes. Provide specific revision instructions.
- **SPLIT**  -  the task should be broken into smaller task cards. Explain why.
- **REJECT**  -  the approach is fundamentally wrong. Explain why and suggest an alternative."

# --- Invoke Codex ---
echo "Invoking Codex for review..."
echo ""

codex exec "$REVIEW_PROMPT"

echo ""
echo "=== Review Complete ==="
