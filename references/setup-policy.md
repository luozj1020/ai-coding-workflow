# Setup and Update Policy

Load this reference only when installing the skill, bootstrapping a repository, refreshing an old workflow, or configuring local context tools.

## Commands

Install or update the user-level skill from a source checkout:

```bash
python scripts/install_for_codex.py
```

Update the skill and refresh the current repository's managed workflow files:

```bash
python scripts/update_skill.py --bootstrap-current
```

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
`--reviewed-continuation <approval-path>`; do not put environment assignments
in front of the launcher.
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

## Environment-Aware Setup

Preview or apply language/tool detection:

```bash
python scripts/install_for_codex.py --auto-setup /path/to/repo
python scripts/install_for_codex.py --auto-setup /path/to/repo --apply
```

The helper detects Python, Node, Go, and Rust profiles; chooses safe user-level package managers; plans LSP tools; initializes CodeGraph only when warranted; and installs Zoekt only for sufficiently large repositories. Missing safe managers are reported as `manual/blocked`, not guessed around.

After bootstrap, run `python ai/doctor_workflow.py`. If it reports `workflow-version` warnings, run the printed refresh command or `update_skill.py --bootstrap-current`.

## Search Services

CodeGraph indexing remains the user's choice. Zoekt is an optional local indexed search service for repeated large-repository work. Sourcegraph is optional external/self-hosted integration, not a default dependency. Use `--code-search-services skip|check` for deterministic non-interactive installation behavior.
