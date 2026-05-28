# Configuration

`untaped` reads its settings from `~/.untaped/config.yml`. Core includes
`untaped config` for editing keys. Profile inventory commands are
provided by the optional `untaped-profile` plugin. Workspace registry
commands are provided by the optional `untaped-workspace` plugin. AWX
settings are available when the optional `untaped-awx` plugin is installed.

- `untaped config` — read and write the *keys* inside a profile
  (`http.ca_bundle`, plus plugin keys like `awx.token`).
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

Every configurable value lives under `profiles.<name>`. Two keys live
*outside* the `profiles` block:

- `active: <name>` — selects which profile is on top. Optional.
- Plugin-owned top-level app state, such as `workspace.workspaces` when
  `untaped-workspace` is installed. App state is not user-tunable scalar
  config; it is managed by the owning plugin's commands.

```yaml
active: prod                       # optional; selects the overlay profile

profiles:
  default:                         # the shared base layer (optional but conventional)
    log_level: INFO
    awx:                              # optional AWX plugin
      base_url: https://aap.example.com
      token: <token>
      # api_prefix defaults to /api/controller/v2/ for AAP. Upstream AWX
      # users should override it to /api/v2/ here.
    github:
      token: ghp_xxx                 # optional GitHub plugin
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

`default` is conventional but not required. When it exists, every other
profile inherits its values; when it doesn't, the active profile is
layered alone and the schema's built-in defaults sit beneath everything.

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

1. `UNTAPED_PROFILE` environment variable.
2. The root `--profile <name>` flag (sugar that sets the env var for one
   invocation).
3. The `active:` key in the YAML.
4. Fallback to `default` if it exists, otherwise no overlay layer
   applies.

`untaped --profile stage awx job-templates list` runs that one command
against `stage` without touching your persisted `active:`.

## Managing profiles — `untaped profile`

`untaped profile` is provided by the standalone
[`untaped-profile`](https://github.com/alexisbeaulieu97/untaped-profile)
plugin. Install both `untaped` and the profile plugin from git with:

```bash
uv tool install "git+https://github.com/alexisbeaulieu97/untaped.git" \
  --with "untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git" \
  --no-sources \
  --force
```

To let `untaped plugins` remember that desired plugin state, give `plugins add`
the same source spec for the core tool:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-profile.git \
  --tool-spec "git+https://github.com/alexisbeaulieu97/untaped.git"
```

For editable source checkouts, use the checkout path and mark the tool editable:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-profile.git \
  --tool-spec /path/to/untaped \
  --editable-tool
```

For direct URLs, `untaped plugins add` infers the plugin name from the
repository basename and stores the canonical `name @ url` form. You can also
provide that form explicitly, for example
`untaped-profile @ git+https://github.com/...`. The normalized name is the
stable key used when replacing or removing a recorded plugin, so
`untaped plugins remove untaped-profile` works.
Without `--tool-spec`, `plugins add` syncs the recorded/default tool spec,
which is `untaped` for the future published package.

```text
untaped profile list                          # list profiles, ✓ marks active
untaped profile current                       # print the active profile name (pipe-friendly)
untaped profile show <name>                   # effective view (default ⤥ named)
untaped profile show <name> --raw             # only the keys this profile literally sets (no `default` merge)
untaped profile show <name> --show-secrets    # reveal token values
untaped profile use <name>                    # persist `active: <name>`
untaped profile create <name>                 # empty profile
untaped profile create <name> --copy-from default
untaped profile delete <name>                 # refused for the active profile
untaped profile rename <old> <new>            # also updates `active:` if it pointed there
```

`profile show` defaults to YAML output; `--format json` wraps the data
in an envelope (`{name, active, raw, data}`) so downstream tools can
address metadata fields with `jq '.data.awx.base_url'`. `show default`
looks the same with or without `--raw` — `default` has no parent layer
to merge under itself.

`profile current` prints just the name to stdout (with the source —
`env` / `config` / `fallback` — going to stderr), so you can use it in
prompts or scripts:

```bash
echo "[$(untaped profile current 2>/dev/null)] $ "
```

## Managing keys — `untaped config`

```text
untaped config list                          # effective values from the active profile
untaped config list --all-profiles           # one row per (profile, key)
untaped config list --show-secrets           # reveal redacted values
untaped config list --format json|yaml|table|raw
untaped config list --format raw --columns key --columns value
                                             # available columns: key, value, default, source, profile
untaped config set <key> <value>             # write to the active profile
untaped config set <key> <value> --profile <name>
untaped config unset <key>                   # remove from the active profile
untaped config unset <key> --profile <name>
```

Keys are dotted paths into the active schema, e.g. `http.ca_bundle` and
`http.verify_ssl`. Installed plugins add their own sections, such as
`awx.token` / `awx.base_url` when the optional AWX plugin is installed,
`github.token` when the optional GitHub plugin is installed, or
`workspace.cache_dir` when the optional workspace plugin is installed.
Values are parsed as YAML scalars, so
`untaped config set http.verify_ssl false` writes a real `false`, not
the string `"false"`.

Writing to a profile that doesn't exist is rejected — create it first
with `untaped profile create <name>` if the profile plugin is installed,
or by editing the YAML directly. The one exception is `default`, which is
auto-bootstrapped on the first write so a fresh install doesn't need a
setup step. So `untaped config set awx.token <tok>` on a brand-new system
writes to `default` when the AWX plugin is installed; `untaped config set
awx.token <tok> --profile prod` requires `prod` to already exist.

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
everything that doesn't change. The `awx.*` commands require the optional
AWX plugin; the `untaped profile` commands require the optional profile
plugin.

```bash
# Start with a shared base.
untaped config set awx.base_url https://aap.example.com
untaped config set awx.token    <dev-token>
untaped config set github.token ghp_xxx  # optional GitHub plugin

# Branch a prod profile from default and override what differs.
# `prod` must exist before any --profile prod write; create it first.
untaped profile create prod --copy-from default
untaped config set awx.base_url https://aap.prod.example.com --profile prod
untaped config set awx.token    <prod-token>                  --profile prod

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
- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github) —
  GitHub plugin command reference.
- [AGENTS.md](../AGENTS.md) — the architecture and contribution rules
  (read this if you're extending `untaped`).
