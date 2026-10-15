# Changelog

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
- A token stored in plain text in `config.yml` (`<section>.token`) is now
  called discouraged instead of deprecated, in the warnings and the docs: it
  keeps working and is not scheduled for removal. `UNTAPED_<SECTION>__TOKEN`
  or a token command (`untaped auth migrate`) keeps it out of the file.
  ([#666](https://github.com/alexisbeaulieu97/untaped/pull/666))

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

## Older releases

Each earlier major has its own file: [10.x](changelog/10.x.md),
[9.x](changelog/9.x.md), [8.x](changelog/8.x.md), [7.x](changelog/7.x.md),
[6.x](changelog/6.x.md), [5.x](changelog/5.x.md) and [4.x](changelog/4.x.md).
