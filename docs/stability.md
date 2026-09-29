# Versioning and stability

`untaped` follows [semantic versioning](https://semver.org/). This page says
what a version number promises: what stays compatible within a major release,
what may change in any release, and how a breaking change reaches you.

This policy applies from 9.0.0 on. Earlier majors were released whenever a
breaking change landed; from 9.0.0 on, breaking changes are collected and
released together.

## Stable within a major release

A minor or patch release never breaks these. Anything new is added
alongside them.

| Contract | Reference |
|---|---|
| Command and flag names, and what a flag means | `untaped COMMAND --help` |
| Exit codes, failure categories and systems | [Exit codes](./reference/exit-codes.md) |
| The `--format pipe` envelope, record kinds and their documented fields | [Pipes and record kinds](./reference/pipes.md) |
| `--format json` and `yaml` records (same fields as the pipe records) | [Pipes and record kinds](./reference/pipes.md) |
| JSON stderr diagnostics | [Stderr diagnostics](./reference/pipes.md#stderr-diagnostics) |
| `config.yml` and `state.yml` settings | [Configuration reference](./reference/config.md) |
| Environment variables | [Environment variables](./reference/environment.md) |
| The provider SDK, versioned separately as `CAPABILITY_API_VERSION` | [Building a capability provider](./plugins.md) |

The pipe envelope is versioned on its own (`"untaped": "1"`) and outlives
application majors.

## Not covered

These may change in any release:

- Human-readable output: table layout and columns shown by default, tree and
  diagram text, colors, and the wording of messages, warnings and hints.
  Scripts read `--format json` or `pipe`, and exit codes, instead.
- Commands and file formats marked experimental (below).
- Anything not documented, including internal modules. Providers import only
  `untaped.capability_api`.

## Experimental

A command or format still being shaped is marked experimental in its
`--help` and its guide. It may change in a minor release, with a changelog
entry that says so. Currently experimental:

- `awx test`: its commands, the suite file format and the
  `awx.test_result` record.

## Breaking changes

- A renamed command or flag keeps working as a hidden, deprecated alias until
  the next major release, and prints a warning naming the new spelling.
- A changed default, a removed command or flag, or a changed record field
  waits for the next major release.
- Breaking changes are collected and released together. Each major release's
  changelog opens with an upgrade section listing them.
