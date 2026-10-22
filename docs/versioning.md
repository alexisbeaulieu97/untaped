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
- The `untaped.sdk`, `untaped.contracts` and `untaped.testing` surface, for
  [plugin authors](./plugins.md), except the
  [experimental](#experimental) objects listed below.
- Spellings `untaped` writes outside its own process, such as the Git
  credential helper (`untaped git credential`, with `--profile` before it)
  in the worktrees it creates, even if hidden.

The pipe envelope is versioned on its own (`"untaped": "1"`) and outlives
application majors.

## Not covered

These may change in any release:

- Human-readable output: table layout and default columns, tree and diagram
  text, colors, and the wording of messages, warnings and hints. Scripts read
  `--format json` or `pipe`, and exit codes, instead.
- What [agent pages](./reference/agent-pages.md) say and how they lay it
  out.
- `--format raw` without `--columns`: it prints the first default column,
  which may change. Name the field, as in `--format raw --columns name`.
- Commands and file formats marked experimental (below).
- The rules `untaped lint` and the deprecated
  `untaped.testing.check_conventions` apply, and the checks and rows
  `untaped.testing.check_plugin` returns: a minor release may add or move
  one, so a plugin's lint test can start failing. Mark a source
  line with `# untaped: allow <rule>` to waive a convention rule there, where
  the rule [allows it](./reference/conventions.md#enforcement).
- `untaped_recipe.hook_api`, which is versioned on its own by
  `HOOK_API_VERSION`.
- Anything not documented, including internal modules, except the record
  fields covered above.

## Experimental

A command or format still being shaped is marked experimental. It sits in an
Experimental panel of its parent's `--help`, its own `--help` ends with a line
saying so, and its guide says so. It may change in a minor release, with a
changelog entry that says so. An SDK object (a class or function, not a
command) is marked with `@experimental` in the code and listed here, not in a
`--help` panel. Currently experimental:

- `agents`: `untaped agents`, its path argument and the pages it prints.
  The stub skills `untaped skills install` writes call it, so a release that
  changes how they call it updates them. See
  [Agent pages](./reference/agent-pages.md).
- `awx test`: its commands, the suite file format, the `awx.test_case` and
  `awx.test_result` records, and the `awx.test_timeout_seconds` and
  `awx.test_parallel` settings with their environment variables.
- `awx lint`: its options, the `awx.resources` record and the rule ids awx
  declares, `untaped_awx.api` (the snapshot models, the document views, the
  rule constants, `target_of` and `location_of`; `Resource` and `Metadata`
  excepted) and `untaped_awx.testing`.
- `workspace`: its commands, record kinds, the `workspace.*` settings, and the
  `UNTAPED_*` variables `workspace run` sets.
- `dotfiles`: its commands, the `dotfiles.yml` manifest, its record kinds,
  the `status.json` and `attention` files, and the `dotfiles.*` settings.
- `lint`: its commands, the `lint.rule` and `lint.finding` records, the
  `lint.ignore` setting, `untaped_lint.api` (the `Linter` contract, `Rules`,
  `rule` and `finding`), `untaped_lint.testing`, and the rule ids lint
  declares. See [Lint](./lint.md).
- `project`: its commands, the `untaped.project`, `untaped.project_outcome`
  and `untaped.hold_outcome` records, `.untaped/project.json` and project
  references, and `Project`, `current_project`, `project_at`,
  `known_projects`, `committed_path`, `ItemStore`, `ReleaseAsk`, `MoveAsk`,
  `LetGo`, `Refused`, `HolderView`, `UnknownHolder` and `HolderState` in
  `untaped.sdk`, and `make_project`, `release_ask`, `move_ask` and
  `plan_release` in `untaped.testing`. See [Projects](./projects.md).
- `GitHost` in `untaped_git.api`: the contract a plugin fills to supply
  credentials for a Git host.
- The fixtures in `untaped.testing.git`, for testing a plugin against the
  repo store.
- `untaped.testing.assert_surface` and the line format of the
  `surface.txt` it writes.
- Stores: `Store`, `holders`, `RefHolder`, `RefTarget` and
  `other_layouts_migration` in `untaped.sdk`, the `stores=` and `store=`
  parameters that take one, and `untaped.testing.assert_schema_pinned` and
  `write_reference`. The files a
  store names are stable from the start: users keep them. See
  [Stores](./caches.md#stores).
- Screens: the runtime in `untaped.sdk` (`Screen`, `Binding`, `Cmd`, `Frame`,
  `Footer` and the message classes `Key`, `Paste`, `Resize`, `CmdError`,
  `Quit`, `Cancel`, `Back`, `Interrupt`, `NextField`, `PrevField`, `Activate`,
  `Submit` and `Help`), the components (`Field`, `TextInput`,
  `PathInput`, `SecretInput`, `NumberInput`, `Check`, `Select`, `SingleList`,
  `MultiList`, `ListItem`, `Cycle`, `Tabs`, `Tab`, `Buttons`, `Button`,
  `Pressed`, `SearchList`, `Tree`, `TreeRow`, `Tags`, `Viewport`, `Form`,
  `Submitted` and `Panes`), `UiContext.run`, `UiContext.require_screen_terminal`,
  and `untaped.testing.drive_screen` with `untaped.testing.ScreenKeys` and
  `untaped.testing.ScreenRun`. See [Screens](./screens.md).
- Data files: `read_data_file`, `write_data_file`, `copy_data_file` and
  what a read answers (`Found`, `Absent`, `Unreadable` and `OtherVersion`)
  in `untaped.sdk`, and `keep_value` and `damage` in `untaped.testing`. The
  files they write are stable from the start: users keep them. See
  [Other files your plugin keeps](./caches.md#other-files-your-plugin-keeps).
- State rows: `StateRows`, `ProjectStateRows` and `RowExists` in
  `untaped.sdk`, `StateMap`'s `project=` (it takes the experimental
  `Project`), and `write_rows` and `read_rows` in `untaped.testing`. The rows
  they write are stable from the start: users keep them, and a stable
  plugin's row fields are `state` lines. See [Plugin state](./state.md).
- Tree output: `TreeGraph` (with `cycle_edges`), `TreeGraphNode`,
  `TreeGraphEdge`, `print_tree` and `tree_text` in `untaped.sdk`. The text a
  tree draws is for people and outside the promise anyway.

## Breaking changes

- A command that goes away with no successor is marked deprecated: it is
  listed by `untaped --deprecated --help`, ends its `--help` with a line
  naming the release that removes it, and warns on every run until then.
- A flag that goes away with no new spelling is marked deprecated the same
  way: `untaped <command> --deprecated --help` lists it under Deprecated
  options with that line, and using it warns until then.
- A renamed command or flag keeps working as a hidden, deprecated alias until
  the major its warning names, and the warning names the new spelling.
- A renamed setting's old name, and its `UNTAPED_*` variable, keeps working
  as a renamed key until the major its warning names; see
  [Renamed settings](./configuration.md#renamed-settings).
- A deprecated setting keeps working too, and `untaped config list` lists it
  under `Deprecated` once it is set; experimental settings sit under
  `Experimental`, and `--format json` carries each setting's `stability`.
- Anything else that breaks a stable contract waits for the next major
  release: a changed default, a removed command or flag, a changed positional
  argument, a removed setting, and a renamed or removed environment variable,
  record kind or record field.
  A file a setting's default names may still move in a minor when the
  default's value is unchanged and the move is a lossless rename (a store's
  bare file renamed to its layout's name), with a changelog entry.
- Breaking changes are collected into the next major release, whose changelog
  opens with an upgrade section listing them.

`tests/repo/surface.txt` lists what this page promises, one line per
promise, and its test fails a minor release that removes a stable line.
