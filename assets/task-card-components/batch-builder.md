## Batch Builder Gate

| Field | Value |
|---|---|
| Transformation rule | replace with one deterministic rule |
| Independent write units | replace with exact non-overlapping paths |
| Source-of-truth example | replace with one existing correct pattern |
| Codex review scope | sampled / bounded |
| Full semantic rereview required | no |
| Partial batch accepted | no — report completed and blocked units separately |

- Apply the reviewed transformation; do not redesign shared contracts.
- Stop when a unit needs architecture judgment or overlaps another owner.
- Return a compact changed-unit manifest plus narrow validation evidence.
