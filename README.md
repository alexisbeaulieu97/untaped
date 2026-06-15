# untaped-workspace

`untaped-workspace` is a standalone CLI for managing local git workspaces,
built on the [`untaped`](https://github.com/alexisbeaulieu97/untaped) SDK. It
manages collections of repos through per-workspace `untaped.yml` manifests and
a local registry.

## Install

```bash
uv tool install untaped-workspace
```

`untaped-workspace` also ships the `untaped-workspace` agent skill for Codex
or Claude.

## Commands

```text
untaped-workspace list
untaped-workspace show [--workspace <ws> | --path <dir>]
untaped-workspace init <name>
untaped-workspace adopt <path>
untaped-workspace import <source.yml> <dest>
untaped-workspace add <url>... [--workspace <ws> | --path <dir>]
untaped-workspace remove <repo>... [--workspace <ws> | --path <dir>]
untaped-workspace branch set <branch> [--workspace <ws> | --path <dir>]
untaped-workspace branch unset [--workspace <ws> | --path <dir>]
untaped-workspace branch apply [--workspace <ws> | --path <dir>] [--repo <repo>]...
untaped-workspace sync [--workspace <ws> | --path <dir>] [--repo <repo>]...
untaped-workspace status [--workspace <ws> | --path <dir>] [--repo <repo>]...
untaped-workspace foreach <cmd> [--workspace <ws> | --path <dir>] [--repo <repo>]...
untaped-workspace path <name>...
untaped-workspace shell-init zsh
untaped-workspace edit [--workspace <ws> | --path <dir>]
```

Profile selection uses the built-in `--profile` option, which works in any
token position, so the selector can stay with the command being run:

```bash
untaped-workspace init prod --profile work
untaped-workspace status --workspace prod --profile work
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
uv run untaped-workspace --help
```

See [AGENTS.md](./AGENTS.md) for architecture rules and workspace-specific
contracts.
