# Configuration

`untaped` is an SDK, not an app: there is no central `untaped` command. Each
tool is an independent CLI (`untaped-github`, `untaped-jira`, `untaped-awx`,
`untaped-ansible`, `untaped-workspace`, `untaped-apple-health`) installed in its
own `uv tool` environment. They all read and write **one shared file**,
`~/.untaped/config.yml`, so configuration is a cross-tool contract — but the
commands that manage it are per tool:

- `<tool> config set|get|list|unset` — read and write the setting *keys*
  (the tool's own section, plus the per-profile `http.*` and `ui.theme`).
- `<tool> profile create|list|use|delete` — manage the *profiles* (named
  overlays such as `dev`, `prod`, `homelab`).

Profiles and themes are built into the SDK and mounted per tool, so every tool
ships these command groups out of the box — there is nothing extra to install.
This page covers what's in the file, how the values are resolved, and how to
manage profiles and keys without hand-editing YAML. Examples use
`untaped-github`; substitute any other tool.

## Config format v2 (SDK 2.x)

Independent tool environments all read and write this one file, so its format is
a shared contract. The previous **v1** format was frozen across all SDK 1.x
releases; SDK **2.0** is the major event that v1 anticipated. The one breaking
change in 2.0: `http` and `ui` are no longer top-level SDK globals — they are
now **ordinary per-profile settings** that live under `profiles.<name>.http` /
`profiles.<name>.ui`, exactly like `log_level` and each tool's own section. A
top-level `http:`/`ui:` block is now ignored (the same as any other misplaced
profile section). The frozen surface for v2:

- Top-level keys: `active:`, `profiles:`, plus tool-owned top-level **state**
  (such as `workspace.workspaces`; see [State vs. profile fields](#state-vs-profile-fields)).
- Per-profile keys, addressed by dotted name: each tool's own section plus
  `http.*` and `ui.*` (e.g. `http.verify_ssl`, `ui.theme`). These layer through
  profiles like any other profile field.
- One section per tool, holding that tool's profile fields plus its disjoint
  top-level state fields (the two field sets do not overlap).
- Profile layering: `profiles.default` sits beneath `profiles.<active>`.
- Env-var override shape: `UNTAPED_<SECTION>__<FIELD>` (uppercased section,
  double underscore before the field).
- Bare config keys address the invoking tool's own section (e.g.
  `untaped-github config set token X` writes `github.token`).

Each tool validates **only its own section** (`extra="ignore"`) and writes are
surgical and filelocked, so an older tool never clobbers a newer tool's section.
See [decisions.md](./decisions.md).

### Migrating a v1 config to v2

If you have a pre-2.0 config with top-level `http:` or `ui:` blocks, move them
under `profiles.default` (anything else under `profiles.default` is unchanged).
Any tool will **warn** on load if it finds a per-profile key stranded at the top
level, naming the key and where it belongs. A top-level block is otherwise
silently ignored — note that a stranded `http.verify_ssl: false` reverts to
verification *on*, and a stranded `http.proxy` stops being applied.

```yaml
# v1 (ignored in v2)            # v2
http:                           profiles:
  verify_ssl: false               default:
ui:                                 http:
  theme: quiet                        verify_ssl: false
                                      ui:
                                        theme: quiet
```

## Where the file lives

```text
~/.untaped/config.yml             # default
$UNTAPED_CONFIG                   # override path for one process
```

`UNTAPED_CONFIG=/tmp/scratch.yml untaped-github config list` is handy for trying
things out without touching your real config.

## File layout

Profile-scoped configuration lives under `profiles.<name>`. This includes each
tool's own section plus `http:` and `ui:`, which are per-profile settings in
SDK 2.x. A couple of things live *outside* the `profiles` block:

- `active: <name>` — selects which profile is on top. Optional.
- Tool-owned top-level **state**, such as `workspace.workspaces` or
  `ansible.sources`. State is not user-tunable scalar config; the owning tool
  writes it programmatically (see [State vs. profile fields](#state-vs-profile-fields)).

```yaml
active: prod                       # optional; selects the overlay profile

profiles:
  default:                         # the shared base layer (optional but conventional)
    http:                             # per-profile HTTP behaviour
      verify_ssl: true
    ui:                               # per-profile terminal presentation
      theme: default                  # built-in presets (BUILTIN_THEMES)
      border: rounded                 # rounded, square, ascii, none
      collection_view: table          # table or list for human output
      detail_view: list               # list or table for single-object views
      color_roles:                    # optional Rich style strings for TTY output
        header: bold cyan
        border: green
        key: cyan
        value: white
        success: green
        info: blue
        warning: yellow
        error: red
    ansible:                          # ansible tool's section
      index_path: ~/.untaped/ansible-index.sqlite3
      repo_cache_path: ~/.untaped/ansible-repositories
      ref_scan_default: all
    awx:                              # awx tool's section
      base_url: https://aap.example.com
      token: <token>
      # api_prefix defaults to /api/controller/v2/ for AAP. Upstream AWX
      # users should override it to /api/v2/ here.
    github:                           # github tool's section
      token: ghp_xxx
    jira:                             # jira tool's section
      base_url: https://jira.example.com
      token: <token>
      default_project: OPS
      default_board_id: 42
    workspace:                        # workspace tool's section
      cache_dir: ~/.untaped/repositories
      workspaces_dir: ~/.untaped/workspaces

  prod:                            # overlays default; only declares what differs
    awx:
      base_url: https://aap.prod.example.com
      token: <prod token>

workspace:                         # tool-owned top-level state
  workspaces:
    - name: prod
      path: ~/work/prod
    - name: stage
      path: ~/work/stage
```

Themes are built into the SDK; their presets live in `BUILTIN_THEMES` and are
selected through `ui.theme`:

```yaml
profiles:
  default:
    ui:
      theme: quiet
```

`default` is conventional but not required. When it exists, every other profile
inherits its values; when it doesn't, the active profile is layered alone and
the schema's built-in defaults sit beneath everything.

`ui:` is a per-profile setting in SDK 2.x, but it still only affects human
terminal rendering for semantic collections, details, and status messages. It
does not change `--format json`, `--format yaml`, or `--format raw`; those
structured formats still work if a configured theme preset is unavailable.

`ui.color_roles` values are Rich style strings, such as `bold cyan`, `green`, or
`red`. Supported role names are `header`, `border`, `key`, `value`, `success`,
`info`, `warning`, and `error`. Color roles are emitted only for interactive
terminal output; redirected output stays plain text.

## Resolution order

For any single value, a tool resolves it from these layers, high to low:

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

1. The `--profile <name>` flag. It is **position-independent** —
   `untaped-github --profile stage config list` and
   `untaped-github config list --profile stage` are equivalent — and applies
   for one invocation only.
2. The `UNTAPED_PROFILE` environment variable.
3. The `active:` key in the YAML.
4. Fallback to `default` if it exists, otherwise no overlay layer applies.

Every command, including each tool's domain subcommands, honours `--profile`.

## Managing profiles — `<tool> profile`

The profile command group is built into the SDK and exposed by every tool:

```text
untaped-github profile list                       # list profiles, ✓ marks active
untaped-github profile current                    # print the active profile name (pipe-friendly)
untaped-github profile show <name>                # effective view (default ⤥ named)
untaped-github profile show <name> --raw          # only the keys this profile literally sets (no `default` merge)
untaped-github profile show <name> --show-secrets # reveal token values
untaped-github profile use <name>                 # persist `active: <name>`
untaped-github profile create <name>              # empty profile
untaped-github profile create <name> --copy-from default
untaped-github profile delete <name>              # confirm before deleting
untaped-github profile delete <name> --yes        # non-interactive delete
untaped-github profile rename <old> <new>         # also updates `active:` if it pointed there
```

`profile show` defaults to YAML output; `--format json` wraps the data in an
envelope (`{name, active, raw, data}`) so downstream tools can address metadata
fields with `jq '.data.github.token'`. `show default` looks the same with or
without `--raw` — `default` has no parent layer to merge under itself.

`profile delete` asks for confirmation in an interactive terminal and refuses
non-interactive deletes unless `--yes` is passed. It still refuses to delete the
persisted active profile.

`profile current` prints just the name to stdout (with the source —
`env` / `config` / `fallback` — going to stderr), so you can use it in prompts
or scripts:

```bash
echo "[$(untaped-github profile current 2>/dev/null)] $ "
```

The profile set is shared across tools, so any tool's `profile` group reads and
writes the same `profiles:` block.

## Managing keys — `<tool> config`

```text
untaped-github config list                       # effective values for the active profile
untaped-github config list --profile <name>      # one-off read against a named profile (any position)
untaped-github config list --all-profiles        # one row per (profile, key)
untaped-github config list --show-secrets        # reveal redacted values
untaped-github config list --format json|yaml|table|raw|pipe
untaped-github config list --format raw --columns key --columns value
                                                 # available columns: key, value, default, source, profile
untaped-github config get <key>                  # print one effective scalar value
untaped-github config get <key> --profile <name> # one-off read against a named profile
untaped-github config get <key> --show-secrets   # reveal a secret value
untaped-github config get <key> --format json|yaml|table|raw|pipe
untaped-github config set <key> <value>          # write (the active profile's own section)
untaped-github config set <key> --stdin          # read one value from stdin
untaped-github config set <key> --prompt         # prompt on stderr using the setting type
untaped-github config set <key> <value> --target-profile <name>   # write to a non-active profile
untaped-github config unset <key>                # remove (the active profile)
untaped-github config unset <key> --target-profile <name>
untaped-github config set ui.theme classic       # write a UI preference (active profile)
untaped-github config set ui.theme classic --target-profile default  # shared base
untaped-github config unset ui.theme             # remove a UI preference
```

Bare keys address the **invoking tool's own section**, so for `untaped-github`,
`config set token ghp_xxx` writes `github.token`. The `http.*` and `ui.*` keys
are per-profile settings addressed by their dotted prefix regardless of which
tool you invoke:

- `http.*` — e.g. `untaped-github config set http.verify_ssl false`.
- `ui.theme` (and other `ui.*` scalars) — e.g.
  `untaped-github config set ui.theme quiet`.

Like any profile key, these honour `--profile` (reads) and `--target-profile`
(writes); there is no `--global` flag. To set a shared base value, target
`--target-profile default`.

`config get` defaults to `--format raw` and prints only the selected value, so
`untaped-github config get ui.theme` is safe to use in shell scripts. Its
structured and table formats include `key`, `value`, `default`, `source`, and
`profile` metadata.

Values are parsed as YAML scalars, so
`untaped-github config set http.verify_ssl false` writes a real `false`, not the
string `"false"`. `--prompt` chooses the prompt shape from the schema: secret
fields use hidden input, booleans and constrained choices use a select menu, and
ordinary scalar values use visible text input. For secrets, prefer `--stdin` or
`--prompt` so the value does not land in shell history or process listings:

```bash
printf '%s\n' "$GITHUB_TOKEN" | untaped-github config set token --stdin
untaped-github config set token --prompt
```

Interactive prompts require a TTY on stdin and render prompt UI on stderr, so
stdout stays data-only for commands that participate in shell pipelines. In
non-interactive runs, use `VALUE` or `--stdin` instead of `--prompt`.

Writes target the active profile by default. Writing to a profile that doesn't
exist is rejected — create it first with `<tool> profile create <name>`. The one
exception is `default`, which is auto-created on the first write so a fresh
install doesn't need a setup step. So `untaped-github config set token --stdin`
writes to `default` (or the active profile) automatically, while
`untaped-github config set token --stdin --target-profile prod` requires `prod`
to already exist.

`config get/set/unset` also supports scalar `ui.*` preferences such as
`ui.theme`, `ui.border`, `ui.collection_view`, and `ui.detail_view`. These are
per-profile keys like any other, so they honour `--profile` (reads) and
`--target-profile` (writes), and layer `profiles.default` beneath the active
profile.

### State vs. profile fields

A tool's section in the config file can hold two disjoint kinds of fields:

- **Profile fields** — the user-tunable scalars above (`github.token`,
  `awx.base_url`, …). These layer through profiles and are set with
  `config set`.
- **State fields** — structured, tool-managed data such as `ansible.sources`,
  `ansible.aliases`, or `workspace.workspaces`. These are written
  programmatically by the owning tool's commands and are **not** settable via
  `config set/unset`. Profile and state field names within a section must not
  overlap.

`ui.color_roles` (a nested mapping rather than a scalar) is likewise edited in
the YAML file directly rather than through `config set`.

## Secrets

Tokens, passwords, and API keys are typed as `SecretStr` in each tool's schema.
That has two consequences:

- `<tool> config list` / `get` and `<tool> profile show` redact them as `***`
  by default. Pass `--show-secrets` to reveal.
- Tracebacks won't leak them — `repr(settings)` shows `***` too.

Mark a new credential field as `pydantic.SecretStr` when you add it to a tool's
schema (see [AGENTS.md](../AGENTS.md) — the "hard rules" section) and the
redaction is automatic.

## TLS verification

By default, every HTTP client built through the SDK uses the **OS trust store**
via the `truststore` package. Corporate root CAs that are already installed in
macOS Keychain, Windows certstore, or the Linux system trust will "just work" —
no configuration needed.

Three overrides, all per-profile `http.*` keys:

```bash
untaped-github config set http.ca_bundle /path/to/corp-ca.pem
untaped-github config set http.verify_hostname false  # keep the chain check, skip hostname/SAN
untaped-github config set http.verify_ssl false       # last-resort escape hatch
```

Because `http.*` is per-profile, these can differ between profiles — for
example a `work` profile that pins a corporate CA or relaxes the hostname check
for internal services, while your default profile keeps full verification for
external ones:

```bash
untaped-github config set http.ca_bundle /path/to/corp-ca.pem --target-profile work
```

`http.verify_hostname: false` keeps TLS chain verification on but skips the
hostname/SAN check. This is the right fix for a **self-signed certificate that
is trusted (its CA is in your trust store or pinned via `http.ca_bundle`) but
still rejected by modern Python because its SAN doesn't match the hostname** you
connect to. It is strictly safer than `http.verify_ssl: false` — the chain is
still validated — so prefer it whenever the hostname is the only thing wrong.

`http.verify_ssl: false` disables certificate validation entirely. Only use it
on a network you trust completely: traffic is open to MITM, and any tokens a
tool sends become visible to anyone on-path. Prefer `http.ca_bundle` (or
`http.verify_hostname false` for a hostname mismatch) whenever the certificate
is the real problem.

## Environment-variable overrides

Any single setting can be overridden for one process via an env var named after
its dotted path:

```text
github.token               → UNTAPED_GITHUB__TOKEN
awx.token                  → UNTAPED_AWX__TOKEN
awx.base_url               → UNTAPED_AWX__BASE_URL
ansible.index_path         → UNTAPED_ANSIBLE__INDEX_PATH
ansible.ref_scan_default   → UNTAPED_ANSIBLE__REF_SCAN_DEFAULT
jira.token                 → UNTAPED_JIRA__TOKEN
jira.base_url              → UNTAPED_JIRA__BASE_URL
workspace.workspaces_dir   → UNTAPED_WORKSPACE__WORKSPACES_DIR
ui.theme                   → UNTAPED_UI__THEME
ui.collection_view         → UNTAPED_UI__COLLECTION_VIEW
http.verify_ssl            → UNTAPED_HTTP__VERIFY_SSL
http.verify_hostname       → UNTAPED_HTTP__VERIFY_HOSTNAME
```

Note the `UNTAPED_` prefix, the **double underscore** between the (uppercased)
section and the field, and that each tool reads its own section plus the
per-profile `http`/`ui` keys. These beat the YAML — useful for CI, one-off
invocations, or keeping a token in a secret manager rather than the file.

## Worked example: dev / prod profiles

A common setup: one profile per environment, sharing a base layer for everything
that doesn't change. Each tool writes its own section into the shared profiles,
so you can build up the base from whichever tools you've installed.

```bash
# Start with a shared base (writes to `default`, which auto-creates).
untaped-awx config set base_url https://aap.example.com
printf '%s\n' "$AWX_DEV_TOKEN" | untaped-awx config set token --stdin
untaped-github config set token --prompt
untaped-jira config set base_url https://jira.example.com
untaped-jira config set token --prompt

# Branch a prod profile from default and override what differs.
# `prod` must exist before any --target-profile prod write; create it first.
untaped-awx profile create prod --copy-from default
untaped-awx config set base_url https://aap.prod.example.com --target-profile prod
printf '%s\n' "$AWX_PROD_TOKEN" | untaped-awx config set token --stdin --target-profile prod

# Day-to-day: do nothing — with no `active:` set, `default` is the
# implicit fallback. (Only run `untaped-awx profile use default` if you
# previously persisted a different active profile and want to switch back.)

# One-off prod call without switching profiles globally:
untaped-awx --profile prod ping
```

## Installing tools

There is no PyPI release yet. Install each tool from its Git repository into its
own `uv tool` environment:

```bash
uv tool install git+https://github.com/alexisbeaulieu97/untaped-github.git
uv tool install git+https://github.com/alexisbeaulieu97/untaped-awx.git
```

## See also

- [`untaped-workspace`](https://github.com/alexisbeaulieu97/untaped-workspace)
  — workspace command reference.
- [`untaped-awx`](https://github.com/alexisbeaulieu97/untaped-awx) — AWX command
  reference.
- [`untaped-ansible`](https://github.com/alexisbeaulieu97/untaped-ansible) —
  Ansible dependency graph command reference.
- [`untaped-github`](https://github.com/alexisbeaulieu97/untaped-github) —
  GitHub command reference.
- [`untaped-jira`](https://github.com/alexisbeaulieu97/untaped-jira) — Jira
  command reference.
- [`untaped-apple-health`](https://github.com/alexisbeaulieu97/untaped-apple-health)
  — Apple Health command reference.
- [decisions.md](./decisions.md) — the architecture decisions behind the
  SDK-only direction.
- [AGENTS.md](../AGENTS.md) — the architecture and contribution rules (read this
  if you're extending the SDK or building a tool).
```
