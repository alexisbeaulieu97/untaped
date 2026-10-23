# Changelog

## 11.1.0

### Added

- A new `lint` plugin (`untaped[lint]`, experimental): `untaped lint` checks
  the plugins a repository's `pyproject.toml` declares against untaped's
  conventions and each contract they fill, or any records piped in with
  `--stdin`, and reports findings with rule ids such as `lint.error-case`.
  `untaped lint rules` lists every rule, `--ignore <rule>[:<glob>]` and the
  `lint.ignore` setting leave findings out, and it exits 3 on an error finding.
  A plugin adds rules by filling the `Linter` contract from `untaped_lint.api`,
  and tests itself with `untaped_lint.testing.assert_clean(NAME)`. A
  `# untaped: allow <rule>` marker may name the rule as `lint.<rule>` too.
  ([#650](https://github.com/alexisbeaulieu97/untaped/pull/650))
- `untaped plugin list` rows carry each plugin's `distribution`, `version`,
  `package`, `source_dir` and, from a checkout, `tests_dir`; `untaped.sdk`
  exports them as `Plugin` with `installed_plugins()`, and `untaped.testing`
  exports `PluginCandidate`, `allows` and `import_targets` for a plugin's
  checks. ([#650](https://github.com/alexisbeaulieu97/untaped/pull/650))
- A deprecation names the major release that removes it: `deprecated()`,
  `deprecated_alias()`, `Cache()` and the new `Renamed("new", removed_in=…)` for
  `renamed_keys` take `removed_in=<major>`, and its warning, `--help` line,
  `config list` note and `doctor` row say "removed in untaped-acme 3.0" (core's
  own: the version alone). Under `--format json` each warning is an
  `untaped.deprecation` line with its `id`, `removed_in`, `distribution` and
  `replacement`, and `config list` rows carry `removed_in` and `distribution`.
  `untaped.testing.assert_surface(..., version=)` (experimental) fails an
  undated or overdue deprecation, and `untaped lint` reports a hand-typed
  deprecation sentence as `lint.hand-typed-deprecation`. Each kind is in
  [deprecation.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/deprecation.md).
  ([#688](https://github.com/alexisbeaulieu97/untaped/pull/688))
- A plugin can declare a `Store` (experimental): a file or folder it keeps
  under a name per layout, such as `index.v4.sqlite3`, so installs of
  different untaped versions that share `~/.untaped` never open each other's
  data. `untaped doctor` gains a `stores` row naming each store's file, any
  other layout left on disk, what holds each layout and why one is kept, and
  `untaped.testing.assert_schema_pinned` fails a SQLite store's tests when its
  schema changes without the right version bump.
  ([#696](https://github.com/alexisbeaulieu97/untaped/pull/696))
- A new `lint.store-format` rule (experimental, with the `lint` plugin) fails
  where a plugin opens a database under its own directory without a `Store`:
  `sqlite3.connect`, `shelve.open` or `dbm.open` called on a `plugin_dir(...)`
  or `~/.untaped` path. It is an error, so a plugin's
  `untaped_lint.testing.assert_clean` (and deprecated `check_conventions`) test
  may fail after the upgrade; open the file through a `Store` (see
  [caches.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/caches.md#stores)),
  or, when every release of the major reads its layout, waive the call with
  `# untaped: allow store-format`.
  ([#698](https://github.com/alexisbeaulieu97/untaped/pull/698))
- `untaped ansible impact ROLE --uses VAR` lists the places the role's
  dependents define, set or read a variable, one `ansible.variable_use_hit`
  row each, with the file, line, YAML key path and how it is used. Repeat
  `--uses` for any of several names. A dependent whose files were never
  analysed is an error line (exit 1) rather than a missing row, and a role that
  defines none of the names gets a hint.
  ([#699](https://github.com/alexisbeaulieu97/untaped/pull/699))
- Installs of different untaped versions that share `~/.untaped` now clean up
  after each other. `untaped setup register` records an install and the store
  layouts it uses in `~/.untaped/refs/` (`--remove` takes it back); a registered
  install then removes, after a complete refresh, every other layout no
  registered install holds and no running untaped has open, with an
  `untaped.pruned` info line for each that `--quiet` keeps. `untaped setup plan`
  lists registration as its first step (`--check` does not count it), and the
  `untaped setup` screen offers it as its first row. `untaped doctor` gains a
  `references` row listing every registered install. `untaped setup
  migrate-dirs` offers what the cleanup leaves: the layouts nobody holds, as
  `ansible.index-layouts`, and the references of installs that are gone, as
  `untaped.references`. `config migrate` and doctor's unknown-keys row name the
  other registered installs that may still read the keys. For plugins,
  experimental: `Store(keep=)` for the layouts a setting points at, `holders()`
  with its `RefTarget` and `RefHolder`, `other_layouts_migration()`, and
  `untaped.testing.write_reference`. How it works is in
  [configuration.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/configuration.md#several-versions-on-one-machine).
  ([#700](https://github.com/alexisbeaulieu97/untaped/pull/700))
- `untaped plugin schema` describes every record the root commands write,
  `untaped.auth_outcome`, `untaped.doctor_check`, `untaped.setup_step` and the
  skills, token, fix and migration rows among them, as well as
  `untaped.plugin_check` and `untaped.scaffold_outcome`, which it could not find
  before; their fields are unchanged.
  ([#701](https://github.com/alexisbeaulieu97/untaped/pull/701))
- A plugin that declares a record kind under another plugin's name (or
  `untaped.*`) is quarantined with the reason `foreign-kind`, beside
  `duplicate-kind`: a kind starts with the name of the plugin whose package
  declares it. ([#701](https://github.com/alexisbeaulieu97/untaped/pull/701))
- `untaped.testing.invoke_root` takes `input=` for what is piped on stdin.
  ([#701](https://github.com/alexisbeaulieu97/untaped/pull/701))
- Plugins name record kinds by their model: `read_stdin_input` and
  `read_identifiers` take `accepts=(Model, ...)` beside `accept_kinds=`, and
  `untaped.sdk` exports `emit_document` for printing a document (a schema, a
  profile's settings) that is not a row, with no kind and no `pipe` envelope.
  ([#701](https://github.com/alexisbeaulieu97/untaped/pull/701))
- A plugin can deprecate a behaviour, not only a command or a setting:
  `deprecated_behaviour("row-kind", removed_in=3, replacement=...)` listed in
  the new `PluginSpec(deprecations=...)` gets a `behaviour acme.row-kind` line
  in the public surface, and its `notify`, `warn` and `sentence` build the
  warning, named after the plugin's distribution and dated like every other
  deprecation
  ([deprecation.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/deprecation.md)).
  ([#703](https://github.com/alexisbeaulieu97/untaped/pull/703))
- `untaped plugin schema` now describes workspace's seven record kinds,
  `workspace.workspace`, `workspace.status` and workspace's five outcome kinds,
  before any workspace command runs; their fields are unchanged.
  ([#705](https://github.com/alexisbeaulieu97/untaped/pull/705))
- `untaped plugin schema` describes jira's eight record kinds, `jira.issue`,
  `jira.issue_outcome`, `jira.comment`, `jira.user`, `jira.transition`,
  `jira.project`, `jira.board` and `jira.sprint`, before any jira command runs.
  ([#708](https://github.com/alexisbeaulieu97/untaped/pull/708))
- `untaped plugin schema` describes dotfiles' nine record kinds,
  `dotfiles.item`, `dotfiles.repo`, `dotfiles.status`, `dotfiles.status.summary`
  and the apply, sync, remove, item and repo outcomes, which it could not find
  before; their fields are unchanged.
  ([#709](https://github.com/alexisbeaulieu97/untaped/pull/709))
- `untaped lint` (experimental) lints the repository it runs in:
  `.untaped/lint.yml` at the root names the repository's own rule modules
  (`.untaped/lint/*.py`, ids such as `house.no-print`), which plugins read its
  files (`readers:`, through the `TreeReader` contract) and what to ignore, and
  every rule reading the repository's files runs on what git tracks or would
  track. `untaped lint new ID` writes a rule and its failing test, `--config
  PATH` lints the current directory with another lint file, and
  `untaped_lint.testing` has `tree`, `run_rule` and `repo_rule` for a rule's
  tests. ([#704](https://github.com/alexisbeaulieu97/untaped/pull/704))
- `untaped.testing.allowed_names(line)` returns the rule names a source line's
  `# untaped: allow` marker names, bare or as `<key>.<id>`, for a linter that
  resolves them itself.
  ([#704](https://github.com/alexisbeaulieu97/untaped/pull/704))
- `untaped plugin schema` now describes github's eight remaining record kinds,
  `github.user`, `github.code`, `github.issue`, `github.user_hit`,
  `github.sweep_file`, `github.sweep_match`, `github.sync_outcome` and
  `github.worktree`, once the plugin is composed; their fields are unchanged.
  ([#711](https://github.com/alexisbeaulieu97/untaped/pull/711))
- `untaped plugin schema` now describes `ansible.dependency`,
  `ansible.dependent`, `ansible.dependency_match`, `ansible.source`,
  `ansible.source_alias`, `ansible.source_alias_outcome`,
  `ansible.source_outcome` and `ansible.source_status`, which it could not find
  before; their fields are unchanged.
  ([#715](https://github.com/alexisbeaulieu97/untaped/pull/715))
- A plugin's Python API can deprecate a function or one of its parameters with a
  date: `@deprecated(removed_in=3, replacement=new_api)` over the
  `warnings.deprecated` literal type checkers read, and
  `@deprecated_parameters` on a function whose parameter carries
  `Annotated[..., deprecated(removed_in=3)]`, which then raises an
  `UntapedDeprecationWarning` whenever that parameter is passed. `untaped lint`
  checks that the two marks agree (`lint.deprecation-text`, with the literal to
  paste), that no deprecation is undated (`lint.undated-deprecation`) and that
  neither half is forgotten (`lint.untyped-deprecation`,
  `lint.unwarned-parameter`, `lint.mark-on-overload`)
  ([deprecation.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/deprecation.md)).
  ([#707](https://github.com/alexisbeaulieu97/untaped/pull/707))
- `untaped plugin schema` describes recipe's thirteen record kinds, such as
  `recipe.apply_outcome`, `recipe.pack`, `recipe.backup` and `recipe.test`,
  which it could not find before.
  ([#716](https://github.com/alexisbeaulieu97/untaped/pull/716))
- `untaped.sdk.check_columns(Model, columns=columns, fmt=fmt)` refuses an
  unknown `--columns` name before a command acts, for a plugin whose rows follow
  a side effect but are not outcome records.
  ([#719](https://github.com/alexisbeaulieu97/untaped/pull/719))
- `untaped plugin schema` describes every record awx writes: each catalog
  resource (`awx.job_template`, `awx.project` and the other thirteen), `awx.job`
  and `awx.unified_template` list the fields untaped reads "plus any others"
  (`additionalProperties`), since a resource row is AWX's own answer; and the
  rows awx builds, from `awx.event`, `awx.log` and `awx.launch_outcome` to
  `awx.document`, `awx.test_case` and `awx.prune_outcome`, which it could not
  find before. ([#721](https://github.com/alexisbeaulieu97/untaped/pull/721))
- A record that passes a service's answer through can set `extra="allow"`,
  with its extras typed `__pydantic_extra__: dict[str, JsonValue]`: its row
  keeps every field it was given, its typed fields first (its base's ahead of
  a subclass's, so the identity leads) and a declared field the answer left out
  still left out; its schema allows other fields, and an unknown `--columns`
  name on it only warns. `untaped.sdk` exports `NOT_COUNTED`, which marks a
  `Failure` field a command reports on its own, so `emit` does not count the
  row as failed. ([#721](https://github.com/alexisbeaulieu97/untaped/pull/721))
- A plugin can deprecate a flag that has no new spelling: mark the option
  `Annotated[bool, Parameter(group=DEPRECATED_OPTIONS), deprecated(removed_in=3,
  replacement="--limit 1")]`. Any spelling of it (`--dry-run`, `--no-dry-run`,
  `--dry-run=false`, a short flag) warns once per run, `--help` lists it under
  Deprecated options only with `--deprecated`, its help being its `Deprecated:`
  line, and its surface line carries the date. `untaped lint` reports a marked
  option outside that panel or with `show=False`, an unmarked one inside it, or
  a mark on a parameter that is not an option (`lint.deprecated-option-panel`),
  and checks the option's replacement and nesting as a command's
  ([deprecation.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/deprecation.md)).
  ([#717](https://github.com/alexisbeaulieu97/untaped/pull/717))
- `untaped awx lint` (experimental) checks AWX resources against rules: apply
  documents before you apply them (`FILES`, with references resolved in the
  batch and then on the controller, or in the batch only with `--offline`), or
  everything the controller holds, from a snapshot kept for an hour
  (`awx.caches.resources.max_age_seconds`, `--refresh`/`--cached`). Ten rules
  ship, from `awx.missing-reference` and `awx.ignored-launch-field` to
  `awx.playbook-missing` and `awx.plaintext-secret`; a live finding carries
  the `awx … patch` that fixes it. `--kind`, `--organization`, `--filter` and
  `--search` narrow what is reported, and the ids, `--ignore`, `--select`,
  `--strict` and exit codes are `untaped lint`'s. `untaped_awx.api` exports
  the snapshot, its document views and the rules for a repository's or a
  plugin's own rules, and `untaped_awx.testing.resources()` builds one from
  YAML for their tests.
  ([#726](https://github.com/alexisbeaulieu97/untaped/pull/726))
- A repository of AWX apply documents lints them with `untaped lint`, offline
  (experimental, as `awx lint` is): name awx under `readers:` in
  `.untaped/lint.yml` (`awx: ["specs/**/*.yml"]`) and the files those globs
  match are checked by the awx rules, and by any rule reading `awx.resources`,
  as `untaped awx lint FILES --offline` would.
  ([#726](https://github.com/alexisbeaulieu97/untaped/pull/726))
- `untaped_lint.api.lint_config(start)` (experimental) builds the
  configuration `lint()` takes (the profile's `lint.ignore` and the repository
  lint file found from `start`), so a plugin's own lint command applies the
  same ignores as `untaped lint` without reading lint's settings.
  ([#726](https://github.com/alexisbeaulieu97/untaped/pull/726))
- `untaped_lint.api.lint()` (experimental) takes `locations=`: it reports only
  the findings at those locations, written as the table writes them
  (`job_templates/14`, `templates.yml#2`), so a plugin can narrow a run to
  exact resources where two share a target.
  ([#726](https://github.com/alexisbeaulieu97/untaped/pull/726))
- `lint`'s `example-command` rule checks that each `$ untaped ...` line in a
  command docstring's `Examples` section parses
  ([agent-pages.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/agent-pages.md#how-a-plugin-shapes-its-pages)).
  ([#733](https://github.com/alexisbeaulieu97/untaped/pull/733))
- Each `untaped.sdk.ExitCode` member carries a one-line `description` of what
  the code means. ([#733](https://github.com/alexisbeaulieu97/untaped/pull/733))
- `untaped.sdk.nearest_pyproject(start)` returns the nearest `pyproject.toml`
  from a folder up to the farther of its git checkout's root and its project's
  root. ([#733](https://github.com/alexisbeaulieu97/untaped/pull/733))
- `untaped agents` (experimental) prints what this untaped can do as Markdown
  pages for coding agents: `untaped agents` is the map of core and every
  installed plugin, `untaped agents PLUGIN` a plugin's commands, extension
  points and settings, and `untaped agents PLUGIN COMMAND` a command's options
  and examples. The map lists the exit codes and which one wins when a run sees
  several. Each page names the untaped that answered, and says when the
  folder's `pyproject.toml` requires another one
  ([agent-pages.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/agent-pages.md)).
  ([#733](https://github.com/alexisbeaulieu97/untaped/pull/733))
- A plugin can ship guides, pages for what belongs to no single command, on
  `PluginSpec.guides`: `untaped agents PLUGIN GUIDE` (experimental) prints one,
  and `lint` checks them
  ([agent-pages.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/agent-pages.md#guides)).
  ([#735](https://github.com/alexisbeaulieu97/untaped/pull/735))
- `untaped skills install` writes a stub skill for a plugin that declares a
  `SkillAsset` without `source`: a short `SKILL.md` from untaped's template and
  the plugin's description that sends the agent to `untaped agents`, so one
  stub serves every untaped on the machine. Its marker, `.untaped-stub.json`,
  records the untaped and plugin versions that wrote it. untaped's own
  `untaped` skill is now a stub. `skills update --force` also replaces a stub
  someone edited or a newer untaped wrote.
  ([#737](https://github.com/alexisbeaulieu97/untaped/pull/737))
- A plugin marks what it needs set up on its settings model: `NeedsSetup` on an
  `X | None = None` field it can't run without. `untaped setup`, `setup plan`,
  `doctor` and `auth` read the marks; a misplaced mark quarantines the plugin
  ([plugins.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/plugins.md#settings-and-the-plugin-app)).
  ([#760](https://github.com/alexisbeaulieu97/untaped/pull/760))
- Plugins keeping worktrees in the repo store can lock them so pruning keeps a
  moved or unmounted folder's entry (`RepoStore.worktree_add(lock_reason=...)`,
  `RepoStore.lock_worktree`), reconnect one after a move
  (`RepoStore.repair_worktree`) and list their worktrees under a folder
  (`RepoStore.worktrees_under`).
  ([#775](https://github.com/alexisbeaulieu97/untaped/pull/775))
- `untaped project` (experimental) makes any folder a project: `project init`
  writes its `.untaped/project.json`, and the home records it by the folder's
  filesystem identity, so a moved project is followed and a copy gets its own
  id. `untaped project list` shows each recorded project and whether its folder
  is present, missing or unreadable; `project move` moves one or repairs a hand
  move, and `project remove` and `project prune` release what a project holds
  through the plugins that own it, with a preview, then delete only what
  untaped wrote. `untaped doctor`'s `references` row also lists projects and
  warns about a missing one. For plugins, experimental: `Project`,
  `current_project()`, `project_at()`, `known_projects()` and
  `committed_path()` in `untaped.sdk`, which names a plugin's own committed
  files under `.untaped/`, and `untaped.testing.make_project`. How it works:
  [projects.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/projects.md).
  ([#779](https://github.com/alexisbeaulieu97/untaped/pull/779))
- A plugin can let projects hold its items with an `ItemStore` in
  `PluginSpec.stores` (experimental, with `ReleaseAsk`, `MoveAsk`, `LetGo` and
  `Refused` in `untaped.sdk` and test helpers in `untaped.testing`), and
  untaped asks it how to release them when a project goes and to repair them
  when one moves:
  [projects.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/projects.md#holding-a-plugins-items).
  ([#786](https://github.com/alexisbeaulieu97/untaped/pull/786))
- `awx job-templates usage` and `awx workflow-templates usage` draw the
  workflows that run a template as a tree with `--format tree` (add
  `--recursive` for the whole chain), and each row now says what the workflow
  runs (`contains`), for which queried template (`target`) and whether the
  depth limit stopped there (`stopped`), so JSON holds the whole graph.
  ([#794](https://github.com/alexisbeaulieu97/untaped/pull/794))
- Plugins can draw a graph as a tree, the way `ansible graph` does, with
  `TreeGraph`, `print_tree` and `tree_text` in `untaped.sdk` (experimental),
  and offer it as `--format tree` with `TreeFormatOption` and `emit(tree=)`.
  ([#794](https://github.com/alexisbeaulieu97/untaped/pull/794))
- A plugin's cache can keep the data itself: `Cache(..., model=Snapshot)` stores
  what each fill returns as `Filled(value=...)`, with its freshness, in one file
  per version of the value, serves another version's data as outdated when a
  refresh fails or under `--cached`, and keeps a credential's data apart with
  `serve(partition=)`. `read_data_file`, `write_data_file` and `copy_data_file`
  (experimental) give any other file a plugin keeps one stamped, owner-only
  format that tells a damaged file from a missing one
  ([caches.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/caches.md#a-cache-that-keeps-its-data)).
  ([#801](https://github.com/alexisbeaulieu97/untaped/pull/801))
- git's `RepoStore.read_private` and `RepoStore.write_private` (experimental)
  keep a plugin's typed file in a store repo, on the shared data-file format.
  github, workspace and ansible use them. github now keeps when it last fetched
  a repo in the file's stamp, so each repo github's sweep had fetched under 11.0
  is fetched once more after upgrading (only what changed downloads), and a
  metadata file it can't read is refetched instead of leaving the repo
  unscanned. Adopting a repo with `setup migrate-dirs` copies another plugin's
  file whole, with only its freshness cleared.
  ([#806](https://github.com/alexisbeaulieu97/untaped/pull/806))
- Plugins can keep typed state rows with `StateRows` (experimental), listed on
  `PluginSpec.stores`: each row is checked against its model, fields a newer
  untaped added are written back as they were, and a damaged row stops the
  command naming the file, the row and the fix.
  `StateRows.per_project(...).of(project)` keeps a project's rows in its own
  `state.yml`, as `StateMap(project=)` does for strings
  ([state.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/state.md)).
  ([#823](https://github.com/alexisbeaulieu97/untaped/pull/823))
- Tokens leave `config.yml`. `untaped auth login [<plugin> [<name>]]` files a
  token for the server this run uses (its URL and how its certificate is
  checked) in the macOS keychain, a Secret Service or `pass`, or with
  `--token-command` as a command that prints it; a token is then only ever sent
  to that server. `auth list` shows every stored login, `auth forget` deletes
  one (`--id` reaches any) and `auth status` says which source answers each
  token for this run. `UNTAPED_<PLUGIN>__TOKEN` still overrides for one
  process, and a missing token exits 4 naming `auth login`, marked as the
  person's step (`fix_by: user`) so an agent hands it over
  ([tokens](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/configuration.md#tokens)).
  A plugin declares its secrets as `Credential`s on `PluginSpec(credentials=)`,
  for a server held by a `CredentialKey` setting, and reads one with
  `Credential.get`; `untaped.testing` keeps logins in memory in tests
  ([credentials.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/credentials.md)).
  ([#826](https://github.com/alexisbeaulieu97/untaped/pull/826))
- `untaped doctor` has a `caches` row. It notes a cache's keys that hold only
  another version's value, and the `cached/` folder of a plugin not installed
  here. `untaped setup migrate-dirs` now offers to delete that folder; nothing
  deletes it on its own, since another install sharing the home may use it.
  ([#825](https://github.com/alexisbeaulieu97/untaped/pull/825))

### Changed

- Deprecation warnings reword: an undated one says "a future major release"
  instead of "the next major release", a renamed command or flag names both
  spellings in full, with the command path, and a setting ignored for its new
  spelling says when it is removed. `ansible`'s deprecated
  `stale_after_seconds` says "removed in 12.0".
  ([#688](https://github.com/alexisbeaulieu97/untaped/pull/688))
- At `ansible.index_path`'s default, the dependency index is now
  `~/.untaped/plugins/ansible/index.v4.sqlite3`: the first command that opens
  it renames `index.sqlite3` there, with nothing rebuilt. A path you set is used
  as it is. Upgrade every install that shares `~/.untaped` to this release:
  from the rename on, 11.0 no longer shares the index and builds its own.
  ([#696](https://github.com/alexisbeaulieu97/untaped/pull/696))
- The ansible index (schema 5) also stores the variables each indexed repo's
  YAML files and templates define, set and read. The index is upgraded in
  place, and the first refresh after the upgrade analyses each repo's files
  once; later refreshes analyse only files that changed. The index now follows
  the version of the variable analysis and of the dependency parser, so an
  untaped upgrade that changes either refreshes each saved source once.
  ([#699](https://github.com/alexisbeaulieu97/untaped/pull/699))
- The git plugin's `untaped doctor` row about the repo store is now
  `git.repo-store` (was `git.store`, the kind of `untaped git store`'s record).
  ([#701](https://github.com/alexisbeaulieu97/untaped/pull/701))
- `untaped github cache delete --dry-run` and `cache prune --dry-run` show
  `full_name`, `ref`, `status`, `fetched_at`, `path` and `disk_bytes` instead of
  every field; `--columns +name`/`-name` edit those, and JSON is unchanged.
  ([#701](https://github.com/alexisbeaulieu97/untaped/pull/701))
- An unknown `--columns` name is now a usage error (exit 2) listing the valid
  columns, as it already was for other record rows, on the root `auth status`,
  `doctor`, `plugin list`, `plugin check`, `skills list`, `skills status` and
  `setup` commands, on `github cache status`, `delete` and `prune` (before they
  delete anything), on `github sweep`'s repo rows and its `--show files` and
  `--show matches` rows (before it fetches), on `ansible source list`,
  `ansible source get` and `ansible source-alias list`, and on `untaped recipe`
  `list`, `get`, `validate`, `packs list`, `packs get`, `hooks list`,
  `hooks get`, `backups get`, `test` and `hooks run` (before they run a hook or
  write a golden); it used to only warn. Rows reporting a change now only warn,
  since the change already happened, as do `doctor`'s and `setup plan`'s
  tables, which show a command to type, the table of `github sweep`'s repo
  rows, and the tables of `recipe get` and `recipe backups get`, which reshape
  a field.
  An unknown name is also a usage error on awx's `jobs events`, `jobs logs`,
  `workflow-templates nodes`, `job-templates usage`, `workflow-templates usage`,
  `test list` and `test run` rows (`test run` before it launches anything), and
  on `<resource> export`'s documents printed with a `--format` other than `yaml`
  (no `--out`). ([#701](https://github.com/alexisbeaulieu97/untaped/pull/701))
- A plugin that still reads a plain-text `<section>.token` (one that declares
  no `Credential`) warns the same wherever it appears (loading, `config set`,
  `doctor` and `setup plan`): "support for a plain-text acme.token in
  config.yml is deprecated and will be removed in 12.0". Under `--format json`
  it is an `untaped.deprecation` line with the id
  `behaviour untaped.plaintext-token`, and `config set acme.token` names
  `untaped auth set acme` as its replacement instead of a hint.
  ([#703](https://github.com/alexisbeaulieu97/untaped/pull/703))
- `jira issues search` and `jira issues assigned` rows now carry the detail
  fields of `jira.issue` (`reporter`, `labels`, `created_at`, `resolution`,
  `description`, `links`, `comments`) as `null`, the shape `jira issues get`
  writes, instead of leaving them out; `--columns` on them now accepts those
  names. ([#708](https://github.com/alexisbeaulieu97/untaped/pull/708))
- `untaped dotfiles apply` and `dotfiles remove` show the plan they ask about
  as unstyled, aligned text on stderr, now with each path's target, instead of
  a table or the `--format` output; a column empty on every row is always left
  out. The records on stdout are unchanged.
  ([#709](https://github.com/alexisbeaulieu97/untaped/pull/709))
- `ansible source list` and `ansible source get` now write
  `ref_scan_default: null` for a source that does not set it, instead of
  leaving the field out.
  ([#715](https://github.com/alexisbeaulieu97/untaped/pull/715))
- The deprecated stderr helpers now say when they go: `report_error`,
  `report_row_errors`, `UiContext.message`, `.success` and `.progress` warn
  "will be removed in 12.0" and name their replacement, and `echo(err=)` and
  `UiContext.styled(err=)` warn whenever `err=` is passed, `err=False` included,
  since 12.0 removes the parameter. `lint.warnings-warn` no longer lets a
  `warnings.warn(..., DeprecationWarning)` through: a Python API deprecates
  through its mark.
  ([#707](https://github.com/alexisbeaulieu97/untaped/pull/707))
- `untaped recipe apply`'s stderr preview prints plain aligned columns (or
  `key: value` lines with `ui.collection_view: list`) instead of a themed
  table. ([#716](https://github.com/alexisbeaulieu97/untaped/pull/716))
- `untaped recipe` rows of one kind now share one set of fields, written as
  null where a row has no value instead of left out: the `list` commands write
  the fields only their `get` fills, `packs get` writes `manifest_name` (null
  unless the pack was installed under another name, which `packs list` now
  names too, with the pack's `project`), `hooks run` writes the other hook
  kind's fields, and `test` writes `error` on every case. `packs get` adds
  `source`, `rev` and `commit` and lists `path` before `recipes` and `hooks`;
  `get` and `hooks get` add `pack` and `name`; `backups get` writes the
  `backups list` fields (its `created_at` the bundle id's UTC time) plus
  `inputs` and `files`, instead of the bundle's raw metadata.
  ([#716](https://github.com/alexisbeaulieu97/untaped/pull/716))
- `awx test run` names a case failed by a broken inventory source with
  `failure.system` `awx.inventories`, not `awx.inventory`, which is the kind of
  an inventory record.
  ([#721](https://github.com/alexisbeaulieu97/untaped/pull/721))
- `awx jobs events --follow` and `jobs logs --follow` with `--columns '?'` list
  the columns from the job's events or lines without following; on a job with
  none yet, they list the default columns, as they do without `--follow`.
  ([#721](https://github.com/alexisbeaulieu97/untaped/pull/721))
- awx rows write a field they used to leave out as `null`: `launch` and `sync`
  rows write every field (`payload`, `detail` and the execution's, with `error`
  last), `delete` rows `detail`, `add`/`remove` rows `partial` and `detail`, and
  `test run` expectations `node` on a job case's checks.
  ([#721](https://github.com/alexisbeaulieu97/untaped/pull/721))
- awx resource, job and unified-template rows in json and yaml lead with the
  fields untaped reads (`id`, `name`, `type` and the resource's own on a
  resource; `id`, `name`, `status` on a job), then AWX's other fields in its
  order; table, raw and pipe output are unchanged.
  ([#721](https://github.com/alexisbeaulieu97/untaped/pull/721))
- `lint.hand-typed-mark` now reads a hidden option's help too: a flag
  deprecated by typing `Deprecated:` in its help takes the mark instead
  ([deprecation.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/deprecation.md)).
  ([#717](https://github.com/alexisbeaulieu97/untaped/pull/717))
- `untaped setup --only` and `setup plan --only` take section names
  (`--only ui`): `setup` lists just those sections, `setup plan` plans the
  services among them, and `setup` now opens when no service is installed.
  ([#725](https://github.com/alexisbeaulieu97/untaped/pull/725))
- `untaped setup` lists every settings section, not only the services: select
  one to see each setting's value, where it comes from and its help, press enter
  to change it as `config set` would, or `delete` to unset it. Writing back the
  value a field opened with changes nothing, and an empty secret field keeps the
  secret. A service's first row holds every setting its plugin marks as needed,
  each in the field its type takes, and a source for each of its secrets,
  checked online before they are saved as before.
  ([#725](https://github.com/alexisbeaulieu97/untaped/pull/725))
- `untaped setup` edits lists: a list of names opens a list editor (add, edit,
  remove and move items, then write the whole list), a setting that holds a
  command to run is one shell-quoted line, and any other list or map is one line
  of JSON, as `config set` takes it.
  ([#727](https://github.com/alexisbeaulieu97/untaped/pull/727))
- `untaped setup` edits symbol and color overrides (`ui.symbols`,
  `ui.color_roles`) name by name: each name shows its value and whether a
  profile, a variable or the theme sets it, and clearing one lets it inherit
  again. ([#728](https://github.com/alexisbeaulieu97/untaped/pull/728))
- `untaped setup` lists each provider ranking of a plugin's contracts as a row:
  reorder or remove providers in the list editor, or add a ranking as
  `untaped plugin rank` takes it. Rankings are written exactly as `plugin rank`
  writes them, with the same checks.
  ([#729](https://github.com/alexisbeaulieu97/untaped/pull/729))
- `agents` is now a reserved root command name: a third-party plugin named
  `agents` is quarantined, as one named `config` already is. Rename such a
  plugin before upgrading.
  ([#733](https://github.com/alexisbeaulieu97/untaped/pull/733))
- The `untaped.skill` record (`skills list`) gains `kind` (`stub` or `copy`),
  and so does `untaped.installed_skill` (`skills status`). A stub's `source`
  is `untaped:stub/1`, the template's identity, not a path: read `kind` before
  treating `source` as a directory. `skills status` reports two new states,
  `newer` and `edited`.
  ([#737](https://github.com/alexisbeaulieu97/untaped/pull/737))
- `skills.updates` now defaults to `auto` and acts only on the home folder
  (`~/.agents/skills`, `~/.claude/skills`): each run refreshes stubs an older
  untaped wrote, leaves ones a newer untaped wrote or you edited, and converts
  copies from 11.0 to stubs once, keeping the old copies under
  `~/.untaped/backups/skills` (never removed automatically, safe to delete).
  The first command after upgrading rewrites those folders. A project's own
  skills are never written by a run; copies from an older untaped there are
  warned about until `untaped skills update` runs in that project, which
  rewrites a committed `.claude/skills` or `.agents/skills` folder (the diff
  shows copies becoming stubs; `--dry-run` previews it). A setting
  of `warn` or `off` is kept. This changes a default in a minor release.
  ([#737](https://github.com/alexisbeaulieu97/untaped/pull/737))
- `untaped skills install workspace` now writes a stub skill that sends the
  agent to `untaped agents workspace`. What the old skill said now lives in the
  workspace commands' own help and examples, the record fields' descriptions
  (`untaped plugin schema workspace.status`) and a `lifecycle` guide
  (`untaped agents workspace lifecycle`). An installed copy of the old skill
  becomes the stub as `skills.updates` describes.
  ([#746](https://github.com/alexisbeaulieu97/untaped/pull/746))
- `untaped-jira`'s skill is now a stub: `untaped skills install jira` writes
  a short skill that sends the agent to `untaped agents jira`. What the old
  skill said now lives in the commands' own help and in the `writes` guide
  (`untaped agents jira writes`); its advice on when to ask the user is gone,
  since that is the agent's and the user's call.
  ([#748](https://github.com/alexisbeaulieu97/untaped/pull/748))
- `untaped skills install github` now writes a stub skill that sends the agent
  to `untaped agents github`. What the old skill said now lives in the github
  commands' own help and examples, the sweep and cache records' field
  descriptions (`untaped plugin schema github.sweep_repo`) and a `scopes` guide
  (`untaped agents github scopes`). An installed copy of the old skill becomes
  the stub as `skills.updates` describes.
  ([#753](https://github.com/alexisbeaulieu97/untaped/pull/753))
- `untaped-ansible`'s skill is now a stub: `untaped skills install ansible`
  writes a short skill that sends the agent to `untaped agents ansible`. What
  the old skill said now lives in the commands' own help, the record fields'
  descriptions, and two guides: `source-data` (which data answered and when a
  source refreshes) and `resolution` (refs, targets and where a walk stops).
  ([#755](https://github.com/alexisbeaulieu97/untaped/pull/755))
- `untaped-recipe`'s skill is now a stub: `untaped skills install recipe`
  writes a short skill that sends the agent to `untaped agents recipe`. What
  the old skill said now lives in the commands' own help and in four guides
  (`untaped agents recipe inputs`, `trust`, `recipe-files` and
  `writing-hooks`); its advice on when to ask the user is gone, since that is
  the agent's and the user's call.
  ([#754](https://github.com/alexisbeaulieu97/untaped/pull/754))
- `untaped-dotfiles`'s skill is now a stub: `untaped skills install dotfiles`
  writes a short skill that sends the agent to `untaped agents dotfiles`. What
  the old skill said now lives in the commands' own help and in two guides,
  `untaped agents dotfiles manifest` and `untaped agents dotfiles policies`;
  its advice on when to ask the user is gone, since that is the agent's and
  the user's call.
  ([#756](https://github.com/alexisbeaulieu97/untaped/pull/756))
- `untaped doctor` shows one `setup` row per service in place of the
  `awx.connection`, `github.connection` and `jira.connection` rows: `not set up`
  for a service you don't use, what is still missing with the command that sets
  it, or where its token comes from. `doctor --online` contacts a service only
  once all of it is set up, and a failure that is not a rejected token or an
  untrusted certificate shows the settings it was set up with. `setup` and
  `setup plan` read what each plugin marks, so a step's placeholder is the
  setting's name, as in `<URL>`, and `setup plan` no longer lists a
  `github.base_url` step: GitHub's default URL needs nothing set up.
  ([#760](https://github.com/alexisbeaulieu97/untaped/pull/760))
- The `untaped-git` skill is now a stub that sends the agent to
  `untaped agents git`; the facts its copy held (authentication failures,
  releasing repos, the store's rules) are on `untaped agents git hosts`,
  `untaped agents git store` and `untaped agents git credential` and in
  `--help`. ([#747](https://github.com/alexisbeaulieu97/untaped/pull/747))
- `untaped skills install --scope local` and `untaped awx test` find the git
  checkout without running git, so they no longer fail where git isn't
  installed. ([#774](https://github.com/alexisbeaulieu97/untaped/pull/774))
- `untaped-awx`'s skill is now a stub: `untaped skills install awx` writes a
  short skill that sends the agent to `untaped agents awx`. What the old skill
  said now lives in the commands' own help, the record fields' descriptions,
  and guides on selecting and changing resources, export and apply documents,
  workflow node graphs, launching and linting, and on `awx test`: writing
  suites (`test-suites`, `test-cases`, `test-examples`), reading a run
  (`test-results`), temporary copies at a git ref (`source-ref`) and the AWX
  user an agent tests as (`agent-profile`).
  ([#765](https://github.com/alexisbeaulieu97/untaped/pull/765))
- `untaped.sdk.file_lock` is re-entrant within a thread: taking a lock the same
  thread already holds no longer waits for itself and fails as busy. A write to
  `config.yml` or `state.yml` (`StateCollection`, `StateMap`, config writes)
  started inside another write to the same file now fails at once instead of
  being lost when the outer write saves.
  ([#779](https://github.com/alexisbeaulieu97/untaped/pull/779))
- `project` is now a reserved root command name and `state` a reserved plugin
  name: a third-party plugin named either is quarantined, as one named `config`
  already is. Rename such a plugin before upgrading.
  ([#779](https://github.com/alexisbeaulieu97/untaped/pull/779))
- `ansible graph`'s tree draws a repeated subtree differently: its later
  occurrence shows the repo on its own line, with one `… see [n]` line under it
  pointing back to where it is drawn in full, instead of `<repo> see [n]`.
  ([#794](https://github.com/alexisbeaulieu97/untaped/pull/794))
- `awx <templates> usage` lists a workflow template that runs itself through
  other workflows as a row of its own usage, and warns about each cycle by
  naming both workflows, including two workflows that run each other.
  ([#794](https://github.com/alexisbeaulieu97/untaped/pull/794))
- A cache now keeps each key's data and freshness together under
  `plugins/<plugin>/cached/`, so most caches refill once after upgrading, and a
  registered install removes 11.0's `caches/` folders by itself, with one
  `untaped.pruned` line each; the ansible index keeps its data and only its
  freshness is read again from it. Each file is readable by its owner only.
  Profiles whose plugin settings read the same now share its cached data.
  ([#801](https://github.com/alexisbeaulieu97/untaped/pull/801))
- Recipe backup metadata a newer untaped wrote is reported as such instead of
  as damaged; backups 11.0 made still list, show and restore.
  ([#804](https://github.com/alexisbeaulieu97/untaped/pull/804))
- `github sweep` and `github cache sync` keep their repos' freshness like every
  other cache: a sweep that scans a repo whose refresh failed, or an old copy
  under `--cached`, says so in one `untaped.stale` line per repo, which replaces
  the "refresh failed for N repos" and "stale REPO" warnings, and a repo whose
  refresh failed is tried again after five minutes, or at once with `--refresh`.
  ([#807](https://github.com/alexisbeaulieu97/untaped/pull/807))
- An API token is only ever sent to the server it is set up for: a request or
  redirect to another server is refused.
  ([#821](https://github.com/alexisbeaulieu97/untaped/pull/821))
- Token and password-store commands run in the config file's folder instead of
  the current one, and a `.` on `PATH` finds them, and `git`, there too. A
  token command whose program is a relative path (`./get-token`) is refused:
  give a bare name found on `PATH` or an absolute path, and give a file the
  command reads as an absolute path too.
  ([#821](https://github.com/alexisbeaulieu97/untaped/pull/821))
- `--verbose` lines mask a URL's whole user name and password and any
  `Authorization` header value.
  ([#821](https://github.com/alexisbeaulieu97/untaped/pull/821))
- `awx`, `jira` and `github` take `server: {url, verify_tls}`: `base_url` is
  now `server.url`, and `verify_tls` is that server's certificate check
  (`true`, a CA file, or `false`). The old key and its variable still read,
  with a warning, and `untaped config migrate` renames them; its value must now
  be a URL. `GithubSettings(base_url=)` still works, with a warning.
  `untaped config migrate` also files each profile's `token` and
  `token_command` as stored logins and removes them from `config.yml`, after a
  confirmation (`--yes` off a terminal); a line it can't move stays, listed as
  kept. git's credential helper line in untaped's worktrees now carries a nonce
  for its worktree, so a call by hand gets nothing.
  ([#826](https://github.com/alexisbeaulieu97/untaped/pull/826))
- Saving a service's form in `untaped setup` writes only the settings you
  changed, in one write, and writes nothing when one of them changed in the
  config file since the form opened.
  ([#827](https://github.com/alexisbeaulieu97/untaped/pull/827))

### Deprecated

- `untaped plugin check` and `untaped.testing.check_conventions` are deprecated
  in favour of `untaped lint` (experimental) and
  `untaped_lint.testing.assert_clean`, and are removed in 12.0. Both still print
  or fail on the same rows, but run the rules that read a plugin's files only
  when `untaped[lint]` is installed, and warn when it is not. A plugin's
  convention test calls `assert_clean(NAME)` with `untaped[lint]>=11.1` in its
  `dev` dependencies, and its CI runs `untaped lint` in the plugin's directory,
  which exits 3 on an error finding where `untaped plugin check NAME` exited 1.
  ([#650](https://github.com/alexisbeaulieu97/untaped/pull/650))
- A plugin whose settings have `base_url` and `token` fields (or a
  `token_sources` attribute) but no `NeedsSetup` mark is deprecated and removed
  in 12.0. It still works, warns once per run where `setup`, `setup plan`,
  `doctor` or `auth` reads it, and `untaped lint` reports it as a warning
  (`lint.unmarked-service`).
  ([#760](https://github.com/alexisbeaulieu97/untaped/pull/760))
- Calling `connected_client` without `base_url=`, which reads `base_url` and
  `token` off the settings by name, and its `required=`, `base_url_field=` and
  `bearer_token_field=` parameters are deprecated and removed in 12.0; pass the
  values (`base_url=`, `bearer=`, `verify=`) instead. Both still work and
  warn; passing both kinds is an error.
  ([#766](https://github.com/alexisbeaulieu97/untaped/pull/766))
- The SDK's `project_root()` is now `checkout_root()`, which returns `None`
  outside a git checkout instead of the start folder, so the caller picks its
  fallback. `project_root()` keeps its answer, warns, and is removed in 12.0:
  use `checkout_root(start) or start`.
  ([#774](https://github.com/alexisbeaulieu97/untaped/pull/774))
- `SkillAsset(source=...)`, a plugin's skill shipped as a copied folder, is
  deprecated and removed in 12.0: passing it warns. Leave `source` out to ship
  a stub, and move the folder's facts into command docstrings, field
  descriptions and guides, which `untaped agents` prints
  ([plugin-skills.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/plugin-skills.md)).
  ([#782](https://github.com/alexisbeaulieu97/untaped/pull/782))
- `github.sweep.max_age_seconds` and `UNTAPED_GITHUB__SWEEP__MAX_AGE_SECONDS`
  are deprecated in favour of `github.caches.sweep.max_age_seconds` and still
  read, with a warning, until 12.0; `untaped config migrate` moves the key in
  `config.yml`. ([#807](https://github.com/alexisbeaulieu97/untaped/pull/807))
- A token in `<plugin>.token` or `<plugin>.token_command`, `auth set`, `auth
  unset` and `auth migrate`, the SDK's `TokenCommand` and `TokenSources`,
  `GithubSettings.base_url` and `GithubSettings.token`, root
  `http.verify_ssl` (read as each server's `verify_tls: false` until then) and
  `http.verify_hostname`, and a git credential helper line without `--nonce`
  are deprecated and removed in 12.0. `UNTAPED_<PLUGIN>__TOKEN` stays.
  ([#826](https://github.com/alexisbeaulieu97/untaped/pull/826))
- untaped 12.0 starts from a fresh home: it reads nothing an 11.x install
  wrote under `~/.untaped`, and on finding an 11.x home it stops with one line
  saying to delete it or stay on 11.1. 11.1 adds no step toward 12.0, so
  nothing here needs doing before then; what 11.1 deprecates is simply absent
  from 12.0. ([#834](https://github.com/alexisbeaulieu97/untaped/pull/834))
- For plugin authors: 12.0 replaces `PluginSpec` with a class each plugin
  defines, and its commands become classes too; Cyclopts leaves `untaped.sdk`.
  `create_app`, `@writes`, the `*Option` aliases, `get_config_section`,
  `read_identifiers`, `emit` from command code, `SkillAsset`, `provides=`,
  `guides=`, `table_columns`, `refresh_option`, `report_errors`,
  `plugin_dir(SPEC)` and plain-string `kind=` and `system=` arguments go with
  it. 11.1 prints no warning for these, since our own plugins still use them; a
  plugin built on `PluginSpec` stops loading in 12.0.
  ([#834](https://github.com/alexisbeaulieu97/untaped/pull/834))

### Fixed

- Commands no longer warn about installed skills that no plugin installed here
  ships, such as plugin skills under `uvx untaped` without extras; `untaped
  doctor` and `untaped skills status` still report them.
  ([#692](https://github.com/alexisbeaulieu97/untaped/pull/692))
- A development checkout and an installed untaped of another version no longer
  share the ansible dependency index: each layout is its own file, and the old
  one is removed once no registered install uses it.
  ([#636](https://github.com/alexisbeaulieu97/untaped/issues/636))
- An unknown `--columns` name on rows reporting a change, such as those of
  `config set`, `profile create` or `awx job-templates copy`, now only warns
  and the rows still print; the command made the change, then exited 2 printing
  none of its rows. `dotfiles subscribe`, `github cache worktree` and
  `workspace status --fetch` now refuse the name before they clone, check out or
  fetch anything, not after.
  ([#719](https://github.com/alexisbeaulieu97/untaped/pull/719))
- `untaped awx export` reads a management job's schedule (AWX's stock cleanup
  schedules) instead of failing on its parent: the parent is written as
  `{kind: SystemJobTemplate, name: ...}`, global, and `apply` creates the
  schedule under that system job template again.
  ([#726](https://github.com/alexisbeaulieu97/untaped/pull/726))
- A plugin whose requirement on untaped has an environment marker that cannot
  be compared, such as `python_version ~= "3"`, is quarantined as bad metadata
  instead of stopping every command with a traceback.
  ([#733](https://github.com/alexisbeaulieu97/untaped/pull/733))
- `untaped jira issues patch` sends its requests once instead of resending them
  after Jira's 429 and 503 answers or a dropped connection, so an `update` add
  (a comment, a label) from `--fields-file` can no longer be applied twice.
  ([#751](https://github.com/alexisbeaulieu97/untaped/pull/751))
- `untaped dotfiles apply` fetches every repo the selected items read from, so
  it places a managed clone's current `copy` and `merge` files and reads its
  current manifest instead of what the last `sync` fetched.
  ([#761](https://github.com/alexisbeaulieu97/untaped/pull/761))
- `untaped plugin schema` and `untaped awx schema` describe the record and
  suite fields they showed bare before: core's records, awx's resources, test
  results and suite checks, lint's findings, rules and trees, and the `error`
  of a failed row.
  ([#763](https://github.com/alexisbeaulieu97/untaped/pull/763))
- `untaped setup plan` shows a set `SecretStr` setting as `set`, and a `doctor
  --online` failure says it `is set`, rather than printing `***`.
  ([#766](https://github.com/alexisbeaulieu97/untaped/pull/766))
- Two `untaped recipe` commands that change the pack library at once (two
  `packs add`, or `packs add` while `sync` records a commit) no longer lose a
  row of `packs.toml`: library writes now take a lock, and a command that waits
  too long on another fails as busy with a hint to retry.
  ([#777](https://github.com/alexisbeaulieu97/untaped/pull/777))
- `untaped recipe packs sync` no longer undoes a pack that another command
  replaced or removed while sync was fetching it: that pack gets a `failed` row
  and stays as the other command left it.
  ([#781](https://github.com/alexisbeaulieu97/untaped/pull/781))
- `awx <templates> usage --by-id` with an id that doesn't exist fails with
  `not found` (exit 1) instead of printing no rows.
  ([#794](https://github.com/alexisbeaulieu97/untaped/pull/794))
- A full disk while writing `state.yml` reports an error, and leaves the file as
  it was, instead of a traceback.
  ([#823](https://github.com/alexisbeaulieu97/untaped/pull/823))

## 11.0.0

### Upgrading

- Upgrade from 10.x in this order. First copy your `shell.aliases` (below) and,
  to keep the repo store somewhere other than `~/.untaped/plugins/git/store`,
  set `git.store_dir` to a new directory, not an old cache root. Then run
  `untaped setup migrate-dirs`: while the old settings still name them, it moves
  github's and workspace's repositories (custom cache directories included) into
  the store and ansible's, recipe's and dotfiles' data under
  `~/.untaped/plugins/`, and deletes the old caches and github's inventory file;
  until it runs, ansible starts an empty index and recipe lists no installed
  packs. Last, run `untaped config migrate`, which renames old keys and deletes
  retired ones, printing the values they held, and rename old `UNTAPED_*`
  variables yourself (below).
  ([#589](https://github.com/alexisbeaulieu97/untaped/pull/589),
  [#591](https://github.com/alexisbeaulieu97/untaped/pull/591),
  [#592](https://github.com/alexisbeaulieu97/untaped/pull/592),
  [#593](https://github.com/alexisbeaulieu97/untaped/pull/593),
  [#654](https://github.com/alexisbeaulieu97/untaped/pull/654),
  [#657](https://github.com/alexisbeaulieu97/untaped/pull/657))
- A plugin's `Record` types drop the field features the definition now rejects
  (a secret stays out of records), define nested types before the record, and
  use the plugin's name as the first segment of their kinds.
  ([#577](https://github.com/alexisbeaulieu97/untaped/pull/577))
- A script that compares timestamps as strings, or expects them to the second,
  parses them as RFC 3339 instead.
  ([#577](https://github.com/alexisbeaulieu97/untaped/pull/577))
- If ssh prompted you while `untaped` ran Git, add the key to `ssh-agent` and
  the host to `known_hosts` (a plain `git fetch` of that remote asks to trust
  it) before running `untaped`. If an HTTPS remote asked for a password through
  `core.askPass` or `SSH_ASKPASS`, store it in a Git credential helper or set
  `GIT_ASKPASS`. ([#576](https://github.com/alexisbeaulieu97/untaped/pull/576))
- A plugin whose settings or state declared a field named `extensions` or
  `caches` renames it and lists the old name in `renamed_keys`. A plugin named
  `caches` takes another name.
  ([#585](https://github.com/alexisbeaulieu97/untaped/pull/585))
- A plugin that read `HttpSettings.timeout` or passed `timeout=` reads and
  passes `timeout_seconds`; `HttpSettings(timeout=…)` is now ignored like any
  unknown field. ([#589](https://github.com/alexisbeaulieu97/untaped/pull/589))
- `untaped config migrate` renames the old keys in `config.yml` but not the
  `UNTAPED_*` variables, which 11.0 no longer reads: rename each one yourself.
  `untaped doctor` names every retired variable still set and what replaces
  it, and the configuration reference's "Renamed settings" table lists both
  spellings. ([#589](https://github.com/alexisbeaulieu97/untaped/pull/589))
- Scripts that ran `untaped capabilities` run `untaped plugin list` and read
  kind `untaped.plugin`. A third-party plugin replaces its entry point
  `<name> = "untaped_<name>:provider"` in the `untaped.capabilities` group
  with `<name> = "untaped_<name>:SPEC"` in `untaped.plugins`, and may delete
  `provider()`. It renames `CapabilitySpec` to `PluginSpec` and
  `CapabilityContext` to `PluginContext` (a doctor check reads only its
  `settings`), drops `CapabilityProvider`, passes `settings=` and `state=`
  instead of `profile_model=` and `state_model=`, and drops `config_section`:
  one name serves as distribution `untaped-<name>`, import package
  `untaped_<name>`, entry point, `PluginSpec` name, config section and
  command group. A plugin whose old `config_section` differed from its name
  moves that section in `config.yml` and `state.yml` and renames its
  `UNTAPED_<SECTION>__*` variables. Tests that called `provider_candidate`
  call `plugin_candidate`.
  ([#554](https://github.com/alexisbeaulieu97/untaped/pull/554),
  [#588](https://github.com/alexisbeaulieu97/untaped/pull/588))
- A script that checked `status == "removed"` after `cache delete` also accepts
  `released`: github no longer holds that repo, though another plugin still
  does. Read worktree and repo paths from the rows rather than building them
  from `github.cache_dir`.
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- Drop `--depth` from `github sweep` and `github cache sync` calls and scripts;
  passing it now exits 2 as an unknown option.
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- In scripts and `jq` filters over github's repo rows, read `.full_name` where
  you read `.repo`, and `.html_url` where you read `.url`; match the kind
  `github.repo` where you matched `github.repo_hit`.
  ([#593](https://github.com/alexisbeaulieu97/untaped/pull/593))
- Install untaped-github beside untaped-workspace (`untaped[workspace,github]`,
  or `untaped[all]`) to keep looking repos up by name. Move a
  `workspace.protocol` value to `github.git_protocol` (`untaped config migrate`
  names it); repos added before 11.0 keep the URLs they were added with, and
  `untaped workspace repos resolve NAME` updates those added since. Replace
  local paths given to `--repo` with the repo's clone URL, and pipe records
  with their kind (`--format pipe`). Workspaces already holding a local-path
  or `http://` repo still list, run, archive and remove; to add such a repo
  again, give its clone URL.
  ([#593](https://github.com/alexisbeaulieu97/untaped/pull/593))
- If you set `ansible.git_clone_protocol: ssh`, set `github.git_protocol: ssh`
  before `untaped config migrate` deletes the four ansible settings.
  ([#592](https://github.com/alexisbeaulieu97/untaped/pull/592))
- Keep a plugin's repositories with `RepoStore.for_url(url, plugin=SPEC)` from
  `untaped_git.api` (depend on `untaped-git`), which asks the host's `GitHost`
  for credentials; `repo_url_parts` moved there too. Pass a credential header to
  `run_git` as `auth_config={"http.<origin>/.extraHeader": header}`.
  ([#592](https://github.com/alexisbeaulieu97/untaped/pull/592))
- A script that treated a run printing `error:` lines as a success because it
  exited 0 now sees exit 1 or the failure's own code; a plugin whose output
  should not count prints it with `ctx.diag.relay(...)` or as a warning.
  ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
- Replace `ErrorInfo` with `Failure`, and `error=note_failure(exc, message=m)`
  with `error=Failure.from_exception(exc, message=m)`; emit the row and its
  failure counts. ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
- Replace `update(f"{d}/{t}", fraction=d / t)` with `update(done=d, total=t)`
  and `new_phase=True` with `phase="<name>"`; drop `write=` from
  `report_error`; report a failure inside a task with
  `ctx.diag.failure(exc, item=…)`; default an inner layer's handle to
  `NO_PROGRESS`. ([#621](https://github.com/alexisbeaulieu97/untaped/pull/621))
- The first 11.0 run takes each saved source's freshness from its last
  `source refresh`, so nothing fresh is refreshed again. After that, scripts
  that ran `impact` and friends on purpose against old data pass `--cached`.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- Rename `UNTAPED_ANSIBLE__STALE_AFTER_SECONDS` to
  `UNTAPED_ANSIBLE__CACHES__INDEX__MAX_AGE_SECONDS`; `untaped config migrate`
  moves `ansible.stale_after_seconds` in `config.yml`.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- Scripts that pass `--cached` to `graph` without `--source`, inline selectors
  or `ansible.default_source` drop the flag.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- Scripts that grepped for "source data is stale" or "refresh of NAME had N
  failures" read the `untaped.stale` warning instead.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- A plugin that imported `ApplicationSpec` declares itself with `PluginSpec`;
  untaped's root is the only application.
  ([#653](https://github.com/alexisbeaulieu97/untaped/pull/653))
- Replace `read_records(accept_kinds=…)` with
  `read_stdin_input(accept_kinds=…, what="records").records`; it is `None`
  when stdin held bare lines, which `read_records` refused, so raise then.
  ([#653](https://github.com/alexisbeaulieu97/untaped/pull/653))
- Drop `renderer=` from any `UiContext(...)` call; restyle output with a theme
  instead. ([#653](https://github.com/alexisbeaulieu97/untaped/pull/653))
- Drop `--continue-on-error` from awx calls and scripts, since continuing is now
  the default; passing it exits 2 as an unknown option. Add `--fail-fast` where
  a script relied on awx stopping at the first failure.
  ([#655](https://github.com/alexisbeaulieu97/untaped/pull/655))
- Drop `--limit 0` from `awx <resource> list` and `awx unified-templates list`
  calls, which list everything without `--limit`. For more than 20 jobs, pass
  `awx jobs list --limit N` with the number you need.
  ([#655](https://github.com/alexisbeaulieu97/untaped/pull/655))
- `untaped config migrate` deletes `shell.aliases` without printing the
  aliases it held, so copy each one from `config.yml` (in every profile) into
  a shell alias or function first
  (`alias failed='untaped awx jobs list --status failed'`). `untaped doctor`
  names a `shell.aliases` key left there.
  ([#654](https://github.com/alexisbeaulieu97/untaped/pull/654))
- Run `untaped awx test validate` with the same files and options to check
  test cases without launching them.
  ([#654](https://github.com/alexisbeaulieu97/untaped/pull/654))
- A profile that sets one of ansible's, recipe's or dotfiles' directory
  settings keeps its own place, which `untaped setup migrate-dirs` leaves
  alone. A prompt or status line reading `~/.untaped/dotfiles/attention` must
  read `~/.untaped/plugins/dotfiles/attention`.
  ([#657](https://github.com/alexisbeaulieu97/untaped/pull/657))

### Added

- `plugin_dir(spec)` in the SDK gives a plugin its own data directory,
  `~/.untaped/plugins/<name>/`.
  ([#554](https://github.com/alexisbeaulieu97/untaped/pull/554))
- `Record` is in the SDK: a record type declares its kind on the class,
  `class Widget(Record, kind="acme-tools.widget")`, and `emit` reads it from
  the rows. A kind may start with a hyphenated plugin name.
  ([#577](https://github.com/alexisbeaulieu97/untaped/pull/577))
- `untaped.contracts` lets a plugin declare an interface other plugins fill
  (`Contract`, `@bridge`, `@cached`, `@listing`) and ask every provider at
  once (`gather`, `select_one`, `convert`); doctor has a row per provider and
  warns about the ones it set aside. A provider that raised is `Failed` with
  `reason="raised"`, and one that composes the root inside its call fails.
  When a provider's live call fails and its cached answer is used, stderr says
  so once with an `untaped.stale` warning naming the cause and the answer's
  age; a cached answer with no match fails with the provider's error alone.
  ([#580](https://github.com/alexisbeaulieu97/untaped/pull/580),
  [#600](https://github.com/alexisbeaulieu97/untaped/pull/600),
  [#629](https://github.com/alexisbeaulieu97/untaped/pull/629))
- `untaped plugin rank` orders the plugins that fill a contract method,
  `untaped plugin list --contracts` lists which plugins fill each contract, and
  `untaped plugin schema KIND` prints a record kind's JSON Schema.
  ([#585](https://github.com/alexisbeaulieu97/untaped/pull/585))
- `check_conventions` flags a plugin that reads the git plugin's repo store
  blobs outside the handle `store.prefetched(...)` returns (rule `blob-reader`).
  ([#587](https://github.com/alexisbeaulieu97/untaped/pull/587))
- A new `git` plugin (`untaped[git]`): `untaped git hosts` lists which plugin
  supplies credentials for each Git host, `untaped git credential` is the
  credential helper untaped writes into its worktrees, and doctor checks for
  Git 2.29 or newer, which the plugin's shared repo store needs.
  ([#587](https://github.com/alexisbeaulieu97/untaped/pull/587))
- `run_git` takes `config=` (Git settings for that one command, kept out of its
  arguments) and `env=` (extra environment variables).
  ([#587](https://github.com/alexisbeaulieu97/untaped/pull/587))
- `untaped.testing.git` (experimental) gives plugin tests a local Git remote at
  an https URL that can ignore the partial-clone filter, refuse fetches by
  object id or drop a pack, plus a hostile `~/.gitconfig` and a `git` shim.
  ([#587](https://github.com/alexisbeaulieu97/untaped/pull/587))
- `untaped plugin new NAME --fills OWNER.CONTRACT` scaffolds a plugin that
  fills a contract, and `untaped plugin check [NAME]` checks an installed one:
  its conventions, its owner's conformance checks, a live call of each filled
  method and the contract schema it was tested against. `untaped.testing`
  adds `compose_with`, `assert_fills` and the experimental `assert_surface`,
  which keeps a plugin's commands, settings and contracts from losing a line
  by accident, and doctor warns when a provider was tested against another
  schema than the installed owner's (`owner-schema-drift`).
  ([#588](https://github.com/alexisbeaulieu97/untaped/pull/588))
- `AppContext.profile` names the profile the invocation acts on (`--profile`,
  `UNTAPED_PROFILE`, then the active one).
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- `untaped git store` reports what the repo store holds, which plugins use
  each repository and what only one uses; doctor's `git-store` row warns about
  paused maintenance and interrupted releases.
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- `github.git_protocol` (`https` or `ssh`) sets how `github sweep` and
  `github cache sync` fetch repositories on the GitHub host; with `ssh` they
  use `git@HOST:OWNER/NAME.git` and your own keys. `http.proxy` now covers
  those fetches from the GitHub host too.
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- A plugin retires a setting with no successor as `Retired(note=…)` in
  `retired_keys`: doctor's `unknown-keys` row prints the note, and
  `untaped config migrate` deletes the key (and a section it leaves empty),
  printing the value it held.
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- `untaped workspace remove NAME` drops a workspace's records (archiving an
  active one first) and releases each repo no other workspace uses from the
  repo store: workspace's refs and its pushed branches go, and the repo itself
  when nothing else uses it. Unpushed commits on the workspace's own branch
  refuse the removal and other unpushed branches are kept, unless `--force`; a
  stash is never deleted.
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- `gather` raises `NoProviderReady` (exit 4) when no provider can be asked; its
  `not_ready` and `quarantined` say why per plugin, so an owner can report its
  own error. ([#593](https://github.com/alexisbeaulieu97/untaped/pull/593))
- `untaped workspace repos resolve [NAME]` asks each repo's provider again and
  saves the URL it lists now, so a changed `github.git_protocol` reaches
  existing workspaces; a URL naming another repo is refused.
  ([#593](https://github.com/alexisbeaulieu97/untaped/pull/593))
- `untaped setup migrate-dirs` moves or deletes the directories older versions
  left, with a preview, `--yes` and `--dry-run`: workspace's and github's 10.x
  caches move into the repo store without downloading anything, the 9.x and
  ansible caches are deleted, and `~/.untaped/repositories` stays until
  `--dissociate` repacks the clones that borrow from it. A directory that holds
  the repo store or untaped's own files, or that two rows claim, is never
  touched. `untaped doctor` warns while anything is left.
  ([#595](https://github.com/alexisbeaulieu97/untaped/pull/595))
- Plugins declare their own `setup migrate-dirs` rows in
  `PluginSpec.migrations`: `DirMigration`, `MigrationRow`, `MigrationOutcome`
  (kind `untaped.migration`) and `MigrationOptions`, with `delete_migration`,
  `move_migration`, `old_dirs`, `dir_bytes`, `unsafe_dir`, `retired_values`
  and `shown_path` in the SDK; `untaped plugin check` runs each preview on an
  empty `HOME`. ([#595](https://github.com/alexisbeaulieu97/untaped/pull/595))
- `untaped ansible source remove NAME` releases each repo only that source
  used from the repo store: the repo goes when nothing else uses it, else
  ansible's refs go and the outcome says who kept it. A repo another saved
  source still selects is kept.
  ([#598](https://github.com/alexisbeaulieu97/untaped/pull/598))
- Every stderr JSON line carries a `kind` (`untaped.message`, `untaped.failure`,
  `untaped.debug`, `untaped.stale`, `untaped.progress`) whose fields
  `untaped plugin schema <kind>` prints; ignore a `level` or `kind` you do not
  know.
  ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600),
  [#621](https://github.com/alexisbeaulieu97/untaped/pull/621))
- `ctx.diag` on `AppContext` and `PluginContext` emits typed stderr diagnostics,
  a plugin's own kinds included, `ctx.diag.info(…, unmuted=True)` prints a
  preview, a stream or output a flag asks for under `-q`, `run_hint()` builds a
  hint's body, and `ctx.diag.relay` prints another tool's output without
  counting it; `untaped.testing`'s `CliResult.diagnostics` lists what a command
  emitted.
  ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600),
  [#622](https://github.com/alexisbeaulieu97/untaped/pull/622))
- A failed row's `error` also carries `exit_code` and `details`.
  ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
- Under `--format json` (or `UNTAPED_DIAGNOSTICS=json`), progress is reported
  as `untaped.progress` lines with `label`, `phase`, `done`, `total`, `elapsed`
  and `state`, at the cadence of text progress on a pipe.
  ([#621](https://github.com/alexisbeaulieu97/untaped/pull/621))
- `untaped.testing` freezes the progress clock during `invoke_cli`;
  `progress_clock()` lets a test advance it and assert `untaped.progress`
  lines. ([#621](https://github.com/alexisbeaulieu97/untaped/pull/621))
- `Cache` in `untaped.sdk` keeps data a plugin fetched from an upstream:
  `serve()` refreshes it once it is older than its max age, failing fast when
  offline, and says when it serves stale data; `refresh_option` adds `--refresh`
  and `--cached`. Each cache listed on `PluginSpec.caches` gets the setting
  `<plugin>.caches.<cache>.max_age_seconds`, and `Cache(renamed_from=)` moves an
  old key into it. `Cache(version=)`, usually with `code_version()`, refreshes
  data made by another version of the code that fills it, and the stale line's
  `outdated` says so.
  ([#623](https://github.com/alexisbeaulieu97/untaped/pull/623))
- `untaped.testing.cache_clock()` lets a test move the clock a cache reads.
  ([#623](https://github.com/alexisbeaulieu97/untaped/pull/623))
- `workspace.caches.repo_source_repos.max_age_seconds` sets how long workspace
  keeps the repos each provider lists (6 hours by default). After a failed
  refresh workspace serves the cached list for 5 minutes without asking again
  (`ctrl-r` in the picker asks at once), and while a cached list exists an
  automatic refresh gives up after 10 s instead of retrying.
  ([#629](https://github.com/alexisbeaulieu97/untaped/pull/629))
- `untaped.testing.check_plugin(NAME)` returns the typed rows of the checks only
  an installed plugin's composition can answer, for a linter to judge.
  ([#649](https://github.com/alexisbeaulieu97/untaped/pull/649))
- `untaped.contracts.providing` composes provider instances as a plugin of
  their own, for an owner that loads providers from somewhere other than an
  installed package.
  ([#649](https://github.com/alexisbeaulieu97/untaped/pull/649))
- `untaped.sdk` exports `kind_of` and `record_model` (a record model's kind and
  back) and `project_root` (the checkout a command acts on).
  ([#649](https://github.com/alexisbeaulieu97/untaped/pull/649))

### Changed

- **Breaking (core):** "capability" is now "plugin" everywhere: `untaped
  capabilities` is `untaped plugin list`, listing records of kind
  `untaped.plugin`, and plugins are discovered from the `untaped.plugins`
  entry-point group.
  Quarantine reasons `reserved-root` and `profile-state-overlap` are now
  `reserved-name` and `settings-state-overlap`; `duplicate-section` and
  `state-shadow` are gone, since a plugin's section is its name.
  ([#554](https://github.com/alexisbeaulieu97/untaped/pull/554))
- **Breaking (sdk):** `CapabilitySpec` is `PluginSpec`, `CapabilityContext`
  is `PluginContext` with only `settings`, and `CapabilityProvider` is gone.
  A plugin's config section and command group are its name, so
  `config_section` is gone; `profile_model` and `state_model` are `settings`
  and `state`, and they and `app_factory` are optional. A name is lowercase
  words joined by hyphens, and a name core keeps for itself is quarantined as
  `reserved-name`.
  ([#554](https://github.com/alexisbeaulieu97/untaped/pull/554))
- **Breaking (sdk):** a `Record` type whose fields would lose data in a JSON
  round trip (a secret, an excluded field, an alias validation does not accept,
  a computed field, a custom serializer or a type not defined yet) fails when it
  is defined, and a kind whose first segment holds an underscore is rejected.
  ([#577](https://github.com/alexisbeaulieu97/untaped/pull/577))
- **Breaking (core):** timestamps keep their microseconds in every format but
  `table` (`2026-01-02T03:04:05.250000Z`), so a record read back is unchanged;
  tables still show them to the second.
  ([#577](https://github.com/alexisbeaulieu97/untaped/pull/577))
- **Breaking (git):** Git commands that `untaped` runs no longer have the
  terminal or a graphical prompt, so an ssh key passphrase, a new host key or
  an HTTPS password that was asked for fails, with a hint naming the fix,
  instead of prompting. Git you run yourself is unaffected.
  ([#576](https://github.com/alexisbeaulieu97/untaped/pull/576))
- **Breaking (sdk):** A plugin whose settings or state model has a field, or
  an alias, named `extensions` or `caches` is quarantined (`bad-settings-keys`):
  untaped adds those keys to plugin sections. `caches` is a reserved plugin
  name too. ([#585](https://github.com/alexisbeaulieu97/untaped/pull/585))
- **Breaking (sdk):** a plugin's entry point names its `PluginSpec` constant
  (`acme = "untaped_acme:SPEC"`). Anything else, a function returning a spec
  included, is quarantined as `not-a-spec` and never called, so `provider()`
  is gone from every plugin. `untaped.testing.provider_candidate` is
  `plugin_candidate`.
  ([#588](https://github.com/alexisbeaulieu97/untaped/pull/588))
- **Breaking (github):** `github.corpus_repo` rows from `github cache delete`
  and `cache prune` now say `removed`, with the `disk_bytes` freed, or
  `released`, with a new `kept` field naming what still uses the repo (another
  plugin, a worktree added by hand) and `disk_bytes` 0. A row's `path` points
  into the repo store under `git.store_dir`, and `cache worktree` checks refs
  out under `~/.untaped/plugins/github/worktrees/`.
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- **Breaking (workspace, github):** workspace and github keep their
  repositories in the git plugin's shared repo store under `git.store_dir`
  (blobless, full history), and `github.cache_dir` and `workspace.cache_dir`
  are gone. Other plugins' refs under `refs/untaped/` show in `git log --all`
  from a workspace, and a local tag named like a remote tag follows the remote.
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- **Breaking (github):** `github repos list` and `github search repos` write one
  record, `github.repo`, with GitHub's own field names (`full_name`,
  `html_url`, `clone_url`, `ssh_url`, …); `github.repo_hit` is gone, and
  `github.sweep_repo` and `github.corpus_repo` rows carry the same repo fields.
  `--stdin` reads `full_name` from all three.
  ([#593](https://github.com/alexisbeaulieu97/untaped/pull/593))
- **Breaking (workspace):** workspace asks every installed plugin that fills
  its `RepoSource` contract for repos, instead of reading GitHub's inventory,
  and no longer depends on untaped-github. A name no provider lists exits 2
  (the guess of a GitHub URL from the name is gone); a typed URL must be
  `https://`, `ssh://` or `user@host:path` and its repo is named by its whole
  path; `--stdin` records need a kind a provider reads; `workspace.protocol`
  is retired in favour of each provider's setting (`github.git_protocol`).
  ([#593](https://github.com/alexisbeaulieu97/untaped/pull/593))
- `untaped --help` lists plugins in their own Plugins panel, after untaped's own
  Commands, and every help screen ends with a Global options panel
  (`--profile`, `--verbose`, `--quiet`, `--deprecated`) instead of mixing them
  into Parameters. `check_conventions` now fails a plugin help panel without a
  `sort_key` below 100 (`unkeyed-panel`) or named like a core panel
  (`reserved-panel`).
  ([#599](https://github.com/alexisbeaulieu97/untaped/pull/599))
- **Breaking (core):** a command that prints a failure exits non-zero even when
  it finishes: the exit code is the most severe failure it printed or emitted as
  a failed row, and a predicate hit (exit 3) no longer hides one. Among shipped
  commands this changes only three dry runs, which now fail on the problem their
  real run fails on: `dotfiles apply --dry-run` (an item gone from the manifest,
  a failed fetch, a placed path it cannot read), `dotfiles remove --dry-run` (an
  item gone from the manifest) and `doctor fix --dry-run` (a fix that cannot
  run). ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
- **Breaking (sdk):** `ErrorInfo` is now `Failure`, and `note_failure` is gone;
  `emit` counts a failed row toward the exit code.
  ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
- Under `--format json`, a plain `error:` line on stderr carries `category`
  `failed`, `exit_code` and `details`.
  ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
- **Breaking (sdk):** `ProgressHandle.update` takes `message=None, *, phase=,
  done=, total=`; `fraction=`, `new_phase=`, `log()` and `report_error(write=)`
  are gone, and `NO_PROGRESS` is the do-nothing handle.
  ([#621](https://github.com/alexisbeaulieu97/untaped/pull/621))
- Progress lines on a pipe start after 2 s, then print per phase, per 10 % and
  every 30 s while a step waits, end with one `done` or `failed` line if a line
  printed, and read the same for every command:
  `Refreshing source 'prod' · probing refs 312/780 (9s)` where it printed
  `probing refs: 312/780 repos`.
  ([#621](https://github.com/alexisbeaulieu97/untaped/pull/621))
- `-q` now mutes commentary only: a preview before a prompt, a stream you asked
  to watch and output a flag asks for still print, while the `doctor`, `setup`,
  `doctor fix` and `plugin check` footers, `profile show`/`current` notes, `awx
  test run`'s summary, awx `save` and no-match notes, the `github cache
  sync`/`status`/`sweep` footers and `recipe test` diffs no longer do, and awx's
  "Edited batch retained at …" is now a warning that always prints.
  ([#622](https://github.com/alexisbeaulieu97/untaped/pull/622))
- Every stderr message now follows `ui.theme` and `ui.symbols`, including
  previews and summaries that printed plain, while the `error:` lines of
  `setup`'s notes, `skills update`, `awx unified-templates get`'s not-found and
  `workspace run`'s summary print plain like every other error.
  ([#622](https://github.com/alexisbeaulieu97/untaped/pull/622))
- **Breaking (ansible):** `deps`, `impact`, `find` and `graph` refresh a
  selected source before answering when it was never refreshed or is older
  than `ansible.caches.index.max_age_seconds` (one day); `--cached` reads only
  what is cached and `--refresh` refreshes now.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- The graph commands' `--backend` applies to whichever source refresh the
  command runs, not only to `--refresh`; with `--cached` it is an error.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- **Breaking (ansible):** `untaped ansible graph --cached` without a selected
  source is a usage error, like `--refresh`, instead of being ignored.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- An inline source is named by its selector flags in tree headers, warnings and
  the refresh's progress line (`inline source --org acme --repo acme/x`) instead
  of a hash. ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- A paused `untaped ansible source refresh` says how to resume in a hint,
  `rerun to resume; finished repos are skipped`, instead of in its error
  message. ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- `untaped ansible source refresh` prints one line of counts
  (`refreshed source prod: 780 repos, 2141 refs, …`), without the elapsed time
  or `(parallel N)`, before its per-repo failures and warnings.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- `untaped ansible source status` reports each source's cache freshness
  (`refreshed_at`, `max_age_seconds`, `partial`, `attempted_at`, `retry_at`,
  `refresh_error`) beside what the index holds (`scanned_at` and the counts),
  and its table shows `refreshed_at` instead of `scanned_at`. A source whose
  last refresh found no refs is `fresh` with zero counts, not `not_refreshed`.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- `untaped config migrate` deletes the 9.x `ansible.stale_after`, printing the
  value it held, instead of renaming it to `ansible.stale_after_seconds`.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- **Breaking (ansible):** a refresh that fails over cached data serves it with
  one `untaped.stale` warning, which replaces the old "source data is stale" and
  "refresh of NAME had N failures" warnings, and an empty answer from a source
  that could not be refreshed exits with the refresh's error.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- **Breaking (awx):** A batch that writes or launches several resources
  (`apply`, `patch`, `edit`, `delete`, `launch`, `sync`) now keeps going after a
  failure and reports every row, as `workspace run` and the core batch helpers
  do. `--fail-fast` stops scheduling after the first failure and skips the rest;
  `--continue-on-error` is gone.
  ([#655](https://github.com/alexisbeaulieu97/untaped/pull/655))
- **Breaking (awx):** `--limit` on `awx <resource> list`, `awx unified-templates
  list` and `awx jobs list` takes a number of 1 or more, as on github and jira;
  `--limit 0` no longer means "no limit". `jobs list` still shows the newest 20
  jobs by default.
  ([#655](https://github.com/alexisbeaulieu97/untaped/pull/655))
- **Breaking (ansible, recipe, dotfiles):** each plugin keeps its own data
  under `~/.untaped/plugins/<name>/`: the ansible index
  (`ansible.index_path`), the recipe library (`recipe.library_dir`) and the
  dotfiles directories (`dotfiles.repos_dir`, `kept_dir` and `state_dir`).
  `untaped setup migrate-dirs` moves the 10.x data there while those settings
  keep their defaults, and repoints dotfiles' links and saved repo paths.
  ([#657](https://github.com/alexisbeaulieu97/untaped/pull/657))
- `untaped awx export` says why it skips a read-only kind such as Credential,
  instead of "not roundtrippable in v0".
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- Under JSON diagnostics, the follow-up commands after the plaintext-token
  warnings and after `auth unset` of an inherited token command are the line's
  `hint` field instead of part of its `message`, and `-q` keeps the latter.
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))

### Deprecated

- `ui.message`, `ui.success`, `ui.progress`, `report_error`,
  `report_row_errors`, and `echo`/`ui.styled` with `err=True` are deprecated in
  favour of `ctx.diag` and raise a `DeprecationWarning`; mypy reports them with
  `enable_error_code = ["deprecated"]`, and they are removed in 12.0.
  ([#622](https://github.com/alexisbeaulieu97/untaped/pull/622))
- `ansible.stale_after_seconds` and `UNTAPED_ANSIBLE__STALE_AFTER_SECONDS` are
  deprecated in favour of `ansible.caches.index.max_age_seconds` and still
  read, with a warning, until 12.0.
  ([#628](https://github.com/alexisbeaulieu97/untaped/pull/628))
- A token stored in plain text in `config.yml` (`<section>.token`) is removed
  in 12.0 and still read, with a warning, until then;
  `UNTAPED_<SECTION>__TOKEN` or a token command (`untaped auth migrate`)
  keeps it out of the file.
  ([#667](https://github.com/alexisbeaulieu97/untaped/pull/667))

### Removed

- **Breaking (sdk):** `HttpSettings.timeout` and `HttpSettings(timeout=…)` are
  gone; use `timeout_seconds`.
  ([#589](https://github.com/alexisbeaulieu97/untaped/pull/589))
- **Breaking (core):** the old names of the nine settings renamed in 10.1
  (`http.timeout`, `awx.test_timeout`, …) and their `UNTAPED_*` variables are
  no longer read. `untaped doctor` names any left in `config.yml`, and any
  `UNTAPED_*` variable of a retired or deleted setting still set, with what
  replaces it; `untaped config migrate` still renames the keys.
  ([#589](https://github.com/alexisbeaulieu97/untaped/pull/589),
  [#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- **Breaking (github):** `--depth` on `github sweep` and `github cache sync` is
  gone: the repo store keeps each repository's full history, blobless, and
  fetches file contents only when a grep or a worktree reads them.
  ([#591](https://github.com/alexisbeaulieu97/untaped/pull/591))
- **Breaking (github):** github no longer keeps a repository inventory file:
  the repos it lists for workspace are cached by workspace for 6 hours, so
  `github.inventory.path` and `github.inventory.max_age_seconds` are retired.
  ([#593](https://github.com/alexisbeaulieu97/untaped/pull/593))
- **Breaking (ansible):** `ansible.cache_dir`, `ansible.git_fetch_depth`,
  `ansible.git_blob_filter` and `ansible.git_clone_protocol` are gone: source
  refreshes keep their repositories in the git plugin's repo store under
  `git.store_dir` (blobless, full history) and clone with `github.git_protocol`.
  A branch or tag whose name git allows but the store cannot hold (a leading
  `-` or `+`, or `refs/`) is no longer indexed; `source refresh` names each one
  in a warning. ([#592](https://github.com/alexisbeaulieu97/untaped/pull/592))
- **Breaking (sdk):** `untaped.sdk` no longer exports `RepoCache`, `cache_key`,
  `cache_path`, `cache_origin`, `list_caches`, `repo_url_parts`,
  `scoped_auth_header` and `git_auth_header`, and `run_git` takes
  `auth_config=` instead of `auth_header=` and `auth_url=`.
  ([#592](https://github.com/alexisbeaulieu97/untaped/pull/592))
- **Breaking (sdk):** `untaped.sdk` no longer exports `ApplicationSpec`, the
  root application's own declaration, which no plugin builds.
  ([#653](https://github.com/alexisbeaulieu97/untaped/pull/653))
- **Breaking (sdk):** `untaped.sdk` no longer has `read_records`;
  `read_stdin_input` reads the same pipe envelopes.
  ([#653](https://github.com/alexisbeaulieu97/untaped/pull/653))
- **Breaking (sdk):** `UiContext` no longer takes `renderer=` or has a
  `renderer` attribute; it always renders with untaped's own theme-aware
  renderer. ([#653](https://github.com/alexisbeaulieu97/untaped/pull/653))
- **Breaking (core):** `untaped alias` and the `shell.aliases` setting are gone,
  and `untaped NAME` no longer runs a stored alias. A plugin may now be named
  `alias`. ([#654](https://github.com/alexisbeaulieu97/untaped/pull/654))
- **Breaking (awx):** the hidden `--dry-run` option of `untaped awx test run`
  is gone. ([#654](https://github.com/alexisbeaulieu97/untaped/pull/654))

### Fixed

- The repo store deletes what an interrupted fetch left in a repository once it
  is an hour old, such as temporary pack files that could hold gigabytes and
  lock files that made every later fetch fail; the 10.x repo caches kept them.
  ([#569](https://github.com/alexisbeaulieu97/untaped/pull/569))
- A Git command that times out now also stops the helper processes it started,
  after giving Git a moment to remove its lock files, so the next fetch no
  longer fails on one the timed-out command left behind.
  ([#576](https://github.com/alexisbeaulieu97/untaped/pull/576))
- A stderr line printed while a progress spinner is up no longer garbles it, and
  text lines emitted while a full-screen picker is open print after it closes.
  ([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
- `untaped workspace create` and `add` accept a tag for `--base`: writable repos
  branch from the tag and read-only ones check it out. Before, only a branch was
  found. ([#627](https://github.com/alexisbeaulieu97/untaped/pull/627))
- A command reading a token from a locked `pass` store with no terminal on
  stdin or stderr, as when an agent runs it, no longer opens gpg's passphrase
  prompt on the screen under the agent and waits for it; it fails at once and
  names the command that unlocks the store from your own terminal. A pinentry
  mode you set yourself is kept.
  ([#644](https://github.com/alexisbeaulieu97/untaped/pull/644))
- A command that stops because a service setting such as `awx.base_url` is
  missing now names the profile it looked in and the other profiles that set
  it, with the `--profile` option or `UNTAPED_PROFILE` value that uses one, so
  an agent can rerun it there instead of asking you for a value you already
  configured. ([#648](https://github.com/alexisbeaulieu97/untaped/pull/648))
- `awx jobs logs --tail` rejects a negative number as a usage error instead of
  treating it like `--tail 0`.
  ([#655](https://github.com/alexisbeaulieu97/untaped/pull/655))
- `untaped awx test run --baseline` labels the baseline run's failures that
  still count `suite/case (baseline)`, and no longer reports its failed tests as
  this run's when the run aborts.
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- `untaped awx apply` reports a membership that needs a resource whose create
  failed as skipped because a dependency failed, instead of an internal
  "planned reference" error.
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- `untaped awx apply`, `delete` and `copy` previews show a parent scope as
  `parent=Inventory/prod (org Default)` instead of an internal object dump.
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- `untaped awx unified-templates get` reports a missing id as
  `unified template not found: '<id>'` with category `not_found`.
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- `untaped.testing.check_conventions` brings back the composition it started
  with and no longer warns about quarantined plugins, as `check_plugin` does.
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- The installed-skills check after a command also stays quiet for `skills` and
  `doctor` spelled another way dispatch accepts, such as `untaped Doctor`.
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- `untaped skills update` exits with a failed skill's own exit code (4 for a
  config error) and puts the structured failure on its row's `error`.
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- `StateCollection.insert` reports a record that already exists as a
  `conflict` (exit 1) instead of a config error (exit 4).
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- `untaped recipe hooks run --content -` fails with "no content provided" when
  nothing is piped, instead of waiting silently on the terminal; piped content
  loses one trailing newline, as other stdin inputs do.
  ([#665](https://github.com/alexisbeaulieu97/untaped/pull/665))
- `untaped.testing.check_conventions` (and `untaped plugin check`) flags a
  `TYPE_CHECKING` import that goes against the layers, as it does a runtime one;
  only infrastructure may still name an application port there.
  ([#686](https://github.com/alexisbeaulieu97/untaped/pull/686))

## Older releases

Each earlier major has its own file: [10.x](changelog/10.x.md),
[9.x](changelog/9.x.md), [8.x](changelog/8.x.md), [7.x](changelog/7.x.md),
[6.x](changelog/6.x.md), [5.x](changelog/5.x.md) and [4.x](changelog/4.x.md).
