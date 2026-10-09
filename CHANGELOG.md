# Changelog

## 10.1.0

### Added

- dotfiles (experimental): a new capability, `untaped[dotfiles]`, that places
  config files from subscribed dotfiles repos.
  ([#436](https://github.com/alexisbeaulieu97/untaped/pull/436))
- `untaped auth set|unset|status|migrate` store API tokens in the machine's
  password store instead of `config.yml`. `auth set`, `auth migrate` and
  `untaped setup` first test the chosen store with a throwaway value (`pass`:
  gpg encrypt and decrypt; `secret-tool`: store and read back) and stop with
  the cause. A capability named `auth` is now quarantined like the other
  management command names.
  ([#442](https://github.com/alexisbeaulieu97/untaped/pull/442),
  [#511](https://github.com/alexisbeaulieu97/untaped/pull/511),
  [#510](https://github.com/alexisbeaulieu97/untaped/issues/510),
  [#564](https://github.com/alexisbeaulieu97/untaped/pull/564))
- `untaped setup plan` lists what a profile still needs, with the command for
  each step and whether you or your agent runs it, and `untaped setup --only`
  limits setup to those services.
  ([#457](https://github.com/alexisbeaulieu97/untaped/pull/457))
- The `untaped` agent skill teaches any agent to install, set up and diagnose
  untaped with you, without a token passing through it.
  ([#457](https://github.com/alexisbeaulieu97/untaped/pull/457))
- Plugins can rename a setting by declaring `renamed_keys` on their settings
  model: the old key and its `UNTAPED_*` variable keep working, with a
  warning naming the new key, until the next major release.
  ([#475](https://github.com/alexisbeaulieu97/untaped/pull/475))
- SDK: `atomic_write` also takes `bytes`, and `yaml_mapping_indent(text)`
  guesses a YAML file's mapping indent so a `ruamel.yaml` rewrite keeps it.
  ([#476](https://github.com/alexisbeaulieu97/untaped/pull/476))
- `untaped config migrate` gives renamed and retired keys their new names in
  every profile of `config.yml`, and `untaped doctor` lists such keys with it
  as an automatic fix; `config set` and `config unset` also remove a key's old
  spelling, and `config list` notes a value still read from one.
  ([#482](https://github.com/alexisbeaulieu97/untaped/pull/482),
  [#485](https://github.com/alexisbeaulieu97/untaped/pull/485))
- `untaped doctor fix` runs every automatic fix, re-checks, and reports what
  it fixed; `--dry-run` only plans. `untaped doctor` and `untaped setup`
  hint at it.
  ([#480](https://github.com/alexisbeaulieu97/untaped/pull/480))
- awx: `apply`, `patch` and `edit` outcome records list
  `dropped_undeclared_secrets`, the `$encrypted$` placeholders dropped from
  fields untaped doesn't treat as secrets, and `dropped_secrets`, the secret
  placeholders a create left out (a new workflow node's `$encrypted$` extra
  vars included); the undeclared-placeholder warning now says to set the real
  value or remove the placeholder.
  ([#461](https://github.com/alexisbeaulieu97/untaped/pull/461),
  [#486](https://github.com/alexisbeaulieu97/untaped/pull/486))
- Plugins can mark a command, group or whole capability `experimental` or
  `deprecated(replacement=…)` once (`@experimental`, `create_app(stability=…)`,
  `CapabilitySpec(stability=…)`). Experimental ones sit in an Experimental panel
  in `--help` and end their help with a line saying so, instead of saying it in
  their one-line summary (`workspace` and `awx test` now sit in that panel);
  deprecated ones warn once per run and leave the `--help` listing and shell
  completion, and the new `untaped --deprecated --help` lists them.
  ([#518](https://github.com/alexisbeaulieu97/untaped/pull/518))
- Plugins can mark a settings field `experimental` or
  `deprecated(replacement=…)` too (`Annotated[int, experimental]`,
  `Annotated[bool, deprecated(replacement=…)]`), and a capability's settings
  inherit its mark. `config list` and `config get` report each setting's
  `stability`.
  ([#519](https://github.com/alexisbeaulieu97/untaped/pull/519))
- `untaped workspace create NAME` makes an empty workspace to `add` repos to
  later: with `--empty`, which skips the picker, without a terminal (where it
  failed with "no repos given"), or when the picker is confirmed with no repos
  selected. `workspace add` still needs repos.
  ([#524](https://github.com/alexisbeaulieu97/untaped/pull/524),
  [#498](https://github.com/alexisbeaulieu97/untaped/pull/498),
  [#497](https://github.com/alexisbeaulieu97/untaped/issues/497))
- `ui.symbols` and `ui.color_roles` gain the names screens use (`chosen`,
  `checked`, `heading`, `dash`, …, `screen.accent`, `screen.caret`,
  `screen.emphasis`, …); the config reference lists them.
  ([#521](https://github.com/alexisbeaulieu97/untaped/pull/521),
  [#526](https://github.com/alexisbeaulieu97/untaped/pull/526),
  [#530](https://github.com/alexisbeaulieu97/untaped/pull/530))
- SDK (experimental): screens, an Elm-style runtime for full-screen terminal UIs
  (`Screen`, `Cmd`, `UiContext.run`, `untaped.testing.drive_screen`), with
  input components (`TextInput`, `SecretInput`, `Select`, `Tabs`, …) and
  `SearchList`, `Viewport`, `Tree`, `Tags`, `Form` and `Panes`. A screen's
  `shared_labels` says what a shared key does there, for its footer and help
  overlay, and its `update` receives `Help` when `?` opens the overlay. See
  [Screens](https://github.com/alexisbeaulieu97/untaped/blob/v10.1.0/docs/screens.md).
  ([#522](https://github.com/alexisbeaulieu97/untaped/pull/522),
  [#525](https://github.com/alexisbeaulieu97/untaped/pull/525),
  [#526](https://github.com/alexisbeaulieu97/untaped/pull/526),
  [#527](https://github.com/alexisbeaulieu97/untaped/pull/527),
  [#530](https://github.com/alexisbeaulieu97/untaped/pull/530))
- SDK: `PickRequest` takes optional `allow_empty`, which lets a picker confirm
  an empty selection, and `command` and `alternative`, which `ui.pick_many`
  names when there is no terminal to draw on.
  ([#524](https://github.com/alexisbeaulieu97/untaped/pull/524),
  [#530](https://github.com/alexisbeaulieu97/untaped/pull/530))
- A custom `untaped.testing.PromptBackend` may define
  `run_screen(screen, *, theme)` to run screens (`ui.run`); the method is
  optional, so a backend written for 10.0 still type-checks, and `ui.run` on
  one without it fails naming the method. A backend may set
  `needs_terminal = False`, as `untaped.testing.ScriptedPromptBackend` does,
  to run screens and `pick_many` without a terminal (a prompt still needs a
  TTY stdin).
  ([#525](https://github.com/alexisbeaulieu97/untaped/pull/525))

### Changed

- `untaped setup` is one full-screen screen: capabilities with their status on
  the left, the selected one's form on the right, and each capability's
  connection is checked online before anything is saved (`Save anyway` keeps
  the old behavior). Ctrl-C exits 130 after reporting what was saved.
  ([#531](https://github.com/alexisbeaulieu97/untaped/pull/531))
- `doctor` and `setup` JSON, YAML and pipe rows carry the fix in a `fix` field
  (an argv list) and an `automatic` flag (true when the fix needs no value or
  input) instead of appending the fix command to `detail`. A plugin's doctor
  check marks such a fix with `DoctorResult(automatic=True)`, and plugin code
  that reads `DoctorResult.fix` must also handle an argv list.
  ([#457](https://github.com/alexisbeaulieu97/untaped/pull/457),
  [#478](https://github.com/alexisbeaulieu97/untaped/pull/478))
- `untaped setup` no longer writes a typed awx, github or jira token to
  `config.yml`; it stores it in the machine's password store.
  ([#442](https://github.com/alexisbeaulieu97/untaped/pull/442))
- `untaped.testing.check_conventions` checks more: stability marks, setting
  names (`settings-naming`), `echo("failed: …")` and `warnings.warn()` (report
  a per-item failure with `report_error(exc, item=…)` and warn with
  `ui.message("warning", …)`), and a plugin's `prompt_toolkit` imports
  (`terminal-boundary`: build the interface with `untaped.sdk` screens, or
  waive a line with `# untaped: allow terminal-boundary`). It fails with the
  reason when the capability is quarantined, a broken rename declaration
  included.
  ([#464](https://github.com/alexisbeaulieu97/untaped/pull/464),
  [#492](https://github.com/alexisbeaulieu97/untaped/pull/492),
  [#518](https://github.com/alexisbeaulieu97/untaped/pull/518),
  [#525](https://github.com/alexisbeaulieu97/untaped/pull/525))
- `awx.api_prefix` no longer needs its trailing `/`; untaped adds it, and the
  prefix must still start with `/`.
  ([#471](https://github.com/alexisbeaulieu97/untaped/pull/471))
- `untaped doctor` and `untaped setup` print a checklist grouped by
  capability, with each fix under its row and passing rows' detail shown;
  `--columns` still prints the table, where `fix` is the command line.
  ([#479](https://github.com/alexisbeaulieu97/untaped/pull/479))
- Settings follow one naming scheme (`_dir` and `_path`, `parallel`, a unit on
  every duration), so nine keys have new names, listed under Renamed settings
  in the configuration reference.
  ([#489](https://github.com/alexisbeaulieu97/untaped/pull/489))
- Root options placed around `--help` now apply (`untaped awx --deprecated
  --help`, `untaped --help --verbose`).
  ([#518](https://github.com/alexisbeaulieu97/untaped/pull/518))
- `config list` prints the experimental settings under an `Experimental`
  heading and the deprecated ones that are set (or all, with `untaped
  --deprecated`) under `Deprecated`, with a `note` column only there;
  `--format json` stays one list. `awx.test_timeout_seconds` and
  `awx.test_parallel` are marked experimental.
  ([#519](https://github.com/alexisbeaulieu97/untaped/pull/519))
- `config set ui.symbols|ui.color_roles` and `doctor` reject names that no
  theme defines; a stray name already in `config.yml` keeps working.
  ([#521](https://github.com/alexisbeaulieu97/untaped/pull/521))
- Prompts (text, secret, select, multiselect, confirm) share the screens' look
  and the theme's colors: a labelled box with the keys of every other screen
  (esc cancels); the answer stays in the scrollback as one plain
  `question: answer` line (a secret as its mask, a multiselect as the chosen
  labels), nothing is left when the prompt is cancelled. With stderr redirected
  (`2>log`) a prompt draws on the controlling terminal instead of the file, as
  `ui.run` does, and refuses (exit 2) when there is no controlling terminal to
  draw on; a long list is cut to a short terminal's height.
  ([#532](https://github.com/alexisbeaulieu97/untaped/pull/532))
- The workspace picker (`workspace create`, `workspace add`) is full screen and
  follows the theme's colors, symbols and border style. Selections show as
  `[✓]` like other multi-choice lists, its footer names tab, enter and ctrl-s
  (create), and a redirected stderr no longer stops it: it draws on the
  controlling terminal (`ui.pick_many` does the same for piped stdin).
  ([#530](https://github.com/alexisbeaulieu97/untaped/pull/530))
- awx: the warnings for a copy `test run --source-ref` teardown leaves behind
  and for a workflow cycle in `usage` and `nodes` now read
  `warning: <item>: <what happened>`, like awx's other warnings.
  ([#561](https://github.com/alexisbeaulieu97/untaped/pull/561))
- Every package requires Python 3.14.1 or newer, so a 3.14 release candidate
  is never picked; plugins and recipe packs should raise `requires-python`
  to `>=3.14.1`.
  ([#455](https://github.com/alexisbeaulieu97/untaped/pull/455))
- The capabilities' agent skills tell an agent to read stderr as well as the
  rows and pass on what you would want to know, whatever its level: a
  deprecated setting or flag, a skipped or partial result, a clamped option.
  ([#469](https://github.com/alexisbeaulieu97/untaped/pull/469))
- In the workspace picker's search and list, esc with no query now quits like
  ctrl-c (asking first when anything is selected); with a query it still clears
  it, and the footer then says `esc clear`.
  ([#563](https://github.com/alexisbeaulieu97/untaped/pull/563))

### Deprecated

- The old names of the nine renamed settings (`http.timeout`,
  `awx.test_timeout`, …) and their `UNTAPED_*` variables still work, with a
  warning, until 11.0; `untaped config migrate` renames them in `config.yml`.
  ([#489](https://github.com/alexisbeaulieu97/untaped/pull/489))
- SDK: `HttpSettings.timeout` and `HttpSettings(timeout=…)` are deprecated,
  removed in 11.0; use `timeout_seconds`.
  ([#489](https://github.com/alexisbeaulieu97/untaped/pull/489))
- awx: `test run --dry-run` is deprecated, removed in 11.0; use
  `test validate`, which now also takes `--case` and `--scm-branch`.
  ([#515](https://github.com/alexisbeaulieu97/untaped/pull/515))
- `untaped alias` and the `shell.aliases` setting are deprecated, removed in
  11.0; use a shell alias or function.
  ([#507](https://github.com/alexisbeaulieu97/untaped/pull/507))
- A plaintext token in `config.yml` is deprecated: it still works but warns
  once per run; `untaped auth migrate` moves it to the password store.
  ([#442](https://github.com/alexisbeaulieu97/untaped/pull/442))

### Fixed

- The `untaped-workspace` agent skill tells an agent started inside a
  workspace to work in the checkouts already there and omit `NAME`, instead of
  cloning the repos again elsewhere.
  ([#539](https://github.com/alexisbeaulieu97/untaped/pull/539))
- github: `cache worktree --format raw` prints the worktree path, so
  `$(untaped github cache worktree OWNER/NAME --format raw)` works (it printed
  the repository name), and the record carries that directory as an absolute
  `target_path`, the field other commands' records use for a path; `path`
  stays.
  ([#540](https://github.com/alexisbeaulieu97/untaped/pull/540),
  [#535](https://github.com/alexisbeaulieu97/untaped/issues/535),
  [#561](https://github.com/alexisbeaulieu97/untaped/pull/561))
- Repo caches (workspace, ansible, github) work from agent shells that set
  git's `safe.bareRepository=explicit`, such as GitHub Copilot CLI, instead of
  failing with "cannot use bare repository".
  ([#534](https://github.com/alexisbeaulieu97/untaped/pull/534))
- A `pass` token command that fails quotes gpg's first error once, with the
  usual fixes, instead of letting gpg repeat it on the terminal, and `doctor`
  fails a `pass` token command that gpg cannot serve here (no gpg, or no key
  for the password store).
  ([#511](https://github.com/alexisbeaulieu97/untaped/pull/511),
  [#510](https://github.com/alexisbeaulieu97/untaped/issues/510))
- `github.sweep.max_age_seconds` and `ansible.stale_after_seconds` now reject
  negative values, and `github.sweep.parallel` rejects values below 1.
  ([#471](https://github.com/alexisbeaulieu97/untaped/pull/471))
- ansible: `source set` and `source patch` report an invalid source as one
  line naming the problem instead of pydantic's full error dump, and
  validation messages across untaped drop pydantic's `Value error, ` prefix.
  ([#483](https://github.com/alexisbeaulieu97/untaped/pull/483))
- awx: `test` commands report an invalid suite file, including an invalid
  header variable, as one line naming the field instead of pydantic's full
  error dump.
  ([#487](https://github.com/alexisbeaulieu97/untaped/pull/487))
- awx: each target a command fails or leaves undone now gets its own attributed
  stderr line (it had none, or an `info` one): `error: <item>: …` for a failed
  request, write (`apply`, `patch`, `edit`, a membership `add` or `remove`) or
  `delete`, and for a refused `jobs cancel` or `jobs relaunch` target;
  `warning: <item>: …` for a `launch` or `sync` job that failed, timed out or
  was skipped, an execution Ctrl-C leaves behind, a `jobs wait` timeout, the
  kinds a bulk `export` skips, the finished executions `jobs cancel` skips, the
  targets a failed `delete` stopped, and a write skipped or stopped by a
  conflict. An invalid `edit` batch and `test`'s unknown-launch-field warning
  are `warning` lines too, and the hint after Ctrl-C reads
  ``hint: run `untaped awx jobs wait …` ``.
  ([#464](https://github.com/alexisbeaulieu97/untaped/pull/464),
  [#488](https://github.com/alexisbeaulieu97/untaped/pull/488),
  [#491](https://github.com/alexisbeaulieu97/untaped/pull/491),
  [#494](https://github.com/alexisbeaulieu97/untaped/pull/494),
  [#496](https://github.com/alexisbeaulieu97/untaped/pull/496),
  [#561](https://github.com/alexisbeaulieu97/untaped/pull/561))
- awx: `job-templates usage --stdin` and `workflow-templates usage|nodes
  --stdin` look up piped `--format pipe` records by their `id` (`--by-id` now
  applies only to bare lines; a record without an `id` is an error), so a
  template name shared across organizations no longer picks the wrong template.
  ([#555](https://github.com/alexisbeaulieu97/untaped/pull/555))
- Each package's PyPI page and the GitHub release notes link to the docs of
  their own release tag instead of `main`, so their links keep working and
  describe that release after the docs move on.
  ([#556](https://github.com/alexisbeaulieu97/untaped/pull/556))
- A paste into the workspace picker goes into the field being typed in instead
  of being replayed as keys, so a pasted space no longer toggles or removes a
  repo. ([#563](https://github.com/alexisbeaulieu97/untaped/pull/563))

## 10.0.0

untaped is now an SDK whose first-party capabilities are plugins: `untaped`
installs the core and SDK, and each capability is an extra (`untaped[all]`,
`untaped[awx]`, …).
Read Upgrading first.

### Upgrading

- 9.x users on `uv tool upgrade untaped` get the core only and every capability
  disappears; reinstall with `uv tool install --reinstall 'untaped[all]'` (or
  `pip install -U 'untaped[all]'`), restating any `--with` tools.
- Two providers claiming one capability name or config section (e.g. a plugin
  claiming `github`) are both disabled; uninstall one.
- awx: `export` no longer writes `custom_virtualenv`, `webhook_key`, an SCM
  project's `local_path` or `spec.organization`, and masks `host_config_key`;
  `get`/`list` now mask `host_config_key` too. `patch`/`edit` exit 2 for
  `webhook_key`, `custom_virtualenv` and an SCM project's `local_path`.
  Re-export stored documents; `apply` ignores the old fields. 9.x exports hold
  `host_config_key` in clear: scrub them or rotate the key.
- workspace: recreate task workspaces with `workspace create` (`untaped
  workspace` is now worktree-based and experimental).
- workspace: delete the `state.yml` key `workspace.workspaces` and drop
  `shell-init` (`uwcd`) from your shell; `init`, `adopt`, `import`, `forget`,
  `sync`, `repos`, `branch` and `edit` are gone.
- workspace: replace `foreach` with `run`; add `--fail-fast` where a failure
  should stop the run, and drop `--all`, `--continue-on-error` and
  `--ignore-errors`.
- workspace: pass workspace names, not paths; give `path` one name and no
  `--stdin`.
- workspace: drop `--repo`, `--dirty` and `--behind` from `status`, and handle
  exit 3 from `status --check`.
- workspace: scripts that piped `repos list` pipe `workspace status --format
  pipe` and read the new `workspace.workspace` and `workspace.status` fields.
- workspace: move an old workspace directory out of `workspaces_dir` before
  `create` reuses its name.
- caches: point `workspace.cache_dir` away from a 9.x cache. Delete
  `~/.untaped/ansible-repositories` and `~/.untaped/github-corpus` (and
  old-layout directories under a custom `ansible.repo_cache_path` or
  `github.corpus_path`); keep `~/.untaped/repositories` while clones made before
  7.0 borrow from it.
- sdk: import first-party code from its top-level package (`untaped_github.api`,
  …) and the SDK from `untaped.sdk` instead of `untaped.capability_api`; stop
  using `CAPABILITY_API_VERSION` and `get_core_settings`.
- sdk: replace `safe_cache_path` with `cache_path`.
- sdk: import core only through `untaped.sdk` and another capability only
  through its `api` module, declaring a dependency on its distribution;
  `check_conventions(NAME)` now fails otherwise.
- registry: drop `api_requires` from providers and keep the `untaped`
  requirement current; scripts reading `untaped capabilities` lose the `api` and
  `origin` columns and get rows in name order.
- core: expect exit 130 when Ctrl-C interrupts a prompt; `PromptInterruptedError` is
  no longer a `ConfigError`.
- ansible: replace `graph --upstream/--downstream/--both` with `--direction
  up|down|both`.
- recipe: in packs scaffolded on 9.x, import the hook contract from
  `untaped_recipe.hook_api` and require `untaped[recipe]>=X,<X+1` in
  `dependency-groups.dev`.

### Added

- **core:** `config.yml` and `state.yml` carry an on-disk format: untaped
  refuses a file written by a newer format (exit 4) instead of misreading it.
  Files without `format_version` are format 1. `format_version` is reserved
  as a capability and section name.
- **core:** bare `untaped --help` and `untaped capabilities` print how to
  install capabilities when none are installed.
- **core:** `@writes` declares a command that writes; command names are no
  longer limited to a closed verb list.
- **core:** `untaped.testing.check_conventions(NAME)` runs the convention checks
  for any capability, plugins included.
- **sdk:** `untaped.testing.plugin` gives a plugin's own tests the hermetic
  environment untaped's tests use.
- **core:** `examples/untaped-hello` is a minimal, tested plugin to start from;
  `untaped.testing.invoke_root(argv)` runs `untaped …` in a plugin's tests.
- **core:** `report_row_errors` reports each failed row's error and hint.
- **sdk:** `UiContext.pick_many` opens an inline two-pane picker: fuzzy search
  with multi-select on the left, per-item settings on the right (`PickRequest`,
  `PickItem`, `PickSetting`, `PickCatalog`, `PickResult`, `Picked`).
  `ScriptedPromptBackend(picks=...)` scripts it in tests.
- **sdk:** `RepoCache`, `cache_path`, `cache_key`, `list_caches`,
  `cache_origin`, `repo_url_parts` and `scoped_auth_header`: one bare-repo cache
  building block for any capability.
- **workspace:** `workspace create`, `add` and `archive` print failed rows'
  errors with hints on stderr.
- **workspace:** `workspace.branch_template` and `workspace.protocol` settings.
- **workspace:** `workspace run` runs a command, a script file or a stdin script
  in each repo, with `UNTAPED_*` context variables (experimental).
- **workspace:** in a terminal, `workspace create` and `add` without repos open
  a repo picker (search the GitHub inventory, set mode/base/branch per repo).
- **awx:** `export --comment TEXT` heads each document with `# TEXT`.
- **github:** `github.inventory` settings (`path`, `orgs`, `teams`,
  `max_age_seconds`) for a cached, metadata-only repository list that workspace
  `create`/`add` resolve names from and the picker searches; it falls back to
  `github.default_org`.
- **recipe:** `recipe backups restore` takes `--format` and `--columns`; with
  `json`, `yaml` or `pipe` it prints one `recipe.restore_outcome` row
  (`planned`, `restored` or `failed`). Table output is unchanged.

### Changed

- **Breaking (core):** `pip install untaped` installs only the core and SDK;
  install `untaped[all]` for every first-party capability, or `untaped[<name>]`
  for one. First-party code moves to top-level packages (`untaped_github.api`,
  …). Upgrading keeps the original spec, so reinstall with the extra: `uv tool
  install --reinstall 'untaped[all]'` or `pip install -U 'untaped[all]'`.
- **Breaking (core):** the SDK module is now `untaped.sdk`;
  `untaped.capability_api` is gone. `CAPABILITY_API_VERSION` and
  `get_core_settings` are removed.
- **Breaking (core):** `PromptInterruptedError` is no longer a `ConfigError`;
  Ctrl-C at a prompt now always exits 130.
- **Breaking (sdk):** `check_conventions(NAME)` runs an `import-boundary` rule:
  a plugin may import core only through `untaped.sdk`, and another capability
  only through its `api` module with a declared dependency on its distribution.
- **Breaking (core):** two or more providers claiming the same capability name
  or config section are now all quarantined, with a warning naming every
  claimant. Before, the first-party capability won, or the first external in
  discovery order. A plugin that claims a first-party name or section (say
  `github`) now disables that capability too; uninstall one to restore the
  other.
- **core:** first-party capabilities register through `untaped.capabilities`
  entry points like any plugin. A failing capability is quarantined instead of
  stopping `untaped`. Each quarantined capability warns once by name, and
  `untaped doctor` names the capability in its quarantine rows.
- **core:** a capability whose commands fail to load fails only its own command,
  with exit 4; `untaped doctor` reports it as a `bad-app-factory` quarantine
  row.
- **Breaking (ansible):** the git cache moves to `~/.untaped/ansible-cache`,
  keyed by host and path so https and ssh URLs share it; delete
  `~/.untaped/ansible-repositories`. The GitHub token is now sent only to the
  GitHub host, and refreshes of one repo no longer run concurrently. An explicit
  `ansible.repo_cache_path` keeps its old-layout directories, which are never
  read again; refresh re-fetches into the new layout, and the old ones can be
  deleted.
- **Breaking (workspace):** workspace caches now live in
  `~/.untaped/workspace-cache`. The 9.x `~/.untaped/repositories` is left
  untouched (clones made before 7.0 may borrow objects from it); a
  `workspace.cache_dir` pointing at a 9.x cache is refused.
- **Breaking (workspace):** `untaped workspace` now manages task workspaces of
  git worktrees (`create`, `add`, `list`, `status`, `path`, `archive`, `run`)
  and is experimental.
- **Breaking (workspace):** `foreach` becomes `run`. Failures no longer stop the
  run; pass `--fail-fast` to stop. `--all`, `--continue-on-error` and
  `--ignore-errors` are gone.
- **Breaking (workspace):** workspace arguments are names, not paths. `path`
  takes one name and no `--stdin`.
- **Breaking (workspace):** `status` drops `--repo`, `--dirty` and `--behind`,
  and `--check` now exits 3 on archive blockers.
- **Breaking (workspace):** old workspace directories are left in place and no
  longer listed. A non-empty one under `workspaces_dir` blocks `create` of that
  name until you move it.
- **Breaking (awx):** `export` writes a job template's `host_config_key` (a
  secret, previously exported in clear) as `$encrypted$`, and `get`/`list`
  records mask a set `host_config_key`; empty secrets stay empty instead of
  becoming placeholders.
- **Breaking (awx):** `export` documents no longer carry controller-derived
  fields (`custom_virtualenv`, `webhook_key`, SCM projects' `local_path`) or a
  `spec.organization` copy; `apply` ignores them in older files.
- **Breaking (awx):** `patch`/`edit` exit 2 when changing `webhook_key`,
  `custom_virtualenv` or an SCM project's `local_path`; these were previously
  sent and ignored by the controller.
- **awx:** `export` writes multi-line text as YAML `|` blocks.
- **Breaking (github):** the sweep cache moves to `~/.untaped/github-cache`,
  keyed by host and path so https and ssh URLs share it; delete
  `~/.untaped/github-corpus` (its worktrees included). Old-layout caches under a
  custom `github.corpus_path` are not listed; sync re-creates them in the new
  layout, and the old directories can be deleted.
- **Breaking (recipe):** the hook contract moved from
  `untaped.capabilities.recipe.hook_api` to `untaped_recipe.hook_api`; packs
  scaffolded on 9.x import the old path under `TYPE_CHECKING`, so update that
  import. Scaffolded packs and the resolver's hint now ask for
  `untaped[recipe]>=X,<X+1` in `dependency-groups.dev` instead of
  `untaped>=X,<X+1`.
- **docs:** `docs/` holds getting-started, configuration, scripting,
  plugins and the config reference; each capability's guide is its package
  README.
- **docs:** the versioning policy now covers every record field `--format
  json` or `--columns '?'` shows, not only documented ones.

### Removed

- **Breaking (core):** `safe_cache_path` is removed; use `cache_path`.
- **Breaking (core):** providers drop `api_requires`; the capability API version
  is gone. A provider's `untaped` requirement is the only compatibility check.
  `untaped capabilities` loses its `api` and `origin` columns and lists
  capabilities in name order.
- **Breaking (ansible):** `graph --upstream/--downstream/--both` are removed;
  use `--direction up|down|both`. Graph sources re-index once.
- **Breaking (workspace):** removed `init`, `adopt`, `import`, `forget`, `sync`,
  `repos`, `branch`, `edit`, `shell-init` (`uwcd`), the `untaped.yml` manifest,
  and the `state.yml` key `workspace.workspaces`. That key is now ignored;
  delete it.
- **Breaking (workspace):** kinds `workspace.repo`, `workspace.repo.summary` and
  the `init_`, `forget_`, `add_`, `remove_`, `sync_`, `branch_`, `branch_unset_`
  and `foreach_outcome` kinds are gone. `workspace.workspace` and
  `workspace.status` have new fields. Pipe `workspace status --format pipe`
  where you piped `repos list`.

## Older releases

Each earlier major has its own file: [9.x](changelog/9.x.md),
[8.x](changelog/8.x.md), [7.x](changelog/7.x.md), [6.x](changelog/6.x.md),
[5.x](changelog/5.x.md) and [4.x](changelog/4.x.md).
