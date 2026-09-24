# Getting started

This page takes you from install to a first command in each capability.

## Install

`untaped` needs Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install untaped
untaped --version
untaped --help
```

Optionally install shell completion:

```bash
untaped --install-completion
```

`untaped doctor` checks the install and your configuration without any
network access. Run it whenever something looks wrong:

```bash
untaped doctor
```

## Store your tokens

Settings live in `~/.untaped/config.yml`. Write them with `untaped config set`
rather than by hand, so they are validated. Use `--prompt` for tokens: the
value is read without echo and never lands in your shell history.

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

Check what is set. Secrets show as `***`:

```bash
untaped config list
untaped config get github.token
```

Every setting, its default and its environment variable is in the
[configuration reference](./reference/config.md).

## Use profiles for more than one environment

A profile is a named set of settings. `default` is the base; other profiles
override it field by field.

```bash
untaped profile create prod --copy-from default
untaped config set awx.base_url https://aap.prod.example.com --target-profile prod
untaped config set awx.token --prompt --target-profile prod

untaped profile list
untaped --profile prod awx ping
untaped profile use prod
untaped profile current
```

`--profile NAME` selects a profile for one command; put it anywhere in the
command, for example `untaped github --profile work whoami`. `profile use`
changes the default for every later command. `UNTAPED_PROFILE` does the same
for one shell session.

## First command in each capability

Each capability has a guide with the full workflow.

```bash
# workspace: register a directory of Git clones and sync it
untaped workspace init demo
untaped workspace add git@github.com:acme/api.git --workspace demo --sync
untaped workspace status --workspace demo

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
untaped ansible graph acme/base-role --downstream

# recipe: see installed recipes
untaped recipe list
```

| Capability | Guide |
|---|---|
| `workspace` | [Workspaces](./workspace/usage.md) |
| `github` | [GitHub](./github/usage.md) |
| `jira` | [Jira](./jira/usage.md) |
| `awx` | [AWX/AAP](./awx/usage.md) |
| `ansible` | [Ansible dependency graphs](./ansible/usage.md) |
| `recipe` | [Recipes](./recipe/usage.md) |

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

Only data goes to stdout. Progress, warnings and errors go to stderr, so a
pipe never carries noise. `-q`/`--quiet` mutes progress and success messages.

```bash
# Clone every non-archived repo of a GitHub team into a workspace
untaped github repos list --team acme/platform --no-archived --format pipe \
  | untaped workspace add --stdin --workspace demo --sync

# Pick a job template with fzf and show it as YAML
untaped awx job-templates list --format raw --columns name \
  | fzf \
  | untaped awx job-templates get --stdin --format yaml
```

See [Pipes and record kinds](./reference/pipes.md) for which commands read
which records.

## Commands that change things

Commands that write, delete or launch show a preview and ask before they act.

- `--dry-run` shows the preview and changes nothing.
- `--yes` (`-y`) skips the question. Without a terminal, such commands exit 2
  unless you pass `--yes` or `--dry-run`.
- Answering no exits 1 with `cancelled; no changes made`.

Exit codes are the same everywhere: see [Exit codes](./reference/exit-codes.md).

## Agent skills

Each capability ships a skill that teaches an AI coding agent to use it:

```bash
untaped skills list
untaped skills install --all --target claude
```

See [Agent skills](./skills.md).

## See also

- [Configuration](./configuration.md)
- [Glossary](./glossary.md)
