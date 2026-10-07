# Versioning

`untaped` follows [semantic versioning](https://semver.org/), from 9.0.0 on.

## Stable within a major release

A minor or patch release never breaks these. Anything new is added alongside
them. For two installs that disagree about a renamed setting, see
[Configuration](./configuration.md#renamed-settings).
`config.yml` and `state.yml` carry an on-disk
[file format](./configuration.md#file-format) that changes only in a major.

- Command and flag names, positional arguments, and what each means
  (`untaped COMMAND --help`).
- Exit codes, failure categories and systems:
  [exit codes](./reference/exit-codes.md).
- The `--format pipe` envelope, record kinds and their fields (as
  `--format json` or `--columns '?'` show them), and the `--format json` and
  `yaml` records, which have the same fields:
  [output and pipes](./scripting.md#output-and-pipes).
- JSON stderr diagnostics:
  [stderr diagnostics](./scripting.md#stderr-diagnostics).
- `config.yml` and `state.yml` settings:
  [configuration reference](./reference/config.md).
- Environment variables:
  [environment variables](./reference/environment.md).
- The `untaped.sdk` and `untaped.testing` surface, for
  [provider authors](./plugins.md), except the
  [experimental](#experimental) objects listed below.

The pipe envelope is versioned on its own (`"untaped": "1"`) and outlives
application majors.

## Not covered

These may change in any release:

- Human-readable output: table layout and default columns, tree and diagram
  text, colors, and the wording of messages, warnings and hints. Scripts read
  `--format json` or `pipe`, and exit codes, instead.
- `--format raw` without `--columns`: it prints the first default column,
  which may change. Name the field, as in `--format raw --columns name`.
- Commands and file formats marked experimental (below).
- The rules `untaped.testing.check_conventions` applies: a minor release may
  add one, so a plugin's convention test can start failing. Mark a line with
  `# untaped: allow <rule>` to waive that rule there.
- Anything not documented, including internal modules, except the record
  fields covered above.

## Experimental

A command or format still being shaped is marked experimental. It sits in an
Experimental panel of its parent's `--help`, its own `--help` ends with a line
saying so, and its guide says so. It may change in a minor release, with a
changelog entry that says so. An SDK object (a class or function, not a
command) is marked with `@experimental` in the code and listed here, not in a
`--help` panel. Currently experimental:

- `awx test`: its commands, the suite file format, the `awx.test_case` and
  `awx.test_result` records, and the `awx.test_timeout_seconds` and
  `awx.test_parallel` settings with their environment variables.
- `workspace`: its commands, record kinds, the `workspace.*` settings, and the
  `UNTAPED_*` variables `workspace run` sets.
- `dotfiles`: its commands, the `dotfiles.yml` manifest, its record kinds,
  the `status.json` and `attention` files, and the `dotfiles.*` settings.
- Screens: the runtime in `untaped.sdk` (`Screen`, `Binding`, `Cmd`, `Frame`,
  `Footer` and the message classes `Key`, `Paste`, `Resize`, `CmdError`,
  `Quit`, `Cancel`, `Back`, `Interrupt`, `NextField`, `PrevField`, `Activate`
  and `Submit`), `UiContext.run`, and `untaped.testing.drive_screen` with
  `untaped.testing.ScreenKeys` and `untaped.testing.ScreenRun`. See
  [Screens](./screens.md).

## Breaking changes

- A command that goes away with no successor is marked deprecated: it is
  listed by `untaped --deprecated --help`, ends its `--help` with a line saying
  so, and warns on every run until the next major release removes it.
- A renamed command or flag keeps working as a hidden, deprecated alias until
  the next major release, and prints a warning naming the new spelling.
- A renamed setting, and its `UNTAPED_*` variable, keeps working as a
  deprecated key until the next major release, with a warning naming the new
  key.
- A deprecated setting keeps working too, and `untaped config list` lists it
  under `Deprecated` once it is set; experimental settings sit under
  `Experimental`, and `--format json` carries each setting's `stability`.
- Anything else that breaks a stable contract waits for the next major
  release: a changed default, a removed command or flag, a changed positional
  argument, a removed setting, and a renamed or removed environment variable,
  record kind or record field.
- Breaking changes are collected into the next major release, whose changelog
  opens with an upgrade section listing them.
