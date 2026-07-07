---
name: ai-coding-workflow
description: use this skill when installing, updating, or using a local multi-agent ai coding workflow for software repositories. triggers include setting up codex and claude code collaboration, creating agents.md or claude.md rules, generating task-card and evidence-packet templates, dispatching work from codex to claude code, reviewing execution evidence, and enforcing lsp/codegraph/mcp-first development workflows.
---

# AI Coding Workflow Skill

Use this skill to install, update, or operate the local Codex / Claude Code coding workflow. Keep the default context lean: load detailed references only when the current task needs them.

## Quick Commands

Install or update the skill for Codex discovery:

```bash
python scripts/install_for_codex.py
```

Convenient update wrapper:

```bash
python scripts/update_skill.py --bootstrap-current
```

Bootstrap a target repository:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

From a cloned copy, install the skill and bootstrap a repository:

```bash
python scripts/install_for_codex.py --bootstrap-repo /path/to/repo
```

After skill installation, `install_for_codex.py` performs a read-only context intelligence check for common LSP tools, CodeGraph CLI availability, and `.codegraph/` initialization for bootstrapped repositories. It prints suggestions only; it does not install tools or run `codegraph init`.

## Modes

### Install

Run `install_for_codex.py` once per machine so Codex can discover this skill. The installer copies the skill directory to `~/.codex/skills/ai-coding-workflow` and prints bootstrap commands for target repositories.

### Update

Run the same install command again. Repository bootstrap updates managed blocks in `AGENTS.md` and `CLAUDE.md`, preserves user-owned content outside managed markers, copies plain workflow files only when missing, and validates shell scripts with `bash -n`.

### Use

Before dispatching work, verify the target repository has `ai/dispatch-to-claude.sh` and `ai/task-card-template.md`. If not, bootstrap it first and run:

```bash
python ai/doctor_workflow.py
```

Core loop:

1. OBSERVE: gather low-token context with LSP, CodeGraph, MCP, and targeted snippets.
2. PLAN: create or revise a task card from `ai/task-card-template.md`.
3. DISPATCH: run `bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md`.
4. VERIFY: Claude edits in an isolated worktree and produces report/checker evidence.
5. REVIEW: run `bash ai/review-with-codex.sh ...` or `bash ai/run-loop.sh ...`.
6. LEARN: carry accept/revise/split/reject decisions into the next iteration.

## Hot-Path Rules

- Codex designs and reviews; Claude Code edits.
- After a Claude execution round, Codex normally accepts, revises, splits, or rejects; it does not patch implementation files directly.
- Claude no-progress, early exit, invalid result, or one failed attempt is not enough for Codex takeover; tighten the task card and re-dispatch Claude.
- Codex may directly intervene only after repeated Claude failure or an external blocker, and must record the intervention reason, scope, and validation.
- Prior-session Claude failures are context, not automatic takeover permission; re-dispatch Claude unless the current task cites matching loop artifacts or the user explicitly asks Codex to take over.
- Use LSP/CodeGraph/MCP before broad reads.
- Delegate whole-file scans, long logs, and multi-file implementation to Claude.
- Task cards must say whether Claude writes tests, runs tests, or leaves verification to Codex/humans; test-code tasks can be delegated to Claude when the user asks for tests or Codex makes them acceptance-critical.
- Preserve large outputs as artifact paths and short summaries.
- Do not merge automatically.
- Destructive or high-risk actions require explicit human approval.
- If Claude appears quiet, inspect `ai/watch-claude.sh` or `ai/status-claude.sh`; continue waiting when partial work matches the plan, and interrupt only when it is off-plan, risky, or no longer useful.

## Common Artifacts

Dispatch artifacts live under `.worktrees/`:

- `*.result.json`, `*.status.txt`, `*.diff`, `*.diffstat.txt`
- `*.result.raw.txt` when Claude exits without valid JSON output
- `*.checker-report.md`, `*.checker-logs/`
- `*.report.md`, `*.claude-progress.md`, `*.progress.log`, `*.pid`
- `*.usage.txt`, `*.worktree-status.txt`, `*.untracked.txt`

## When To Load More

Read only the relevant reference for the current need:

- `references/mcp-policy.md`: context retrieval order and LSP/CodeGraph/MCP use.
- `references/loop-model.md`: loop state machine, wait policy, stop conditions.
- `references/review-policy.md`: Codex review decisions and checker evidence.
- `references/benchmark-policy.md`: quality/speed/cost/stability summary.
- `references/operating-model.md`: role boundaries and handoff model.

For local usage details, see installed `ai/README.md` or repository `README.md`.
