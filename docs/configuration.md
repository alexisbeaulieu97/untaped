# Configuration

`untaped` reads its settings from `~/.untaped/config.yml`. The two
commands you'll use to manage that file are:

- `untaped profile` — manage the *profiles* (named overlays such as
  `dev`, `prod`, `homelab`).
- `untaped config` — read and write the *keys* inside a profile
  (`awx.token`, `http.ca_bundle`, …).

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
- `workspace.workspaces` — the workspace registry. App state, not
  user-tunable config; managed by `untaped workspace` commands.

```yaml
active: prod                       # optional; selects the overlay profile

profiles:
  default:                         # the shared base layer (optional but conventional)
    log_level: INFO
    awx:
      base_url: https://aap.example.com
      token: <token>
      # api_prefix defaults to /api/controller/v2/ for AAP. Upstream AWX
      # users should override it to /api/v2/ here.
    github:
      token: ghp_xxx

  prod:                            # overlays default; only declares what differs
    awx:
      base_url: https://aap.prod.example.com
      token: <prod token>

workspace:                         # registry: name → path only
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

```text
untaped profile list                          # list profiles, ✓ marks active
untaped profile current                       # print the active profile name (pipe-friendly)
untaped profile show <name>                   # effective view (default ⤥ named)
untaped profile show <name> --raw             # only the keys this profile literally sets
untaped profile show <name> --show-secrets    # reveal token values
untaped profile use <name>                    # persist `active: <name>`
untaped profile create <name>                 # empty profile
untaped profile create <name> --copy-from default
untaped profile delete <name>                 # refused for the active profile
untaped profile rename <old> <new>            # also updates `active:` if it pointed there
```

`profile show` defaults to YAML output; `--format json` wraps the data
in an envelope (`{name, active, raw, data}`) so downstream tools can
address metadata fields with `jq '.data.awx.base_url'`.

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
untaped config list --format json            # machine-readable
untaped config set <key> <value>             # write to the active profile
untaped config set <key> <value> --profile <name>
untaped config unset <key>                   # remove from the active profile
untaped config unset <key> --profile <name>
```

Keys are dotted paths into the schema, e.g. `awx.token`,
`awx.base_url`, `awx.default_organization`, `http.ca_bundle`,
`http.verify_ssl`, `github.token`. Values are parsed as YAML scalars,
so `untaped config set http.verify_ssl false` writes a real `false`,
not the string `"false"`.

Setting a key on a profile that doesn't exist creates the profile
(except for `default`, which is auto-bootstrapped if any profile is
written to).

## Secrets

Tokens, passwords, and API keys are typed as `SecretStr` in the schema.
That has two consequences:

- `untaped config list` and `untaped profile show` redact them as `***`
  by default. Pass `--show-secrets` to reveal.
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

`http.verify_ssl: false` disables certificate validation entirely; only
use it on a network you trust, since traffic is then open to MITM.

## Environment-variable overrides

Any single setting can be overridden for one process via an env var
named after its dotted path:

```text
awx.token                  → UNTAPED_AWX__TOKEN
awx.base_url               → UNTAPED_AWX__BASE_URL
http.verify_ssl            → UNTAPED_HTTP__VERIFY_SSL
github.token               → UNTAPED_GITHUB__TOKEN
```

Note the **double underscore** between the section and the field, and
the section name is uppercased. These beat the YAML — useful for CI,
one-off invocations, or keeping a token in a secret manager rather
than the file.

## Worked example: dev / prod profiles

A common setup: one profile per environment, sharing a base layer for
everything that doesn't change.

```bash
# Start with a shared base.
untaped config set awx.base_url https://aap.example.com
untaped config set awx.token    <dev-token>
untaped config set github.token ghp_xxx

# Branch a prod profile from default and override what differs.
untaped profile create prod --copy-from default
untaped config set awx.base_url https://aap.prod.example.com --profile prod
untaped config set awx.token    <prod-token>                  --profile prod

# Day-to-day: stay on default.
untaped profile use default

# One-off prod call without switching profiles globally:
untaped --profile prod awx ping
```

## See also

- [`workspace.md`](./workspace.md) — `untaped workspace` commands and
  the manifest / registry split.
- [`awx.md`](./awx.md) — `untaped awx` commands and the resource
  framework.
- [`github.md`](./github.md) — the (small) GitHub domain.
- [AGENTS.md](../AGENTS.md) — the architecture and contribution rules
  (read this if you're extending `untaped`).
