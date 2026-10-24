# Getting started

## Install

`untaped` needs Python 3.14.1 or newer, [uv](https://docs.astral.sh/uv/) and Git
2.29 or newer (`untaped doctor` checks Git's version).

```bash
uv tool install 'untaped[all]'   # or 'untaped[<name>]' for one plugin, e.g. 'untaped[awx]'
untaped --version
untaped --help
```

Extras combine (`'untaped[awx,github]'`). Add a third-party plugin with
`uv tool install 'untaped[all]' --with <plugin>`. Re-running `uv tool install`
restates the whole set and replaces the old one, so list every extra and
`--with` you want, for example
`uv tool install 'untaped[github,awx]' --with acme-untaped`. Only the
`untaped` package ships the command, so `uv tool install untaped-<name>` does
not work: uv finds no executable in a plugin package. In an environment
you manage, `pip install 'untaped[<name>]'` or `pip install <plugin>` adds to
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

`untaped setup` is one full-screen screen for one profile, in a terminal. The
left lists every settings section: the services (`awx`, `github`, `jira`)
first, with their status, then `untaped` (untaped's own) and the other
plugins' sections. The right lists the selected section's settings, one row each, with
its value and where it comes from (`profile:work`, `profile:default`,
`default` for the built-in value, or the environment variable that sets it);
under the rows is the focused setting's description, its bounds, its built-in
default and its variable. Enter opens a setting's field and enter writes it,
exactly as `config set` would, with the same error when the value is invalid;
esc closes it unchanged, and an empty field unsets the setting. A secret's
field opens empty, so leaving it empty keeps the secret. Writing back the
value a field opened with changes nothing, so an inherited or default value
stays inherited; `config set` writes it into the profile. `delete` (or
`backspace`) unsets a setting the profile sets, once you answer `y`.

A list of names opens a list editor: enter edits an item or adds one on
`+ add`, `delete` removes one, `m` then up or down moves one, and enter on
`done` (or ctrl-s) writes the whole list, an empty one included. A setting
that holds a command to run is one line, as you would type it in a shell.
A map of known names, such as `untaped.ui.symbols` or `untaped.ui.color_roles`, lists every
name with its value and where it comes from (a profile, a variable, or the
theme): enter edits a value in place (an empty one too), `delete` clears
the profile's own one so the name inherits again, and enter on `done` (or
ctrl-s) writes the names the profile sets. A plugin that owns contracts lists
each ranking of its `extensions` as a row of its own, edited in the list
editor and written as `untaped plugin rank` writes it; its `+ add a ranking`
row takes `CONTRACT METHOD PROVIDER...` as `plugin rank` does. Any other list
or map is one line of JSON, as `config set` takes it.

A service's first row is what it needs set up. Enter opens its form: every
setting the plugin marks as needed (such as its server's URL), each in the
field its type takes, then, for each of its secrets (usually one token), how
to get it (tabs): store it with your password store (as `untaped auth login`
does), move a plaintext one there, give a command that prints it, use an
environment variable, or keep the current one. Each secret is filed for the
server the form saves. Saving checks the service online with what you typed
before anything is written; if the check fails, the reason is shown under the
form and you can fix the values, or `Save anyway`. Only the settings you
changed are written, in one write, so a value the profile inherits stays
inherited; if one of them changed in the config file since the form opened,
nothing is written and the form says so. An exported
`UNTAPED_<SECTION>__TOKEN` (for example `UNTAPED_GITHUB__TOKEN`) wins over the
token you type in that check, as it does in `doctor`.

Esc leaves the screen; ctrl-c leaves it too and exits 130. Naming a new
profile in the profile field shows `default`'s values and creates the profile
on its first write. Afterwards `setup` prints a line for each setting it wrote,
then the doctor checks of the services whose connection you saved, and exits 1
if one fails.

```bash
untaped setup
untaped --profile work setup --only awx,jira   # these sections only
untaped setup --only ui
```

To script the same settings, use `config set` as below.

### Set up with an agent

Any AI coding agent can do the setup with you; untaped calls no model
itself. `untaped setup plan` lists what a profile still needs, with the
command for each step, and marks every step that asks for or reveals a token
as yours to run, so a token never passes through the agent. Paste this into your agent:

```text
Install untaped with `uv tool install 'untaped[all]'`, then install its
agent skills with `untaped skills install --all --target codex` (or
`claude`). Read `untaped agents`, then `untaped agents untaped setup plan`,
and set up a profile with me: run the steps that are yours and hand me the
ones that ask for a token.
```

## Store your tokens

Settings live in `~/.untaped/config.yml`. Write them with `untaped config set`
rather than by hand, so they are validated. Tokens do not belong there:
`untaped auth login` reads one without echo and stores it with this machine's
password store (macOS keychain, Secret Service or `pass`), filed for the
server the settings name.

```bash
untaped auth login github
untaped config set jira.server.url https://jira.example.com
untaped auth login jira
untaped config set awx.server.url https://aap.example.com
untaped auth login awx
```

In scripts, pipe the token in instead:

```bash
printf '%s\n' "$GITHUB_TOKEN" | untaped auth login github
```

Already have tokens in `config.yml`? `untaped config migrate` moves them all.
More in [Tokens](./configuration.md#tokens).

Check what is set:

```bash
untaped config list
untaped auth status
```

The [configuration reference](./reference/config.md) lists every setting,
its default and its environment variable.

## Use profiles for more than one environment

A profile is a named set of settings. `default` is the base; other profiles
override it field by field.

```bash
untaped profile create prod --copy-from default
untaped --profile prod config set awx.server.url https://aap.prod.example.com
untaped --profile prod auth login awx

untaped profile list
untaped --profile prod awx ping
untaped profile use prod
untaped profile current
```

`--profile NAME` selects a profile for one command, anywhere in the command;
`profile use` changes the default for every later command. See
[Profiles](./configuration.md#profiles).

## First command in each plugin

Each plugin's guide shows its main workflows and links its full reference:
[workspace](../packages/untaped-workspace/README.md), [github](../packages/untaped-github/README.md),
[jira](../packages/untaped-jira/README.md), [awx](../packages/untaped-awx/README.md),
[ansible](../packages/untaped-ansible/README.md), [recipe](../packages/untaped-recipe/README.md),
[dotfiles](../packages/untaped-dotfiles/README.md), [git](../packages/untaped-git/README.md) and
[lint](../packages/untaped-lint/README.md).

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

## Output and piping

Most commands take `--format table|json|yaml|raw|pipe` and `--columns`:

```bash
untaped github repos list --org acme --format json
untaped github repos list --org acme --format raw --columns full_name
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

To change the `table` default, set `untaped.ui.format` or export `UNTAPED_FORMAT`:

```bash
untaped config set untaped.ui.format json
```

Only data goes to stdout. Progress, warnings and errors go to stderr, so a
pipe carries only records. `-q`/`--quiet` mutes progress and messages;
warnings, errors, hints, a preview before a prompt, a stream you asked to
watch and output a flag asks for (`--columns ?`) still print.

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
[`jira.confirm`](./reference/config.md#jira)).

- `--dry-run` shows the preview and changes nothing, even with `--yes`.
- `--yes` (`-y`) skips the question. Without a terminal, such commands exit 2
  unless you pass `--yes` or `--dry-run`.
- Answering no exits 1 with `cancelled; no changes made`.

Every command uses the same [exit codes](./reference/exit-codes.md).

## Agent skills

Each plugin ships an agent skill that teaches an AI coding agent to use
it. Install every composed plugin's skill for your agent:

```bash
untaped skills install --all --target all
```

[Agent skills](./skills.md) covers targets, scopes and keeping installed
copies current.
