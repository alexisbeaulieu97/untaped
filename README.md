# untaped

A personal DevOps CLI suite. One binary (`untaped`), one sub-command
per domain, designed to pipe into the next.

```text
untaped config ...       # inspect and edit ~/.untaped/config.yml
untaped plugins ...      # add, sync, list, and diagnose plugins
untaped awx ...          # optional plugin: Ansible Automation Platform / AWX
untaped workspace ...    # optional plugin: manage local git workspaces
untaped github ...       # optional plugin: GitHub user/search commands
untaped profile ...      # optional plugin: manage configuration profiles
```

Row-oriented `list`/`get`/`status`-style commands accept
`--format json|yaml|table|raw` and `--columns`, so their output
composes:

```bash
untaped awx job-templates list --format raw --columns name \
  | fzf \
  | untaped awx job-templates get --stdin --format json
```

## Requirements

Python 3.14 and [uv](https://docs.astral.sh/uv/).

## Install

Clone and run from source:

```bash
git clone https://github.com/alexisbeaulieu97/untaped
cd untaped
uv sync
uv run untaped --help
```

Or install an editable `untaped` binary on your `PATH`:

```bash
uv tool install --editable .
```

`uv` resolves the core package in place, so local edits are picked up
without reinstalling.

`untaped awx`, `untaped profile`, `untaped github`, and `untaped workspace`
are not bundled in core. Install the standalone plugins when you want those
commands. For example, with an editable source install, pass the source
checkout as the tool spec when you add a plugin:

```bash
untaped plugins add git+https://github.com/alexisbeaulieu97/untaped-profile.git \
  --tool-spec /path/to/untaped \
  --editable-tool
```

See [Plugins](./docs/plugins.md) for direct git installs, managed plugin
state, and multi-plugin sync examples.

## Documentation

User-facing docs live in [`docs/`](./docs/README.md):

- [Configuration](./docs/configuration.md) — profiles, secrets, TLS.
- [Plugins](./docs/plugins.md) — installing, syncing, listing, and
  diagnosing optional plugins.

Plugin command references live in their plugin repos:

- [Workspaces](https://github.com/alexisbeaulieu97/untaped-workspace)
- [AWX / AAP](https://github.com/alexisbeaulieu97/untaped-awx)
- [GitHub](https://github.com/alexisbeaulieu97/untaped-github)
- [Profile](https://github.com/alexisbeaulieu97/untaped-profile)

## Contributing

See [AGENTS.md](./AGENTS.md) for the architecture, hard rules, and
recipes for extending the project.
