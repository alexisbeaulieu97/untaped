# Getting started

## Install

`untaped` needs Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install 'untaped[all]'   # or 'untaped[<name>]' for one tool, e.g. 'untaped[awx]'
untaped --version
untaped --help
```

Extras combine (`'untaped[awx,github]'`). Add a third-party tool with
`uv tool install untaped --with <tool>`. Re-running `uv tool install`
restates the whole set and replaces the old one, so list every extra and
`--with` you want, for example
`uv tool install 'untaped[github,awx]' --with acme-untaped`. Only the
`untaped` package ships the command, so `uv tool install untaped-<name>` does
not work: uv finds no executable in a capability package. In an environment
you manage, `pip install 'untaped[<name>]'` or `pip install <tool>` adds to
the same environment.

`untaped --install-completion` adds shell completion.

`untaped doctor` checks the install and your configuration without network
access. Run it whenever something looks wrong. `--online` also contacts each
configured service and prints the command that fixes each failure.

```bash
untaped doctor
untaped doctor --online
```

## Set up your services

`untaped setup` walks you through one profile in a terminal. For each service
you pick (`awx`, `github`, `jira`) it asks for the base URL and how to get
the token: type it, give a command that prints it (`token_command`), or keep
the current one. It checks the answers before writing any of them, then
checks each service online and exits 1 if one fails. Naming a new profile creates it.

```bash
untaped setup
```

To script the same settings, use `config set` as below.

## Store your tokens

Settings live in `~/.untaped/config.yml`. Write them with `untaped config set`
rather than by hand, so they are validated. Use `--prompt` for tokens: the
value is read without echo and stays out of your shell history.

```bash
untaped config set github.token --prompt
untaped config set jira.base_url https://jira.example.com
untaped config set jira.token --prompt
untaped config set awx.base_url https://aap.example.com
untaped config set awx.token --prompt
```

In scripts, pipe the token in instead:

```bash
printf '%s\n' "$GITHUB_TOKEN" | untaped config set github.token --stdin
```

A token set this way is stored in plain text; to keep it out of
`config.yml`, see [Tokens](./configuration.md#tokens).

Check what is set. Secrets show as `***`:

```bash
untaped config list
untaped config get github.token
```

The [configuration reference](./reference/config.md) lists every setting,
its default and its environment variable.

## Use profiles for more than one environment

A profile is a named set of settings. `default` is the base; other profiles
override it field by field.

```bash
untaped profile create prod --copy-from default
untaped --profile prod config set awx.base_url https://aap.prod.example.com
untaped --profile prod config set awx.token --prompt

untaped profile list
untaped --profile prod awx ping
untaped profile use prod
untaped profile current
```

`--profile NAME` selects a profile for one command, anywhere in the command;
`profile use` changes the default for every later command. See
[Profiles](./configuration.md#profiles).

## First command in each capability

Each capability's guide has its full workflow:
[workspace](../packages/untaped-workspace/README.md), [github](../packages/untaped-github/README.md),
[jira](../packages/untaped-jira/README.md), [awx](../packages/untaped-awx/README.md),
[ansible](../packages/untaped-ansible/README.md) and [recipe](../packages/untaped-recipe/README.md).

```bash
# workspace: a task directory of git worktrees on a shared branch
untaped workspace create demo --repo acme/api
untaped workspace status demo

# github: check the token, then list an org's repos
untaped github whoami
untaped github repos list --org acme --limit 10

# jira: check the token, then list your open issues
untaped jira whoami
untaped jira issues assigned

# awx: check the connection, then list job templates
untaped awx ping
untaped awx job-templates list

# ansible: show what a role depends on
untaped ansible deps acme/base-role

# recipe: see installed recipes
untaped recipe list
```

Shorten a command you repeat with an [alias](./configuration.md#command-aliases):
`untaped alias set failed -- awx jobs list --status failed`, then
`untaped failed`.

## Output and piping

Most commands take `--format table|json|yaml|raw|pipe` and `--columns`:

```bash
untaped github repos list --org acme --format json
untaped github repos list --org acme --format raw --columns repo
```

- `table` (the usual default) is for people.
- `json` and `yaml` are for scripts and `jq`.
- `raw` prints plain text with no header: the first field of each row, or the
  `--columns` you name separated by tabs. It suits `fzf`, `awk` and `xargs`.
- `pipe` prints records that another `untaped` command reads with `--stdin`.

A table fits the terminal by narrowing its widest columns (a cell that does
not fit ends in `…`), and table output that does not go to a terminal is not
wrapped. `--columns ?` lists every column and marks the defaults with `*`;
`--columns +url` adds a column, `--columns=-url` removes one, and
`--columns name,url` shows exactly those.

To change the `table` default, set `ui.format` or export `UNTAPED_FORMAT`:

```bash
untaped config set ui.format json
```

Only data goes to stdout. Progress, warnings and errors go to stderr, so a
pipe carries only records. `-q`/`--quiet` mutes progress and success
messages.

```bash
# Check out every non-archived repo of a GitHub team into a workspace
untaped github repos list --team acme/platform --format pipe \
  | untaped workspace create demo --stdin

# Pick a job template with fzf and show it as YAML
untaped awx job-templates list --format raw --columns name \
  | fzf \
  | untaped awx job-templates get --stdin --format yaml
```

[Scripting](./scripting.md#output-records) says which commands read
which records.

## Commands that change things

Commands that write, delete or launch show a preview and ask before they act.
Jira asks only before destructive writes by default (see
[`jira.confirm`](../packages/untaped-jira/README.md#change-issues)).

- `--dry-run` shows the preview and changes nothing, even with `--yes`.
- `--yes` (`-y`) skips the question. Without a terminal, such commands exit 2
  unless you pass `--yes` or `--dry-run`.
- Answering no exits 1 with `cancelled; no changes made`.

Every command uses the same [exit codes](./scripting.md#exit-codes).

## Agent skills

Each capability ships an agent skill: a directory with `SKILL.md` and its
reference files that teaches an AI coding agent to use that capability.
`untaped skills` lists and installs the skills of every composed capability,
first-party or third-party.

### Install skills

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

### Keep installed skills up to date

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

#### Skills committed to a repository

A `--scope local` install can be committed so everyone who works in the
repository gets the skills. Other agents that read `.agents/skills`, such as
GitHub Copilot, pick them up too. Everyone whose `untaped` version does not
match the committed copies sees the warning. Run `untaped skills update`
after upgrading and commit the result. To catch drift in CI:

```bash
untaped skills status --check   # exits 3 when a skill is outdated or orphaned
```

To write a capability's skill, see
[Packaged skills](./plugins.md#packaged-skills).
