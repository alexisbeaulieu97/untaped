# untaped

**untaped** is a batteries-included CLI for DevOps workflows. One install
gives you six capabilities that share one config file, the same profiles, and
the same output and piping rules:

- **`workspace`**: task workspaces: one directory of git worktrees across
  repos on a shared branch, archived when the work is pushed.
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
uv tool install 'untaped[all]'   # or 'untaped[<name>]' for one tool, e.g. 'untaped[awx]'
untaped --version
untaped doctor
```

## Quick start

```bash
# Store a token without echoing it
untaped config set github.token --prompt
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
  a first command in each capability, piping, and agent skills.
- [Configuration](./docs/configuration.md): the config and state files,
  profiles, settings, aliases, TLS and tokens.
- [Scripting](./docs/scripting.md): output formats and the pipe envelope,
  record kinds, exit codes and environment variables.
- [Configuration reference](./docs/reference/config.md): every setting, its
  default and its environment variable.
- [Building a capability provider](./docs/plugins.md): add a capability from
  your own package, the command conventions and the skill template.

Each capability's guide: [workspace](./docs/workspace/usage.md),
[github](./docs/github/usage.md), [jira](./docs/jira/usage.md),
[awx](./docs/awx/usage.md), [ansible](./docs/ansible/usage.md) and
[recipe](./docs/recipe/usage.md).

## Versioning

`untaped` follows [semantic versioning](https://semver.org/), from 9.0.0 on.

### Stable within a major release

A minor or patch release never breaks these. Anything new is added alongside
them. Two installs of different major versions sharing one config can
disagree about a renamed setting; give the second its own config file (see
`UNTAPED_CONFIG` in [Configuration](./docs/configuration.md#file-and-layout)).

- Command and flag names, positional arguments, and what each means
  (`untaped COMMAND --help`).
- Exit codes, failure categories and systems:
  [exit codes](./docs/scripting.md#exit-codes).
- The `--format pipe` envelope, record kinds and their documented fields, and
  the `--format json` and `yaml` records, which have the same fields:
  [output and pipes](./docs/scripting.md#output-and-pipes).
- JSON stderr diagnostics:
  [stderr diagnostics](./docs/scripting.md#stderr-diagnostics).
- `config.yml` and `state.yml` settings:
  [configuration reference](./docs/reference/config.md).
- Environment variables:
  [environment variables](./docs/scripting.md#environment-variables).
- The `untaped.sdk` and `untaped.testing` surface, for
  [provider authors](./docs/plugins.md).

The pipe envelope is versioned on its own (`"untaped": "1"`) and outlives
application majors.

### Not covered

These may change in any release:

- Human-readable output: table layout and default columns, tree and diagram
  text, colors, and the wording of messages, warnings and hints. Scripts read
  `--format json` or `pipe`, and exit codes, instead.
- `--format raw` without `--columns`: it prints the first default column,
  which may change. Name the field, as in `--format raw --columns name`.
- Commands and file formats marked experimental (below).
- Anything not documented, including internal modules.

### Experimental

A command or format still being shaped is marked experimental in its `--help`
and its guide. It may change in a minor release, with a changelog entry that
says so. Currently experimental:

- `awx test`: its commands, the suite file format, the `awx.test_case` and
  `awx.test_result` records, and the `awx.test_timeout` and
  `awx.test_parallel` settings with their environment variables.
- `workspace`: its commands, record kinds, the `workspace.*` settings, and the
  `UNTAPED_*` variables `workspace run` sets.

### Breaking changes

- A renamed command or flag keeps working as a hidden, deprecated alias until
  the next major release, and prints a warning naming the new spelling.
- Anything else that breaks a stable contract waits for the next major
  release: a changed default, a removed command or flag, a changed positional
  argument, and a renamed or removed setting, environment variable, record
  kind or record field.
- Breaking changes are collected into the next major release, whose changelog
  opens with an upgrade section listing them.

## Security

Please report suspected vulnerabilities privately. See
[SECURITY.md](./SECURITY.md).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) and [AGENTS.md](./AGENTS.md) for the
local workflow and architecture rules. Releases follow
[docs/release.md](./docs/release.md).

## License

MIT. See [LICENSE](./LICENSE).
