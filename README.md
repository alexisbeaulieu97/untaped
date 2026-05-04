# untaped

A personal DevOps CLI suite. One binary (`untaped`), one sub-command per
domain, designed to pipe into the next.

```text
untaped profile ...      # manage configuration profiles
untaped config ...       # inspect and edit ~/.untaped/config.yml
untaped workspace ...    # manage local git workspaces
untaped awx ...          # Ansible Automation Platform / AWX
untaped github ...       # inspect the authenticated GitHub user
```

## Install

The workspace root **is** the `untaped` package — it owns the binary
and aggregates every domain. Two ways to use it:

### Dev mode (fast, recommended while developing)

```bash
git clone https://github.com/<you>/untaped
cd untaped
uv sync --all-packages
uv run untaped --help
```

`uv run untaped` always runs against the current source tree.

### Global editable install

To get an `untaped` binary on your `PATH` that picks up local edits
across every workspace member:

```bash
uv tool install --editable .
```

`uv` resolves each `workspace = true` source to the local member and
installs all of them editably in the tool environment.

## Documentation

User-facing docs live in [`docs/`](./docs/README.md):

- [Configuration](./docs/configuration.md) — profiles, secrets, TLS.
- [Workspaces](./docs/workspace.md) — `untaped workspace`.
- [AWX / AAP](./docs/awx.md) — `untaped awx`.
- [GitHub](./docs/github.md) — `untaped github`.

## Contributing

See [AGENTS.md](./AGENTS.md) for the architecture, hard rules, and
recipes for extending the project.
