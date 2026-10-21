# Agent skills

Each plugin ships an agent skill that tells an AI coding agent the plugin
exists and when to reach for it. untaped itself ships the `untaped` skill.
`untaped skills` lists and installs the skills of every composed plugin,
first-party or third-party.

A skill comes in one of two kinds (the `kind` field of `skills list`):

- A **stub** is a short `SKILL.md` that untaped writes from its own template
  and the plugin's description. It lists no commands: it sends the agent to
  `untaped agents`, which prints what the untaped it runs can do as Markdown
  pages (see [Agent pages](./reference/agent-pages.md)). One stub serves every
  untaped installed on the machine. Its `source` is `untaped:stub/1`.
- A **copy** is a directory with `SKILL.md` and reference files, copied from
  the plugin's package. Its `source` is that directory, so an agent whose
  harness has no skills folder can read it in place. A plugin shipping a copy
  is [deprecated](./plugin-skills.md#copies).

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

Each skill lands in `<root>/<full-id>/` with a marker: `.untaped-stub.json`
for a stub, `.untaped-skill.json` for a copy. A stub's marker records the
untaped version and the plugin's distribution and version that wrote it, and
a digest of what it wrote. The full ID is used even when you installed by
short selector. An existing directory is replaced only with `--force`.

Agents usually pick up changed skills on their own. Restart the agent if a
new skill does not appear in its catalog.

## Keep installed skills up to date

After every command that succeeds (except `untaped skills …` and
`untaped doctor`, and not after a preview such as `--dry-run` or `--check`),
untaped looks at the skills in your home folder, `~/.agents/skills` and
`~/.claude/skills`:

- A stub an older untaped wrote, or one an older version of its plugin wrote,
  is refreshed. A stub a newer one wrote is left alone, so two installed
  versions never take turns rewriting it. When the stub's plugin now comes
  from another distribution, or its version is unknown, only untaped's
  version decides.
- A stub you edited is never rewritten by a run. `skills status` and
  `untaped doctor` show it as `edited`; `untaped skills update --force NAME`
  replaces it.
- A copy from untaped 11.0 or earlier, for a plugin that now ships a stub,
  is converted to the stub once (11.1.0 and later only). The old copy is kept
  under `~/.untaped/backups/skills/<target>/<full-id>`, and untaped says so:

  ```text
  converted 3 agent skills to stubs; the old copies are in ~/.untaped/backups/skills
  ```

  Backups are never removed automatically and are safe to delete.
- A copy a third-party plugin still ships is updated when its files differ
  from this version's.

The `skills.updates` setting picks what happens: `auto` (the default) does the
above, `warn` prints a warning instead, and `off` does nothing; see the
[configuration reference](./reference/config.md). After a failed or
previewing command `auto` writes nothing: stubs and copies to convert wait
for the next command that succeeds, and a third-party copy that is out of
date is warned about. `untaped skills update`
keeps a backup of a home-folder copy it converts, too.

```text
warning: agent skills from an older untaped: untaped-awx, untaped-github
hint: run `untaped skills update` (set skills.updates to auto or off to change this)
```

```bash
untaped config set skills.updates warn
UNTAPED_SKILLS__UPDATES=off untaped …      # one process only
```

Skills inside a project (`.agents/skills` and `.claude/skills` at the current
git root, or the current directory outside a repository) are never changed by
a run, and a stub there is never warned about. A copy from an older untaped
there is reported until someone runs `untaped skills update` in that project.
Only directories with a marker count, so skills you wrote yourself are never
touched. Installs made with `--target-dir` are not checked.

To manage installs by hand:

```bash
untaped skills status                   # every installed skill and its state
untaped skills update --dry-run         # show what would change
untaped skills update github awx        # update these in place
untaped skills update awx --force       # also replace an edited or newer stub
untaped skills remove awx --target claude --scope local
```

`status` reports each install's `kind` and `state`: `current`, `outdated`
(an older untaped wrote it, or a copy's files differ from this version's),
`newer` (a newer untaped or plugin wrote this stub), `edited` (someone changed
this stub), or `orphaned` (no plugin installed here ships it: either it was
dropped, or the plugin that ships it is missing, as with `uvx untaped`
without extras). The per-run check never reports orphaned skills;
`untaped doctor` and `skills status` do. Install the plugin, or run
`untaped skills remove`.
`update` rewrites an install where it is, keeping its target and scope; it
never installs anywhere new. It follows the same rule as the per-run check:
it leaves a `newer` or `edited` stub and says why (`left untaped-jira:
written by a newer untaped (11.3.0)`) unless you pass `--force`. `remove` previews the directories and asks
before deleting; pass `--yes` when not interactive. `status`, `update` and
`remove` accept short selectors, `--stdin`, and `--project-dir PATH` for
another project's local skills.

### Skills committed to a repository

A `--scope local` install can be committed so everyone who works in the
repository gets the skills. Other agents that read `.agents/skills`, such as
GitHub Copilot, pick them up too. Committed stubs change only when someone
runs `untaped skills update` in the repository; committed copies from an older
untaped are warned about until then. Run it after upgrading and commit the
result (the diff shows copies becoming stubs). To catch drift in CI:

```bash
untaped skills status --check   # exits 3 when a skill is outdated or orphaned
```

To write a plugin's skill, see
[Writing a plugin's skill](./plugin-skills.md).
