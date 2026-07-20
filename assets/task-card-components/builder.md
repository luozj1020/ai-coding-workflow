## Builder Contract

- Implement only the scoped production change.
- Do not write acceptance tests or run broad validation unless explicitly assigned.
- Continue after a non-blocking `proceed` acknowledgement in the same run.

## Post-Implementation Contract

| Field | Value |
|---|---|
| Narrow validation assigned | no — replace with one exact command when required |
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
