## Exploratory Builder Contract

| Field | Value |
|---|---|
| Builder mode | exploratory |
| Exploration boundary | <!-- exact modules/directories; never whole-repo by default --> |
| Durable output required | yes |
| Accepted durable outputs | source diff / tests / runnable prototype / structured repository asset |
| Read-only completion accepted | no |
| Codex review scope | interfaces, critical semantics, deviations |

- Explore only enough to choose and implement a viable path inside the boundary.
- Keep discovery and implementation in the same run; do not stop after a prose plan.
- Prefer one working vertical slice over a broad unfinished design.
- Record material assumptions and rejected alternatives briefly in the report.
- If no durable output can be produced, report the concrete blocker; a repository summary alone is not completion.

## Post-Implementation Contract

| Field | Value |
|---|---|
| Narrow validation assigned | no — replace with one exact command when required |
| Bounded self-review assigned | yes — durable output and assigned acceptance only |
| Documentation assigned | no — replace with exact files when required |
| Long validation owner | not-required — replace with checker/helper/human when required |
| Additional cleanup allowed | no, unless explicitly listed in Required Changes |
| Exit after assigned tail work | yes |

- Once the durable assigned output is complete, set `Implementation Complete: yes`.
- Finish only the assigned tail work, set `Tail Work Complete: yes`, `Completion Ready: yes`, and `Next Check: exit`, then exit normally.
- Do not reopen exploration after implementation unless a concrete blocker invalidates the selected path.

## Required Exploratory Report

- Durable outputs produced and how to run or inspect them
- Implementation path selected and one-line rationale
- Assumptions validated or disproved
- Alternatives rejected only when they affect Codex review
- Remaining product/architecture decision that genuinely requires Codex or human authority
