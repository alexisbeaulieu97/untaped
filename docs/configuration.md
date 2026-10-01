# Configuration

Settings live in profiles in `config.yml`; data a capability manages itself
lives in `state.yml`. The [configuration reference](./reference/config.md)
lists every setting, its default and its environment variable. Capability
settings live in their own sections (`github.token`, `awx.base_url`).

## File and layout

```text
~/.untaped/config.yml             # settings and profiles (default)
$UNTAPED_CONFIG                   # one-process override
~/.untaped/state.yml              # capability state (default: derived from the config file)
$UNTAPED_STATE                    # one-process override
```

The state file sits in the config file's directory and is named after it:
`config.yml` pairs with `state.yml`, and any other `<name>.<ext>` pairs with
`<name>.state.yml`. So `UNTAPED_CONFIG=~/work.yml` keeps its state in
`~/work.state.yml`, and sibling config files never share state.
`UNTAPED_STATE` puts it elsewhere, but must not name the config file itself.

`config` and `profile` commands write only `config.yml`; capability state
writes touch only `state.yml`.

`config.yml` keeps settings under `profiles.<name>`. `active` is optional;
when it is absent, `default` is the fallback profile.

```yaml
active: prod

profiles:
  default:
    http:
      verify_ssl: true
    ui:
      theme: default
      border: rounded
      collection_view: table
      detail_view: list
    awx:
      base_url: https://aap.example.com
      token: <token>
    github:
      token: <token>
    jira:
      base_url: https://jira.example.com
      token: <token>
      default_project: OPS
      default_board_id: 42
    workspace:
      cache_dir: ~/.untaped/repositories
      workspaces_dir: ~/.untaped/workspaces
      branch_template: "{name}"

  prod:
    awx:
      base_url: https://aap.prod.example.com
      token: <prod token>
```

`state.yml` holds one section per capability, outside profiles:

```yaml
workspace:
  active:
    - name: PROJ-123
      path: ~/.untaped/workspaces/PROJ-123
```

Things that surprise people:

- Only `active` and `profiles` are read at the top level of `config.yml`. A
  section placed there by mistake (`http:` instead of
  `profiles.default.http:`), or a state section left there, is ignored;
  `untaped doctor` flags it in its `unknown-keys` row. Move it by hand.
- Each section is validated only when a command reads it. An invalid
  `awx.page_size: abc` breaks `untaped awx ...` but not `untaped github ...`;
  the error names the section and the file. `untaped doctor` and
  `untaped config list` still report every invalid section.
- The document root, `profiles` and each profile must be mappings; anything
  else, or an unreadable file, is a configuration error naming the file. An
  empty entry (`prod:` with nothing under it) is an empty profile.

Both files are written atomically, owner-only (`0600`) and under a lock (see
`UNTAPED_CONFIG_LOCK_TIMEOUT` in
[Environment variables](./reference/environment.md)). A symlinked file stays a
symlink. Writes rewrite only the keys they change and keep your comments, key
order, quoting and indentation.

A setting's value comes from the first of these that has it: its
`UNTAPED_<SECTION>__<FIELD>` variable (for example `UNTAPED_GITHUB__TOKEN`),
the active profile, `profiles.default`, the built-in default.

## Profiles

A profile is a named set of settings. `default` is the base layer: a selected
profile overlays it field by field.

```bash
untaped profile create stage --copy-from default
untaped --profile stage config set awx.base_url https://aap.stage.example.com
untaped profile use stage
untaped profile delete stage --dry-run
```

The active profile is the first of: the root `--profile`, `UNTAPED_PROFILE`,
the `active:` key, then `default`. The first three must name an existing
profile; before any profile exists, only `default` may be named.

`--profile` is a root option and goes anywhere in the command: before the
capability, between command names (`untaped github --profile work whoami`) or
after the command. So do `--verbose`/`-v` and `--quiet`/`-q`. Tokens after
`--` belong to the command and are never read as `untaped` options.

- `default` is created by the first setting write. Other profiles must exist
  before a write targets them.
- `profile create`, `delete` and `rename` accept `--dry-run`, which checks the
  change and writes nothing. `profile delete` asks first; pass `--yes` when
  not interactive.
- `profile show` prints YAML with secrets redacted unless you pass
  `--show-secrets`.
- `profile current` writes only the name to stdout and its source (`flag`,
  `env`, `config` or `fallback`) to stderr, so it is safe in a prompt:

  ```bash
  echo "[$(untaped profile current 2>/dev/null)] $ "
  ```

## Settings

Capability keys are always fully qualified (`awx.base_url`, never
`base_url`). `http.*`, `ui.*` and `skills.*` are shared root sections.

```bash
untaped config list --all-profiles
untaped config get github.token --show-secrets
untaped config set http.timeout 60 --dry-run
untaped --profile prod config unset awx.token
untaped config set ui.symbols '{"ok": "✓", "fail": "✗"}'
untaped config edit
```

`config set` validates the value against the setting's type instead of
parsing it as YAML:

- string and secret settings store the input verbatim (`p4ss #word`,
  `0123456` and `no` stay as typed);
- mapping and list settings take the whole value as JSON or YAML;
- for an optional non-string setting, the literal `null` stores an explicit
  null; `config unset` removes the key instead;
- an invalid value is rejected before anything is written.

`config set` and `config unset` write to the active profile, or the one the
root `--profile` names, and print an `untaped.setting_outcome` record that
never echoes the value. Settings a capability manages as state are rejected
with a "managed by untaped …" error; use the owning capability's commands.

`config get` prints only the value (nothing when unset). Structured output
adds its source, profile and default, with secrets masked as `"***"` unless
you pass `--show-secrets`.

Because a command validates only the section it reads, you can repair a
broken key through the CLI: `config set KEY --prompt` offers the raw stored
value as the default.

`config edit` opens `VISUAL`, falling back to `EDITOR`; include your GUI
editor's wait option (`EDITOR="code --wait"`). It validates the result before
saving. If the result is invalid, the file changed while you edited, or the
save fails, `config.yml` is left as it was and the error names the copy that
holds your edits. If the editor itself exits with an error, the edits are not
checked: copy the file over `config.yml` yourself and run `untaped doctor`.

`untaped doctor` checks offline, one row per check: the config and state
files, the selected profile, every section, unknown keys, installed skills
and each capability's own checks. `--online` also authenticates against each
configured service. A failed check makes it exit nonzero; a `warn` row does
not. `untaped setup` writes a profile's service settings interactively,
checks the answers before writing any of them, then runs the same online
checks; see [Getting started](./getting-started.md#set-up-your-services).

## Command aliases

An alias is a shortcut for a longer command. Put the command after `--`:

```bash
untaped alias set failed -- awx jobs list --status failed
untaped failed --limit 5   # untaped awx jobs list --status failed --limit 5
untaped alias set pj --profile prod -- awx jobs list   # stored in profile prod
untaped alias set prod-jobs -- --profile prod awx jobs list   # runs with --profile prod
untaped alias remove failed --yes
```

`untaped NAME [ARGS…]` runs the stored command with `ARGS` appended. The
stored argv goes to `untaped` as is; no shell runs it.

- Aliases are stored per profile in the `shell.aliases` setting (name to argv
  list). Aliases in `profiles.default` apply beneath the active profile's.
- `alias set` and `alias remove` change the active profile, or the one a
  root `--profile` before `--` names; after `--`, `--profile` is part of the
  alias. `untaped NAME` looks the alias up the same way, so
  `untaped pj --profile prod` works too.
- To remove an alias inherited from `default`, run
  `untaped --profile default alias remove NAME`.
- Names use lowercase letters, digits and dashes. An alias never shadows a
  built-in command or capability: `alias set` rejects the name (exit 2) and a
  stored one is ignored.
- An alias is expanded once; it cannot run another alias.

## TLS and shared UI settings

`http.*` and `ui.*` are profile settings shared by all capabilities. HTTP
clients use the operating system trust store by default. To trust a corporate
certificate, prefer a pinned CA bundle:

```bash
untaped config set http.ca_bundle /path/to/corp-ca.pem
untaped --profile work config set http.verify_hostname false
```

`http.verify_ssl: false` disables certificate validation; keep it for a
controlled network. `ui.theme` picks a built-in theme and `ui.format` the
default `--format`; the [configuration reference](./reference/config.md#root)
lists every `http.*` and `ui.*` setting.

A table fits the terminal by narrowing its widest columns (a cell that does
not fit ends in `…`), and table output that does not go to a terminal is not
wrapped. `--columns ?` lists every column and marks the defaults with `*`;
`--columns +url` adds a column, `--columns=-url` removes one, and
`--columns name,url` shows exactly those.

## Tokens

`github`, `jira` and `awx` read their API token from the first of these that
is set:

1. `<section>.token`, from the active profile or its
   `UNTAPED_<SECTION>__TOKEN` override;
2. `<section>.token_command`, a command whose standard output is the token;
3. the service's conventional environment variables, in this order:
   - `github`: `GH_TOKEN`, then `GITHUB_TOKEN` (as the GitHub CLI reads them);
   - `jira`: `JIRA_API_TOKEN` (as `jira-cli` reads it);
   - `awx`: `CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN`, then `AAP_TOKEN`
     (as the `ansible.controller` and `awx.awx` collections read them).

These variables are not tied to a profile: one applies to every profile that
sets neither `token` nor `token_command`, whatever its `base_url`. For the
same reason, `doctor` and `setup` count one as configuring a service only
when the section also has a `base_url`.

`token_command` keeps the token out of `config.yml`. It is an argv list, run
without a shell, at most once per process and only when a command first needs
the token:

```bash
untaped config set github.token_command '["gh", "auth", "token"]'
untaped config set jira.token_command '["op", "read", "op://work/jira/token"]'
untaped config set awx.token_command '["pass", "show", "aap/token"]'
```

`untaped` strips surrounding whitespace from the output. It never prints the
token, the command's arguments or its output: a failure is reported as
`error: jira.token_command: 'op' exited with status 1` (or `not found on
PATH`, `printed no token`, `timed out after 60s`). The command's own stderr
goes straight to your terminal.

`untaped` does not run `gh auth token` on its own. To reuse the GitHub CLI's
login, set `github.token_command` as above.

A token in `<section>.token` is stored in plain text in `config.yml`, which is
what `config set <section>.token` and `untaped setup`'s "Enter a token"
choice do. `untaped doctor` warns about it and names `token_command` and an
environment variable to use instead.

## Debug logs

`--verbose`/`-v` sends debug logs to stderr through Python's `logging`, as
`DEBUG untaped.<area>: <message>` lines:

- `untaped.http`: each request's method, URL (with any password masked),
  status or transport error, and time, plus each retry wait;
- `untaped.git`: each `git` command's arguments (URL credentials masked), its
  directory, exit status and time, and `[auth header]` when a token was
  passed (the header itself never appears);
- `untaped.auth`: which token source was used (never the token).

`--verbose` is the only switch; there is no log-level setting.
