# Changelog

## Unreleased

### Added

- dotfiles: a new capability (`untaped[dotfiles]`, experimental) that places
  config files from subscribed dotfiles repos. A repo's `dotfiles.yml` lists
  items of `link`, `copy` or `merge` (JSON/YAML) files with per-OS and per-tag
  filters; each machine enables items with a `sync`, `once` or `manual`
  policy. `apply` shows its plan and keeps replaced local files aside,
  `sync` is safe on a timer and exits 3 when something needs the user, and
  `status` writes a one-line `attention` file for prompt segments.
- `untaped auth set|unset|status|migrate` keep API tokens out of `config.yml`.
  `auth set awx` reads a token from a hidden prompt or `--stdin`, stores it
  with this machine's password store (macOS `security`, `secret-tool`, then
  `pass`), reads it back, and writes the `awx.token_command` that serves it;
  `auth migrate` moves every plaintext token there, in every profile. See
  [Tokens](docs/configuration.md#tokens).

### Changed

- `untaped setup` no longer stores a typed token in `config.yml`: it offers the
  password store (or moving a plaintext token there), a `token_command`, or an
  environment variable. A model without `token_command` still takes a typed
  token.
- A token stored in plain text in `config.yml` is deprecated, still read: using
  one warns once per run, as does `config set <section>.token`. `doctor`'s
  warning and the rejected- or missing-token hints now point at
  `untaped auth migrate` and `untaped auth set <section>`.

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

## 9.1.0

A minor release (capability SDK 3.1, additive). Tables open on a few curated
default columns, now declared by each record type (`table_columns`), and
`ansible` graphs mark where reading stopped (`stopped`). The packaged skills
are rewritten as short maps with reference files; run `untaped skills update`.
Scripts may notice three changes, each a **Behavior change** entry below:
`--format raw` on `profile list`, `config list` and `config get` prints native
values instead of `✓`/`—`; an unknown `--columns` name on GitHub `repos list`,
`search` and `cache sync` exits 2 instead of warning; and `awx.job` records
gain `elapsed`.

- Core
  - **New (SDK):** a record type picks its default table columns with a
    `table_columns` ClassVar and table-only glyphs with `TableGlyph` (API `3.1`).
  - **Behavior change:** `profile list`, `config list` and `config get` print
    native values in `--format raw`, as json does, instead of `✓` and `—`
    (an unset `config get` prints nothing); only tables show the glyphs.
    `capabilities`, `alias list` and `doctor` tables show default columns, and
    the `doctor` and `setup` tables leave a passing check's detail blank.
  - **Fix:** `--columns=-name` outside a table keeps the record's field order
    and no longer adds an omitted `error` as `null`.
  - **Fix:** with `ui.detail_view: table`, a single record's status and
    outcome values are colored by meaning, as in lists.
  - **Changed:** the packaged skills are rewritten as short maps with
    reference files, and their descriptions (shown by `skills list`) are
    shorter third-person summaries. Refresh installed copies with
    `untaped skills update`.
- GitHub
  - **Fix:** `cache delete` and `cache prune` report the space each removed
    repository frees in `disk_bytes` (it was always 0), including under
    `--dry-run`.
  - **Changed:** `repos list`, `search repos`, `search code`, `search issues`,
    `search users` and `cache sync` tables show curated default columns
    (`repos list` records gain `description`), and `sweep` leaves out the
    negated-predicate counts, which are always 0; `--columns +name` now edits
    the `sweep` defaults.
  - **Behavior change:** an unknown `--columns` name on `repos list`, `search`
    and `cache sync` exits 2 instead of warning, as on every other command.
- AWX
  - **Behavior change:** the `jobs wait` table shows when each job finished
    (`finished_at`) and how long it ran (`elapsed`). `awx.job` records and
    `launch`/`sync --wait` rows gain `elapsed` (seconds).
- Jira
  - **Fix:** `issues links create --help` shows how a link reads
    (`KEY OUTWARD-PHRASE OTHER`); the placeholder was missing from the help.
- Ansible
  - **Fix:** `--all-refs` exits 2 with `--live` or without a source
    instead of being silently ignored: live reads see only the default
    branch, and every ref exists only in a source's cache.
  - **New:** graph nodes and `deps`/`impact` rows carry `stopped`
    (`depth` or `not_cached`) where reading stopped; the tree marks them
    `…`, and a depth limit prints a `hint:`.
  - **New:** `deps`, `impact`, `find` and `source status` tables show a few
    default columns, and a table shortens `path` to `a → … → z`.

## 9.0.0

A major release that makes untaped a harness for AI agents, and the first
under the [versioning and stability](docs/stability.md) policy: from here on,
breaking changes are collected into major releases. Every failure says what
kind it is and which system is responsible, and selects the exit code: **4**
means fix the environment, **5** means retry later (capability SDK 3.0).
`awx test` (now [experimental](docs/stability.md#experimental)) explains why a
case failed and gains regression checks, baseline comparison, workflow suites
and temporary test sets from a git ref (`--source-ref`). Records name things
the same way everywhere (`repo`, `url`, `*_at` UTC timestamps), tables show a
few curated columns that fit the terminal (`--columns +name`/`-name` edit
them), `ansible graph` draws a readable tree, and every built-in skill is a
self-contained manual for the installed CLI.

**Upgrading.** Scripts and providers that use `untaped` will hit these
changes (details in the **Breaking** entries below):

- Exit codes: a failure is no longer always 1. **4** means fix the setup (a
  config error, a rejected token, a missing permission) and **5** means
  retry later (network, timeout, 5xx, 429, a busy lock); a run exits with its
  most severe failure. More misuse exits 2. Scripts that test for `1` must
  also handle 4 and 5.
- Failed rows carry a structured `error` object. `github.sync_outcome` and
  `recipe.apply_outcome` move their old string `error` to `detail`, and a
  failed `awx.test_result` carries a `failure` object instead of
  `failure_reason`, `failed_tasks` and `log_tail`.
- Renamed record fields:
  - github: `full_name` becomes `repo` and `html_url` becomes `url`;
    `name`, `repository_url` and the nested `repository` are gone; the sweep
    records' `synced_at` becomes `fetched_at`.
  - awx: `started`/`finished` become `started_at`/`finished_at`, UTC to the
    second.
  - recipe: `recipe.check` rows are `name`, `type`, `status` (`fail`, was
    `error`), `path` and `detail` (pack counts are in `packs list`);
    `backups prune` emits `recipe.prune_outcome` rows.
- Tables: default columns changed for most commands. Parse json, yaml or
  pipe output, or pass `--columns`. A few `--format raw` outputs changed
  too: `workspace repos list` prints repo names (pass `--columns workspace`
  for the old output), awx `schedules list` prints `unified_job_template`
  where the always-blank `last_run` was, and `groups`/`inventory-sources
  list` add a trailing `inventory` column. awx `jobs list`, `jobs events`,
  `unified-templates list` and `workflow-templates nodes` json/yaml carry
  whole records instead of the table columns.
- ansible: without `--ref`, `deps`, `find` and `graph` read a target's
  dependencies at its default branch (`--all-refs` for every cached ref);
  `graph` defaults to `--depth unlimited` (pass `--depth 3`);
  `--upstream`/`--downstream`/`--both` become `--direction up|down|both`
  (the old flags warn and go away in 10.0).
- Capability providers: declare `((3, 0), (4, 0))` and give errors a
  `category` and `system` instead of an `exit_code`.

- Core
  - **New:** a [versioning and stability](docs/stability.md) policy: what
    stays compatible within a major release, what is experimental, and,
    from 9.0.0 on, breaking changes collected into major releases.
  - **New:** every built-in skill is a complete manual for the installed CLI.
    Each has a description naming the words that should make an agent load
    it, no skill points at `docs/` or the source repository any more, and the
    AWX, recipe, GitHub and Ansible skills keep `SKILL.md` to about 900 words
    and ship the details in `references/` (plus example suites for AWX). Run `untaped skills update`
    to refresh installed copies. Tests now parse every `untaped …` command a
    skill quotes against the real CLI.
  - **Breaking:** failures say what kind they are and who is responsible.
    Every error carries a `category` (`usage`, `config`, `auth`,
    `permission`, `not_found`, `invalid`, `conflict`, `unavailable`,
    `failed`, `interrupted`) and a `system` (`untaped`, `local`, `git`, or
    the service: `awx`, `jira`, `github`), which select the exit code.
    Two exit codes are new: **4** means the environment needs fixing (config,
    a rejected token, missing permission), and **5** means a temporary
    failure (network, timeout, 5xx, 429, a busy lock; retry later). Scripts
    that test for `1` must also handle 4 and 5. A run exits with the most
    severe failure it saw: `130 > 2 > 4 > 5 > 1 > 3 > 0`, so a batch with one
    rejected token exits 4 however many other items failed. See
    [exit codes](docs/reference/exit-codes.md).
  - **Breaking:** `ConfigError` exits 4. Errors that are really about the
    input rather than the setup were recategorized and still exit 1: an
    invalid or missing `--vars-file`/`--fields-file`/`--patch-file`, an
    invalid pipe record or empty stdin, a rejected `config set` value, an
    invalid `config edit` result, an unknown or existing profile, alias or
    skill. `config` commands that read a broken `config.yml`, a missing
    `$EDITOR`, an undefined active profile, and a failing `token_command`
    exit 4. A config or state lock another process holds exits 5, and a
    `config edit` whose file changed meanwhile is a `conflict` (exit 1).
  - **Breaking:** `skills install/update/remove` flag conflicts
    (`--target-dir` without `--target`, `--project-dir` without
    `--scope local`, names plus `--all`, a duplicate name) exit 2, and a
    prompt with no terminal on stdin exits 2 (`usage`).
  - **New:** with `--format json`, `yaml` or `pipe` (the flag,
    `UNTAPED_FORMAT` or `ui.format`), or `UNTAPED_DIAGNOSTICS=json`, stderr
    is JSON Lines: one object per error
    (`level`, `message`, `category`, `system`, `retryable`, `hint`,
    `exit_code`, `details`), per-item error (plus `item`), warning, hint and
    note (including the installed-skills warning after a command), and
    progress is silent. A parse error and a quarantined-provider warning
    follow a `--format` on the command line or in `UNTAPED_FORMAT`.
    `UNTAPED_DIAGNOSTICS=text` keeps text.
    stdout and the pipe envelope are unchanged. See
    [stderr diagnostics](docs/reference/pipes.md#stderr-diagnostics).
  - **New:** failed rows of outcome records (`*_outcome` kinds) carry an
    `error` object (`category`, `system`, `retryable`, `message`, `hint`)
    next to their `detail`; rows that did not fail have no `error` key, and
    tables leave it out.
  - **New:** HTTP failures take their category from the status (401 auth,
    403 permission, 404 not_found, 409 conflict, 400/422 invalid, 429/5xx
    unavailable; no response at all is unavailable), name the service as
    their `system`, and record `status`, `url` and the number of `attempts`.
    Git failures are `git`; a timeout or a transient transport error is
    `unavailable`, and a missing `git` binary is `config`.
  - **New:** tables are easier to scan. A table leaves out a column that is
    empty on every row (`ui.hide_empty_columns`, on by default; a column
    named in `--columns` always shows) and gathers its columns from every
    row, not just the first. It fits the terminal by narrowing only its
    widest columns: a cell that does not fit ends in `…` and each row stays
    on one line, while `detail`, `message` and `hint` wrap. Nested values
    read as `key=value` pairs instead of Python reprs, durations (`*_s`,
    `elapsed`) as `1m42s`, other decimals to two places, and commits are
    shortened to 10 characters. Status and outcome words are colored by
    meaning, including in the default theme. json, yaml, raw and pipe keep
    every field and value (see the fixes below for the few exceptions).
  - **New:** `--columns +name` adds a column to a table's default columns and
    `--columns=-name` removes one (`--columns +url,-kind` does both); in
    `raw` the edits also start from the defaults; in json and yaml, `-name`
    removes a field from the whole record. `--columns ?` marks the default
    columns with `*`. A single record in table format leaves out its empty
    fields too. For providers, `emit` and `render_rows` take
    `table_columns=` for a command's default columns.
  - **Fix:** asking for a column that exists on the record but is absent from
    every row (such as `error` when nothing failed) no longer warns that it
    is unknown; `--quiet` mutes the `No … found.` line; a column name
    containing a dot (`has-file:release.txt`) selects that key in every
    format; a table header is never read as Rich markup; a `datetime` in a
    plain row renders as `2026-01-02T03:04:05Z` in every format (a string in
    yaml); `doctor` and `capabilities` accept `--columns a,b` and
    `--columns ?` even when the settings are broken.
  - **Behavior change:** an outcome record's `action` comes right after the
    field identifying the row (`repo`, or `id` and `name`) instead of last,
    in tables and in json/yaml key order. Fields are unchanged.
  - **Breaking (SDK):** the capability API is `3.0`
    (`CAPABILITY_API_VERSION = (3, 0)`); providers must declare
    `((3, 0), (4, 0))`. `UntapedError` gains `category`, `system`, `hint` and
    `details` (class defaults, overridable per instance by keyword) and
    derives `exit_code` and `retryable` from the category; a class no longer
    sets `exit_code`. `BatchOutcome` keeps `failures` (each failed item with
    its error) and derives `failed`; `ExitCode` gains `ENVIRONMENT` (4) and
    `UNAVAILABLE` (5); `OutcomeRecord` and `TargetRecord` reserve `error`.
    New exports: `ErrorCategory`, `ErrorInfo`, `attribution`,
    `note_failure`, `report_error`, `most_severe`, `rejected_token_error`.
    `note_failure(exc)` counts a failure and returns its `ErrorInfo` (a
    failed row reads `error=note_failure(exc)`); `ErrorInfo.from_exception`
    only builds one. See
    [Raise with a category](docs/conventions.md#raise-with-a-category-or-inherit-one).
  - **Breaking:** a git command the remote refuses for its credentials
    (authentication failed, no username, `Permission denied (publickey)`,
    HTTP 401) is `auth` (403: `permission`) with a hint, so an expired token
    during `workspace sync`, `github cache sync` or a pack fetch exits 4.
  - **Behavior change:** a missing setting reads `<section>.<field> is not
    configured` with the `config set …`/environment variable follow-up on a
    `hint:` line (the JSON `hint`), instead of in the message.
  - **Fix:** a URL password (`https://user:secret@host`) never appears in an
    error message, a JSON diagnostic's `details`, or a row's `error`.
  - **Fix:** `alias set` and `alias remove` read and rewrite the alias map
    under the config lock, so two overlapping alias commands no longer both
    succeed while one of them drops the other's alias.
  - **Fix:** `config edit` keeps the private copy when the editor saves
    changes and then exits with an error (for example a wrapper's post-save
    step fails): the config is left unchanged and the error names the copy
    and how to apply it. Only an unchanged copy is removed.
  - **New:** `ui.styled(text, truncate=True)` ends each line too wide for
    the terminal in an ellipsis instead of wrapping.
- Workspace
  - **Breaking:** a `sync` (or `add --sync`, `import --sync`) whose git call
    timed out or lost the network exits 5; git not installed exits 4; a
    broken `$EDITOR` for `edit` exits 4; an invalid `workspace.parallel`
    setting or an invalid registry entry in `state.yml` exits 4; `init` with
    an invalid NAME, and `edit --editor` with an empty or badly quoted
    command, exit 2. Failed `sync`, `branch apply` and uninspectable
    `status` rows carry `error`, and `status --check` exits with the
    failure's own code (5 when `git status` timed out).
  - **Fix:** a repo `repos remove` could not remove (not declared, or a
    refused `--prune`) is a `failed` row with `detail` and `error`, after
    the removed ones; it had no row. A `--prune` that took the repo out of
    the manifest but could not delete its clone is a `partial` row
    (`pruned: false`, exit 1) instead of aborting the batch.
    `workspace.remove_outcome` rows gain `detail` for this.
  - **Behavior change:** during `sync`, a repo job failing for a reason
    other than git (such as a busy cache lock) becomes that repo's `failed`
    row with its own exit code (5 for a busy lock) instead of aborting the
    whole sync with 1.
  - **Behavior change:** workspace tables show a command's usual columns;
    `--columns ?` marks them and json, yaml, raw and pipe keep every field.
    `status` shows `repo`, `cloned`, `branch`, `upstream`, `ahead`,
    `behind`, `modified`, `untracked` and `detail`; `sync` shows `repo`,
    `action` and `detail`; `branch apply` shows `repo`, `target_branch`,
    `action` and `detail`; `repos list`
    shows `repo`, `url` and `target_branch`; `repos add` shows `repo`,
    `url`, `branch` and `action`. `status --all` and `sync --all` lead with
    `workspace`.
  - **New:** `workspace.status` rows carry `upstream`, the branch's
    configured upstream (`origin/main`), or `null` when unset.
  - **Breaking:** `workspace.repo` (`repos list`) rows lead with `repo`
    instead of `workspace`, so `--format raw` prints the repo names rather
    than the workspace name on every line. A script that read the workspace
    name passes `--columns workspace`.
- GitHub
  - **Breaking:** a rejected token (401) or a missing permission (403) exits
    4, and a rate limit (429, rate-limited 403) or an unavailable API exits
    5, for every command including `sweep` and `cache sync`.
  - **Breaking:** `github.sync_outcome` (`cache sync`) renames its string
    field `error` to `detail`; a failed row's `error` is now the structured
    object. A fetch that timed out exits 5.
  - **Breaking:** github records name a repository only in `repo` and a web
    page only in `url`: `full_name`, `html_url` and the duplicate `name`
    (`github.repo`, `github.repo_hit`), `repository_url` (`github.issue`) and
    the nested `repository` (`github.code`) are gone. The sweep records
    (`github.sweep_repo`, `github.sweep_file`, `github.sweep_match`) rename
    `full_name` to `repo` and `synced_at` to `fetched_at`, as in `github
    cache`. `--stdin` reads `repo` from a piped record.
- Jira
  - **Breaking:** a rejected token (401, same hint text) and a missing
    permission (403) exit 4; 5xx, 429 and network failures exit 5; a missing
    issue stays 1 (`not_found`).
  - **New:** `issues search` and `issues assigned` rows carry `issue_type`
    and `priority`, as `issues get` does. `jira.transition` records
    (`issues transitions`) carry `to_status`, the status the transition
    leads to.
  - **Behavior change:** tables show fewer, more useful columns by default.
    `issues search` and `issues assigned` show `key`, `issue_type`,
    `status`, `priority`, `assignee` (not under `assigned`), `summary` and
    `updated_at`, without `url` and `api_url`. `issues comments list` drops
    `issue_key` (so does `issues get --comments` for one issue); a
    transition of several issues shows `key`, `transition_id` and `action`;
    `projects list` drops `id`, `boards list` drops `api_url` and
    `sprints list` drops `origin_board_id`. The `issues get` detail view
    leaves out empty fields. `--columns +name` adds a column back; JSON,
    YAML and pipe output keep every field.
- AWX
  - **Behavior change:** `awx test` is
    [experimental](docs/stability.md#experimental) and may change in a minor
    release.
  - **New:** `untaped awx test init TEMPLATE` writes a commented starter
    suite for a job template from its survey and launch prompts: required
    survey variables get their default, first choice or `TODO` (a password
    with a stored default gets `$encrypted$`, which AWX replaces with the
    stored value at launch), and optional variables and the enabled launch
    prompts are listed as comments. It writes `.untaped/awx/tests/<name>.yml`
    at the git root (or `--out PATH`), prints the path, and never replaces a
    file.
  - **New:** `untaped awx schema AwxTestSuite` prints the JSON Schema of a
    test suite (json by default, `--format yaml`), generated from the
    installed models; every suite field now has a description.
  - **Behavior change:** in `awx test`, launch names (`inventory`,
    `credentials`, `labels`, and `!ref` without its own scope) resolve in the
    suite's `organization` when it sets one, instead of always in
    `awx.default_organization`. A `!ref` scope still wins.
  - **Behavior change:** a suite's `---` header may follow blank lines and
    `#` comments, and a lone leading `---` with no closing `---` is read as a
    YAML document-start marker instead of failing as an unclosed header.
  - **Behavior change:** every error while reading a suite (header, variable
    values, Jinja2, YAML, validation) starts with the file's path.
  - **Fix:** a `variables:` key in a suite body is rejected (`declare
    variables in the '---' header, not the body`) instead of being silently
    discarded.
  - **Fix:** `job_slice_count` in a case's `launch:` no longer warns as an
    unknown launch field.
  - **Fix:** the launch preflight (`awx test run`/`validate`, and `launch`
    of a job template) accepts values the template has already, as AWX
    does: an `scm_branch` equal to the project's branch when the template
    sets none, and extra vars it saves with those values. `awx test` leaves
    them out of the launch, so an `idempotent` rerun no longer pins a commit
    AWX would ignore. Any other value is still refused.
  - **Fix:** a suite whose `kind: AwxTestSuite` has a trailing `# comment`
    (or is a flow mapping) is found when its directory is run; it was
    silently skipped.
  - **Fix:** a test case whose launch AWX answered with `ignored_fields`
    cancels the job AWX launched anyway (unless `--no-cancel`); it was left
    running.
  - The `untaped-awx` skill documents the complete suite format, the
    `awx.test_result` record, the agent profile, and the resource, document
    and job commands in `references/`, with `smoke`, `variants` and
    `negative` example suites. `docs/awx/agent-profile.md` and the test-suite
    section of `docs/awx/usage.md` now point at the skill's pages.
  - **New:** workflow template documents carry their node graph under
    `spec.nodes` (`id`, `run` or `approval`, `prompts` by name, `success`/
    `failure`/`always` edges, `all_parents_must_converge`). `export` writes the
    whole graph and no longer adds the partial-fidelity header comment;
    `apply` reconciles nodes by `id` (create, patch, delete, then edges and
    approval templates) with the usual preview, `--dry-run`, `--check` and
    confirmation, and creates job templates of the same batch before the
    workflow that runs them. Duplicate ids, edges to unknown ids, cycles,
    workflows that run each other and unknown template names are refused
    before any write. Nodes may run management jobs
    (`run: {system_job_template: NAME}`); a node whose template was deleted is
    left out of exports with a warning and left alone by apply. The awx skill's
    `references/specs.md` documents the node format and `--source-ref`.
  - **New:** job template exports carry `instance_groups` by name, in their
    fallback order, and `apply` reconciles them.
  - **New:** `apply` accepts several files and directories as one batch, and
    `apply --source-ref REF PATH...` reads them as they are at a git ref of the
    current repository instead of the working tree (`HEAD` must be pushed;
    symbolic links at the ref are refused).
  - **Behavior change:** membership replacement keeps its rules (adds first,
    same-type credentials removed first and restored if the add fails) and now
    applies them to workflow node credentials and instance groups too.
  - **Behavior change:** workflow template exports now include `nodes`, so
    applying one sets the workflow's graph to the exported one (`nodes: []`
    deletes every node). A document without `nodes`, such as an export from an
    earlier version, still leaves the graph alone.
  - **Breaking:** a rejected token (401) or a missing permission (403) exits
    4, and an unreachable controller, a timeout, a 5xx or a 429 exits 5, for
    every command; `test run` exits 4 or 5 when a launch fails for those
    reasons, instead of 1. `--scm-branch HEAD` that cannot resolve a pushed
    branch, and `test run`/`list`/`validate` without paths and without
    `git`, exit 4, as do `apply --source-ref` with an unpushed commit or
    outside a git repository, `edit` with a broken `$EDITOR`, the private
    edit file failing, and `copy` that AWX refuses (`can_copy: false`). A
    missing required `--var`, `patch --stdin` without `--set`/`--patch-file`,
    selection flag misuse, `edit --field` naming a field that cannot be
    edited, and `--<scope>` on a kind without that scope exit 2. An invalid
    resource file, suite or vars file stays 1. A partial write whose survey
    write AWX refused keeps its cause's code (4 for a rejected token, 5 for an
    outage), `edit` no longer reports a rejected token or an outage while
    preparing the batch as an invalid edit, and `apply --continue-on-error`
    stops at a rejected token of any kind.
  - **New:** AWX API errors are HTTP errors: a bodiless one names its URL,
    and `--verbose` shows the raw response body. A transport failure stays
    `unavailable` instead of becoming a status-less API error.
  - **Breaking:** an `awx.test_result` row that did not pass carries a
    `failure` object instead of the flat `failure_reason`, `failed_tasks`
    and `log_tail`: the same `category`, `system`, `retryable`, `message`
    (replaces `failure_reason`) and `hint` as the `error` of any failed row,
    plus `evidence` (`job_explanation`, the end of `result_traceback`, the
    `related` update that failed first with its real status, and
    `log_tail`, `failed_tasks` and `unreachable_hosts` of the responsible
    execution, so a failed project update shows its own log instead of the
    empty job log, and a `note` such as a log that failed to download).
    `system` says who is responsible: `awx.suite`, `awx.credentials`,
    `awx.controller`, `awx.scm`, `awx.inventory`, `awx.hosts`,
    `awx.playbook` or `awx.expectation` (an error that is not AWX's keeps
    its own, such as `untaped` or `local`). The table shows
    `failure.system` and `failure.message`. See the awx skill's
    `references/test-results.md`.
  - **Breaking:** `awx test run` exits with the most severe case's category:
    a failed inventory update or a credential lookup exits 4; a job that
    ended in `error`, never left `pending` before its timeout, failed only
    on unreachable hosts, or failed (or was checked beyond its status)
    before AWX saved its events exits 5, instead of 1. A preflight failure names `awx.suite` (or
    `awx.credentials`, `awx.scm`) as its `system`.
  - **Breaking:** the `awx.job` record (`jobs wait`) and the `launch` and
    `sync` rows rename `started` and `finished` to `started_at` and
    `finished_at`. These and the `started_at`/`finished_at` of
    `awx.test_result` rows are UTC timestamps to the second
    (`2026-01-02T03:04:05Z`) instead of AWX's strings with microseconds.
  - **Fix:** a case that expects its job to fail no longer passes when the
    job failed because a project or inventory update failed first: the
    playbook never ran, and the case fails as `awx.scm` or `awx.inventory`.
    Failed tasks are read only once AWX has saved the job's events, and a
    task a `rescue` block handled is not a failed task, even on a host that
    failed later: a host that counts N failures failed on its last N failed
    tasks.
  - **Fix:** an `awx test` log check no longer passes (or fails) on a log AWX
    is still writing: the finished job is re-read until AWX has saved its
    events, and a case whose job is still being saved after that is an
    `awx.controller` error (exit 5) naming the job, instead of passing
    `not_contains` on a short log.
  - **New:** with `--format json`, `yaml` or `pipe`, every `awx.test_result`
    row carries `hosts`, each host's PLAY RECAP counters (`ok`, `changed`,
    `failed`, `unreachable`, `skipped`, `rescued`, `ignored`) read once from
    the job's host summaries, cut at 500 hosts with failed and unreachable
    hosts kept first (`hosts_truncated: true`).
  - **Fix:** `jobs logs --follow`, `launch --follow` and `sync --follow` read
    only the new job events on each poll (ANSI colours removed, event output
    in full) instead of downloading the whole log again, which made
    following a long job quadratic; an event AWX saves late is printed in
    order once the ones before it arrive, and one that never arrives is
    warned about. `jobs events --follow` without `--filter` keeps the same
    order. `jobs logs --tail N --follow` reads only the newest events, and
    following a job that already finished downloads its log once. `jobs logs`
    without `--follow` still downloads the log once. A test case's log tail
    also comes from the newest events only.
  - **Fix:** `launch` and `sync` with `--wait --cancel` (or `--follow
    --cancel`) cancel each execution as soon as its own watch stops (a
    polling error, `--timeout`) instead of after every other execution
    finished, so one that failed early no longer runs unwatched meanwhile.
    An execution AWX created while ignoring fields is cancelled before the
    wait starts.
  - **Behavior change:** a launch AWX answers with `ignored_fields` fails its
    row as `invalid` (still exit 1) instead of `failed`, and a launch field
    the template does not prompt for names the field in its error's
    `details`.
  - **New:** failed, partial and conflict rows of `apply`, `patch`, `edit`,
    `delete`, membership changes (workflow node credentials too), launches,
    syncs and job actions carry `error`. `test validate`, `usage` and
    workflow `nodes` report each failed case or target as an attributed
    `error: <item>: …` line (a JSON error line with `item`).
  - **New:** `awx test` cases can expect more under `expect:`, inherited from
    `defaults` like `status` and `log`: `changed` (the most changed tasks
    over every host), `hosts` (per-host bounds on `failed`, `unreachable` and
    `changed`; `"*"` for every host, a named host's bound wins), `failed_tasks`
    (the failed tasks that prove a negative case failed for the right reason)
    and `idempotent: true` (run a passing case again; the rerun must succeed
    and change nothing, and a failure lists the tasks it changed). A failed
    check is `awx.expectation`. These checks read a job's host summaries and
    events (a workflow's, each node job's) only once AWX has saved them; a
    job AWX is still saving makes the case an `awx.controller` error (exit
    5), and a `failed_tasks` entry matched only by a failure the host
    summaries cannot show was unhandled an `awx.expectation` error (exit 1),
    never a pass. Result rows gain
    `rerun_job_id`. `test validate` warns about `status: failed` without
    `failed_tasks`, and refuses `idempotent` with another status than
    `successful`. The awx skill ships an `idempotent.yml` example.
  - **New:** `awx test run --compare FILE` compares the run with the saved
    `--format json` (or `pipe`) output of an earlier run, and
    `--baseline REF` runs every selected case on `REF` first, then compares.
    Each row gains `baseline` and a `change` (`regression`, `unverified`,
    `fixed`, `still_failing`, `pass`, `new`, `removed`); the table adds a
    `change` column and stderr counts each change. Only a regression, an
    `unverified` row or a failing new case fails the run; a case that fails
    as it did in the baseline is reported as `still_failing`. Exit 4 and 5
    still win.
  - **New:** workflow suites. A suite names `workflowTemplate:` instead of
    `jobTemplate:` (exactly one) and its cases launch that workflow.
    `approvals: approve|deny` answers the approvals it waits on; without it a
    pending approval fails the case at once (`awx.suite`). `expect.nodes`
    checks each node's job with a case's checks, plus `status: never_ran`.
    Workflow rows list their `nodes`, and a failed workflow is blamed on the
    node that failed it (`node deploy: …`, with that job's evidence).
    `awx test init TEMPLATE --workflow` writes a starter suite for a
    workflow. The awx skill ships a `workflow.yml` example.
  - **Behavior change:** `awx.test_result` rows gain `nodes` (`null` for a
    job case) and `failure.evidence.node` (`null` for a job case); a
    baseline row gains `node`, and a workflow case still fails the same way
    only in the same node. `awx.test_case` rows gain `workflow_template`, and
    their `job_template` is `null` for a workflow suite.
  - **New:** `awx test run --source-ref REF` runs each suite whose template
    has a spec under `.untaped/awx/` at REF's commit against a temporary copy
    of it (`NAME [untaped-test SHA RUN]`, pinned to the commit), refuses any
    other template that would not run the commit, and always deletes the
    copies after the run (`--keep` keeps them). It cannot go with
    `--scm-branch` or `--baseline`.
  - **New:** `awx test validate --source-ref REF` (and `awx test run
    --dry-run`) checks everything but the writes (each copy's links, its
    free name, the project's branch override, each case against the copy's
    spec) and prints the copies as `awx.provision_outcome` rows, with the
    prompts each enables. `validate` gains `--format` and `--columns`.
  - **New:** `awx test prune [--older-than 2h] [--run RUN] [--dry-run] [--yes]`
    deletes the copies a killed run left behind, found by their name and
    description marker, after a preview and a confirmation;
    `awx.prune_outcome` rows.
  - **Behavior change:** awx tables show a few default columns a human
    scans (`jobs get` no longer prints every AWX field; `jobs list` adds
    `launch_type`, `started` and `elapsed`; outcome tables leave out
    `scope`, `identity`, `partial`, `unverified`, `preserved_secrets` and a
    constant `kind`; `jobs cancel` leaves out the status read before the
    cancel; `schedules list` shows the template it runs instead of an
    always-empty `last_run`; `groups` and `inventory-sources` show their
    `inventory`), and `list`/`get` tables name foreign keys from
    `summary_fields`. `--columns +name` / `--columns=-name` edit them. json
    and yaml now carry whole records where they were narrowed to the table
    columns (`jobs list`, `jobs events`, `unified-templates list`,
    `workflow-templates nodes`); `raw` output is unchanged, except the
    `schedules`, `groups` and `inventory-sources` list columns.
- Ansible
  - **Breaking:** `source refresh` exits 5 when it pauses at the GraphQL
    rate-limit floor, hits a global rate limit, or any repo failed
    transiently; `--refresh` on the graph commands exits 5 only for the
    pause (per-repo failures stay warnings with exit 0).
    `ansible.default_source` naming a missing source, a broken saved source,
    or an index written by a newer untaped exits 4; `impact` (and
    `graph --direction up`) without any source exits 2.
  - **Fix:** live graphs (`--live`, or no source) resolve an unpinned
    dependency's current default branch from GitHub instead of trusting the
    source's recorded one, so a repo that renamed its default branch (keeping
    the old one) is no longer walked at the old branch. An unpinned hop at the
    depth limit, which is not read, stays ref-less.
  - **Behavior change:** each repo a `source refresh` could not index is an
    `error: <repo>: <reason>` line (was `failed <repo>: <reason>`), and the
    final error carries the most severe repo's category.
  - **Behavior change:** `graph`'s tree is easier to read. It opens with the
    target, its data source and depth, lists "used by" above "depends on"
    with `├──`/`└──` connectors (ASCII with the `plain` theme), names the
    file that declares each dependency and whether it is `unpinned`, numbers
    a shared subtree `[n]` and refers back with `see [n]` (was
    `(see above)`), and ends with a count of repos, edges, cycles and
    unresolved dependencies. It is colored on a terminal, and a line too
    wide for it ends in `…` instead of wrapping.
  - **Behavior change:** `graph` prints its warnings on stderr for every
    format, as `deps` and `impact` do; they no longer appear in the tree,
    the Mermaid comments or the `--out` file. `--format json` still carries
    them in `warnings`.
  - **Breaking:** without `--ref`, `deps`, `find` and `graph` read what a
    target depends on at its default branch only, instead of at every cached
    ref (a repo with many tags gave one block or set of rows per tag). Pass
    `--all-refs` for the old result. When the source has not scanned the
    default branch (a tags-only source), every ref is still read, with a
    warning. What depends on
    a target (`impact`, `graph`'s "used by") still covers every ref a
    dependent pins. A local checkout is unaffected.
  - **Breaking:** `graph` defaults to `--depth unlimited`, like `deps`,
    `impact` and `find` (was 3). Pass `--depth 3` for the old result.
  - **Behavior change:** `graph --direction up|down|both` replaces
    `--upstream`, `--downstream` and `--both`, which still work with a
    deprecation warning until the next major release. Refresh hints now
    suggest `--direction`.
- Recipe
  - **Breaking:** recipe errors keep their own category instead of being
    reported as configuration errors: a missing recipe, pack, hook, backup or
    file is `not_found`, an invalid recipe or input `invalid`, local edits a
    `conflict`, a failing hook `failed` (all exit 1). A missing `uv` exits 4,
    as do `backups prune` without `--keep`/`--older-than` or settings and a
    broken `$EDITOR`; a transient git failure fetching a pack exits 5; an
    invalid YAML value in `hooks run --arg`, an invalid `packs add --rev`, and
    `--var`/`--vars-file` combined with `--input-from` for the same input
    exit 2.
  - **Behavior change:** recipe tables show a command's usual columns;
    `--columns ?` marks them and json, yaml, raw and pipe keep every field.
    `apply` shows `target_path`, `action`, `files_changed`, `warnings` and
    `detail` (not `recipe` or `inputs`); `list` shows `pack` and `name`;
    `packs list` leaves out `path` and `commit`; `hooks list` shows `pack`,
    `name` and `module`; `test` shows `pack` only when several packs ran;
    `backups list` shows `id`, `created_at` and `recipe`.
  - **New:** `recipe.backup` rows from `backups list` carry the bundle's
    `created_at` (from the bundle id, as `2026-01-02T03:04:05Z`) and
    `recipe` (from its metadata; `null`, with a warning, when the metadata
    cannot be read).
  - **Breaking:** `recipe.apply_outcome` renames its string field `error` to
    `detail`; a failed row's `error` is now the structured object. Errored
    `recipe.test` rows gain `error` too.
  - **Breaking:** `recipe.check` (`validate`) rows share one shape, with
    fields `name` (the pack, `PACK/RECIPE` ref or built-in hook), `type`
    (`pack`, `recipe` or `hook`), `status`, `path` and `detail`. Scripts
    must read `name` instead of `pack`/`recipe` and `detail` instead of the
    string `error` (`detail` is `null` on a pass), test `status == "fail"`
    instead of `"error"`, and take pack `recipes`/`hooks` counts from
    `packs list`.
  - **Fix:** `packs sync` and `packs remove` print a `failed` row (with
    `detail` and `error`) for a pack that could not be fetched, installed
    or removed; such packs had no row. A `packs remove` that fails to delete
    a pack's files reports it and moves on to the next pack; one that
    stopped partway (some files, or the `packs.toml` row, left behind) is a
    `partial` row (exit 1), and running it again finishes the removal.
    `validate` flags a pack directory with no `pyproject.toml`.
    `recipe.add_outcome`, `recipe.sync_outcome` and `recipe.remove_outcome`
    rows gain `detail` for this, and `remove` rows fill `source`, `rev` and
    `commit` from the removed pack (they were always `null`).
  - **Breaking:** `backups prune` emits `recipe.prune_outcome` rows (fields
    `id`, `size_bytes`, `action`, `detail`) instead of `recipe.backup` rows,
    so a `planned` (`--dry-run`) row is told apart from a `deleted` one.
    Scripts that select prune output by kind must use
    `recipe.prune_outcome`.
  - **Fix:** a bundle `backups prune` fails to delete is a `failed` row with
    `detail` and `error`; it had no row.

## 8.1.0

A backwards-compatible release: shared core helpers (capability SDK 2.1),
`launch`/`sync --cancel`, plain-text token warnings in `doctor`, and AWX/Jira
token environment variables. Several `--vars-file` error messages and edge
cases changed; see the **Behavior change** entries.

- Core
  - **New:** the capability SDK adds `git_toplevel`, `file_lock`,
    `same_origin` and `UiContext.can_prompt`; `read_structured_file` gains
    `flag=` (errors name the CLI flag, as in `--vars-file file <path> …`) and
    `read_stdin_input` gains `allow_empty` (an empty pipe yields nothing
    instead of an error). The additions are backwards compatible:
    `CAPABILITY_API_VERSION` is now `(2, 1)`, and providers declaring
    `((2, 0), (3, 0))` keep composing. Built-in capabilities use these instead
    of their own copies.
  - **Behavior change:** `read_structured_file` (jira `--fields-file`, awx
    `--patch-file` and `--extra-vars @file`, and now every `--vars-file`)
    expands `~`, reads a blank file as no values, rejects non-string keys,
    reports a non-UTF-8 file as `could not read file <path>: …` instead of a
    traceback, and words its errors `file not found: <path>`,
    `file <path> is invalid YAML|JSON: …` and `file <path> must contain a
    mapping`.
  - **Fix:** `skills install --scope local` finds the project root through
    the hardened git runner (an inherited `GIT_DIR` no longer redirects it).
  - **New:** `untaped doctor` warns (a `warn` row, which does not fail it)
    when the config file stores a service token in plain text
    (`<section>.token`), naming `token_command` and an environment variable
    to use instead. A missing token, and one `doctor --online` finds
    rejected, name the same alternatives.
  - A token environment variable alone (such as `JIRA_API_TOKEN`) does not
    make a service configured for `doctor`, `doctor --online` or `setup`:
    the section also needs a `base_url`.
- Workspace
  - **Behavior change:** `foreach --stdin` with an empty pipe runs nothing
    and exits 0 (it used to fail with `no identifiers received on stdin`).
- AWX
  - **Behavior change:** `test run`/`list`/`validate` without paths report a
    missing `git` instead of silently searching the current directory.
  - **Behavior change:** `--vars-file` errors name the flag and the file
    (`--vars-file file not found: vars.yml`, and `could not read --vars-file
    file vars.yml: …` for any other read error), a leading `~` in the path
    is expanded, and a `.json` vars file is parsed as JSON.
  - **New:** without `awx.token` or `awx.token_command`, the token comes from
    `CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN`, then `AAP_TOKEN`, the
    variables the `ansible.controller` collection reads, in its order.
  - `launch --cancel` and `sync --cancel` (with `--wait` or `--follow`)
    cancel every execution the command stops watching (timeout, polling
    error, Ctrl-C, or one AWX created while ignoring fields) instead of
    leaving it running, as `awx test run` does: the row's `detail` ends with
    `cancel requested` (or `cancel failed: …`), and a cancelled execution gets
    no `jobs wait` hint.
  - When AWX refuses to cancel a job because it just ended, `launch`/`sync
    --cancel` and `awx test run` re-read it and report `it ended (<status>)
    before the cancel` with its final status, instead of `cancel failed`.
  - A negative `launch`/`sync --timeout` is now rejected by the option parser
    (`Invalid value "-1.0" for --timeout. Must be >= 0.`, exit 2).
- Recipe
  - `recipe validate NAME` and `recipe test NAME` resolve recipe refs through
    the same resolver as `apply`, `get` and `edit`, so a miss on a name that
    is also an on-disk path now carries the same "pass it as an explicit
    path" hint.
  - **Behavior change:** `recipe hooks get|edit NAME` for a built-in hook
    name falls back to the built-in only when no installed pack exports
    `NAME`; other library errors (an ambiguous ref, an unreadable
    `packs.toml`) are now reported instead, as `apply` already did.
  - `recipe get|edit NAME` hints at `recipe hooks get|edit NAME` when `NAME`
    is a hook exported by several installed packs.
  - **Behavior change:** `--vars-file`/`--args-file` errors name the file,
    an unreadable file is reported as `could not read …` instead of
    `… file not found`, non-string keys are rejected instead of being
    turned into strings, and a `.json` file is parsed as JSON (tab indentation
    works). A file holding an empty list or other non-mapping value (`[]`,
    `false`, `0`, `''`) is now an error (`must contain a mapping`) instead of
    being read as no values.
- GitHub
  - **Fix:** a corpus repo lock that cannot be created is reported as an
    error instead of a traceback.
- Jira
  - **New:** without `jira.token` or `jira.token_command`, the token comes from
    `JIRA_API_TOKEN` (as `jira-cli` reads it).

## 8.0.0

A clean breaking release: the spellings deprecated in 7.x are removed without
aliases, and the capability SDK moves to API version 2.0 (`untaped.api` is
gone; import from `untaped.capability_api`). Only the **Breaking** entries
are kept here; the full entry is in the git history.

- Core
  - **Breaking:** `config set` and `config unset` lost `--target-profile`;
    they write to the active profile, and the root `--profile NAME` selects
    another one (`untaped --profile prod config set awx.token --prompt`).
  - **Breaking:** the capability API version is a `(major, minor)` tuple of
    ints, now `(2, 0)`, so `1.10` can no longer compare equal to `1.1`.
    Providers declare `api_requires = ((2, 0), (3, 0))`; float bounds and
    ranges capped below 2.0 are quarantined with an `api-range` reason that
    names the running version (and, for a missing, malformed or inverted
    range, a range that admits it); `untaped capabilities` shows a malformed
    declaration as written instead of `unknown`.
  - **Breaking:** removed the deprecated `untaped.api` module and the
    `from untaped import X` forwarding at the package root; import from
    `untaped.capability_api`. Names only `untaped.api` published are no
    longer part of the SDK: `ensure_config`, `get_settings`,
    `invalidate_settings_cache`, `read_tool_state`, `mutate_tool_state`,
    `existing_directory`, `missing_setting_error`, `read_stdin_text`,
    `common_kind`, `ThemeSpec`, `DiffStats`/`diff_stats`, and
    `FileChange`/`FileWriteError`/`apply_file_changes`. Use `StateCollection`/
    `StateMap` for state.
  - **Breaking:** capability state is only read from `state.yml`; the
    one-time move of a state section left at the top level of `config.yml`
    (and its warning and `legacy-state` doctor row) is gone. Such a section
    is now an ignored unknown key that `doctor`'s `unknown-keys` row flags:
    move it into `state.yml` by hand before upgrading. (Internally,
    `read_tool_state`/`mutate_tool_state` lost their `config_path` argument.)
  - **Breaking:** removed the `log_level` setting and `UNTAPED_LOG_LEVEL`
    (it never had an effect); `--verbose` is the only logging switch. A leftover
    `log_level` key, in a profile or at the top level, is reported by
    `doctor` as an unknown key.
- Workspace
  - **Breaking:** repo membership commands moved under a `repos` noun:
    `workspace add` → `workspace repos add`, `workspace remove` →
    `workspace repos remove`, and `workspace get` (which listed the declared
    repos) → `workspace repos list`. The deprecated `show` alias is gone. No
    aliases are kept for the old spellings.
  - **Breaking:** the workspace is now the first positional argument, `WS`,
    of every command that acts on one (`repos add|remove|list`, `sync`,
    `status`, `foreach`, `edit`, `branch set|unset|apply`); the
    `--workspace/-w` and `--path/-p` options are removed. `WS` is a
    registered name or a path inside a workspace (`.` is the current
    directory); omitted, it is the workspace containing the current
    directory. `repos add WS URL...` / `repos remove WS REPO...` need it
    before positional repos; `foreach [WS] CMD` and `branch set [WS] BRANCH`
    take it before the command or branch. A path must exist, and workspace
    names can no longer start with `~`.
  - **Breaking:** `sync` and `foreach` run in parallel by default: the new
    `workspace.parallel` profile setting sets the default worker count,
    `min(8, 2 × CPUs)` when unset; `--parallel` overrides it (`-j 1` restores
    serial runs).
- AWX
  - **Breaking:** the per-kind `awx <kind> apply FILE` commands are removed;
    `awx apply` applies documents of every kind.
  - **Breaking:** the deprecated spellings kept until 8.0 are removed:
    `awx save`/`awx <kind> save` (use `export`), `launch --limit` (use
    `--host-pattern`), and `usage -r`/`nodes -r` (use `--recursive`).
    `AwxApiError.status` is gone; use `status_code`.
  - **Breaking:** `launch --inventory` is now `launch --launch-inventory
    NAME|ID` (digits mean an AWX id), the inventory the job runs against.
    `--inventory` is only ever a lookup scope, and launch-capable kinds have
    none.
  - **Breaking:** each resource group offers only the scope options it
    supports: `--organization` on organization-scoped kinds, `--inventory`,
    `--inventory-organization` and `--parent` on hosts, groups and inventory
    sources, `--parent` on schedules. Any other scope option is gone from
    `--help` and is an unknown option (exit 2) instead of a runtime error.
  - **Breaking:** `jobs logs -f` means `--format`, as on every other command;
    `--follow` has no short form.
  - **Breaking:** `launch`/`sync --track` (`-t`) is replaced by `--follow`,
    which waits like `--wait` while streaming each job's log to stderr,
    ending with its PLAY RECAP, each line as AWX stores it (never wrapped or
    tab-expanded; a styled `[template] ` prefix when several executions run;
    workflow jobs print status changes). `--timeout` now
    needs `--wait` or `--follow`. Use `jobs events --follow` for structured
    per-task events.
  - **Breaking:** `awx test` cases declare what their job must produce in
    `expect:`: a `status` (default `successful`) and `log` checks
    (`contains`, `not_contains`, `matches`). The checks can be set in
    `defaults.expect` and overridden per case. The reserved `assert:` block
    is gone. A case may set `timeout:` (or `defaults.timeout` for the suite),
    and `launch:` may be omitted.
  - **Breaking:** `awx test list` writes one `awx.test_case` row per case in
    every format (`suite`, `case`, `job_template`, `organization`, `path`,
    `variables`); the per-suite json/yaml rows with a `cases` list are gone.
- Jira
  - **Breaking:** the Jira-shaped YAML/JSON document flag is now
    `--fields-file` on both `issues create` (was `--template`) and
    `issues patch` (was `--body-file`), with no alias. `issues comment
    --body-file` still reads the comment text.
  - **Breaking:** the new `jira.confirm` setting (`always`, `destructive`,
    `never`; default `destructive`) picks which writes ask first. By default
    only destructive writes ask: `issues transition`, and an `issues patch`
    that sets a field, changes the assignee or has an `update` operation other
    than `add`. `issues create`, `issues comment`, `issues links create` and
    add-only patches are now sent without asking and no longer need `--yes`
    without a terminal; set `jira.confirm: always` for the old behavior.
    `--yes` is unchanged.
  - **Breaking:** write previews (before a prompt and under `--dry-run`) show
    each request's changes as readable lines (`summary: "old" → "new"`,
    `assignee: alice → bob`, `status: To Do → In Progress`) instead of the
    raw JSON body. Patch and transition previews read the issue's current
    values first (one read per issue; an unreadable issue shows `(unknown)`
    in a transition preview); nothing is read when no preview is shown. So
    `issues patch --dry-run` and `issues transition --dry-run` now need
    working credentials, and a patch dry run exits 1 when the issue cannot be
    read; create, comment and link dry runs stay offline.
  - **Breaking:** the deprecated spellings are removed: `me`, `issue`,
    `project`, `board`, `sprint`, `issues edit`, `--field` and
    `--json-field`. Use `whoami`, `issues`, `projects`, `boards`, `sprints`,
    `issues patch`, `--set` and `--set-json`.
- Ansible
  - **Breaking:** `ansible alias` is renamed `ansible source-alias`, with
    record kinds `ansible.source_alias` and `ansible.source_alias_outcome`.
    The old name is gone (`alias` is now the root `untaped alias` command).
  - **Breaking:** the deprecated spellings are removed: `alias add`,
    `source save`/`edit`/`show`, and `--concurrency` (`source refresh`,
    `graph`) and `--output` (`graph`). Use `source-alias set`,
    `source set`/`patch`/`get`, `--parallel` and `--out`.
  - **Breaking:** the ignored `ansible.freshness_ttl` setting and its
    `ansible.deprecated-settings` doctor check are removed; `doctor`'s
    `unknown-keys` row now reports a leftover key.
- Recipe
  - **Breaking:** packs, hooks and backups are nouns. `add`, `sync`,
    `remove` move to `recipe packs add|sync|remove`; `list --packs` is
    `packs list`, and `packs get`/`packs edit`/`packs init NAME` show, edit
    (`pyproject.toml`) and scaffold a pack. `hook run` is `recipe hooks run`;
    `list --hooks` is `hooks list`, and `hooks get`/`hooks edit`/`hooks init
    PACK/HOOK` cover hooks (including built-ins for `get`). `backup …` is
    `recipe backups …`. Recipe verbs stay at the top: `list`, `get` and `edit`
    now act on recipes only, and `init PACK/RECIPE` scaffolds a recipe (the
    `pack|recipe|hook` positional is gone). Old spellings are usage errors
    (exit 2); there are no aliases. `get`/`edit` on a pack or hook name, and
    `init NAME` without a `/`, hint at the `packs`/`hooks` command.
  - **Breaking:** variable flags match `awx test`. `apply --vars-file`
    repeats (a later file wins; `--var` wins over every file). `apply
    --interactive` is gone: a required input that is still missing is
    prompted for when stdin is a terminal, never without one (piped
    `--stdin` targets included); `--non-interactive` (and `--check`) fail
    instead. Optional and defaulted inputs are no longer prompted. Prompts
    now run for every target, in order, before planning starts, so `-j N`
    planning stays parallel and Ctrl-C at a prompt exits 130. The error now
    reads `missing required input: NAME; pass --var NAME=VALUE or
    --vars-file FILE`. `hooks run` takes inputs with `--var`/`--vars-file`
    (were `--input`/`--inputs`) and args with `--arg`/`--args-file` (was
    `--args`), all repeatable.
  - **Breaking:** removed the deprecated `check`, `show`, `new`, `backup
    show` and `apply --vars` spellings, and the hidden no-op `add --yes`.
- GitHub
  - **Breaking:** `--archived` is now `--archived include|exclude|only` on
    `repos list`, `search repos`, `sweep` and `cache sync`, defaulting to
    `exclude` everywhere. It used to mean "only archived" on `repos list` and
    `search repos` (which included archived repos by default) but "include
    archived" on `sweep` and `cache sync`. `--no-archived` and the bare
    `--archived` flag are gone: drop `--no-archived`, and use `--archived
    only` or `--archived include` for the old meanings. `search repos` now
    adds `archived:false` to the query by default, unless the query already
    has an `archived:` qualifier.
  - **Breaking:** removed the deprecated `cache clean` (use `cache delete` or
    `cache prune`), `search --repo-stdin` (use `--stdin`), and the `sweep`
    spellings `-w` (use `--word-regexp`), `--sync` (use `--refresh`) and
    `--no-sync` (use `--cached`).

## 7.1.0

- Core
  - **Behavior change:** after every command, the root warns on stderr when an
    installed agent skill differs from the copy this version ships, or is no
    longer shipped, so agents do not follow stale instructions. The new
    `skills.updates` setting picks `warn` (default), `auto` (update outdated
    skills in place) or `off`. `untaped skills` and `untaped doctor` skip the
    check.
  - New `untaped skills status` (with `--check`), `untaped skills update` and
    `untaped skills remove` manage installed skills, all or by name.
  - `doctor` now finds project-local skills from any subdirectory of a git
    repository, and its `skills` row points to `untaped skills update`
    instead of a `skills install --force` command that installed globally.

## 7.0.0

Major cleanup release: exit codes, confirmations, record kinds and some flags
changed. Renamed commands and flags kept hidden, warning aliases until 8.0.
Only the upgrade notes are kept here; the full entry is in the git history.

**Upgrading.** Scripts that call `untaped` will hit these changes:

- Exit codes: usage errors exit 2 (most were 1), predicate hits
  (`github sweep --fail-on-match`/`--strict`, `recipe apply --check`) exit 3
  (were 1), Ctrl-C exits 130, and a declined confirmation exits 1.
- Write commands now confirm first: jira
  `issues create/patch/comment/transition`, ansible `alias remove`/`source
  remove`, awx launches and syncs of several targets, workspace `remove --prune`/`forget --prune`/`sync --prune`, and
  recipe `apply`/`remove`/`backup restore`/`backup prune`. Without a terminal
  they exit 2 unless you pass `--yes`; `--dry-run` previews and wins over
  `--yes`.
- Renamed commands and flags (`jira me`, `recipe check`, `awx save`,
  `--repo-stdin`, `--concurrency`, ...) keep hidden deprecated aliases that
  print one warning each and go away in 8.0.
- Record kinds and fields are renamed: root kinds are `untaped.*`, mutation
  results are `<cap>.<verb>_outcome` with an `action` field, jira fields are
  snake_case with `*_at` UTC timestamps, file records carry an absolute
  `target_path`, and `github search repos`/`search users` emit
  `github.repo_hit`/`github.user_hit`. Pipe and JSON consumers keyed on the
  old names must update.

## 6.0.1

- `github sweep` now retries transient Git transport failures (dropped TLS/TCP
  connections, `early EOF`, HTTP 429/5xx) with short backoff instead of
  reporting the repository as unscanned.
- Wide sweeps (`--refs branches|tags|all`, `--ref GLOB`) list remote refs first
  and fetch only new or moved refs in bounded batches, so interrupted refreshes
  resume where they stopped and unchanged repositories skip fetching.

## 6.0.0

- Added consistent AWX bulk patch and external-editor workflows across eight
  writable object types, with complete preflight, conflict checks, one batch
  confirmation, and per-object outcomes.
- Added inventory and inventory-source management, including constructed
  inventories and explicit inventory refresh through `sync`.
- Standardized project and inventory refresh, execution tracking, and failure
  reporting.
- Breaking changes: use `patch` or `edit` instead of `apply --stdin`; use
  `--continue-on-error` instead of `--fail-fast`; use `projects sync` instead
  of `projects update`. `apply FILE` remains declarative.
- Updated AWX user guidance and packaged skills. Private capabilities require
  `untaped-private` 1.2.0 with this release.

## 5.0.0

- Removed the orchestration capability, its configuration section, and packaged
  skill. No compatibility command is provided.
- Planning now uses GitHub Issues and Projects; architectural rationale lives
  in plain Markdown decisions in the owning repository.
- The six remaining public capabilities keep their existing command roots.
- Private capabilities require `untaped-private` 1.1.0 with this release.

## 4.0.0

- Completed the unified v4 release boundary: one `untaped` wheel and source
  archive, a public source-provenance manifest, and shared local/published
  executable smoke coverage for all seven built-in capabilities.
- Added a restartable, exact-identity release path that validates the reviewed
  candidate, GitHub draft assets, package-index hashes, published smoke, and
  final GitHub draft publication without overwriting immutable release state.
- Preserved imported standalone source history and stable skill IDs while
  documenting the seven root capability commands.

## Earlier history

The standalone `untaped-workspace` 0.x releases and the imports that merged it
into `untaped` are in the git history before 4.0.0.
