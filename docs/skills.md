# Agent skills

A capability may ship a packaged agent skill: a directory containing `SKILL.md`
and optional `references/`, `scripts/`, or `assets/` resources. The capability
adds each asset to its `CapabilitySpec.skills` tuple. The unified root discovers
the union of shell and capability assets, so one command can list or install
skills from the whole composed application.

The stable asset ID remains the `SkillAsset.name` value. Built-in skills keep
their full IDs (for example, `untaped-github`, `untaped-awx`, and
`untaped-workspace`) even when selected through the unified root.

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

## Keep installed skills up to date

An installed skill is a copy. Upgrading untaped does not change it, and a
stale copy can send an agent to commands or flags this version no longer has.
After every command (except `untaped skills …` and `untaped doctor`), the root
compares each installed skill with the copy this version ships and prints a
warning when one differs:

```text
warning: installed skills are out of date: untaped-awx, untaped-github
hint: run `untaped skills update` (set skills.updates to auto or off to change this)
```

It looks in the global Codex and Claude skill directories and in
`.agents/skills`/`.claude/skills` at the current git root (or the current
directory outside a repository). Only directories with the
`.untaped-skill.json` marker count; skills you wrote yourself are never
touched. Installs made with `--target-dir` are not checked.

The `skills.updates` setting picks what the check does:

| Value | Behavior |
|---|---|
| `warn` (default) | Print the warning above. |
| `auto` | Update outdated skills in place, then print `updated N outdated skills`. |
| `off` | Do nothing. |

```bash
untaped config set skills.updates auto
UNTAPED_SKILLS__UPDATES=off untaped …      # one process only
```

A skill this version no longer ships is reported as no longer shipped, even
with `auto`. Remove it with `untaped skills remove`.

### Inspect, update, and remove

```bash
untaped skills status                   # every installed skill and its state
untaped skills status --check           # exit 3 when one is outdated or unshipped
untaped skills update                   # update every outdated install in place
untaped skills update github awx        # only these skills
untaped skills update --dry-run         # show what would change
untaped skills remove awx               # remove from every target and scope
untaped skills remove awx --target claude --scope local
untaped skills remove --all --yes
```

`status` reports each install's `state`: `current`, `outdated` (its files
differ from this version's copy), or `orphaned` (this version no longer ships
it). `update` rewrites an install where it already is: it keeps the target and
scope and never installs anywhere new. `remove` previews the directories and
asks before deleting; pass `--yes` when not interactive. Like `status`, both
accept short selectors, `--stdin`, and `--project-dir PATH` to use another
project's local skills.

### Skills committed to a repository

A `--scope local` install can be committed so everyone who works in the
repository gets the skills. The Codex root `.agents/skills` is also read by
other agents that load skills from that directory, such as GitHub Copilot.
Everyone who runs untaped in the repository sees the warning when the
committed copies do not match their untaped version. Run
`untaped skills update` after upgrading and commit the result. To catch drift
in CI, run:

```bash
untaped skills status --check
```

## Authoring rules

A built-in capability owns its skill source under
`src/untaped/capabilities/<name>/skills/<full-id>/` and declares that directory
in `SPEC.skills`. An external provider packages the same asset with its
provider distribution. Update the owning skill when its capability command,
settings, workflow, or contract changes; do not duplicate the shared install
mechanics in a capability-specific skill. Start a new skill from the
[skill template](./templates/SKILL.md): its frontmatter, sections and length
rules keep skills consistent across capabilities.

See [Capability authoring](./plugins.md) for the provider entry-point contract
and the current `untaped capabilities` output for the composed providers.
