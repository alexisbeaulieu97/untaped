# untaped

A personal DevOps CLI suite. One binary (`untaped`), one sub-command
per domain, designed to pipe into the next.

```text
untaped config ...       # inspect and edit ~/.untaped/config.yml
untaped plugins ...      # add, sync, list, and diagnose plugins
untaped workspace ...    # manage local git workspaces
untaped awx ...          # Ansible Automation Platform / AWX
untaped github ...       # inspect the authenticated GitHub user
untaped profile ...      # optional plugin: manage configuration profiles
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

`uv` resolves the core package and remaining in-repo plugin packages in
place, so local edits are picked up without reinstalling.

`untaped profile` is no longer bundled in core. Install the standalone
plugin when you want profile inventory commands. For an editable source
install, record the plugin first, then sync the tool from this checkout:

```bash
untaped plugins add "untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile.git" --no-sync
untaped plugins sync --tool-spec /path/to/untaped --editable-tool
```

## Documentation

User-facing docs live in [`docs/`](./docs/README.md):

- [Configuration](./docs/configuration.md) — profiles, secrets, TLS.
- [Workspaces](./docs/workspace.md) — `untaped workspace`.
- [AWX / AAP](./docs/awx.md) — `untaped awx`.
- [GitHub](./docs/github.md) — `untaped github`.

## Contributing

See [AGENTS.md](./AGENTS.md) for the architecture, hard rules, and
recipes for extending the project.
