# untaped

**untaped** is a batteries-included CLI for DevOps workflows. `untaped[all]`
gives you seven capabilities that share one config file, the same profiles, and
the same output and piping rules:

- **`workspace`**: task workspaces: one directory of git worktrees across
  repos on a shared branch, archived when the work is pushed.
- **`github`**: repo inventory, GitHub search, and content sweeps across
  hundreds of repos.
- **`jira`**: search, create, update and transition Jira Data Center issues.
- **`awx`**: inspect, change, launch, sync and test AWX/AAP resources.
- **`ansible`**: Ansible role dependency graphs and upstream impact.
- **`recipe`**: plan, preview and apply file changes across many directories.
- **`dotfiles`**: place config files from dotfiles repos, with a policy per
  item per machine (experimental).

Root commands manage the tool itself: `setup`, `config`, `profile`,
`skills`, `doctor` and `capabilities`.

## Install

Python 3.14.1 or newer and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv tool install 'untaped[all]'   # or 'untaped[<name>]' for one tool, e.g. 'untaped[awx]'
untaped --version
untaped doctor
```

## Quick start

```bash
# Store a token in your password store, not in config.yml
untaped auth set github
untaped github whoami

# List an org's repos and check them out into a task workspace
untaped github repos list --org acme --format pipe \
  | untaped workspace create acme --stdin

# Check what is left to push, then archive
untaped workspace status acme
untaped workspace archive acme
```

Most commands take `--format table|json|yaml|raw|pipe` and `--columns`.
`--format pipe` writes typed records that another `untaped` command reads with
`--stdin`. Only data goes to stdout; progress and errors go to stderr.

## Documentation

- [Getting started](./docs/getting-started.md): install, tokens, profiles,
  a first command in each capability, and piping.
- [Configuration](./docs/configuration.md): the config and state files,
  profiles, settings, aliases, TLS and tokens.
- [Scripting](./docs/scripting.md): pipes, structured output and stderr
  diagnostics in scripts and CI.
- [Agent skills](./docs/skills.md): install skills for your agent and keep
  them current.
- [Troubleshooting](./docs/troubleshooting.md): common symptoms and where
  they are explained.
- [Versioning](./docs/versioning.md): what stays stable within a major
  release.
- Reference: [settings](./docs/reference/config.md),
  [output records](./docs/reference/records.md),
  [exit codes](./docs/reference/exit-codes.md) and
  [environment variables](./docs/reference/environment.md).
- [Building a capability provider](./docs/plugins.md): add a capability from
  your own package, with its [conventions](./docs/reference/conventions.md)
  and [how composition works](./docs/composition.md).
- [Building a screen](./docs/screens.md): an interactive terminal UI on the
  SDK's runtime, with the keys, theme and tests it shares.

Each capability's guide: [workspace](./packages/untaped-workspace/README.md),
[github](./packages/untaped-github/README.md), [jira](./packages/untaped-jira/README.md),
[awx](./packages/untaped-awx/README.md), [ansible](./packages/untaped-ansible/README.md),
[recipe](./packages/untaped-recipe/README.md) and
[dotfiles](./packages/untaped-dotfiles/README.md).

## Versioning

`untaped` follows [semantic versioning](https://semver.org/). Commands, flags,
exit codes, record fields, settings and environment variables stay stable
within a major release; some commands are
[experimental](./docs/versioning.md#experimental) and may change in a minor.
[Versioning](./docs/versioning.md) lists what is covered.

## Security

Please report suspected vulnerabilities privately. See
[SECURITY.md](./SECURITY.md).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the local workflow, the
repository layout and releasing.

## License

MIT. See [LICENSE](./LICENSE).
