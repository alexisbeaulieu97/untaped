# Configuration

This page covers the config and state files, profiles, settings, command
aliases and tokens. Every setting, its default and its environment variable is
in the [configuration reference](./reference/config.md). Capability settings
live in their own sections (`github.token`, `awx.base_url`).

Each section is validated only when a command reads it, so an invalid value in
one capability's section (say `awx.page_size: abc`) does not break unrelated
commands such as `untaped github ...`. The error, raised by the commands that
do read the section, names the section and the config file. `untaped doctor`
and `untaped config list` still report every invalid section.

## File and layout

The default file is `~/.untaped/config.yml`. Set `UNTAPED_CONFIG` to use a
process-specific path. Capability-managed state (for example the workspace
registry and ansible source aliases) lives in a separate state file in the config
file's directory, named after it: `config.yml` pairs with `state.yml`, and any
other config file `<name>.<ext>` pairs with `<name>.state.yml` (so
`UNTAPED_CONFIG=~/work.yml` keeps its state in `~/work.state.yml`, and sibling
config files never share state). Set `UNTAPED_STATE` to put it elsewhere:

```text
~/.untaped/config.yml             # settings and profiles (default)
$UNTAPED_CONFIG                   # one-process override
~/.untaped/state.yml              # capability state (default: derived from the config file)
$UNTAPED_STATE                    # one-process override
```

`config` and `profile` commands only ever write `config.yml`; capability
state writes only write `state.yml`. `UNTAPED_STATE` must not name the
config file itself.

The document root, `profiles`, and each profile must be mappings; anything
else (or an unreadable file) is reported as a configuration error naming the
file. An empty profile entry (`prod:` with nothing under it) is an empty
profile. Both files are written atomically and owner-only (`0600`) under a
lock (see `UNTAPED_CONFIG_LOCK_TIMEOUT` in
[Environment variables](./reference/environment.md)); a symlinked file stays a
symlink. Writes rewrite only the keys they change, keeping your comments, key
order, quoting and indentation. `config edit` validates your result before
saving it; if the result is invalid, the file changed while you edited, the
save fails, or the editor exits with an error, `config.yml` is left as it was
and the error names the copy that holds your edits. After an editor error the
edits are not checked: copy the file over `config.yml` yourself and run
`untaped doctor` to keep them.

`config.yml` keeps profile-scoped settings under `profiles.<name>`. `active`
is optional; when it is absent, `default` is the fallback profile.

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

  prod:
    awx:
      base_url: https://aap.prod.example.com
      token: <prod token>
```

`state.yml` holds one section per capability, outside profiles:

```yaml
workspace:
  workspaces:
    - name: prod
      path: ~/work/prod
```

Profile-scoped sections accidentally placed at the YAML top level are ignored
by profile resolution; `untaped doctor` flags them in its `unknown-keys` row.
This applies to `http`, `ui`, `skills`, and registered capability sections;
move them under `profiles.default.<section>`.

State is written by the owning capability and is not writable through
`untaped config set`. State is only ever read from `state.yml`: a state
section left at the top level of `config.yml` is ignored like any other
unknown top-level key (`untaped doctor` flags it); move it into `state.yml`
by hand.

A setting's value comes from the first of these that has it: its
`UNTAPED_<SECTION>__<FIELD>` override (for example `UNTAPED_GITHUB__TOKEN`),
the active profile, `profiles.default`, the built-in default. See
[Environment variables](./reference/environment.md).

## Profiles

`--profile` is a root option and is position-independent: it can go before the
capability, between command names (`untaped github --profile work whoami`), or
after the command. The same holds for `--verbose`/`-v` and `--quiet`/`-q`.
Tokens after `--` belong to the command and are never read as root options,
so `untaped workspace foreach -- "tool --profile x"` passes `--profile x`
through. It
applies to the root management commands and to a capability invocation for one
process:

```bash
untaped profile list
untaped profile current
untaped profile show prod
untaped profile show prod --raw
untaped profile show prod --show-secrets
untaped profile use prod
untaped profile create stage
untaped profile create stage --copy-from default
untaped profile delete stage --dry-run
untaped profile delete stage --yes
untaped profile rename stage staging --format json

untaped --profile stage config list
untaped config list --profile stage
untaped --profile prod awx ping
```

Active-profile selection follows this order: root `--profile`,
`UNTAPED_PROFILE`, the persisted `active:` key, and the `default` profile
fallback. The first three sources must name an existing profile (even when no
profiles are defined yet, only the conceptual `default` may be named). When a
non-default profile is selected, its values overlay `profiles.default` per
field; `default` is the shared base layer.

`profile show` emits YAML by default and accepts `--format json`. Secrets are
redacted unless `--show-secrets` is passed. `profile current` writes only the
profile name to stdout; its source (`flag`, `env`, `config`, or `fallback`) is
reported on stderr, so it is safe in a prompt or pipeline:

```bash
echo "[$(untaped profile current 2>/dev/null)] $ "
```

`profile create`, `delete` and `rename` accept `--dry-run`, which checks the
change and writes nothing. `profile delete` confirms first
(pass `--yes` without a terminal); `--dry-run` shows the preview without
prompting.

`default` is created automatically by the first setting write. Other profiles
must exist before a write targets them.

## Settings

The root config command lists every composed section and requires a fully
qualified key for capability reads and writes. Root keys are the documented
exceptions: `http.*`, `ui.*` and `skills.*` are shared profile fields. Bare
keys are never expanded to a capability section.

```bash
untaped config list
untaped config list --all-profiles
untaped config list --show-secrets
untaped config list --format json
untaped config get github.token
untaped config get github.token --show-secrets
untaped config set github.token --prompt
printf '%s\n' "$GITHUB_TOKEN" | untaped config set github.token --stdin
untaped config set awx.base_url https://aap.example.com
untaped --profile prod config unset awx.token
untaped config set http.timeout 60 --dry-run --format json
untaped config set ui.theme quiet
untaped config set http.verify_ssl false
untaped config set ui.symbols '{"ok": "✓", "fail": "✗"}'
untaped config edit
```

`config set` validates the value against the setting's type rather than
parsing it as YAML: string and secret settings store the input verbatim
(`p4ss #word`, `0123456` and `no` stay as typed), and an invalid value is
rejected before anything is written. Mapping and list settings take the whole
value as JSON or YAML. For an optional non-string setting, the literal
`null` stores an explicit null (a string setting stores it as text); to remove
the key, use `config unset`. Both write to the
active profile, or to the one the root `--profile NAME` names, and print an
`untaped.setting_outcome` record that never echoes the value.

Because only the section a key belongs to is validated, you can repair a
broken key through the CLI (`config set KEY --prompt` offers the raw stored
value as the default). `config edit` opens `VISUAL`, falling back to
`EDITOR`; include your GUI editor's wait option (`EDITOR="code --wait"`).
`config get` prints only the value (raw; nothing when unset) by default;
structured output adds its source, profile and default, with secrets masked
as `"***"` unless `--show-secrets` is passed.

Capability state fields produce a “managed by untaped …” error when passed to
`config set` or `config unset`. Use the owning capability's commands for state
mutations.

`untaped doctor` checks the config and state files, the selected profile,
every section, unknown keys, installed skills and each capability's own checks
offline, one row per check; `untaped doctor --online` also authenticates
against each configured service. A failed row makes it exit nonzero; a `warn`
row does not. `untaped setup` writes a profile's service settings
interactively, checks each service's answers before writing any of them, and
then runs the same online checks; see
[Getting started](./getting-started.md#set-up-your-services).

## Command aliases

An alias is a shortcut for a longer command. Put the command after `--`:

```bash
untaped alias set failed -- awx jobs list --status failed
untaped failed --limit 5   # untaped awx jobs list --status failed --limit 5
untaped alias set prod-jobs -- --profile prod awx jobs list
untaped alias set pj --profile prod -- awx jobs list   # stored in profile prod
untaped alias list
untaped alias remove failed --yes
```

`untaped NAME [ARGS…]` runs the stored command with `ARGS` appended. Aliases
are stored per profile in the `shell.aliases` setting (a mapping of name to
argv list); `profiles.default` aliases apply beneath the active profile's, and
`alias set`/`alias remove` change the active profile (or the one the root
`--profile` names; before `--` it is a root option, after `--` it is part of
the alias). `untaped NAME` looks the alias up in the profile a `--profile`
anywhere before `--` names (`untaped pj --profile prod` works too). To remove
an alias inherited from `default`, run
`untaped --profile default alias remove NAME`. Names use lowercase letters,
digits and dashes. An alias
can never shadow a built-in command or capability (`alias set` rejects the
name with exit 2, and a stored one is ignored), and an alias is expanded once:
it cannot run another alias. The stored argv is passed to `untaped` as is; no
shell runs it. The records `alias` prints are in
[Pipes and record kinds](./reference/pipes.md).

## TLS and shared UI settings

`http.*` and `ui.*` are profile-scoped root settings shared by all capabilities.
HTTP clients use the operating system trust store by default. Prefer a pinned
CA bundle when a corporate certificate needs to be trusted:

```bash
untaped config set http.ca_bundle /path/to/corp-ca.pem
untaped --profile work config set http.verify_hostname false
```

`http.verify_ssl: false` disables certificate validation and should be
reserved for a controlled network. `ui.theme` picks a built-in theme and
`ui.format` the default `--format`; the
[configuration reference](./reference/config.md#root) lists every `http.*` and
`ui.*` setting.

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

untaped strips surrounding whitespace from the output. It never prints the
token, the command's arguments or its output: a failure is reported as
`error: jira.token_command: 'op' exited with status 1` (or `not found on
PATH`, `printed no token`, `timed out after 60s`). The command's own stderr
goes straight to your terminal.

untaped does not run `gh auth token` on its own. To reuse the GitHub CLI's
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

## See also

- [Configuration reference](./reference/config.md) — every setting, default and
  environment variable.
- [Environment variables](./reference/environment.md).
- [Agent skills](./skills.md) — root skill discovery and installation.
- [Capability authoring](./plugins.md) — building an external capability for
  the unified executable.
