# Agent Skills

Each untaped tool can ship packaged **agent skills**: directories with a
`SKILL.md` file plus optional resources such as `references/`, `scripts/`, or
`assets/`. These teach Codex, Claude, and compatible agents how to use that
tool's CLI surface.

Skills are **per tool**. A tool declares its skills as `SkillAsset`s in its
`ToolSpec`, and the SDK mounts a `skills` command group on the tool that lists
and installs *only that tool's* skills. There is no central `untaped skills`
command and no SDK-level skill — install each tool's skills from that tool.

```python
from untaped.api import SkillAsset, ToolSpec

ToolSpec(
    command="untaped-github",
    section="github",
    profile_model=GithubProfile,
    skills=(
        SkillAsset(
            name="untaped-github",
            source=Path(__file__).parent / "skills" / "untaped-github",
            description="Drive the untaped-github CLI from an agent.",
        ),
    ),
)
```

## List Available Skills

Each tool exposes its own `skills list`:

```bash
untaped-github skills list
untaped-github skills list --format raw
untaped-github skills list --format json
```

The list shows only the skills that tool ships (the `SkillAsset`s in its
`ToolSpec`). Other tools, such as `untaped-awx` or `untaped-workspace`, list and
install their own skills from their own `skills` group.

## Install Skills

Install selected skills by name from the owning tool:

```bash
untaped-github skills install untaped-github --target codex
untaped-workspace skills install untaped-workspace --target claude
untaped-awx skills install untaped-awx --target all --scope local
```

Install every skill the tool ships with `--all`:

```bash
untaped-github skills install --all --target all
```

Batch selection also works through stdin:

```bash
untaped-github skills list --format raw | untaped-github skills install --stdin --target codex
```

Exactly one selector source is allowed: positional names, `--stdin`, or
`--all`. There is no cross-tool `skills --all`; each tool installs only its own
skills, so run the command once per tool you want installed.

## Targets And Scopes

`--target codex` installs Codex-readable skills (the default). `--target claude`
installs Claude Code-readable skills. `--target all` installs into both target
roots for the selected scope.

`--scope global` is the default. It means user/personal, not admin or
enterprise-wide:

- Codex global: `~/.agents/skills`
- Claude global: `~/.claude/skills`

`--scope local` installs into a project directory:

- Codex local: `<project-root>/.agents/skills`
- Claude local: `<project-root>/.claude/skills`

When `--project-dir PATH` is provided, `PATH` is the project root (it requires
`--scope local`). Without `--project-dir`, local installs use the current git
repository root when available, otherwise the current working directory.

Local installs create project files that can be committed if the skill should
travel with the repository. Keep them uncommitted for machine-local
experiments.

Use `--target-dir PATH` to override the skills directory for one selected
target. It cannot be combined with `--target all` or `--project-dir`.

Codex and Claude usually discover skill changes automatically. If a newly
created skills directory does not appear, restart the target agent so it
reloads its skill catalog.

## Overwrite Policy

`skills install` refuses to replace an existing skill directory unless
`--force` is passed:

```bash
untaped-awx skills install untaped-awx --target codex --force
```

## Skill Marker

Each installed skill lands in `.claude/skills/<name>/` (Claude) and/or
`.agents/skills/<name>/` (Codex), and every installed directory includes a
`.untaped-skill.json` marker so future tooling can identify the skill's `name`,
`source`, installation `target`, `scope`, and resolved `install_root`.
