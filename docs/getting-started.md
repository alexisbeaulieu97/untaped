# Getting started

## Install

`untaped` needs Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install 'untaped[all]'   # or 'untaped[<name>]' for one capability, e.g. 'untaped[awx]'
untaped --version
untaped --help
```

Extras combine (`'untaped[awx,github]'`). Add a third-party provider with
`uv tool install 'untaped[all]' --with <provider>`. Re-running `uv tool install`
restates the whole set and replaces the old one, so list every extra and
`--with` you want, for example
`uv tool install 'untaped[github,awx]' --with acme-untaped`. Only the
`untaped` package ships the command, so `uv tool install untaped-<name>` does
not work: uv finds no executable in a capability package. In an environment
you manage, `pip install 'untaped[<name>]'` or `pip install <provider>` adds to
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

Each capability's guide shows its main workflows and links its full reference:
[workspace](../packages/untaped-workspace/README.md), [github](../packages/untaped-github/README.md),
[jira](../packages/untaped-jira/README.md), [awx](../packages/untaped-awx/README.md),
[ansible](../packages/untaped-ansible/README.md), [recipe](../packages/untaped-recipe/README.md)
and [dotfiles](../packages/untaped-dotfiles/README.md).

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

# dotfiles: subscribe to a repo, then see the items it offers
untaped dotfiles subscribe https://github.com/acme/dotfiles
untaped dotfiles items
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
wrapped. `--columns '?'` lists every column and marks the defaults with `*`;
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

[Output records](./reference/records.md) says which commands read which
records.

## Commands that change things

Commands that write, delete or launch show a preview and ask before they act.
Jira asks only before destructive writes by default (see
[`jira.confirm`](../packages/untaped-jira/src/untaped_jira/skills/untaped-jira/references/writes.md#which-writes-ask-first)).

- `--dry-run` shows the preview and changes nothing, even with `--yes`.
- `--yes` (`-y`) skips the question. Without a terminal, such commands exit 2
  unless you pass `--yes` or `--dry-run`.
- Answering no exits 1 with `cancelled; no changes made`.

Every command uses the same [exit codes](./reference/exit-codes.md).

## Agent skills

Each capability ships an agent skill that teaches an AI coding agent to use
it. Install every composed capability's skill for your agent:

```bash
untaped skills install --all --target all
```

[Agent skills](./skills.md) covers targets, scopes and keeping installed
copies current.
