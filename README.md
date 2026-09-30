# untaped

**untaped** is a batteries-included CLI for DevOps workflows. One install
gives you six capabilities that share one config file, the same profiles, and
the same output and piping rules:

- **`workspace`**: declare sets of Git repos, clone and sync them, run a
  command in each.
- **`github`**: repo inventory, GitHub search, and content sweeps across
  hundreds of repos.
- **`jira`**: search, create, update and transition Jira Data Center issues.
- **`awx`**: inspect, change, launch, sync and test AWX/AAP resources.
- **`ansible`**: Ansible role dependency graphs and upstream impact.
- **`recipe`**: plan, preview and apply file changes across many directories.

Root commands manage the tool itself: `setup`, `config`, `profile`, `alias`,
`skills`, `doctor` and `capabilities`.

## Install

Python 3.14 and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv tool install untaped
untaped --version
untaped doctor
```

## Quick start

```bash
# Store a token without echoing it
untaped config set github.token --prompt
untaped github whoami

# List an org's repos and clone them into a workspace
untaped workspace init acme
untaped github repos list --org acme --format pipe \
  | untaped workspace repos add acme --stdin --sync

# Run a command in every repo
untaped workspace foreach acme 'git status -s'
```

Most commands take `--format table|json|yaml|raw|pipe` and `--columns`.
`--format pipe` writes typed records that another `untaped` command reads with
`--stdin`. Only data goes to stdout; progress and errors go to stderr.

## Documentation

Start with [Getting started](./docs/getting-started.md). The
[documentation index](./docs/README.md) links the capability guides, the
configuration, pipe, exit-code and environment references, the
[stability policy](./docs/stability.md), [agent skills](./docs/skills.md) and
[provider authoring](./docs/plugins.md).

## Security

Please report suspected vulnerabilities privately. See
[SECURITY.md](./SECURITY.md).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) and [AGENTS.md](./AGENTS.md) for the
local workflow and architecture rules. Releases follow
[docs/release.md](./docs/release.md).

## License

MIT. See [LICENSE](./LICENSE).
