# Agent Skills

`untaped` and its plugins can ship packaged agent skills: directories with a
`SKILL.md` file plus optional resources such as `references/`, `scripts/`, or
`assets/`. These teach Codex, Claude, and compatible agents how to use the CLI
surface without mixing that state into plugin installation.

Plugin sync and skill install are intentionally separate:

- `untaped plugins add/remove/sync` manages Python packages in the managed
  untaped virtual environment.
- `untaped skills list/install` manages files in agent skill directories.

## List Available Skills

```bash
untaped skills list
untaped skills list --format raw
untaped skills list --format json
```

The built-in `untaped` skill is always available. Installed plugins may add
their own prefixed skills such as `untaped-awx`, `untaped-workspace`,
`untaped-github`, `untaped-profile`, `untaped-jira`, or `untaped-ansible`.

## Install Skills

Install selected skills by name:

```bash
untaped skills install untaped untaped-awx --target codex
untaped skills install untaped-workspace --target claude
untaped skills install untaped-github --target all --scope local
```

Install every registered skill explicitly with `--all`:

```bash
untaped skills install --all --target all
```

Batch selection also works through stdin:

```bash
untaped skills list --format raw | untaped skills install --stdin --target codex
```

Exactly one selector source is allowed: positional names, `--stdin`, or
`--all`.

## Targets And Scopes

`--target codex` installs Codex-readable skills. `--target claude` installs
Claude Code-readable skills. `--target all` installs into both target roots for
the selected scope.

`--scope global` is the default. It means user/personal, not admin or
enterprise-wide:

- Codex global: `~/.agents/skills`
- Claude global: `~/.claude/skills`

`--scope local` installs into a project directory:

- Codex local: `<project-root>/.agents/skills`
- Claude local: `<project-root>/.claude/skills`

When `--project-dir PATH` is provided, `PATH` is the project root. Without
`--project-dir`, local installs use the current git repository root when
available, otherwise the current working directory.

Local installs create project files that can be committed if the skill should
travel with the repository. Keep them uncommitted for machine-local
experiments.

Use `--target-dir PATH` to override the skills directory for one selected
target. It cannot be combined with `--target all` or `--project-dir`.

Codex and Claude usually discover skill changes automatically. If a newly
created skills directory does not appear, restart the target agent so it
reloads its skill catalog.

## Overwrite Policy

`untaped skills install` refuses to replace an existing skill directory unless
`--force` is passed:

```bash
untaped skills install untaped-awx --target codex --force
```

Each installed skill directory includes `.untaped-skill.json` so future tooling
can identify the skill name, source, installation target, scope, and resolved
install root.
