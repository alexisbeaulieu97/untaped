# Versioning

`untaped` follows [semantic versioning](https://semver.org/), from 9.0.0 on.

## Stable within a major release

A minor or patch release never breaks these. Anything new is added alongside
them. For two installs that disagree about a renamed setting, see
[Configuration](./configuration.md#renamed-settings).
`config.yml` and each plugin's `state.yml` carry an on-disk
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
- `config.yml` settings:
  [configuration reference](./reference/config.md).
- Environment variables:
  [environment variables](./reference/environment.md).
- The modules a plugin imports, which
  [SDK stability](./reference/conventions.md#sdk-stability) lists, for
  [plugin authors](./plugins.md), except the
  [experimental](#experimental) objects listed below.
- Spellings `untaped` writes outside its own process, such as the Git
  credential helper (`untaped git credential`)
  in the worktrees it creates, and the `untaped docs` addresses the stub
  skills name, even if hidden.

The pipe envelope is versioned on its own (`"untaped": "1"`) and outlives
application majors.

## Not covered

These may change in any release:

- Human-readable output: table layout and default columns, tree and diagram
  text, colors, and the wording of messages, warnings and hints. Scripts read
  `--format json` or `pipe`, and exit codes, instead.
- What the [pages of `untaped docs`](./reference/docs.md) say and how they
  lay it out.
- `--format raw` without `--columns`: it prints the first default column,
  which may change. Name the field, as in `--format raw --columns name`.
- Commands and file formats marked experimental (below).
- The rules `untaped lint` applies, and the checks and rows
  `untaped.sdk.testing.check_plugin` returns: a minor release may add or move
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

- `awx test`: its commands, the suite file format, the `awx.suite_case` and
  `awx.case_result` records, and the `awx.test_timeout_seconds` and
  `awx.test_parallel` settings with their environment variables.
- `awx lint`: its options, the `awx.resources` record and the rule ids awx
  declares, `untaped_awx.api` (the snapshot models, the document views, the
  rule constants, `target_of` and `location_of`; `Resource` and `Metadata`
  excepted) and `untaped_awx.testing`.
- `ansible source set`: its `--stdin` and `--from` and the source it saves,
  which holds the git plugin's experimental `RepoSelector`.
- `ansible source list`: its `ansible.source` rows, which carry that selector.
- `ansible source get`: the same row for one source.
- `workspace`: its commands, record kinds, the `workspace.*` settings, and the
  `UNTAPED_*` variables `workspace run` sets.
- `dotfiles`: its commands, the package file format (`.untaped/dotfiles/*.yml`,
  which keeps its own `version:`), its record kinds, the `status.json` and
  `attention` files, and the `dotfiles.*` settings.
- `lint`: its commands, its record kinds, the `lint.ignore` setting,
  `untaped_lint.api` (the `Linter` and `TreeReader` contracts, `Rules`,
  `rule`, `finding`, `lint_report` and `lint_config`), `untaped_lint.testing`, and
  the rule ids lint declares. See [Lint](./lint.md).
- `project`: its commands, the `untaped.recorded_project`, `untaped.project_outcome`
  and `untaped.hold_outcome` records, `.untaped/project.json` and project
  references, `Project`, `current_project`, `project_at`, `known_projects`,
  `init_project` (with what it answers, `Initialized`), `remove_project` and
  `ProjectRemoval` in `untaped.sdk.projects`, `ReleaseAsk`, `MoveAsk`,
  `LetGo`, `Refused`, `HolderView` and `UnknownHolder` in
  `untaped.sdk.storage`, and `make_project`, `release_ask`, `move_ask` and
  `plan_release` in `untaped.sdk.testing`. See [Projects](./projects.md).
- `GitHost` in `untaped_git.api`: the contract a plugin fills to supply
  credentials for a Git host.
- `RepoSource`, `Repo`, `RepoFinder`, `typed_repo` and `looks_like_url` in
  `untaped_git.api`: the contract a plugin fills to list repos by name, its
  `git.repo` record, and the lookup that turns a typed name or URL into one
  repo.
- `clone` in `untaped_git.api`: a working copy the user owns afterwards,
  cloned with untaped's credentials for its host.
- `StepKind`, `StepFields` and `StepResult` in `untaped_dotfiles.api`: the
  contract a plugin fills to add a dotfiles step kind.
- `providers` and `ProviderOffer` in `untaped.sdk.contracts`: every offer to
  fill a contract, with each provider's own record and its kind, or why it
  was refused.
- `Stdin.text` in `untaped.sdk`: stdin's whole text for an argument whose
  `-` the command tells from a value itself.
- `RepoStore.read_private` and `RepoStore.write_private` in `untaped_git.api`:
  a plugin's typed private file in a store repo.
- The fixtures in `untaped_git.testing`, for testing a plugin against the
  repo store, and its fake providers of git's contracts.
- The upstream budget: `capped` and `succeeded` in `untaped.sdk.network`,
  which a call to an upstream reads and reports to so `doctor --online` and a
  cache's automatic refresh fail fast offline.
- `safe_path_segment` in `untaped.sdk.storage`: any text as one
  filesystem-safe path segment.
- `untaped.sdk.testing.assert_surface` and the line format of the
  `surface.txt` it writes.
- `untaped.sdk.testing.deprecation_of`, which tells a linter whether a name
  a plugin imports carries a deprecation mark.
- Writing a plugin's own settings: `PluginHandle.plan_settings`,
  `SettingsPlan` and `SettingsWritten` in `untaped.sdk`.
- Stores: `Dir(layout=)`, `holders`, `RefHolder`, `RefTarget` and the
  `HomeOnly` mark in `untaped.sdk`, `Cache(follows=)`, and
  `assert_schema_pinned` and `write_reference` in `untaped.sdk.testing`. The
  files a store names are stable from the start: users keep them. See
  [Stores](./caches.md#stores).
- Screens: the runtime in `untaped.sdk` (`Screen`, `Binding`, `Cmd`, `Frame`,
  `Footer` and the message classes `KeyPress`,
  `Paste`, `Resize`, `CmdError`, `Quit`, `Cancel`, `Back`, `Interrupt`,
  `NextField`, `PrevField`, `Activate`, `Submit` and `Help`), the components
  (`Field`, `TextInput`, `PathInput`, `SecretInput`, `NumberInput`, `Check`,
  `Select`, `SingleList`,
  `MultiList`, `ListItem`, `Cycle`, `Tabs`, `Tab`, `Buttons`, `Button`,
  `Pressed`, `SearchList`, `Tree`, `TreeRow`, `Tags`, `Viewport`, `Form`,
  `Submitted` and `Panes`), `Terminal.run`, `Terminal.require_screen_terminal`,
  and `untaped.sdk.testing.drive_screen` with `untaped.sdk.testing.ScreenKeys` and
  `untaped.sdk.testing.ScreenRun`. See [Screens](./screens.md).
- Data files: `DataFile` and what a read answers (`Found`, `Absent`,
  `Unreadable` and `OtherVersion`) in `untaped.sdk.storage`, and `keep_value` and `damage` in
  `untaped.sdk.testing`. The files they write are stable from the start:
  users keep them. See
  [Other files your plugin keeps](./caches.md#other-files-your-plugin-keeps).
- Operation locks and places: `Lock` and `Dir` in `untaped.sdk`. See
  [Other files your plugin keeps](./caches.md#other-files-your-plugin-keeps).
- State rows: `StateRows`, `ProjectStateRows` and `RowExists` in
  `untaped.sdk`, and `write_rows` and `read_rows` in `untaped.sdk.testing`. The rows
  they write are stable from the start: users keep them, and a stable
  plugin's row fields are `state` lines. See [Plugin state](./state.md).
- Tree output: `TreeGraph` (with `cycle_edges`), `TreeGraphNode`,
  `TreeGraphEdge` and `tree_text` in `untaped.sdk` (a renderer returns a
  `TreeGraph` and core prints it). The text a
  tree draws is for people and outside the promise anyway.
- Table cells: `TableText` in `untaped.sdk`, a nested model's `table_text()`
  as its cell (lint's locations are its one consumer).
- Server APIs: `ServerApi`, `ApiAdapter`, `ApiMismatch`, `ServerVersion` and
  `Release` in `untaped.sdk.network`, which keep a plugin's commands
  independent of the API version its server runs.
- `HttpFactory` in `untaped.sdk.network`: what `self.ctx.http` and `http_of`
  return, which builds a plugin's HTTP clients and gives untaped's proxy and
  trust, while the HTTP client's place outside core is designed.

## Names plugins use

A plugin built on one major keeps working on the next. The names plugins
import or implement (the modules
[SDK stability](./reference/conventions.md#sdk-stability) lists, each
plugin's `api` and `testing` modules, contracts and their methods, and the
conventions core reads) and a record kind's own name, which users write into
their files, last two majors after their deprecation:

- A deprecation names a removal at least two majors past the release that
  ships it: one deprecated in 12.x goes in 14.0 at the earliest.
- Such a name goes only at the major its deprecation names. A changed meaning
  gets a new name: a changed signature, default or return type is a removal,
  so the old spelling stays, deprecated, beside the new name.
- A parameter goes only with its function. A field of a pydantic model that
  keeps unknown fields (`extra="allow"`) may go when due; one of a model that
  refuses them (`extra="forbid"`) goes only with its model, since a plugin
  still passing it would meet a validation error; a model that ignores them
  can't carry a deprecated field.
- An experimental name may still go in a minor, but never changes shape
  under the same name.
- Only a security fix removes one early, with a Security changelog entry.

A record kind's fields, and everything else on this page, keep the one-major
rule; a model that is both (a record or settings model a plugin's `api`
exports) keeps the longer window. `assert_surface(Plugin, version=...)` checks the same rules for a
plugin's own names, so a plugin that runs it promises plugins built on it
the same.

## Plugins across releases

untaped acts on what a plugin declares and on what happens while it runs;
it never reads a plugin's code. Its declared requirements are checked
before its code runs. What it uses is known two ways:

- a contract method its provider fills is checked against the contract when
  the plugin composes; a deprecated one is kept on the plugin's declaration;
- a deprecated name its code looks up or calls while a command runs warns,
  naming the plugin and its version, once per run per plugin and name. The
  SDK's modules warn at the lookup itself, an import included, so a plugin
  that imports a deprecated name and rarely calls it is seen too. untaped
  records each use in its own state, once per plugin version, name and
  command; a name a plugin's module looks up as it loads is recorded under
  the command whose start loaded it. Recording never fails or slows the
  command: on a home it can't write, or a lock another untaped holds, it
  skips, and a later run records it. Rows of plugin versions no longer
  installed are dropped on the next record.

untaped's own plugins, which release in lockstep with it, are left out of
both. `untaped doctor` lists each as a `plugin-uses` row, one per plugin and
owner: the name, how the plugin uses it (it fills it, or a command uses it),
the release that removes it, and "upgrade untaped-acme once a release stops
using it". `untaped lint` reports each deprecated name a plugin fills or
imports as a `uses-deprecated` warning, and a star import of untaped or
another plugin as an `import-boundary` error, since nobody can tell what it
binds.

Once such a name is gone, a plugin that looks it up while its modules load
is disabled alone (`uses-missing`), a command that reaches it while it runs
stops with an error and exits 4, and a contract provider that reaches it
answers with that error. Each states the facts: the name by the module
plugins import it from, the owner's installed version, what the plugin
requires of it, and both fixes ("upgrade untaped-acme or untaped, or remove
untaped-acme"). The quarantine names the file and line; for a running
command, `--verbose` adds the traceback. That covers a name imported, a
member gone from an object, and a module attribute or submodule looked up.
untaped translates an error only when it shows that a public module or class
of another distribution lacks the name; anything else, such as a name
missing from the plugin's own code or from a library, or a field a model
refuses, keeps its own error and traceback.

## Breaking changes

- A command that goes away with no successor is marked deprecated: it is
  listed by `untaped --deprecated --help`, ends its `--help` with a line
  naming the release that removes it, and warns on every run until then.
- A flag that goes away with no new spelling is marked deprecated the same
  way: `untaped <command> --deprecated --help` lists it under Deprecated
  options with that line, and using it warns until then.
- A renamed command keeps working as a hidden, deprecated alias until the
  major its warning names, and the warning names the new spelling.
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
- Breaking changes are collected into the next major release, whose changelog
  opens with an upgrade section listing them.

`tests/repo/surface.txt` lists what this page promises, one line per
promise, and its test fails a minor release that removes a stable line. A
record kind gets `record` lines: a bare owner line and one per field with
its JSON type, so a field that goes away or changes type fails. A kind that
scripts write, because a command reads it on `--stdin` (`Stdin[R]` names its
model or a base of it) or from a file, also gets an owner line naming its
required fields and a `default=` line per optional field. Models nested in a
kind are listed as `record <plugin>.<ClassName>`. Core's stderr kinds
(`untaped.failure`, `untaped.progress`...) are listed with their `level`; a
plugin's kinds are listed from its declaration.
