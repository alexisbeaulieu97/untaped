# untaped

A personal DevOps CLI suite. One binary (`untaped`), one sub-command
per domain, designed to pipe into the next.

```text
untaped profile ...      # manage configuration profiles
untaped config ...       # inspect and edit ~/.untaped/config.yml
untaped workspace ...    # manage local git workspaces
untaped awx ...          # Ansible Automation Platform / AWX
untaped github ...       # inspect the authenticated GitHub user
```

Data-emitting commands accept `--format json|yaml|table|raw` and
`--columns`, so their output composes:

```bash
untaped workspace status --all --format raw \
    --columns workspace --columns repo --columns behind \
  | awk '$3 > 0 { print }'
```

## Requirements

Python 3.14 and [uv](https://docs.astral.sh/uv/).

## Install

Clone and run from source:

```bash
git clone https://github.com/alexisbeaulieu97/untaped
cd untaped
uv sync --all-packages
uv run untaped --help
```

Or install an editable `untaped` binary on your `PATH`:

```bash
uv tool install --editable .
```

`uv` resolves every workspace member in place, so local edits across
packages are picked up without reinstalling.

## Documentation

User-facing docs live in [`docs/`](./docs/README.md):

- [Configuration](./docs/configuration.md) — profiles, secrets, TLS.
- [Workspaces](./docs/workspace.md) — `untaped workspace`.
- [AWX / AAP](./docs/awx.md) — `untaped awx`.
- [GitHub](./docs/github.md) — `untaped github`.

## Contributing

See [AGENTS.md](./AGENTS.md) for the architecture, hard rules, and
recipes for extending the project.
