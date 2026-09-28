# Configuration

`untaped` is one unified CLI. It composes the built-in capabilities under
`untaped <capability> ...` and can discover external capabilities through the
`untaped.capabilities` entry-point group. Install the product once:

```bash
uv tool install untaped
```

The root owns the cross-cutting command groups:

- `untaped config` reads and writes settings.
- `untaped profile` manages named profile overlays.
- `untaped skills` lists, installs, updates, and removes the composed skill assets.
- `untaped doctor` runs isolated health checks and reports configuration
  problems.
- `untaped capabilities` lists ready and quarantined providers.

Capability settings remain in their own sections. Existing section names and
stored keys, such as `github.token` and `awx.base_url`, are part of the config
contract.

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
profile. Writes take an advisory lock on `<config>.lock` (state writes on
`<state>.lock`); set
`UNTAPED_CONFIG_LOCK_TIMEOUT` (seconds, a non-negative number) to change the
default 5-second wait. Both files are rewritten atomically and durably
(fsynced) through a unique temporary file that is created owner-only (`0600`),
so secrets are never briefly world-readable. A symlinked `config.yml` or
`state.yml` stays a symlink: the file it points at is rewritten.

Writes (`config set/unset`, `profile` commands, and capability state updates
to `state.yml`) rewrite only the keys they change: your comments, key order, quoting, and
indentation are kept. New keys are appended to their mapping, and new string
values that YAML would read as another type (`no`, `0123`, `~`) are quoted.
`config edit` saves exactly what you wrote, line endings included: it opens a
private copy, validates your result, and only then writes it back like any
other write (under the lock, owner-only, through a symlink). Saving without
changes writes nothing. If the result is invalid, the file changed while you
edited, or the save fails, `config.yml` is left as it was, the command exits 1,
and the error names the copy that holds your edits.

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

The profile model and state model for a capability must have disjoint field
sets. State is written by the owning capability and is not writable through
`untaped config set`. State is only ever read from `state.yml`: a state
section left at the top level of `config.yml` by a release before 8.0 is
ignored like any other unknown top-level key (`untaped doctor` flags it);
move it into `state.yml` by hand.

The environment override shape is unchanged:

```text
UNTAPED_<SECTION>__<FIELD>
```

For example, `UNTAPED_GITHUB__TOKEN`, `UNTAPED_AWX__BASE_URL`,
`UNTAPED_HTTP__VERIFY_SSL`, and `UNTAPED_UI__THEME` override one process's
resolved values. Capability fields still require their fully qualified
section key. For a setting value, precedence is the
environment override, the selected active profile, `profiles.default`, and
then the schema default.

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

`profile create`, `delete` and `rename` print an `untaped.profile_outcome`
record (`name`, `previous_name`, `copied_from`, `action`) in any `--format`;
`action` is `created`, `deleted`, `renamed`, or `planned` under `--dry-run`,
which checks the change and writes nothing. `profile delete` confirms first
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
parsing it as YAML. String and secret settings store the input verbatim, so
`p4ss #word`, `0123456`, `no`, or `[abc` are kept exactly as typed (via
`VALUE`, `--stdin`, or `--prompt`). Booleans accept `true`/`false`/`yes`/`no`/
`1`/`0`, numbers and enumerated choices are checked, and paths are stored as
strings; an invalid value is rejected before anything is written. For an
optional non-string setting, the literal `null` stores an explicit null; a
string setting stores `null` as text. To clear a value, use `config unset`.

`config set` and `config unset` print an `untaped.setting_outcome` record
(`key`, `profile`, `action`) in any `--format`; the value itself is never
echoed. `action` is `updated` for a set, `deleted` or `unchanged` for an unset,
and `planned` under `--dry-run`, which validates the value and target profile
without writing. Both write to the active profile; the root `--profile NAME`
option (anywhere in the command) writes to another existing profile instead.

Mapping and list settings (`ui.symbols`, `ui.color_roles`,
`ansible.dependency_paths`) take the whole value as JSON or YAML
(`'{"ok": "✓"}'` or `'{ok: ✓}'`), validated against the setting's type;
`config set` replaces the stored value and `config unset` removes the whole
key. `config get` and `config list` print such a value as compact JSON in
table and raw output and as a native mapping or list in `json`, `yaml` and
`pipe`.

Reads and writes validate only the section a key belongs to, so one invalid
value (for example a typo in `jira.page_size`) never blocks `config get`,
`config set`, or `config unset` for other keys; you can repair the broken key
through the CLI (`config set KEY --prompt` offers the raw stored value as the
default). `config list` still lists every key: an invalid section shows
its raw values and prints a warning naming the problem.

The configuration editor uses `VISUAL`, falling back to `EDITOR`, and waits for
it to exit before validating the saved configuration. Arguments are parsed
without a shell; quote executable paths containing spaces and include your GUI
editor's wait option (for example, `EDITOR="code --wait"`).

`--format raw --columns key,value` (or `--columns key --columns value`) is
useful when a script needs a stable two-column view. `config get` defaults to
raw output and returns only the selected value. Structured output (`json`,
`yaml`, `pipe`) includes the key, value, source, profile, and default metadata
as native values: an unset value or default is `null`, booleans and numbers
keep their types, and secrets stay masked as `"***"` unless `--show-secrets`
is passed. The `—` placeholder for unset values appears only in table and raw
output. Likewise `profile list` reports `active` as a boolean in structured
output and as `✓` in table/raw output.

Capability state fields produce a “managed by untaped …” error when passed to
`config set` or `config unset`. Use the owning capability's commands for state
mutations. Root config diagnostics are deliberately separate:

```bash
untaped doctor
```

`doctor` runs offline and reports one row per check, without allowing one
broken section to hide the rest:

- `config` — the config file and the state file load (readable, valid YAML,
  mapping root);
- `profile` — the selected profile (`--profile`, `UNTAPED_PROFILE`, or
  `active:`) exists;
- `settings` for the shell — one row each for `http` (including a readable
  `http.ca_bundle`), `ui` (including a known `ui.theme`) and `skills`;
- `settings` per capability — the capability's profile section;
- `state` per capability with a state model — its section in `state.yml`,
  naming the file on failure;
- `config` (permissions) — `warn` when other users can read or write the
  config file (it can hold tokens); fix it with `chmod 600`; `warn` rows do
  not fail `doctor`;
- `unknown-keys` — `warn` naming every key, in any profile, that no settings
  model declares (usually a typo, which is otherwise silently ignored), and
  every top-level key other than `active` and `profiles` (for example a
  pre-8.0 state section or `log_level`);
- `skills` — `warn` when a skill installed by `untaped skills install` (in the
  global Codex/Claude skill directories or the current git root's
  `.agents/skills`/`.claude/skills`) differs from the packaged copy or is no
  longer shipped, pointing at `untaped skills update` or `skills remove`
  (see [Agent skills](./skills.md#keep-installed-skills-up-to-date));
- each capability-contributed health check (a check can report a
  non-failing `warn`), and any quarantined provider. The built-in
  capabilities contribute:
  - `github.connection`, `jira.connection`, `awx.connection` — the resolved
    profile's `base_url` and where the token comes from (see
    [Tokens](#tokens)); `warn` when only one of the pair is set, or when the
    token is stored in plain text in `config.yml` (`<section>.token`). A
    section with neither passes as `not configured`. `token_command` is
    named, never run;
  - `workspace.git`, `github.git`, `ansible.git`, `recipe.git`, `recipe.uv` —
    `warn` when the program is not on `PATH`.

`doctor --online` also runs the online checks capabilities contribute:
`awx.api`, `github.api` and `jira.api` authenticate against the configured
service (the same call as `awx ping`, `github whoami` and `jira whoami`) for
the selected profile. A section with no token and no URL of its own (a
built-in default such as GitHub's does not count) passes as `not configured`.
Each probe makes one attempt, with no retries, and its timeout is capped at 10
seconds, so an unreachable service fails quickly. A failed row keeps one line
of the error and ends with the command that fixes it, for example
``run `untaped config set awx.token --prompt` `` for a rejected token,
`config set http.ca_bundle PATH` for an untrusted certificate, or
`config set awx.base_url URL` when the service cannot be reached, names the
wrong host, or answers with something unexpected. Plain `doctor` never
touches the network.

`untaped setup` writes a profile's service settings interactively and then
runs the same checks for the services it configured; see
[Getting started](./getting-started.md#set-up-your-services). It checks each
service's answers before writing any of them. For example, a token command
that does not parse, or one that a token inherited from `profiles.default`
would override, stops `setup` before it writes anything for that service.

Settings rows apply `UNTAPED_*` environment overrides on top of the file and
name the variable when an override is the invalid value (for example
`UNTAPED_HTTP__TIMEOUT=abc`). Any failed row makes `doctor` exit nonzero.

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
shell runs it. `alias set` and `alias remove` print an
`untaped.alias_outcome` record (`name`, `profile`, `action`); `alias list`
prints `untaped.alias` records (`name`, `command` shell-quoted, `argv`,
`profile`).

## TLS and shared UI settings

`http.*` and `ui.*` are profile-scoped root settings shared by all capabilities.
HTTP clients use the operating system trust store by default. Prefer a pinned
CA bundle when a corporate certificate needs to be trusted:

```bash
untaped config set http.ca_bundle /path/to/corp-ca.pem
untaped --profile work config set http.verify_hostname false
```

`http.ca_bundle` must point to a readable PEM file; a missing, unreadable, or
unparsable file is reported as a configuration error naming the path (and
fails `doctor`'s `http` row).
`http.verify_hostname: false` keeps chain validation enabled while skipping the
hostname check. `http.verify_ssl: false` disables certificate validation and
should be reserved for a controlled network.

Themes are selected through `ui.theme` and must name a built-in theme
(`default`, `plain`, `compact`, `high-contrast`, `quiet`, or `classic`):
`config set ui.theme` rejects anything else and `doctor` reports an unknown
theme already in the file. Human table/detail rendering follows the theme,
while JSON, YAML, raw, and pipe output remain machine-readable and stable.

`ui.format` replaces the `table` default of every command that takes the
shared `--format` option (`json`, `yaml`, `table`, `raw` or `pipe`). The
`UNTAPED_FORMAT` environment variable wins over it, and an explicit `--format`
wins over both. Commands whose own default is another format (`config get`
prints `raw`) keep it. Table output that does not go to a terminal is not
wrapped: only `COLUMNS` or a real terminal width bounds it.

## Worked profile setup

This example writes capability-qualified keys through the root and then invokes
a capability with a one-off profile:

```bash
untaped config set awx.base_url https://aap.example.com
printf '%s\n' "$AWX_DEV_TOKEN" | untaped config set awx.token --stdin
untaped config set github.token --prompt
untaped config set jira.base_url https://jira.example.com
untaped config set jira.token --prompt

untaped profile create prod --copy-from default
untaped --profile prod config set awx.base_url https://aap.prod.example.com
printf '%s\n' "$AWX_PROD_TOKEN" | untaped --profile prod config set awx.token --stdin

untaped --profile prod awx ping
```

Keep credentials in `SecretStr` fields in capability models. Root listing and
profile output redact those fields by default, and `--show-secrets` is an
explicit opt-in.

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
sets neither `token` nor `token_command`, whatever its `base_url`.

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
choice do. `untaped doctor` reports it as a `warn` row (which does not fail
`doctor`) naming `token_command` and an environment variable to use instead,
for example `token from awx.token, stored in plain text in config.yml; use
awx.token_command or $CONTROLLER_OAUTH_TOKEN instead`. A token set by
`UNTAPED_<SECTION>__TOKEN` is not stored in the file and is reported as
`token from $UNTAPED_<SECTION>__TOKEN`.

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
