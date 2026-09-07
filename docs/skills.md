# Agent skills

A capability may ship a packaged agent skill: a directory containing `SKILL.md`
and optional `references/`, `scripts/`, or `assets/` resources. The capability
adds each asset to its `CapabilitySpec.skills` tuple. The unified root discovers
the union of shell and capability assets, so one command can list or install
skills from the whole composed application.

The stable asset ID remains the `SkillAsset.name` value. Existing IDs such as
`untaped-github`, `untaped-awx`, and `untaped-workspace` are not renamed when
the command moves to the root.

```python
from pathlib import Path

from untaped.capability_api import CapabilitySpec, SkillAsset

SPEC = CapabilitySpec(
    name="acme",
    app_factory=build_app,
    config_section="acme",
    profile_model=AcmeSettings,
    skills=(
        SkillAsset(
            name="untaped-acme",
            source=Path(__file__).parent / "skills" / "untaped-acme",
            description="Use the acme capability from the unified untaped CLI.",
        ),
    ),
)
```

## List available skills

The root list includes every skill accepted by the current composition:

```bash
untaped skills list
untaped skills list --format raw
untaped skills list --format json
```

The normal display uses each asset's full installed ID. Use the short selector
as the primary form when installing a built-in skill; the root resolves
`github` to the existing `untaped-github` asset, for example. The full ID is
also accepted when a script already has it.

## Install skills

Choose a skill by its short selector, or use `--all` for the composed set:

```bash
untaped skills install github --target codex
untaped skills install workspace --target claude
untaped skills install awx --target all --scope local
untaped skills install --all --target all
```

The selector source is exactly one of positional names, `--stdin`, or `--all`.
A raw list emits the stable full IDs, which can be fed back to the root:

```bash
untaped skills list --format raw | untaped skills install --stdin --target codex
```

A bare `untaped skills install` is a usage error. It must receive names,
`--stdin`, or `--all`.

## Targets and scopes

`--target codex` is the default and installs into the Codex skill root.
`--target claude` installs into the Claude Code skill root. `--target all`
installs into both target roots.

`--scope global` is the default:

- Codex: `~/.agents/skills`
- Claude: `~/.claude/skills`

`--scope local` installs beneath a project root:

- Codex: `<project-root>/.agents/skills`
- Claude: `<project-root>/.claude/skills`

Use `--project-dir PATH` with `--scope local` to select the project root. When
it is omitted, the current git repository root is used when available,
otherwise the current working directory is used. Use `--target-dir PATH` to
select a target directory directly; it cannot be combined with `--target all`
or `--project-dir`.

Local installs create project files that can be committed when a skill should
travel with the repository. Keep machine-local experiments uncommitted.

## Overwrite policy and markers

Installation refuses to replace an existing skill directory unless `--force`
is passed:

```bash
untaped skills install awx --target codex --force
```

Codex installs land in `.agents/skills/<full-id>/`; Claude installs land in
`.claude/skills/<full-id>/`. Each installed directory contains a
`.untaped-skill.json` marker recording the asset name, source, target, scope,
and resolved install root. The full ID in the directory and marker remains
stable even when the short selector was used.

Agents usually discover changed skills automatically. Restart the target agent
if a newly created directory does not appear in its skill catalog.

## Authoring rules

A built-in capability owns its skill source under
`src/untaped/capabilities/<name>/skills/<full-id>/` and declares that directory
in `SPEC.skills`. An external provider packages the same asset with its
provider distribution. Update the owning skill when its capability command,
settings, workflow, or contract changes; do not duplicate the shared install
mechanics in a capability-specific skill.

See [Capability authoring](./plugins.md) for the provider entry-point contract
and [the composition specification](./capabilities-spec.md) for validation and
collision rules.
