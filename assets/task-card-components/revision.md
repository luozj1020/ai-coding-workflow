## Revision Delta

<!-- Do not repeat the original plan. Bind the accepted baseline artifact and list only corrections. -->

- Accepted baseline task/diff:
- Preserve unchanged:
- Review findings inline (external paths alone are invalid):
  - `finding_id=F-01 | evidence=<file:symbol or exact observation> | required_change=<bounded correction> | acceptance=<exact check>`
- Required corrections (reference finding IDs):
- Exact files/symbols:
- New write paths allowed:
- Narrow validation:
- Re-route if:

## Required Report

- Each correction completed/not completed
- Files and symbols changed in this continuation
- Deviations from the accepted baseline
- Validation evidence and remaining blocker
- For every completed finding, emit
  `resolved_finding=F-01|file=<changed path>|symbol=<changed symbol>|test=<exact test name or not-required>`.
- End with the same exact file/count/cleanliness and assigned test/validation
  machine claims required by the Builder or Checker contract.
