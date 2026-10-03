# Changelog

## Unreleased

### Added

- dotfiles (experimental): a new capability, `untaped[dotfiles]`, that places
  config files from subscribed dotfiles repos.
  ([#436](https://github.com/alexisbeaulieu97/untaped/pull/436))
- `untaped auth set|unset|status|migrate` store API tokens in the machine's
  password store instead of `config.yml`.
  ([#442](https://github.com/alexisbeaulieu97/untaped/pull/442))
- `untaped setup plan` lists what a profile still needs, with the command for
  each step and whether you or your agent runs it; `untaped setup --only`
  preselects services in the wizard.
  ([#457](https://github.com/alexisbeaulieu97/untaped/pull/457))
- The `untaped` agent skill teaches any agent to install, set up and diagnose
  untaped with you, without a token passing through it.
  ([#457](https://github.com/alexisbeaulieu97/untaped/pull/457))
- `doctor` rows carry a `fix` field: the command that repairs a failed or
  warned check.
  ([#457](https://github.com/alexisbeaulieu97/untaped/pull/457))
- Plugins can rename a setting by declaring `renamed_keys` on their settings
  model: the old key and its `UNTAPED_*` variable keep working, with a
  warning naming the new key, until the next major release.
  ([#475](https://github.com/alexisbeaulieu97/untaped/pull/475))
- SDK: `atomic_write` also takes `bytes`, and `yaml_mapping_indent(text)` guesses
  a YAML file's mapping indent so a `ruamel.yaml` rewrite keeps it.
  ([#476](https://github.com/alexisbeaulieu97/untaped/pull/476))
- Doctor rows carry `automatic`, and a doctor check marks a fix that needs no
  value or input with `DoctorResult(automatic=True)`; `skills update` and
  `auth migrate` are automatic.
  ([#478](https://github.com/alexisbeaulieu97/untaped/pull/478))
- `untaped config migrate` renames deprecated keys in every profile of
  `config.yml`; `config set` and `config unset` also remove a key's old
  spelling, and `config list` notes a value still read from one.
  ([#482](https://github.com/alexisbeaulieu97/untaped/pull/482))

### Changed

- `doctor` and `setup` JSON, YAML and pipe rows no longer append the fix
  command to `detail`; read `fix` instead, and plugin code that reads
  `DoctorResult.fix` must also handle an argv list.
  ([#457](https://github.com/alexisbeaulieu97/untaped/pull/457))
- `untaped setup` no longer writes a typed awx, github or jira token to
  `config.yml`.
  ([#442](https://github.com/alexisbeaulieu97/untaped/pull/442))
- A plaintext token in `config.yml` is deprecated: it still works but warns
  once per run; `untaped auth migrate` moves it to the password store.
  ([#442](https://github.com/alexisbeaulieu97/untaped/pull/442))
- `untaped.testing.check_conventions` now flags `echo("failed: …")` and
  `warnings.warn()`; report a per-item failure with `report_error(exc, item=…)`
  and warn with `ui.message("warning", …)`.
  ([#464](https://github.com/alexisbeaulieu97/untaped/pull/464))
- `awx.api_prefix` no longer needs its trailing `/`; untaped adds it, and the
  prefix must still start with `/`.
  ([#471](https://github.com/alexisbeaulieu97/untaped/pull/471))
- `setup plan` marks the plaintext-token step (`auth migrate`) `by: agent`: a
  step is the user's only when it asks for or reveals a token.
  ([#478](https://github.com/alexisbeaulieu97/untaped/pull/478))
- `untaped doctor` and `untaped setup` print a checklist grouped by
  capability, with each fix under its row and passing rows' detail shown;
  `--columns` still prints the table, where `fix` is the command line.
  ([#479](https://github.com/alexisbeaulieu97/untaped/pull/479))

### Fixed

- Every package requires Python 3.14.1 or newer, so a 3.14 release candidate
  is never picked; plugins and recipe packs should raise `requires-python`
  to `>=3.14.1`.
  ([#455](https://github.com/alexisbeaulieu97/untaped/pull/455))
- awx: `apply`, `patch` and `edit` outcome records now list
  `dropped_undeclared_secrets`, the `$encrypted$` placeholders dropped from
  fields untaped doesn't treat as secrets.
  ([#461](https://github.com/alexisbeaulieu97/untaped/pull/461))
- awx: a refused `jobs cancel` or `jobs relaunch` target, and `test`'s
  unknown-launch-field warning, are now `error` and `warning` lines under JSON
  stderr diagnostics.
  ([#464](https://github.com/alexisbeaulieu97/untaped/pull/464))
- `github.sweep.max_age_seconds` and `ansible.stale_after` now reject negative
  values, and `github.sweep.sync_concurrency` rejects values below 1.
  ([#471](https://github.com/alexisbeaulieu97/untaped/pull/471))

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
