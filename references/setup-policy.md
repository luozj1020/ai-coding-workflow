# Setup and Update Policy

Load this reference only when installing the skill, bootstrapping a repository, refreshing an old workflow, or configuring local context tools.

## Commands

Install or update the user-level skill from a source checkout:

```bash
python scripts/install_for_codex.py
```

Update the skill and refresh the current repository's managed workflow files:

```bash
python scripts/update_skill.py
```

The normal updater uses compact summaries and disables optional service prompts.
It hashes the filtered Skill package before activation: identical content only
refreshes provenance and does not create staging/backup trees or replace the
installed directory. Project refresh still verifies every managed destination,
but unchanged shell launchers skip repeated `bash -n`; created or changed
launchers are always validated. Failures and bounded mismatch paths remain
visible even in compact mode. Direct installer diagnostics can opt out by
omitting `--summary-only`.

To refresh only a project from the selected source—without changing the
user-level Skill—use the same wrapper. This is the compatibility path for a
direct `install_workflow.py --update-workflow-files` refresh:

```bash
python scripts/update_skill.py --project-only
python scripts/update_skill.py --project-only --bootstrap-repo /path/to/repo --local-only --doctor
```

`--bootstrap-current` and `--bootstrap-repo` select a target in either full
or project-only mode. `--local-only` is forwarded to the project installer;
`--doctor` runs the matching doctor after a successful refresh. A normal
update may also use those flags, for example
`python scripts/update_skill.py --bootstrap-current --doctor`.

An updater run from the installed Skill never treats that installed directory
as its own source. Installation records the real source-checkout path, HEAD,
dirty state, and package content hash. The installed updater reuses that
checkout; if the provenance is missing, stale, self-referential, or no longer a
Git checkout, it fails before project bootstrap and requires an explicit
`--source /path/to/checkout`.

Preview or apply guided setup:

```bash
python scripts/update_skill.py --setup-current
python scripts/update_skill.py --setup-current --apply
python scripts/update_skill.py --setup-repo /path/to/repo
python scripts/update_skill.py --setup-repo /path/to/repo --apply
```

Bootstrap directly from the installed skill:

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py . --local-only
```

Refresh an already-bootstrapped repository with `--update-workflow-files`. Without it, the installer reports outdated plain `ai/*` files but does not overwrite them. Managed blocks in `AGENTS.md` and `CLAUDE.md` preserve user-owned content outside their markers.

Bootstrap also installs the managed project rule
`.codex/rules/ai-coding-workflow.rules`. In a trusted project, after Codex is
restarted, it pre-authorizes only these standard repository entrypoints:

```bash
bash ai/dispatch-to-claude.sh ...
bash ai/run-codex-spark.sh ...
```

The rule does not authorize arbitrary Bash, `scripts/*` source helpers,
environment-wrapped commands, merge, deployment, or destructive operations.
After host authority is granted, keep the approved launcher shape and use
`--execution-env host`. Claude transport retries add
`--retry-in-place-task-id <task-id>` or
`--reviewed-continuation <approval-path>`. A dirty snapshot adds
`--dirty-source-mode snapshot` to initial dispatch and normalized host retry.
An explicit Claude tool set adds `--tool-profile minimal-builder` (or another
supported profile) instead of a leading `CLAUDE_CODE_TOOL_PROFILE=...`
assignment. Normalized host retries preserve this CLI option.
The handoff receipt's CLI args are authoritative; its environment map is legacy
compatibility evidence. Do not put environment assignments in front of the launcher.
Spark remains advisory and Codex still performs routing and bounded semantic
review; actions that require human authority keep their existing approval
boundary. Existing projects receive or refresh the rule through
`--update-workflow-files`.

When a workflow must suppress a project-specific API configuration without
reading the real file, keep the standard approved prefix and use the bounded
wrapper option:

```bash
bash ai/run-codex-spark.sh CARD ... --empty-api-config-env PROJECT_API_CONFIG_FILE
bash ai/dispatch-to-claude.sh CARD --empty-api-config-env PROJECT_API_CONFIG_FILE
```

The name must be uppercase and end in `_API_CONFIG_FILE`; the wrapper exports
that variable as `/dev/null`. Do not prepend an environment assignment to the
command because that changes the command shape and cannot match the narrow
trusted-project rule.

Before changing the target repository, the project installer validates that
every required asset, helper, schema, profile, and example exists and that no
two sources target the same path. A broken source package therefore fails
before creating or updating project files. Each individual file refresh uses a
same-directory atomic replacement and preserves the existing permission mode,
so an interrupted write cannot leave a truncated managed file. This is
per-file atomicity, not a repository-wide rollback; the final doctor phase is
still the cross-file consistency gate for guided setup.

The user-level Skill update follows the same fail-safe principle at directory
scope: it copies into a sibling staging directory, validates the minimum
executable Skill surface, then atomically switches the installed directory. If
activation fails after the previous directory is moved aside, the previous
Skill is restored before the command returns an error. Repository bootstrap
starts only after that Skill activation succeeds.

With `--update-workflow-files`, the project installer also performs a final
content comparison across managed blocks, rules, helpers, schemas, profiles,
and examples. Any missing or stale managed destination makes the command fail
instead of reporting a successful refresh.

Repository refresh applies to the current project worktree; it never rewrites
historical execution worktrees. New dispatchers therefore snapshot their
model-facing helpers from the same installed managed package and mount that
task bundle read-only. A reviewed continuation reuses historical product files,
not historical workflow helpers. This prevents a partial update from producing
a new-launcher/old-writer runtime combination.

## Environment-Aware Setup

Preview or apply language/tool detection:

```bash
python scripts/install_for_codex.py --auto-setup /path/to/repo
python scripts/install_for_codex.py --auto-setup /path/to/repo --apply
```

The helper detects Python, Node, Go, and Rust profiles; chooses safe user-level package managers; plans LSP tools; initializes CodeGraph only when warranted; and installs Zoekt only for sufficiently large repositories. Missing safe managers are reported as `manual/blocked`, not guessed around.

The updater also exposes that legacy direct mode without activating the Skill:

```bash
python scripts/update_skill.py --auto-setup /path/to/repo
python scripts/update_skill.py --auto-setup /path/to/repo --apply
```

After bootstrap, run `python ai/doctor_workflow.py`, or add `--doctor` to an
updater project refresh. A normal `update_skill.py` run automatically refreshes
an already-bootstrapped current repository; use `--skill-only` only as an
explicit opt-out. If doctor reports workflow-version drift or a stale launcher
error, run the printed refresh command before any model call. Never compensate
with an environment-prefixed launcher.

Skill and managed `AGENTS.md` changes do not replace instructions already loaded
into a running Codex conversation. After an update reports success, start a new
Codex session before judging routing behavior. Automatic `solution-planner`
routing or Codex-authored route/freeze artifacts after the new policy was
installed indicate that the current session still carries old instructions.

## Search Services

CodeGraph indexing remains the user's choice. Zoekt is an optional local indexed search service for repeated large-repository work. Sourcegraph is optional external/self-hosted integration, not a default dependency. `update_skill.py` defaults to `--code-search-services skip`; pass `ask` or `check` to forward the explicit choice to the Skill installer.
