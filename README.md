# untaped

A personal DevOps CLI suite. One binary (`untaped`), one sub-command per
domain, each piped into the next.

```
untaped config ...      # inspect and edit ~/.untaped/config.yml
untaped workspace ...   # manage local git workspaces
untaped awx ...         # Ansible Automation Platform / AWX
untaped github ...      # inspect the authenticated GitHub user
```

## Install

The workspace root **is** the `untaped` package — it owns the `untaped`
binary and aggregates every domain. Two ways to use it:

### Dev mode (fast, recommended while developing)

```bash
git clone https://github.com/<you>/untaped
cd untaped
uv sync --all-packages
uv run untaped --help
```

`uv run untaped` always runs against the current source tree.

### Global editable install

To get an `untaped` binary on your `PATH` that picks up local edits across
every workspace member:

```bash
uv tool install --editable .
```

uv resolves each `workspace = true` source to the local member and installs
all of them editably in the tool environment.

## Configure

Settings live in `~/.untaped/config.yml` (override the path with
`UNTAPED_CONFIG`). The schema is **profile-based**: every configurable
value lives under `profiles.<name>`. The `default` profile is required
whenever `profiles:` is non-empty — it's the fallback layer that every
other profile inherits from. Other profiles only declare the keys that
differ.

Resolution order (high → low):

```
UNTAPED_<SECTION>__<FIELD> env var  >  active profile  >  default profile  >  schema default
```

The active profile comes from `UNTAPED_PROFILE` (the root `untaped
--profile <name>` flag is sugar that sets this env var for one process),
then the `active:` key in the YAML, falling back to `default`.

```yaml
active: prod                    # optional; selects the overlay profile

profiles:
  default:
    log_level: INFO
    awx:
      base_url: https://aap.example.com
      token: <token>
      # api_prefix defaults to /api/controller/v2/ for AAP. Upstream AWX
      # users should override it to /api/v2/ here.
    github:
      token: ghp_xxx

  prod:
    awx:
      base_url: https://aap.prod.example.com
      token: <prod token>

workspace:                      # registry: name → path only
  workspaces:
    - name: prod
      path: ~/work/prod
    - name: stage
      path: ~/work/stage
```

Repos belong to a workspace, not the registry: each workspace directory
holds its own `untaped.yml` manifest declaring its repos. The top-level
`workspace.workspaces` block above is the central registry that lets
`untaped workspace path <name>` and `--name <name>` find a workspace by
name.

TLS verification reads the OS trust store by default (so corporate CAs
installed system-wide just work). Override with
`http.ca_bundle: /path/to/corp-ca.pem` for an explicit bundle, or
`http.verify_ssl: false` as a last-resort escape hatch.

Use `untaped config set <key> <value>` to write to the active profile (or
`--profile <name>` to target a specific one), and `untaped profile <…>`
to manage the profile inventory itself.

## Pipe-friendly by design

Every command supports `--format json|yaml|table|raw` and `--columns name`.
`--format raw` is what you pipe into `fzf`, `awk`, or another `untaped`
command. Logs go to stderr; only data hits stdout.

## Contributing

See [AGENTS.md](./AGENTS.md) for the architecture, rules, and recipes.
