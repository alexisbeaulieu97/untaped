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
- `untaped skills` lists and installs the composed skill assets.
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
registry and ansible aliases) lives in a separate state file in the config
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
state writes only write `state.yml` (apart from the one-time move described
under [Capability state](#capability-state)). `UNTAPED_STATE` must not name the
config file itself.

The document root, `profiles`, and each profile must be mappings; anything
else (or an unreadable file) is reported as a configuration error naming the
file. An empty profile entry (`prod:` with nothing under it) is an empty
profile. Writes take an advisory lock on `<config>.lock` (state writes on
`<state>.lock`); set
`UNTAPED_CONFIG_LOCK_TIMEOUT` (seconds, a non-negative number) to change the
default 5-second wait. Both files are rewritten atomically through a unique
temporary file that is created owner-only (`0600`), so secrets are never
briefly world-readable.

Writes (`config set/unset`, `profile` commands, and capability state updates
to `state.yml`) rewrite only the keys they change: your comments, key order, quoting, and
indentation are kept. New keys are appended to their mapping, and new string
values that YAML would read as another type (`no`, `0123`, `~`) are quoted.
`config edit` saves exactly what you wrote.

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
by profile resolution and produce a warning. This applies to `log_level`,
`http`, `ui`, and registered capability sections; move them under
`profiles.default.<section>`.

The profile model and state model for a capability must have disjoint field
sets. State is written by the owning capability and is not writable through
`untaped config set`.

### Capability state

Earlier releases kept capability state at the top level of `config.yml`. Such
a section keeps working: when `state.yml` has no copy of a section, untaped
reads it from `config.yml` and prints one deprecation warning per run. The
next change to that section (for example `untaped workspace add`) moves it:
untaped writes the section to `state.yml` first and then removes it from
`config.yml`, holding both files' locks and keeping the rest of `config.yml`
(comments included) as written. If `config.yml` cannot be rewritten (read-only
directory, or a symlink untaped will not replace), the command still succeeds,
warns, and `state.yml` takes precedence; delete the stale section by hand.
`untaped doctor` lists every section still in `config.yml`.

The environment override shape is unchanged:

```text
UNTAPED_<SECTION>__<FIELD>
```

For example, `UNTAPED_GITHUB__TOKEN`, `UNTAPED_AWX__BASE_URL`,
`UNTAPED_HTTP__VERIFY_SSL`, and `UNTAPED_UI__THEME` override one process's
resolved values. The root scalar `log_level` is addressed as `log_level` and
can be overridden with `UNTAPED_LOG_LEVEL`. It is deprecated and has no
effect; `untaped doctor` warns when it is set. Capability fields still require
their fully qualified section key. For a setting value, precedence is the
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
exceptions: `log_level` is a root scalar, and `http.*` and `ui.*` are shared
profile fields. Bare keys are never expanded to a capability section.

```bash
untaped config list
untaped config list --all-profiles
untaped config list --show-secrets
untaped config list --format json
untaped config get github.token
untaped config get github.token --show-secrets
untaped config set github.token --prompt
printf '%s\n' "$GITHUB_TOKEN" | untaped config set github.token --stdin
untaped config set awx.base_url https://aap.example.com --target-profile default
untaped config unset awx.token --target-profile prod
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
without writing.

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
- `settings` for the shell — one row each for `log_level` (a warning when the
  deprecated setting is set), `http` (including a readable `http.ca_bundle`),
  and `ui` (including a known `ui.theme`);
- `settings` per capability — the capability's profile section;
- `state` per capability with a state model — its section in `state.yml` (or
  the legacy copy in `config.yml`), naming the file on failure;
- `legacy-state` — `warn` when capability state is still at the top level of
  `config.yml`, saying whether the next state change moves it or it is
  shadowed by `state.yml` and should be deleted; `warn` rows do not fail
  `doctor`;
- each capability-contributed health check (a check can report a
  non-failing `warn`, such as ansible's `ansible.deprecated-settings` while
  `ansible.freshness_ttl` is set), and any quarantined provider.

Settings rows apply `UNTAPED_*` environment overrides on top of the file and
name the variable when an override is the invalid value (for example
`UNTAPED_HTTP__TIMEOUT=abc`). Any failed row makes `doctor` exit nonzero.

## TLS and shared UI settings

`http.*` and `ui.*` are profile-scoped root settings shared by all capabilities.
HTTP clients use the operating system trust store by default. Prefer a pinned
CA bundle when a corporate certificate needs to be trusted:

```bash
untaped config set http.ca_bundle /path/to/corp-ca.pem
untaped config set http.verify_hostname false --target-profile work
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
untaped config set awx.base_url https://aap.prod.example.com --target-profile prod
printf '%s\n' "$AWX_PROD_TOKEN" | untaped config set awx.token --stdin --target-profile prod

untaped --profile prod awx ping
```

Keep credentials in `SecretStr` fields in capability models. Root listing and
profile output redact those fields by default, and `--show-secrets` is an
explicit opt-in.

## See also

- [Configuration reference](./reference/config.md) — every setting, default and
  environment variable.
- [Environment variables](./reference/environment.md).
- [Agent skills](./skills.md) — root skill discovery and installation.
- [Capability authoring](./plugins.md) — building an external capability for
  the unified executable.
