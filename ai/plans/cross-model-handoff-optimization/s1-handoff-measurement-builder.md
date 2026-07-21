<!-- task-card-components: preset=builder; gates=large-repo; schema=1 -->

# Task Card

<!-- Generated from selected components. Fill concise task-specific facts; delete unused placeholder rows. -->

## ID

cross-model-handoff-s1-measurement

## Task Mode

| Field | Value |
|---|---|
| Mode | builder |

## Goal

Implement the frozen contract's S1 handoff-measurement slice so every real
Claude dispatch records one schema-valid, append-only handoff event and local
summaries expose observed values without treating missing measurements as zero.

## Scope

- Write paths: `scripts/event_writer.py`, `schemas/run-event-v2.schema.json`, `schemas/handoff-event-v1.schema.json`, `scripts/record-handoff-event.py`, `scripts/summarize-handoff-metrics.py`, `scripts/summarize-loop-run.py`, `scripts/dispatch-to-claude.sh`, `scripts/run-workflow.py`, `scripts/install_workflow.py`, `tests/test_handoff_metrics.py`; adjust an existing directly related test only if required
- Read paths: the listed paths, existing event/summary/dispatcher tests, frozen contract
- Forbidden paths: installed/generated `ai/`, `.git/`, later S2–S9 implementation, unrelated docs/features
- Explicitly out of scope: State IR generation, delta/ACK, Evidence Object Store, Pull Context Broker, routing changes, model activation/KV work

## Claude Context Packet

| Field | Value |
|---|---|
| Target files/modules | Existing `EventWriter`/`build_event`; Claude dispatcher model-call boundary/finalization; `RunContext` event log; loop summary metrics; installer asset lists |
| Exact symbols/tests | `scripts/event_writer.py:EventWriter`, `scripts/run-workflow.py:RunContext`, `scripts/summarize-loop-run.py:summarize`; new `tests/test_handoff_metrics.py` |
| Root-cause evidence or relevant excerpt | Existing metrics include task-card/review-packet sizes but do not directly record sender/receiver payload, reconstruction activity, rediscovery, or handoff-induced revision. |
| Reference implementation/source of truth | Frozen contract `ai/plans/cross-model-handoff-optimization/solution-contract.frozen.json`, hash `2183a493e5e8ae7b9993dff2c083f8f0d288c31c40f81a1af8f49048c36cfa43`; existing run-event v2 writer/hash chain and installer tests |
| Known constraints | Canonical sources are top-level tracked paths. Python 3.9 compatible. Reuse the existing append-only event/hash-chain infrastructure; do not create a competing truth ledger. Missing/unobservable numeric or boolean metrics must be explicit `unknown`, never `0`, `false`, or inferred. Existing run results and legacy summaries remain compatible. |
| Do not read/modify | Untracked root `ai/`; planning DOCX; later-slice files |
| Context sufficient for execution? | yes |
| Execution-only eligible? | yes |

## Handoff Contract

- Must do: add a strict handoff-event detail schema and deterministic recorder/summarizer; connect the recorder to the real Claude dispatch boundary exactly once; register installed assets; add focused tests.
- Must not do: infer rediscovery or revision values from access counts; double-record the same integrated-run dispatch; change model routing or timeout behavior; make preview invoke a model.
- May decide: whether handoff detail is embedded in run-event v2 or referenced by it, provided the existing run-event log stays canonical and the helper remains independently testable.
- Stop and report when: exactly-once emission cannot be achieved without expanding outside the allowed dispatcher/runner paths, or schema compatibility requires a breaking migration.

## Acceptance Criteria

- [ ] Each actual Claude dispatcher invocation emits exactly one `handoff_recorded` event; an integrated `run-workflow` invocation does not duplicate it.
- [ ] Required observed fields include task/run identity, sender, receiver, payload/task-card/review-packet bytes, novel/repeated payload bytes, receiver reads/searches before first action, seconds to first meaningful action, known facts rediscovered, rejected hypotheses revisited, handoff-induced revision, and context-cache requests/hits.
- [ ] Any unavailable metric is serialized and summarized as `unknown`; missing legacy fields are not converted to zero/false.
- [ ] Recorder rejects malformed types/unsafe paths and appends through the existing atomic/hash-chained event mechanism.
- [ ] Summary reports handoff count, known byte totals, redundancy/cache ratios only when denominators are known and valid, and groups by task type when present.
- [ ] Preview/dry-run tests prove zero model calls; instrumentation does not change the dispatch result or exit status.
- [ ] Installer copies the new helpers/schema; existing Markdown task-card and legacy run behavior remain compatible.

## Testing Responsibility

| Responsibility | Owner |
|---|---|
| Implementation | Claude execution-builder |
| Test writing | Claude, focused S1 unit/integration tests |
| Narrow validation | Claude, then Codex rerun |
| Checker model dispatch | no; deterministic tests and shell syntax evidence are sufficient |
| Direction review | Codex |
| Final review | Codex |

## Validation Contract

- Local validation allowed: yes
- Exact narrow command: `python -m pytest -q tests/test_handoff_metrics.py tests/test_summarize_loop_run.py tests/test_run_workflow.py && bash -n scripts/dispatch-to-claude.sh && python -m py_compile scripts/record-handoff-event.py scripts/summarize-handoff-metrics.py`
- Required evidence: changed-file list, test output, shell syntax result, sample event and summary showing both known and `unknown` fields, proof of exactly-once integration

## Execution Progress

- [ ] Read this card and update `CLAUDE_PROGRESS.md`.
- [ ] Complete the assigned responsibility.
- [ ] Write the required report.

## Stop Conditions

- Scope, solution, or required context materially expands.
- A required contract is ambiguous or unavailable.

## Builder Contract

- Implement only the scoped production change.
- Do not write acceptance tests or run broad validation unless explicitly assigned.
- Continue after a non-blocking `proceed` acknowledgement in the same run.

## Post-Implementation Contract

| Field | Value |
|---|---|
| Narrow validation assigned | yes — exact command in Validation Contract |
| Bounded self-review assigned | yes — changed files and assigned acceptance only |
| Documentation assigned | no — replace with exact files when required |
| Long validation owner | not-required — replace with checker/helper/human when required |
| Additional cleanup allowed | no, unless explicitly listed in Required Changes |
| Exit after assigned tail work | yes |

- When the assigned implementation is complete, set `Implementation Complete: yes` in `CLAUDE_PROGRESS.md`.
- Perform only the bounded diff review, narrow validation, documentation, and report work explicitly assigned above. A bounded self-review uses Claude's built-in Read/diff/search tools over changed files; it is not a plugin and does not replace Codex semantic review.
- Then set `Tail Work Complete: yes`, `Completion Ready: yes`, and `Next Check: exit`; write the final report and exit normally.
- Do not start broad tests, opportunistic cleanup, documentation expansion, or new discovery during the tail phase.

## Required Report

- Direction: on-plan / partial / deviated
- Changed files and symbols
- Unknowns resolved and newly discovered
- Narrow sanity checks run, if assigned
- Remaining blocker or next Checker responsibility

## Worktree / Large Repo Strategy Gate

- Repository scale / I/O evidence: 246 tracked files; CodeGraph initialized; source and generated installed control plane are separate.
- Strategy: fresh from clean HEAD.
- Large-repo mode/evidence tradeoff: full evidence; targeted reads only; no broad repository scan.
- Reset authorization, if any: none.
- Cleanup/retention expectation: retain worktree and diff for Codex review; do not merge.
