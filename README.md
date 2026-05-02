# untaped

A personal DevOps CLI suite. One binary (`untaped`), one sub-command per
domain, each piped into the next.

```
untaped config ...      # inspect and edit ~/.untaped/config.yml
untaped workspace ...   # manage local git workspaces
untaped awx ...         # Ansible Automation Platform / AWX
untaped github ...      # GitHub search & inspection
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
`UNTAPED_CONFIG`). Configuration is **profile-based**: every tunable value
lives under `profiles.<name>`, and `active: <name>` selects which one is
in force. Resolution order, high → low:

```
UNTAPED_<SECTION>__<FIELD>  >  active profile  >  default profile  >  schema default
```

Individual fields can be overridden per-shell with
`UNTAPED_<SECTION>__<FIELD>` env vars (e.g. `UNTAPED_AWX__TOKEN=…`).
`profiles.default` is required and acts as the bottom layer for every other
profile — name a key once in `default`, override it in `prod`/`stage`/etc.
The active profile can also be set per-process via `UNTAPED_PROFILE=<name>`
or the root flag `untaped --profile <name>`.

```yaml
active: prod                    # which profile is in force

profiles:
  default:                      # required; the fallback layer
    log_level: INFO
    awx:
      base_url: https://aap.example.com
      api_prefix: /api/v2/      # upstream AWX; AAP keeps the default
    github: {}

  prod:                         # only declares what differs from default
    awx:
      token: <aap-token>
    github:
      token: ghp_xxx

# `workspace.workspaces` is *app state* (not user-tunable config) — it
# lives at the top level and is managed by `untaped workspace init/add`.
# Per-workspace repo lists live in each workspace's own `untaped.yml`,
# not here.
workspace:
  workspaces:
    - name: prod
      path: ~/work/prod
```

Use the `untaped config` and `untaped profile` commands to edit the file
safely (validated against the schema, no hand-rolled YAML).
`untaped config set <key> <value>` writes to the active profile by default;
pass `--profile <name>` to target a different one.

## Pipe-friendly by design

Every command supports `--format json|yaml|table|raw` and `--columns name`.
`--format raw` is what you pipe into `fzf`, `awk`, or another `untaped`
command. Logs go to stderr; only data hits stdout.

## Contributing

See [AGENTS.md](./AGENTS.md) for the architecture, rules, and recipes.
