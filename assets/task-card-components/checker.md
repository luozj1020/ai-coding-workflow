## Checker Contract

- Write/update only assigned tests and execute only assigned validation.
- Do not broadly rewrite the implementation; report semantic failures to Codex.
- Separate command approval/environment failure from implementation failure.
- Before using an interface, verify the exact signature and runnable example in
  the Context Packet. Stop if they are missing or conflict with the repository.
- After each test file write, immediately run its syntax/import check and its
  narrow single-file test before creating another test file.
- Use `$TMPDIR` for generated validation helpers; do not create repository-root
  scratch scripts.

| Runtime field | Value |
|---|---|
| Per-file validation command | replace with a shell-free argv template containing `{path}`; for example `python -m pytest {path} -q` |

## Required Report

- Validation commands and exit status
- Tests added/changed
- Acceptance criteria covered and uncovered
- Failure evidence with exact file/symbol/test
- Recommended accept / revise / split
- End with exact file/count/cleanliness claims. When tests are assigned, include
  `claimed_test_count=<n>`; when validation is assigned, include
  `claimed_validation_command=<exact command>` and
  `claimed_validation_exit_code=<code>`.
