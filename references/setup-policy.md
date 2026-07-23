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
