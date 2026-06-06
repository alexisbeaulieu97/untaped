# Configuration

`untaped` reads its settings from `~/.untaped/config.yml`. Core includes
`untaped config` for editing keys. Profile inventory commands are
provided by the optional `untaped-profile` plugin. Workspace registry
commands are provided by the optional `untaped-workspace` plugin.
Profile-scoped settings are contributed by optional plugins such as
Ansible, AWX, GitHub, Jira, and Workspace when those plugins are installed.

- `untaped config` — read and write the *keys* inside a profile
  (`http.ca_bundle`, plus plugin keys like `awx.token` and
  `jira.base_url` or `ansible.index_path`).
- `untaped profile` — manage the *profiles* (named overlays such as
  `dev`, `prod`, `homelab`) when the `untaped-profile` plugin is
  installed.

Both end up editing the same file. This page covers what's in it, how
the values are resolved, and how to manage profiles and keys without
hand-editing YAML.

## Where the file lives

```text
~/.untaped/config.yml             # default
$UNTAPED_CONFIG                   # override path for one process
```

`UNTAPED_CONFIG=/tmp/scratch.yml untaped config list` is handy for
trying things out without touching your real config.

## Profiles

Profile-scoped configuration lives under `profiles.<name>`. A few keys
live *outside* the `profiles` block:

- `active: <name>` — selects which profile is on top. Optional.
- `ui:` — global terminal presentation preferences. These are user
  interface preferences, not profile overlays.
- Plugin-owned top-level app state, such as `workspace.workspaces` or
  `ansible.sources` when those plugins are installed. App state is not
  user-tunable scalar config; it is managed by the owning plugin's commands.

```yaml
active: prod                       # optional; selects the overlay profile

ui:                                # optional global terminal presentation
  theme: default                   # built-ins: default, plain, compact
  border: rounded                  # rounded, square, ascii, none
  collection_view: table           # table or list for human output
  detail_view: list                # list or table for single-object views
  color_roles:                     # optional Rich style strings for TTY output
    header: bold cyan
    border: green
    key: cyan
    value: white
    success: green
    info: blue
    warning: yellow
    error: red

profiles:
  default:                         # the shared base layer (optional but conventional)
    log_level: INFO
    ansible:                          # optional Ansible plugin
      index_path: ~/.untaped/ansible-index.sqlite3
      repo_cache_path: ~/.untaped/ansible-repositories
      ref_scan_default: all
    awx:                              # optional AWX plugin
      base_url: https://aap.example.com
      token: <token>
      # api_prefix defaults to /api/controller/v2/ for AAP. Upstream AWX
      # users should override it to /api/v2/ here.
    github:
      token: ghp_xxx                 # optional GitHub plugin
    jira:                            # optional Jira plugin
      base_url: https://jira.example.com
      token: <token>
      default_project: OPS
      default_board_id: 42
    workspace:
      cache_dir: ~/.untaped/repositories       # optional Workspace plugin
      workspaces_dir: ~/.untaped/workspaces

  prod:                            # overlays default; only declares what differs
    awx:                              # optional AWX plugin
      base_url: https://aap.prod.example.com
      token: <prod token>

workspace:                         # optional Workspace plugin state
  workspaces:
    - name: prod
      path: ~/work/prod
    - name: stage
      path: ~/work/stage
```

Theme plugins can contribute additional `ui.theme` presets. For example,
installing `untaped-themes` adds `high-contrast`, `quiet`, and `classic`:

```yaml
ui:
  theme: quiet
```

`default` is conventional but not required. When it exists, every other
profile inherits its values; when it doesn't, the active profile is
layered alone and the schema's built-in defaults sit beneath everything.

`ui:` is intentionally global. It affects human terminal rendering for
semantic collections, details, and status messages. It does not change
`--format json`, `--format yaml`, or `--format raw`; those structured
formats still work if a configured theme preset is unavailable.

`ui.color_roles` values are Rich style strings, such as `bold cyan`,
`green`, or `red`. Supported role names are `header`, `border`, `key`,
`value`, `success`, `info`, `warning`, and `error`. Color roles are
emitted only for interactive terminal output; redirected output stays
plain text.

## Resolution order

For any single value, `untaped` resolves it from these layers, high to
low:

```text
UNTAPED_<SECTION>__<FIELD> env var   (highest)
        ↓
active profile
        ↓
default profile (if it exists)
        ↓
schema default                      (lowest)
```

The active profile is selected by, in order:

1. A command-local `--profile <name>` flag on commands that expose it.
2. The root `--profile <name>` flag (sugar that sets the env var for one
   invocation).
3. `UNTAPED_PROFILE` environment variable.
4. The `active:` key in the YAML.
5. Fallback to `default` if it exists, otherwise no overlay layer
   applies.

For example, `untaped config list --profile stage` reads that one
command against `stage` without touching your persisted `active:`.
The root form still works for every command:
`untaped --profile stage config list`.

## Managing profiles — `untaped profile`

`untaped profile` is provided by the standalone
[`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile)
plugin. Install it with the generic workflows in
[Plugins](./plugins.md); its release package spec is
`untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git@v0.1.0`.
The plugin repo owns the profile command reference; this page only documents
the profile commands that matter when explaining core configuration
resolution.

```text
untaped profile list                          # list profiles, ✓ marks active
untaped profile current                       # print the active profile name (pipe-friendly)
untaped profile show <name>                   # effective view (default ⤥ named)
untaped profile show <name> --raw             # only the keys this profile literally sets (no `default` merge)
untaped profile show <name> --show-secrets    # reveal token values
untaped profile use <name>                    # persist `active: <name>`
untaped profile create <name>                 # empty profile
untaped profile create <name> --copy-from default
untaped profile delete <name>                 # confirm before deleting
untaped profile delete <name> --yes           # non-interactive delete
untaped profile rename <old> <new>            # also updates `active:` if it pointed there
```

`profile show` defaults to YAML output; `--format json` wraps the data
in an envelope (`{name, active, raw, data}`) so downstream tools can
address metadata fields with `jq '.data.awx.base_url'`. `show default`
looks the same with or without `--raw` — `default` has no parent layer
to merge under itself.

`profile delete` asks for confirmation in an interactive terminal and refuses
non-interactive deletes unless `--yes` is passed. It still refuses to delete
the persisted active profile.

`profile current` prints just the name to stdout (with the source —
`env` / `config` / `fallback` — going to stderr), so you can use it in
prompts or scripts:

```bash
echo "[$(untaped profile current 2>/dev/null)] $ "
```

## Managing keys — `untaped config`

```text
untaped config list                          # effective values from the active profile
untaped config list --profile <name>         # one-off read against a named profile
untaped config list --all-profiles           # one row per (profile, key)
untaped config list --show-secrets           # reveal redacted values
untaped config list --format json|yaml|table|raw
untaped config list --format raw --columns key --columns value
                                             # available columns: key, value, default, source, profile
untaped config get <key>                     # print one effective scalar value
untaped config get <key> --profile <name>    # one-off read against a named profile
untaped config get <key> --show-secrets      # reveal a secret value
untaped config get <key> --format json|yaml|table|raw
untaped config set <key> <value>             # write to the active profile
untaped config set <key> --stdin             # read one value from stdin
untaped config set <key> --prompt            # prompt without echoing input
untaped config set <key> <value> --target-profile <name>
untaped config unset <key>                   # remove from the active profile
untaped config unset <key> --target-profile <name>
untaped config set ui.theme classic          # write a global UI preference
untaped config unset ui.theme                # remove a global UI preference
```

`--profile` and `--all-profiles` are mutually exclusive: one reads a
single effective profile, the other inspects the raw per-profile config.
`config get` defaults to `--format raw` and prints only the selected value,
so `untaped config get ui.theme` is safe to use in shell scripts. Its
structured and table formats include `key`, `value`, `default`, `source`,
and `profile` metadata.

Keys are dotted paths into the active schema, e.g. `http.ca_bundle` and
`http.verify_ssl`. Plugin packages contribute their own sections, such as
`awx.token` / `awx.base_url` when the optional AWX plugin is installed,
`github.token` when the optional GitHub plugin is installed, or
`workspace.cache_dir` when the optional workspace plugin is installed.
Values are parsed as YAML scalars, so
`untaped config set http.verify_ssl false` writes a real `false`, not
the string `"false"`. For secrets, prefer `--stdin` or `--prompt` so the
value does not land in shell history or process listings:

```bash
printf '%s\n' "$AWX_TOKEN" | untaped config set awx.token --stdin
untaped config set github.token --prompt
```

Writing to a profile that doesn't exist is rejected — create it first
with `untaped profile create <name>` if the profile plugin is installed,
or by editing the YAML directly. The one exception is `default`, which is
auto-bootstrapped on the first write so a fresh install doesn't need a
setup step. So `untaped config set awx.token --stdin` on a brand-new system
writes to `default` when the AWX plugin is installed; `untaped config set
awx.token --stdin --target-profile prod` requires `prod` to already exist.

`untaped config get/set/unset` also supports scalar `ui.*` preferences such
as `ui.theme`, `ui.border`, `ui.density`, `ui.collection_view`, and
`ui.detail_view`. These read and write the top-level `ui:` block because UI
preferences are global, not profile overlays. Do not pass `--profile` to
`config get ui.*`, or `--target-profile` to `config set/unset ui.*`.

Structured top-level app state is still owned by domain commands or direct
YAML editing. `plugins.*`, `workspace.*`, `ansible.sources`,
`ansible.aliases`, `ui.symbols`, and `ui.color_roles` are not managed
through `config get/set/unset`.

## Secrets

Tokens, passwords, and API keys are typed as `SecretStr` in the schema.
That has two consequences:

- `untaped config list` and the profile plugin's `untaped profile show`
  redact them as `***` by default. Pass `--show-secrets` to reveal.
- Tracebacks won't leak them — `repr(settings)` shows `***` too.

Mark a new credential field as `pydantic.SecretStr` when you add it to
the schema (see [AGENTS.md](../AGENTS.md) — the "hard rules" section)
and the redaction is automatic.

## TLS verification

By default, every HTTP client `untaped` makes uses the **OS trust
store** via the `truststore` package. Corporate root CAs that are
already installed in macOS Keychain, Windows certstore, or the Linux
system trust will "just work" — no configuration needed.

Two overrides:

```bash
untaped config set http.ca_bundle /path/to/corp-ca.pem
untaped config set http.verify_ssl false       # last-resort escape hatch
```

`http.verify_ssl: false` disables certificate validation entirely. Only
use it on a network you trust completely: traffic is open to MITM, and
tokens configured through installed plugins become visible to anyone
on-path. Prefer `http.ca_bundle` whenever the certificate is the real
problem.

## Environment-variable overrides

Any single setting can be overridden for one process via an env var
named after its dotted path:

```text
awx.token                  → UNTAPED_AWX__TOKEN       # optional AWX plugin
awx.base_url               → UNTAPED_AWX__BASE_URL    # optional AWX plugin
ansible.index_path         → UNTAPED_ANSIBLE__INDEX_PATH
                                                    # optional Ansible plugin
ansible.ref_scan_default   → UNTAPED_ANSIBLE__REF_SCAN_DEFAULT
                                                    # optional Ansible plugin
jira.token                 → UNTAPED_JIRA__TOKEN      # optional Jira plugin
jira.base_url              → UNTAPED_JIRA__BASE_URL   # optional Jira plugin
ui.theme                   → UNTAPED_UI__THEME
ui.collection_view         → UNTAPED_UI__COLLECTION_VIEW
http.verify_ssl            → UNTAPED_HTTP__VERIFY_SSL
github.token               → UNTAPED_GITHUB__TOKEN  # optional GitHub plugin
workspace.workspaces_dir   → UNTAPED_WORKSPACE__WORKSPACES_DIR
                                                    # optional Workspace plugin
```

Note the **double underscore** between the section and the field, and
the section name is uppercased. These beat the YAML — useful for CI,
one-off invocations, or keeping a token in a secret manager rather
than the file.

## Worked example: dev / prod profiles

A common setup: one profile per environment, sharing a base layer for
everything that doesn't change. Plugin settings require their optional
plugins to be installed; the `untaped profile` commands require the
optional profile plugin.

```bash
# Start with a shared base.
untaped config set awx.base_url https://aap.example.com
printf '%s\n' "$AWX_DEV_TOKEN" | untaped config set awx.token --stdin
untaped config set github.token --prompt  # optional GitHub plugin
untaped config set jira.base_url https://jira.example.com  # optional Jira plugin
untaped config set jira.token --prompt

# Branch a prod profile from default and override what differs.
# `prod` must exist before any --target-profile prod write; create it first.
untaped profile create prod --copy-from default
untaped config set awx.base_url https://aap.prod.example.com --target-profile prod
printf '%s\n' "$AWX_PROD_TOKEN" | untaped config set awx.token --stdin --target-profile prod

# Day-to-day: do nothing — with no `active:` set, `default` is the
# implicit fallback. (Only run `untaped profile use default` if you
# previously persisted a different active profile and want to switch
# back.)

# One-off prod call without switching profiles globally:
untaped --profile prod awx ping
```

## See also

- [`plugins.md`](./plugins.md) — install and sync optional plugin packages.
- [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace)
  — workspace plugin command reference.
- [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx) —
  AWX plugin command reference.
- [`untaped-ansible`](https://github.com/alexisbeaulieu97/untaped-ansible)
  — Ansible dependency graph command reference.
- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github) —
  GitHub plugin command reference.
- [`untaped-jira`](https://github.com/alexisbeaulieu97/untaped-jira) —
  Jira plugin command reference.
- [`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile) —
  profile plugin command reference.
- [`untaped-themes`](https://github.com/alexisbeaulieu97/untaped-themes) —
  theme preset plugin reference.
- [AGENTS.md](../AGENTS.md) — the architecture and contribution rules
  (read this if you're extending `untaped`).
