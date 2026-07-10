# MCP Policy  -  Information Retrieval Order

When gathering information about a codebase, follow this order from cheapest to most expensive. Always start at the top and only move down when the current level is insufficient. In very large repositories, "cheapest" is measured by bounded wall time as well as token volume; a 300 second graph query is not cheap.

## 1. LSP Definitions, References, Diagnostics

Use the Language Server Protocol to find:

- Where a symbol is defined
- Where a symbol is referenced
- Current diagnostics (errors, warnings, hints)

This is the cheapest and most precise form of code intelligence. Use it first for any question about "where is X defined" or "what uses X."

## 2. Bounded Locator / Codegraph Intelligence

In large repositories, first run the bounded lexical locator:

```bash
python ai/locate-code.py "symbol or behavior" --path <area> --max-files 12
```

Use its candidate files, snippets, and suggested line reads to seed the task card's Claude Context Packet. It uses `git ls-files` plus `rg`/`git grep`, which is often cheaper and more predictable than a broad graph query.

Use CodeGraph for concrete files, symbols, callers, callees, dependencies, and impact radius when the query is narrow and bounded by a short timeout. Do not run repeated broad CodeGraph queries in large repositories.

Use codegraph tools to find:

- Who calls a function (callers)
- What a function calls (callees)
- Module-level dependency graphs
- Impact radius of a change  -  what would break if this module changes

Use this when LSP references or locator output are not enough  -  for example, when you need to understand the blast radius of a concrete change across modules. If CodeGraph times out once, record the timeout and fall back to targeted search plus line reads.

## 3. Targeted Search

Use grep, ripgrep, or similar tools to search for:

- String patterns in code
- Configuration values
- Error messages
- Documentation references

Use this when LSP, locator output, and bounded CodeGraph cannot answer the question  -  for example, searching for a string that appears in comments, configs, or logs.

## 4. Targeted Snippet Reads

Read specific lines or small regions of files. Use line numbers from LSP, codegraph, or search results to read only what is needed.

Use this when you need to see the actual code, not just metadata about it.

## 5. Whole-File Reads

Read an entire file when:

- The file is small (< 200 lines)
- You need to understand the overall structure
- Multiple scattered references need to be understood in context

Use this sparingly. If you find yourself reading many whole files, reconsider whether `ai/locate-code.py`, a narrower CodeGraph query, or a more focused search would be more efficient.

## 6. Full Repository Scan

Scan the entire repository only when:

- No other approach has answered the question
- The human has explicitly approved a full scan
- You are performing an initial onboarding survey of an unfamiliar codebase

Full repository scans are expensive and slow. They should be the exception, not the norm.

## Budget Gates and Delegation Thresholds

This policy applies to both Codex (during OBSERVE/PLAN) and Claude Code (during EXECUTE), but the delegation boundary is different:

### Codex budget gate

Codex should stop reading and dispatch to Claude when:
- A file exceeds 200 lines and LSP/locator/CodeGraph cannot answer the question.
- More than 3 whole-file reads would be needed to plan the task.
- A full repository scan is required.
- Long test logs or CI output need analysis.

In these cases, Codex records what it knows and delegates the high-token investigation to Claude Code in the task card.

### Claude evidence compression gate

Claude must not return large pasted content to Codex. Instead:
- Summarize findings in one paragraph per file.
- Link to artifact paths (diff files, reports, diagnostics).
- Provide pass/fail counts, not full test output.
- Record actual token budget used in the evidence packet.

### Delegation checklist for task cards

Every task card should include a `## High-Token Delegation Gate` section listing which reads or investigations are delegated to Claude. The reviewer checks whether this policy was followed.

## Principle

**Read less, query with budgets.** Every file read costs tokens, but every tool call also costs wall time. Prefer LSP, `ai/locate-code.py`, bounded CodeGraph, and targeted snippets over broad repository reads or repeated graph queries.
