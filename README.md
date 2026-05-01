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
`UNTAPED_CONFIG`). Individual fields can be overridden per-shell with
`UNTAPED_<SECTION>__<FIELD>` env vars (e.g. `UNTAPED_AWX__TOKEN=…`).

```yaml
log_level: INFO

awx:
  base_url: https://aap.example.com
  token: <token>

github:
  token: ghp_xxx

workspace:
  workspaces:
    - name: prod
      path: ~/work/prod
      repos:
        - https://github.com/org/svc-a.git
        - https://github.com/org/svc-b.git
```

## Pipe-friendly by design

Every command supports `--format json|yaml|table|raw` and `--columns name`.
`--format raw` is what you pipe into `fzf`, `awk`, or another `untaped`
command. Logs go to stderr; only data hits stdout.

## Contributing

See [AGENTS.md](./AGENTS.md) for the architecture, rules, and recipes.
