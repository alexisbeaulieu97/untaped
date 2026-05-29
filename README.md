# untaped-workspace

`untaped-workspace` is the local git workspace plugin for
[`untaped`](https://github.com/alexisbeaulieu97/untaped). It adds the
`untaped workspace` command group for managing collections of repos through
per-workspace `untaped.yml` manifests and a local registry.

## Install

Install both `untaped` and this plugin from git:

```bash
uv tool install "git+https://github.com/alexisbeaulieu97/untaped.git" \
  --with "untaped-workspace @ git+https://github.com/alexisbeaulieu97/untaped-workspace.git" \
  --no-sources \
  --force
```

For managed plugin state, editable source installs, and multi-plugin sync
examples, see the core
[`untaped` plugin docs](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/plugins.md).

## Commands

```text
untaped workspace list
untaped workspace show [--name <ws> | --path <dir>]
untaped workspace init <name>
untaped workspace adopt <path>
untaped workspace import <source.yml> <dest>
untaped workspace add <url>...
untaped workspace remove <repo>...
untaped workspace branch set <branch> [--repo <repo>]
untaped workspace branch unset [--repo <repo>]
untaped workspace branch apply [--repo <repo>]
untaped workspace sync
untaped workspace status
untaped workspace foreach <cmd>
untaped workspace path <name>...
untaped workspace shell-init zsh
untaped workspace edit <name>
```

See [docs/workspace.md](./docs/workspace.md) for manifest shape, command
details, registry behavior, and shell helper examples.

## Development

```bash
uv sync
uv run pytest
uv run mypy
uv run ruff check --fix
uv run ruff format
uv run untaped workspace --help
```

See [AGENTS.md](./AGENTS.md) for architecture rules and workspace-specific
contracts.
