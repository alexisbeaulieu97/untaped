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
process-specific path:

```text
~/.untaped/config.yml             # default
$UNTAPED_CONFIG                   # one-process override
```

The document root, `profiles`, and each profile must be mappings; anything
else (or an unreadable file) is reported as a configuration error naming the
file. An empty profile entry (`prod:` with nothing under it) is an empty
profile. Writes take an advisory lock on `<config>.lock`; set
`UNTAPED_CONFIG_LOCK_TIMEOUT` (seconds, a non-negative number) to change the
default 5-second wait. The file is rewritten atomically through a unique
temporary file that is created owner-only (`0600`), so secrets are never
briefly world-readable.

The current layout keeps profile-scoped settings under `profiles.<name>` and
keeps capability-managed state at the top level. `active` is optional; when it
is absent, `default` is the fallback profile.

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

# Capability-managed state is outside profiles.
workspace:
  workspaces:
    - name: prod
      path: ~/work/prod
```

Profile-scoped sections accidentally placed at the YAML top level are ignored
by profile resolution and produce a warning. This applies to `log_level`,
`http`, `ui`, and registered capability sections; move them under
`profiles.default.<section>`. Capability-managed state, such as
`workspace.workspaces`, remains top-level and is not treated as a misplaced
profile setting.

The profile model and state model for a capability must have disjoint field
sets. State is written by the owning capability and is not writable through
`untaped config set`.

The environment override shape is unchanged:

```text
UNTAPED_<SECTION>__<FIELD>
```

For example, `UNTAPED_GITHUB__TOKEN`, `UNTAPED_AWX__BASE_URL`,
`UNTAPED_HTTP__VERIFY_SSL`, and `UNTAPED_UI__THEME` override one process's
resolved values. The root scalar `log_level` is addressed as `log_level` and
can be overridden with `UNTAPED_LOG_LEVEL`; capability fields still require
their fully qualified section key. For a setting value, precedence is the
environment override, the selected active profile, `profiles.default`, and
then the schema default.

## Profiles

`--profile` is a root option and is position-independent. It applies to the
root management commands and to a capability invocation for one process:

```bash
untaped profile list
untaped profile current
untaped profile show prod
untaped profile show prod --raw
untaped profile show prod --show-secrets
untaped profile use prod
untaped profile create stage
untaped profile create stage --copy-from default
untaped profile delete stage --yes
untaped profile rename stage staging

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
untaped config set ui.theme quiet
untaped config set http.verify_ssl false
untaped config set log_level DEBUG
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

- `config` — the config file loads (readable, valid YAML, mapping root);
- `profile` — the selected profile (`--profile`, `UNTAPED_PROFILE`, or
  `active:`) exists;
- `settings` for the shell — one row each for `log_level`, `http` (including a
  readable `http.ca_bundle`), and `ui` (including a known `ui.theme`);
- `settings` per capability — the capability's profile section;
- `state` per capability with a state model — its top-level state section;
- each capability-contributed health check, and any quarantined provider.

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

- [Agent skills](./skills.md) — root skill discovery and installation.
- [Capability authoring](./plugins.md) — building an external capability for
  the unified executable.
