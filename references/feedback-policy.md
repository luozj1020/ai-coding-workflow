# User-Triggered Workflow Feedback

Load this reference only when the user explicitly asks for experience feedback,
a workflow retrospective, or an assessment of Skill problems. Feedback is a
read-only Codex response, not a telemetry subsystem and not a workflow phase.

## Procedure

1. Use the current conversation as the primary description of the experience.
2. Read only the smallest relevant runtime receipts needed to verify a concrete
   claim. Do not start Spark or Claude and do not read API configuration.
3. Separate user observations, deterministic evidence, model claims, inferred
   causes, and recommendations.
4. Report strengths, problems, likely causes, priority, and bounded suggested
   changes. Mark unsupported or ambiguous conclusions explicitly.
5. Do not modify code, create a task card, write a feedback artifact, or begin a
   remediation until the user separately asks to make changes.

## Evidence and Privacy Boundary

Do not create or read a feedback ledger. Do not persist conversation content,
ratings, comments, source, diffs, prompts, raw logs, command bodies, API/MCP
configuration, environment values, user names, or absolute paths as feedback.
Existing runtime receipts remain ordinary workflow evidence and are not copied
into another store.

Feedback never refreshes progress, changes failure attribution, authorizes a
retry/takeover/merge, or grants broader access. Spark and Claude are unnecessary
for the retrospective itself. If the user later requests remediation, route that
new change normally from the accepted feedback scope.

## Priority Guidance

- P0: confirmed safety boundary failure, concurrent writer, destructive risk, or
  evidence that incorrect output could be accepted.
- P1: confirmed correctness/recovery defect or repeated required human recovery.
- P2: repeated latency, approval, monitoring, or explainability friction without
  correctness impact.
- P3: isolated low-impact usability or documentation issue.

State both priority and confidence. One deterministic invariant violation may
confirm P0/P1; efficiency issues normally need repeated observations. Model
opinion alone never confirms a level.
