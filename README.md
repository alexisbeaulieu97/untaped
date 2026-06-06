# untaped-workspace

`untaped-workspace` is the local git workspace plugin for
[`untaped`](https://github.com/alexisbeaulieu97/untaped). It adds the
`untaped workspace` command group for managing collections of repos through
per-workspace `untaped.yml` manifests and a local registry.

## Install

Install both `untaped` and this plugin from git:

```bash
uv tool install "git+https://github.com/alexisbeaulieu97/untaped.git@v0.1.1" \
  --with "untaped-workspace @ git+https://github.com/alexisbeaulieu97/untaped-workspace.git@v0.1.0" \
  --no-sources \
  --force
```

For managed plugin state, editable source installs, and multi-plugin sync
examples, see the core
[`untaped` plugin docs](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/plugins.md).

This plugin also contributes the `untaped-workspace` agent skill. After the
plugin is installed, use the core
[`untaped` agent skill docs](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/skills.md)
to install it for Codex or Claude.

## Commands

```text
untaped workspace list
untaped workspace show [--workspace <ws> | --path <dir>]
untaped workspace init <name>
untaped workspace adopt <path>
untaped workspace import <source.yml> <dest>
untaped workspace add <url>... [--workspace <ws> | --path <dir>]
untaped workspace remove <repo>... [--workspace <ws> | --path <dir>]
untaped workspace branch set <branch> [--workspace <ws> | --path <dir>]
untaped workspace branch unset [--workspace <ws> | --path <dir>]
untaped workspace branch apply [--workspace <ws> | --path <dir>] [--repo <repo>]...
untaped workspace sync [--workspace <ws> | --path <dir>] [--repo <repo>]...
untaped workspace status [--workspace <ws> | --path <dir>] [--repo <repo>]...
untaped workspace foreach <cmd> [--workspace <ws> | --path <dir>] [--repo <repo>]...
untaped workspace path <name>...
untaped workspace shell-init zsh
untaped workspace edit [--workspace <ws> | --path <dir>]
```

Workspace commands that read registry or profile settings accept
command-local `--profile <name>`, so the selector can stay with the
workspace command:

```bash
untaped workspace init prod --profile work
untaped workspace status --workspace prod --profile work
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
