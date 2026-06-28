# untaped-workspace

`untaped-workspace` is a standalone CLI for managing local git workspaces,
built on the [`untaped`](https://github.com/alexisbeaulieu97/untaped) SDK. It
manages collections of repos through per-workspace `untaped.yml` manifests and
a local registry.

## Install

```bash
uv tool install git+https://github.com/alexisbeaulieu97/untaped-workspace.git
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
untaped-workspace sync [--workspace <ws> | --path <dir> | --all] [--repo <repo>]... [-j N]
                   [--timeout N]
untaped-workspace status [--workspace <ws> | --path <dir> | --all] [--repo <repo>]...
untaped-workspace foreach <cmd> [--workspace <ws> | --path <dir>] [--repo <repo>]...
                         [--timeout N]
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

`sync -j N` runs up to `N` repo sync jobs concurrently, whether syncing one
workspace or `--all`. Missing clones still use the central bare cache plus
`git clone --reference`; existing clones fetch and pull their own working
remotes without touching the cache.

`sync --all` and `status --all` keep going when a registered workspace has a
missing or invalid manifest. They emit a workspace-level `unavailable` row with
`repo=""` and a detail message; malformed registry entries remain hard errors.

`foreach` closes stdin for child commands and applies a 600s per-repo timeout by
default. Interactive commands receive EOF, and commands that exceed the timeout
return `124` unless `--timeout N` is raised.

Prune operations protect local git work before deleting clones. `sync --prune`
skips unsafe orphan clones, while `remove --prune` and `forget --prune` refuse
before mutating manifests, registry state, or files. The local-only safety check
blocks dirty/untracked/staged work, stash entries, and commits or local tags not
reachable from local remote-tracking refs. Because it does not fetch, stale
remote-tracking refs are trusted as the offline safety boundary.

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

## Security

Please report suspected vulnerabilities privately. See
[SECURITY.md](./SECURITY.md).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) and [AGENTS.md](./AGENTS.md) for the
local workflow, architecture rules, and workspace-specific contracts.

## License

MIT. See [LICENSE](./LICENSE).
