# Task Card Component Catalog

Codex reads this catalog, not the legacy full template. Select one preset and
only the gates whose decisions materially affect this task. The local composer
reads component bodies and produces the card; Codex then fills that short card.

```bash
python ai/compose_task_card.py --preset builder --gate root-cause --output ai/task-cards/TASK.md
```

## Presets

| Preset | Select when | Adds |
|---|---|---|
| `builder` | One scoped implementation responsibility | implementation boundaries and report |
| `batch-builder` | Mechanical transformation across independent write units | one rule, non-overlapping paths, sampled/bounded Codex review |
| `solution-planner` | A large/ambiguous feature needs one convergent end-state plan before implementation | structured solution contract, one Codex adversarial review, freeze boundary |
| `exploratory-builder` | Stable goal/boundary with an unclear implementation path | bounded exploration, source changes, durable evidence, explicit stop conditions |
| `checker` | Test writing or assigned validation | validation ownership and evidence |
| `revision` | Direction accepted; bounded corrections remain | delta-only revision contract |
| `control-plane` | Workflow repair explicitly owned by Codex/human | exception evidence |

## Optional Gates

| Gate | Select only when |
|---|---|
| `spec` | product/API/UX/data-model direction is ambiguous |
| `root-cause` | bug, regression, or repeated failed fix |
| `tdd` | acceptance requires red/green evidence |
| `large-repo` | worktree or repository I/O strategy matters |
| `parallel` | independent write scopes may run concurrently |
| `advisor` | one bounded strategic advisor call is authorized |
| `spark` | persistent Spark routing evidence must live in the card |

## Selection Rules

- ROUTE before selecting components; an explicit Codex fast path needs no card.
- Use `solution-planner` for a large/multi-phase feature and freeze it after one
  adversarial Codex review; route frozen slices back to Claude.
- Auto-select `exploratory-builder` when the goal and boundaries are stable but
  exact files or implementation path are not yet known.
- Do not select a gate merely because its subject has low or no risk.
- Use `revision` instead of copying the original card for a narrowed retry.
- The generated card is the audit source for this task. Runtime evidence stays
  in dispatcher artifacts rather than being prefilled into the card.
- Use `python ai/compose_task_card.py --list` for machine-readable availability.
- The legacy `ai/task-card-template.md` remains available only for compatibility.
