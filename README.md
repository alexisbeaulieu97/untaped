# untaped

A personal DevOps CLI suite. One binary (`untaped`), one sub-command
per domain, designed to pipe into the next.

```text
untaped config ...       # inspect and edit ~/.untaped/config.yml
untaped plugins ...      # add, sync, list, and diagnose plugins
untaped awx ...          # optional plugin: Ansible Automation Platform / AWX
untaped ansible ...      # optional plugin: Ansible dependency graphs
untaped jira ...         # optional plugin: Jira Data Center tickets
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

Domain commands such as `untaped awx`, `untaped ansible`, `untaped jira`,
`untaped profile`, `untaped github`, and `untaped workspace` are not bundled
in core. Install the standalone plugins when you want those commands. See
[Plugins](./docs/plugins.md) for direct git installs, managed plugin state,
editable source installs, and multi-plugin sync examples.

## Documentation

User-facing docs live in [`docs/`](./docs/README.md):

- [Configuration](./docs/configuration.md) — profiles, secrets, TLS.
- [Plugins](./docs/plugins.md) — installing, syncing, listing, and
  diagnosing optional plugins.
- [Agent Skills](./docs/skills.md) — installing Codex/Claude skills
  contributed by core and plugins.

Plugin docs and command references live in their plugin repos:

- [Workspaces](https://github.com/alexisbeaulieu97/untaped-workspace)
- [AWX / AAP](https://github.com/alexisbeaulieu97/untaped-awx)
- [Ansible](https://github.com/alexisbeaulieu97/untaped-ansible)
- [GitHub](https://github.com/alexisbeaulieu97/untaped-github)
- [Jira](https://github.com/alexisbeaulieu97/untaped-jira)
- [Profile](https://github.com/alexisbeaulieu97/untaped-profile)
- [Themes](https://github.com/alexisbeaulieu97/untaped-themes)

## Contributing

See [AGENTS.md](./AGENTS.md) for the architecture, hard rules, and
recipes for extending the project.
