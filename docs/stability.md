# Versioning and stability

`untaped` follows [semantic versioning](https://semver.org/), from 9.0.0 on.

## Stable within a major release

A minor or patch release never breaks these. Anything new is added
alongside them.

| Contract | Reference |
|---|---|
| Command and flag names, positional arguments, and what each means | `untaped COMMAND --help` |
| Exit codes, failure categories and systems | [Exit codes](./reference/exit-codes.md) |
| The `--format pipe` envelope, record kinds and their documented fields | [Pipes and record kinds](./reference/pipes.md) |
| `--format json` and `yaml` records (same fields as the pipe records) | [Pipes and record kinds](./reference/pipes.md) |
| JSON stderr diagnostics | [Stderr diagnostics](./reference/pipes.md#stderr-diagnostics) |
| `config.yml` and `state.yml` settings | [Configuration reference](./reference/config.md) |
| Environment variables | [Environment variables](./reference/environment.md) |
| The provider SDK: `CAPABILITY_API_VERSION` changes major only in a major release | [Building a capability provider](./plugins.md) |

The pipe envelope is versioned on its own (`"untaped": "1"`) and outlives
application majors.

## Not covered

These may change in any release:

- Human-readable output: table layout and columns shown by default, tree and
  diagram text, colors, and the wording of messages, warnings and hints.
  Scripts read `--format json` or `pipe`, and exit codes, instead.
- `--format raw` without `--columns`: it prints the first default column,
  which may change. Name the field, as in `--format raw --columns name`.
- Commands and file formats marked experimental (below).
- Anything not documented, including internal modules. Providers import only
  `untaped.sdk`.

## Experimental

A command or format still being shaped is marked experimental in its
`--help` and its guide. It may change in a minor release, with a changelog
entry that says so. Currently experimental:

- `awx test`: its commands, the suite file format, the `awx.test_case` and
  `awx.test_result` records, and the `awx.test_timeout` and
  `awx.test_parallel` settings with their environment variables.
- `workspace`: its commands, record kinds, the `workspace.*` settings, and the
  `UNTAPED_*` variables `workspace run` sets.

## Breaking changes

- A renamed command or flag keeps working as a hidden, deprecated alias until
  the next major release, and prints a warning naming the new spelling (see
  [conventions](./conventions.md)).
- Anything else that breaks a stable contract waits for the next major
  release: a changed default, a removed command or flag, a changed
  positional argument, and a renamed or removed setting, environment
  variable, record kind or record field.
- Breaking changes are collected into the next major release, whose
  changelog opens with an upgrade section listing them.
