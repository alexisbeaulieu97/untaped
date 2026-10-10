# Agent skills

Each plugin ships an agent skill: a directory with `SKILL.md` and its
reference files that teaches an AI coding agent to use that plugin.
untaped itself ships the `untaped` skill, which covers install, profiles,
setup (`setup plan`), `doctor` and skills. `untaped skills` lists and installs
the skills of every composed plugin, first-party or third-party.

An agent whose harness has no skills folder can read a skill in place: the
`source` field of `untaped skills list --format json` is the directory that
holds its `SKILL.md`.

## Install skills

A skill has a stable full ID (`untaped-github`) and a short selector
(`github`). `skills list` shows full IDs; install commands accept either.
Choose skills with exactly one of names, `--stdin` or `--all`; a bare
`skills install` is a usage error.

```bash
untaped skills list
untaped skills install github --target claude
untaped skills install --all --target all --scope local
untaped skills list --format raw | untaped skills install --stdin --target codex
```

`--target` picks the agent (`codex`, the default, `claude`, or `all`), and
`--scope` where its skill root is:

| Scope | Codex | Claude |
|---|---|---|
| `global` (default) | `~/.agents/skills` | `~/.claude/skills` |
| `local` | `<project-root>/.agents/skills` | `<project-root>/.claude/skills` |

For `local`, the project root is `--project-dir PATH`, else the current git
repository root, else the current directory. `--target-dir PATH` names the
directory outright; it cannot be combined with `--target all` or
`--project-dir`.

Each skill lands in `<root>/<full-id>/` with a `.untaped-skill.json` marker
recording the asset, source, target, scope and install root. The full ID is
used even when you installed by short selector. An existing directory is
replaced only with `--force`.

Agents usually pick up changed skills on their own. Restart the agent if a
new skill does not appear in its catalog.

## Keep installed skills up to date

An installed skill is a copy. Upgrading `untaped` does not change it, and a
stale copy can send an agent to commands or flags this version no longer has.
After every command (except `untaped skills …` and `untaped doctor`), the root
compares each installed skill with the copy this version ships and warns when
one differs:

```text
warning: installed skills are out of date: untaped-awx, untaped-github
hint: run `untaped skills update` (set skills.updates to auto or off to change this)
```

The check looks in the global Codex and Claude roots and in
`.agents/skills`/`.claude/skills` at the current git root (or the current
directory outside a repository). Only directories with the marker count, so
skills you wrote yourself are never touched. Installs made with
`--target-dir` are not checked.

The `skills.updates` setting picks what the check does: `warn` (the
default) prints the warning above, `auto` updates outdated skills in place and
prints `updated N outdated skills` (after a failed command or a preview such as
`--dry-run` or `--check` it only warns), and `off` does nothing; see the
[configuration reference](./reference/config.md).

```bash
untaped config set skills.updates auto
UNTAPED_SKILLS__UPDATES=off untaped …      # one process only
```

To manage installs by hand:

```bash
untaped skills status                   # every installed skill and its state
untaped skills update --dry-run         # show what would change
untaped skills update github awx        # update these in place
untaped skills remove awx --target claude --scope local
```

`status` reports each install's `state`: `current`, `outdated` (its files
differ from this version's copy), or `orphaned` (this version no longer ships
it). `auto` never removes an orphaned skill; run `untaped skills remove`.
`update` rewrites an install where it is, keeping its target and scope; it
never installs anywhere new. `remove` previews the directories and asks
before deleting; pass `--yes` when not interactive. `status`, `update` and
`remove` accept short selectors, `--stdin`, and `--project-dir PATH` for
another project's local skills.

### Skills committed to a repository

A `--scope local` install can be committed so everyone who works in the
repository gets the skills. Other agents that read `.agents/skills`, such as
GitHub Copilot, pick them up too. Everyone whose `untaped` version does not
match the committed copies sees the warning. Run `untaped skills update`
after upgrading and commit the result. To catch drift in CI:

```bash
untaped skills status --check   # exits 3 when a skill is outdated or orphaned
```

To write a plugin's skill, see
[Writing a plugin's skill](./plugin-skills.md).
