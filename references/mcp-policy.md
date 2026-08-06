# MCP Policy  -  Information Retrieval Order

When gathering information about a codebase, follow this order from cheapest to most expensive. Always start at the top and only move down when the current level is insufficient. In very large repositories, "cheapest" is measured by bounded wall time as well as token volume; a 300 second graph query is not cheap.

## 1. Bounded CodeGraph for Indexed Code

When a repository has a healthy current-worktree `.codegraph/` index and the
question concerns a concrete indexed-code symbol, caller, callee, dependency,
or impact radius, run one bounded CodeGraph query first. This is the default
for both small and large repositories: file count alone must not suppress a
healthy index. Bound it with a short timeout and one result-size cap; after one
timeout or unusable result, record the reason and continue with the fallback.

For behavior or file discovery, use:

```bash
python ai/locate-code.py "symbol or behavior" --path <area> --max-files 12
```

Its default `--codegraph auto` makes the same bounded attempt whenever the
current index is healthy, then retains lexical candidates as fallback evidence.
Use lexical search directly for Shell, configuration, prose, log/error text,
or a language that CodeGraph does not index.

## 2. LSP Definitions, References, Diagnostics

Use the Language Server Protocol to find:

- Where a symbol is defined
- Where a symbol is referenced
- Current diagnostics (errors, warnings, hints)

This is often the cheapest and most precise form of code intelligence. Use it
when CodeGraph is unavailable, the language is unsupported, or the question is
about editor diagnostics rather than repository relationships.

## 3. Locator and Fallback Evidence

Use locator candidates, LSP results, and CodeGraph output to seed the task
card's Claude Context Packet. Do not run repeated broad graph queries.

CodeGraph evidence is worktree-bound. Before using it, compare the current Git top level with `codegraph status . -j` fields `projectPath` and `worktreeMismatch`. A different worktree fails closed: do not quote, summarize, or place those results in a Context Packet. Fall back to LSP, `ai/locate-code.py`, targeted search, and targeted reads. In delegated execution, the dispatcher records `<task-id>.codegraph-worktree.json`; only `status=ready` permits graph use. Default `CLAUDE_CODE_CODEGRAPH_POLICY=fallback` avoids rebuilding ephemeral indexes. Explicit `repair` may sync a current index or reindex the execution worktree when its expected value justifies the wall time.

Use codegraph tools to find:

- Who calls a function (callers)
- What a function calls (callees)
- Module-level dependency graphs
- Impact radius of a change  -  what would break if this module changes

If CodeGraph times out once, record the timeout and fall back to targeted
search plus line reads.

## 4. Targeted Search

Use grep, ripgrep, or similar tools to search for:

- String patterns in code
- Configuration values
- Error messages
- Documentation references

Use this when LSP, locator output, and bounded CodeGraph cannot answer the question  -  for example, searching for a string that appears in comments, configs, or logs.

## 5. Targeted Snippet Reads

Read specific lines or small regions of files. Use line numbers from LSP, codegraph, or search results to read only what is needed.

Use this when you need to see the actual code, not just metadata about it.

## 6. Whole-File Reads

Read an entire file when:

- The file is small (< 200 lines)
- You need to understand the overall structure
- Multiple scattered references need to be understood in context

Use this sparingly. If you find yourself reading many whole files, reconsider whether `ai/locate-code.py`, a narrower CodeGraph query, or a more focused search would be more efficient.

## 7. Full Repository Scan

Scan the entire repository only when:

- No other approach has answered the question
- The human has explicitly approved a full scan
- You are performing an initial onboarding survey of an unfamiliar codebase

Full repository scans are expensive and slow. They should be the exception, not the norm.

## Budget Gates and Delegation Thresholds

This policy applies to both Codex (during OBSERVE/PLAN) and Claude Code (during EXECUTE), but the delegation boundary is different:

### Codex budget gate

Codex should stop broad reading and reassess the cheapest evidence path when:
- A file exceeds 200 lines and LSP/locator/CodeGraph cannot answer the question.
- More than 3 whole-file reads would be needed to plan the task.
- A full repository scan is required.
- Long test logs or CI output need analysis.

In these cases, Codex records what it knows and routes again. Prefer a bounded local index/query or direct targeted read. Delegate to Claude only when a durable structured artifact, implementation, test, or evidence result demonstrably removes substantial Codex work; prose-only investigation is not sufficient value.

### Claude evidence compression gate

Claude must not return large pasted content to Codex. Instead:
- Summarize findings in one paragraph per file.
- Link to artifact paths (diff files, reports, diagnostics).
- Provide pass/fail counts, not full test output.
- Record actual token budget used in the evidence packet.

Cache reusable locator/LSP/graph evidence with repository identity, not a
caller-supplied label alone. `context-cache.py --repo` binds HEAD, exact file
hashes, symbols, and tool version into the content key. A dirty file, new HEAD,
symbol-set change, or tool-version change must produce a miss rather than reuse
stale context. Legacy unbound cache records remain readable only through the
legacy invocation and are marked `legacy-unverified`.

### Delegation checklist for task cards

The monolithic compatibility card may include a `## High-Token Work Routing Gate`; short component cards include only material routing/context facts. Record why high-token work belongs to Codex, local tools, or Claude. The reviewer checks economic value and durable output rather than enforcing delegation by size.

## Principle

**Read less, query with budgets.** Every file read costs tokens, but every tool call also costs wall time. Prefer LSP, `ai/locate-code.py`, bounded CodeGraph, and targeted snippets over broad repository reads or repeated graph queries.
