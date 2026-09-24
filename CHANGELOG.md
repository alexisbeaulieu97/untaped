# Changelog

## Unreleased

CLI polish: root options, broken pipes, structured output and root
mutations. Items marked **behavior change** alter output, exit codes, or
defaults.

- Core
  - Fixed: root options (`--profile`, `--verbose`/`-v`, `--quiet`/`-q`)
    placed between command names, as in `untaped github --profile work
    whoami`, failed with "Unknown command". They now work before, between
    and after command names at any depth.
  - Fixed: `untaped --help | head` (help into a closed pipe) exited 1 through
    Rich's broken-pipe handler. It now exits 0 quietly, like data commands.
  - `config set/unset` and `profile create/delete/rename` take `--format`,
    `--columns` and `--dry-run`, and print `untaped.setting_outcome` /
    `untaped.profile_outcome` records (table by default) after the existing
    stderr message. `--dry-run` validates and writes nothing; `profile delete
    --dry-run` shows the preview without prompting.
  - Mapping and list settings (`ui.symbols`, `ui.color_roles`,
    `ansible.dependency_paths`) show up in `config list/get` (compact JSON in
    table/raw, native values in json/yaml/pipe). `config set KEY VALUE` takes
    their whole value as JSON or YAML, validated against the setting's type,
    and `config unset` removes the whole key. They were "unknown setting"
    before.
- awx
  - **Behavior change:** `jobs events` and `jobs logs` with several ids print
    one json/yaml array instead of one document per job. Event and log rows
    carry the job id as `job` (first in the default json/yaml event columns;
    log rows are `{job, line}`, and `raw`/`table` still show the line).
    `--follow --format json` still streams NDJSON.

Correctness and safety fixes from a whole-codebase review. Items marked
**behavior change** alter output, exit codes, or defaults.

- Core
  - **Behavior change:** capability state (workspace registry, ansible
    aliases and sources, third-party state) moves from the top level of
    `config.yml` to a separate state file beside it: `state.yml` for
    `config.yml`, `<name>.state.yml` for any other `UNTAPED_CONFIG` name
    (override with `UNTAPED_STATE`). Existing state keeps working: it is read from
    `config.yml` with a one-time deprecation warning and moved to `state.yml`
    on its next change, keeping `config.yml`'s comments. `config`/`profile`
    writes never touch `state.yml`. `doctor` checks the state file and adds a
    non-failing `legacy-state` `warn` row for sections left in `config.yml`.
  - `config set` validates the raw value against the setting's type instead of
    parsing it as YAML: `#` no longer truncates secrets, and numeric strings
    such as `0123456` are accepted for string settings. **Behavior change:**
    for string settings `null` is now stored literally; it still clears
    optional non-string settings. Use `config unset KEY` to clear a setting.
  - `config` and `profile` commands validate only the section they touch, so a
    broken section can be repaired from the CLI (including with
    `config set KEY --prompt`). Non-mapping config shapes are
    reported as config errors instead of tracebacks.
  - Capability settings are validated per section on first use: an invalid
    value in one capability's section no longer breaks other capabilities'
    commands, and the error names the section and config file. `doctor` and
    `config list` still report every invalid section. **Behavior change**
    for SDK code: `AppContext` no longer accepts a `settings=` argument; get
    one from `app_context()`.
  - Config writes (`config set/unset`, `profile`, capability state) preserve
    comments, key order, and formatting in `config.yml`, rewriting only the
    changed keys (`profile rename` keeps the profile in place with its
    comments). **Behavior change:** keys are no longer sorted alphabetically
    on write; new keys are appended.
  - `doctor` reports one row per core, capability, and state section,
    including `UNTAPED_*` overrides and the selected profile.
  - **Behavior change:** `config list/get` and `profile list` emit native
    values (`null`, booleans, numbers) in json/yaml/pipe; glyphs remain in
    tables only. A closed output pipe exits 0. `--verbose` with `--quiet` is a
    usage error. `--columns` accepts comma lists and rejects unknown columns
    for typed records.
  - `profile current` honors `--profile`; a missing, unreadable, or invalid
    `http.ca_bundle`, an unknown `ui.theme`, and cross-origin pagination links
    are reported clearly. Config writes create the temporary file with mode
    0600.
- workspace
  - Repo and workspace names must be a single safe path segment; the bare
    cache path is sanitized.
  - **Behavior change:** `forget --prune` deletes only managed clones and the
    manifest, and leaves other files in place. `sync --prune` now confirms
    (`--yes` to skip). `sync` and `branch apply` report failures as `failed`
    and exit 1. `branch apply` no longer creates missing branches unless
    `--create` is passed.
  - Git never runs in an enclosing repository, does not wait for interactive
    credential prompts (ssh runs in `BatchMode` unless `GIT_SSH_COMMAND`,
    `GIT_SSH`, or `core.sshCommand` is set), and fast-forwards from the branch's upstream. The bare cache
    now actually refreshes; new clones copy objects out of it
    (`--dissociate`) and it is never auto-gc'd, so pruned cache branches
    cannot corrupt clones. Ctrl-C stops queued work and every running child
    process, including under `foreach --parallel`; `foreach` streams rows as
    repos finish.
- github
  - `sweep --grep` always uses extended regular expressions, so `a|b` works
    and git config cannot change results.
  - `search code`/`search issues` batch repo scopes within GitHub's operator
    limit instead of truncating or failing.
  - CODEOWNERS matching follows GitHub's gitignore-style rules.
  - One bad repo no longer aborts a sweep; failed refreshes that fall back to
    cached copies are reported. `cache clean --all --org X` only removes X.
    Branches and tags with the same name are both scanned.
- jira
  - **Behavior change:** `issue assigned --jql` now ANDs the given JQL with
    `jira.assigned_jql` instead of replacing it, so results stay limited to
    your assigned issues. Use `issue search --jql` for an unrestricted query.
  - Bare `issue search` uses `jira.assigned_jql`. JQL starting with
    `ORDER BY` and sprint functions render correctly. `issue get` shows
    description, type, priority, reporter, labels, created, and resolution.
- awx
  - `--extra-vars` is sent as a mapping (`KEY=VAL`, `@file`, or JSON/YAML).
    `KEY=VAL` decodes only `true`/`false`/`null`, integers, and JSON
    objects/arrays (`version=1.10` stays a string); YAML dates become ISO
    strings, and values JSON cannot carry are a usage error.
  - **Behavior change:** launch checks the template's ask-on-launch flags
    first and fails rows whose fields AWX ignored; a value equal to the
    template's own is allowed, and extra vars outside a survey are rejected
    when the template prompts only through its survey. Launching or syncing
    more than one target asks for confirmation (`--yes` to skip). `patch`
    rejects unknown fields that look like typos of a known field, with a
    "did you mean" hint (`--allow-unknown-fields` to opt out); other unknown
    fields are sent with a warning. `jobs list` defaults to 20 rows, and
    `--limit 0` means no limit on every awx list.
  - **Behavior change:** apply scopes documents without an organization by
    `awx.default_organization`; `spec.organization` and an explicit
    `metadata.organization: null` (now written by `save` for org-less
    records) take precedence. Directory apply reads `*.yaml` as well as
    `*.yml`, and read errors name the file.
  - Relationship replacement adds members before removing old ones (same-type
    credentials excepted) and restores removed members if an add fails.
    `patch`/`apply` know more AWX fields (execution environments, project
    signature credential, workflow tags, schedule job type).
  - Replacing a credential with one of the same type works. `patch --set`
    keeps string fields as strings. Ctrl-C interrupts waits and submissions
    and prints the executions not known to have finished. `save --out`
    writes through symlinks, FIFOs and `-` (stdout). `ping` validates the
    token. Fewer API requests for scoped selection, `list --limit`, and
    `delete`.
- recipe
  - Writes preserve file permissions. Glob steps work through symlinked
    targets. One broken installed pack no longer breaks every command.
    `yaml_edit` leaves files untouched when nothing changes.
  - **Behavior change:** `hook run` no longer runs hooks from the current
    directory implicitly (use `--project`), and bare hook names inside a pack
    resolve to that pack's hooks and built-ins only (use `pack/hook` across
    packs). The hook worker runs isolated from untaped's own modules.
- ansible
  - `graph ./repo` resolves the origin through git (worktrees, duplicate
    config keys) and git output parsing no longer depends on the locale.
  - Shared dependencies are expanded once, fixing exponential time and
    output on large graphs.
  - Versions such as `1.10` stay strings, repo names match
    case-insensitively, GitHub Enterprise URLs resolve, and unpinned
    dependents are included for the default branch. The first refresh after
    upgrading rescans each source: an outdated dependency index is rebuilt
    automatically (with a warning) instead of asking you to delete it.
  - **Behavior change:** graph node ids fold the repo part to lowercase
    (`Acme/Base@v1` becomes `acme/base@v1` in json/yaml ids and edge
    endpoints); labels keep the display casing.
- Tests run hermetically: local runs no longer read the developer's config or
  depend on terminal width, and coverage is gated in CI at 89%.

Shared infrastructure consolidation (git, recipe, awx). Items marked
**behavior change** alter output, exit codes, or error text.

- Core
  - New `untaped.git` module (`run_git`, `GitResult`, `GitCommandError`,
    `git_auth_header`, `safe_cache_path`, `safe_path_segment`, re-exported by
    `untaped.api` and `untaped.capability_api`): every capability's git
    subprocesses now share one hardened runner (no prompts, stdin closed, ssh
    `BatchMode` unless ssh is configured, C locale, inherited `GIT_DIR`-style
    variables dropped, auth header only in a private temporary include file
    and redacted from errors, timeouts and bounded transient retries).
  - `bounded_map` gains an optional `while_running` hook for foreground work
    on the calling thread while workers run.
- workspace, github
  - Git runs through `untaped.git`. Workspace git error messages are now
    always in English (C locale).
- ansible
  - The GitHub token is sent only to each repository's own HTTPS origin,
    never to other hosts or to non-HTTPS remotes.
- recipe
  - `recipe add` from git runs through `untaped.git`: it never prompts
    (ssh in `BatchMode`) and times out after 10 minutes.
  - **Behavior change:** a missing lockfile is reported as
    `pack project is missing uv.lock`, and pack problems are reported in the
    order contract, lock, modules, recipes.
  - **Behavior change:** `recipe show` json/yaml replaces `file_or_files`
    with `files`, `globs`, and `exclude`.
  - **Behavior change:** a non-string `[project].name` or a malformed
    `[tool.untaped_recipe.recipes]` table is an error; `new recipe|hook` on
    a pack that fails to load reports why.
  - **Behavior change:** scaffolded packs (and the missing-dependency hint)
    use `untaped>=<installed version>,<next major>` as the dev requirement.
- awx
  - Batch writes, selected actions (`launch`, `sync`, `delete`, ...), `awx
    test`, and `--wait`/`--track` share one bounded scheduler, and workers
    see `--quiet`. Ctrl-C during a serial submission lets the in-flight
    request finish so its execution is reported.
  - The mutation engine's patch/edit guard now rejects the same identity and
    ancestry fields the `patch`/`edit` commands already rejected (one
    `ResourceSpec.immutable_fields` set).

Startup and SDK surface.

- Built-in capability command trees load lazily: `untaped --help` and
  `untaped <capability> ...` import only the selected capability's CLI
  (about a third fewer modules; root `--help` roughly 0.85s to 0.55s).
  `CapabilitySpec` gains an optional one-line `help` for the root listing, and
  each app factory now runs at most once per composition (external factories
  were called twice).
- `untaped.capability_api` is the single public SDK surface for built-in and
  external capabilities. It now also exports the shared helpers built-ins
  use (HTTP client and pagination, `bounded_map`, `batch_apply`, output and
  argument helpers, `atomic_write`, `StateMap`, ...). The additions are
  backwards compatible: `CAPABILITY_API_VERSION` is now `1.1`, and providers
  declaring `(1.0, 2.0)` keep composing.
- `untaped.api` is deprecated: it still re-exports every name it published
  (without a warning) and will be removed in a later release. Import from
  `untaped.capability_api` instead.
- **Behavior change:** the package root no longer star-exports the SDK
  (`from untaped import *` and `untaped.__all__` are gone). `from untaped
  import X` still resolves `untaped.capability_api` and former `untaped.api`
  names lazily, so importing `untaped` loads nothing; this is deprecated and
  goes away with `untaped.api`. `untaped.app_context` now names the submodule
  rather than the function.

UX conventions: every command follows one set of rules for exit codes,
messages, flags, confirmations and records (see `docs/conventions.md`).

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
  print one warning each and go away in 7.0.
- Record kinds and fields are renamed: root kinds are `untaped.*`, mutation
  results are `<cap>.<verb>_outcome` with an `action` field, jira fields are
  snake_case with `*_at` UTC timestamps, file records carry an absolute
  `target_path`, and `github search repos`/`search users` emit
  `github.repo_hit`/`github.user_hit`. Pipe and JSON consumers keyed on the
  old names must update.

- Core
  - Exit codes follow one contract: 1 failure or declined confirmation, 2
    usage error, 3 predicate hit, 130 interrupted. **Behavior change:** Ctrl-C
    exits 130 without a traceback, including at a prompt (was 1). Declining
    the confirmation of workspace `remove --prune`/`forget --prune`, recipe
    `backup restore`/`backup prune`, github `cache clean` or `profile delete`
    prints `cancelled; no changes made` and exits 1 (was a silent exit 0, or
    `delete cancelled`). A command that must confirm but has no terminal exits
    2 (was 1). Mixing positional identifiers with `--stdin`, passing
    none, and `--body`/`--body-file` together exit 2.
  - Confirmations reach the controlling terminal when stdin carries piped data,
    so `... --format pipe | untaped workspace remove --stdin` prompts instead of
    refusing. Without a terminal, pass `--yes`.
  - `untaped.capability_api` adds (API 1.1, backwards compatible): `UsageError`,
    `OperationCancelledError`, `ExitCode`, `plural`, `q`, `not_found`, `hint`,
    `summary`, `YesOption`, `DryRunOption`, `StdinOption`, `ParallelOption`,
    `LimitOption`, `OutcomeRecord`, `TargetRecord`, `CheckRecord`,
    `UtcTimestamp`, `read_stdin_input`, `StdinInput`, `read_records` and
    `deprecated_alias`, which keeps a renamed command or flag working as a
    hidden spelling that prints a deprecation warning.
    `read_identifiers`/`read_records` accept `accept_kinds` (a record of another
    kind exits 2). `UiContext` gains `success`, `confirm_action` and `terminal`.
    `finish()` takes `predicate_hit`, and `BatchOutcome` gains `cancelled`.
    `untaped.testing.invoke_cli` gains `terminal=`.
  - Fixed: `untaped awx job_templates --help` (any underscore or camelCase
    spelling of a multi-word command) crashed with a `KeyError`. It now
    resolves to the registered name.
  - Fixed: the `config list` warning for an invalid section repeated itself. It
    is now one sentence.
  - `http.proxy` (and any URL setting) no longer prints a `user:password@`
    password in `config list/get` or `profile show` unless you pass
    `--show-secrets`.
  - **Behavior change:** root pipe records use `untaped.*` kinds
    (`untaped.setting`, `untaped.profile` (was `profile.profile`),
    `untaped.capability`, `untaped.doctor_check`, `untaped.skill`). "Profile not
    found" errors read `profile not found: 'x'; known: …` with a `hint:` line.
    The meaningless `--empty-columns` flag is gone, and so are `--no-*`
    flags for root options that default to off (`--no-show-secrets`,
    `--no-stdin`, ...). `skills install` takes names positionally only (no
    `--skill-names`).
  - `tests/conventions/` lints the help tree, messages, capability structure and
    layer imports against per-capability baselines that may only shrink.
  - Records built on `OutcomeRecord`/`TargetRecord` list their own fields
    before the inherited `action`/`target_path`, so the identifying field
    leads tables and `--format raw`.
  - `UiContext.styled` prints Rich-styled lines (color only on a terminal).
    `DoctorResult` gains `warn=False`: a capability check can report a `warn`
    row, which does not fail `doctor`.
  - Fixed: typing `y` at a `[y/N]` confirmation re-prompted with "Please
    answer y or n." because the default letter was pre-filled; Enter alone
    now takes the default.
- github
  - **Behavior change:** `github search repos|code|issues --repo-stdin` →
    `--stdin` (deprecated alias kept). Stdin only accepts `github.repo`,
    `github.repo_hit` or `github.sweep_repo` records; any other kind exits 2.
    `sweep --stdin` follows the same rule.
  - **Behavior change:** `search repos` emits `github.repo_hit` and `search
    users` emits `github.user_hit`; `github.repo` and `github.user` each have
    one schema.
  - **Behavior change:** `cache clean` is replaced by `cache delete
    REPO...|--all` and `cache prune --org` (both with `--yes`/`--dry-run`);
    `cache clean` still works but warns it is deprecated.
  - **Behavior change:** `sweep --fail-on-match`/`--strict` exit 3 instead of
    1. `--sync/--no-sync` → `--refresh/--cached`, `-w` → `--word-regexp`
    (deprecated aliases kept).
  - **Behavior change:** usage errors exit 2 instead of 1 (malformed `--team`,
    missing scope, bad `--regex`/grep pattern/pathspec, cache selection
    errors, `--parallel 0`, `--team` with `--cached`).
  - **Behavior change:** `cache worktree REPO`, `repos list PATTERN` and the
    search `QUERY` are positional-only; the `--empty-*` flags are gone.
  - Added `repos list --limit`, a `url` (web URL) field on
    repo/code/issue/user records, and a `repo` field on repo records.
  - An HTTP 401 from GitHub now hints `untaped config set github.token
    --prompt`.
- jira
  - **Behavior change:** `jira me` → `jira whoami`; groups
    `issue`/`project`/`board`/`sprint` →
    `issues`/`projects`/`boards`/`sprints`; `issue edit` → `issues patch`;
    `--field`/`--json-field` → `--set`/`--set-json` on create/patch (old
    spellings are hidden deprecated aliases).
  - **Behavior change:** records use snake_case (`display_name`,
    `email_address`, `issue_type`, `project_type_key`, `origin_board_id`);
    `updated`/`created` → `updated_at`/`created_at`; sprint
    `startDate`/`endDate` → `start_at`/`end_at`; timestamps are UTC `…Z`
    (unparseable → null); `self` → `api_url`; issue rows gain `api_url`.
  - **Behavior change:** `create`/`patch`/`comment`/`transition` emit
    `jira.issue_outcome` (`action`
    created/updated/commented/transitioned/planned, plus
    key/id/url/api_url/transition_id/comment_id) instead of
    `jira.issue`/`jira.comment`.
  - **Behavior change:** these writes now confirm before sending and print the
    REST request; without a terminal they need `--yes` or exit 2 (unattended
    scripts must add `--yes`).
  - Added `--dry-run` on these writes (request on stderr, `planned` outcome;
    wins over `--yes`).
  - Added: `issues get`/`issues transition` accept several keys or `--stdin`
    (per-key failure → exit 1); `--stdin` accepts `jira.issue` and
    `jira.issue_outcome`.
  - **Behavior change:** usage mistakes exit 2 instead of 1 (blank `--jql`,
    both/neither of `--id`/`--to`, `sprints list` without a board id).
  - Jira errors: 401 hints `untaped config set jira.token --prompt`; 404 reads
    `issue not found: 'KEY'`; other HTTP errors include Jira's own messages;
    an unknown transition lists the known ones.
- ansible
  - **Behavior change:** `alias add` → `alias set`; `source save` → `source
    set`, `source edit` → `source patch`, `source show` → `source get` (hidden
    deprecated aliases).
  - **Behavior change:** `--concurrency` → `--parallel/-j` on `source refresh`
    and `graph` (values over 32 are capped with a warning instead of
    rejected); `graph --output` → `--out/-o` (aliases kept).
  - **Behavior change:** `alias set/remove` and `source set/patch/remove` take
    `--format`/`--columns` and emit
    `ansible.alias_outcome`/`ansible.source_outcome` on stdout instead of a
    stderr success line.
  - **Behavior change:** `alias remove` and `source remove` confirm first;
    without a terminal they need `--yes` (exit 2); a decline exits 1; added
    `--dry-run`.
  - **Behavior change:** flag problems exit 2 instead of 1 (invalid alias
    target, source definition errors, `source patch` with no flags,
    `--ref-scan-default` with `--clear-ref-scan-default`). Unknown names read
    `source not found: 'x'; known: …`.
  - **Behavior change:** `source status` states are
    `not_refreshed`/`missing_source`; `scanned_at` renders as
    `2026-01-02T03:04:05Z`.
  - `doctor` reports a non-failing `ansible.deprecated-settings` `warn` row
    while `ansible.freshness_ttl` is set.
  - Refresh warnings go through the UI, the summary is muted by `-q`, and
    counts are pluralized correctly. ansible now reads GitHub settings through
    github's public facade.
- workspace
  - **Behavior change:** sync actions are
    `cloned`/`pulled`/`unchanged`/`skipped`/`removed`; branch-apply actions
    are `checked_out`/`unchanged`/`skipped`. Sync, status, foreach, branch
    apply and `get` rows carry an absolute `target_path`.
  - **Behavior change:** `workspace show` → `get` (hidden deprecated alias).
  - **Behavior change:** `init`/`add`/`remove`/`forget`/`branch unset` emit
    `workspace.<verb>_outcome` rows on stdout and take `--format`/`--columns`;
    `remove` gains `--dry-run`.
  - **Behavior change:** `foreach -j 0` exits 2 (it ran serially);
    `--repo-name` with several URLs exits 2; `edit` exits 1 with `error:
    editor exited with status N` instead of passing the editor's code through.
  - **Behavior change:** the sync summary reads `sync: 2 cloned` / `sync:
    nothing to do`.
  - `add --stdin` accepts `github.repo`, `github.repo_hit`,
    `github.sweep_repo` and `workspace.repo` records, so `github search repos
    --format pipe | workspace add --stdin` works; `remove --stdin`/`path
    --stdin` check record kinds (other kinds exit 2). Success lines are muted
    by `-q`.
- recipe
  - **Behavior change:** `recipe check` → `validate`, `show` → `get`, `backup
    show` → `backup get`, `new pack|recipe|hook` → `init pack|recipe|hook`,
    `apply --vars` → `--vars-file` (old spellings warn).
  - **Behavior change:** `apply` records are kind `recipe.apply_outcome`:
    `status` → `action`
    (`planned`/`applied`/`unchanged`/`skipped`/`cancelled`/`failed`; old
    `check`/`dry-run` become `planned`/`unchanged`), `target` → absolute
    `target_path`, `warnings` is a list, `error` is null when nothing failed.
  - **Behavior change:** `apply --check` exits 3 on drift (was 1). Declining
    the prompt in `apply`/`remove` exits 1 with `cancelled; no changes made`
    (`remove` exited 0).
  - **Behavior change:** `add` never prompts (`-y` is a hidden no-op) and
    prints a `recipe.add_outcome` table (`action` created/updated) instead of
    the bare pack name.
  - `remove` gains `--dry-run`/`--format` and emits `recipe.remove_outcome`;
    `backup prune`/`backup restore` gain `--dry-run`.
  - **Behavior change:** usage errors exit 2 (conflicting flags, missing
    targets, `--keep`/`--older-than`/`--hook-timeout` ranges, `--rev` on a
    local path, `test --update` without a ref, no terminal).
  - `hook run` failures print as `error: <traceback>`; `add`/`init` notes go
    through the UI (`warning:` prefix, info muted by `-q`).
- awx
  - **Behavior change:** `awx save` / `awx <kind> save` → `export`; `launch
    --limit` → `--host-pattern`; `jobs logs -f` → `--follow`; `usage
    -r`/`nodes -r` → `--recursive`; `input_inventories`/`instance_groups` →
    kebab-case (old spellings warn).
  - **Behavior change:** names/ids are positional-only and other options
    keyword-only (`ping json` → `ping -f json`); list `--empty-*` flags are
    gone.
  - **Behavior change:** `get` (per kind, `jobs get`, `unified-templates get`)
    defaults to a table; `list -f json/yaml/pipe` returns full records.
  - **Behavior change:** `--yes` with `--dry-run` is allowed (dry-run wins);
    declining a prompt exits 1 with `cancelled; no changes made`; no terminal
    exits 2; `--allow-unverified` without `--yes` and conflicting
    selection/scope flags exit 2.
  - **Behavior change:** `--stdin` rejects records of the wrong kind (exit 2)
    and empty stdin is an error (it selected nothing and exited 0).
  - **Behavior change:** launch/sync rows are
    `awx.launch_outcome`/`awx.sync_outcome` (accepted by `jobs --stdin`); the
    `preview` action is `planned`; `fields_changed`/`preserved_secrets` are
    lists.
  - `jobs events --follow` and `launch --track` print live events through the
    shared UI. Every parameter has help text; HTTP 401 hints `untaped config
    set awx.token --prompt`; `test run` gains `-j`.

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

## Historical imports (Wave 1.5)

- Imported `untaped-workspace` (approved OID `f9c2fd6`) as the built-in
  `untaped workspace` capability with history preserved. Standalone
  packaging (console script, per-tool version, release workflow, lockfile)
  is retired; behavior is preserved per the Wave 1.5 parity matrix.

## Standalone history (`untaped-workspace`, preserved)

## 0.11.0 - 2026-07-03

- Adopted `untaped>=3.0.0,<4` and renamed pipe kinds rejected by SDK 3.0
  validation: `workspace.summary` → `workspace.repo.summary`,
  `workspace.sync-outcome` → `workspace.sync_outcome`,
  `workspace.foreach-outcome` → `workspace.foreach_outcome`, and
  `workspace.branch-outcome` → `workspace.branch_outcome`.
- Changed `remove --prune` and `forget --prune` to use the SDK batch
  preview/confirmation contract. Declining a prompt exits cleanly without
  mutation, and noninteractive prune still requires `--yes` / `-y`.
- Moved workspace registry storage to the SDK `StateCollection` helper; duplicate
  workspace names and paths remain workspace-specific errors, while malformed
  registry collection shape now uses SDK state wording.

## 0.10.0 - 2026-06-28

- Changed `foreach` to close child stdin and apply a 600s default per-repo
  timeout. Timed-out commands return `124` with a `timed out after <Ns>s`
  stderr detail; use `--timeout N` for longer-running commands.
- Changed `sync --all` and `status --all` to keep going when a valid registry
  entry points at a workspace whose manifest is missing, unreadable,
  YAML-invalid, or schema-invalid. These cases now emit workspace-level
  `action="unavailable"` rows with `repo=""` and a detail message.
- Added `action` and `detail` fields to `status` structured output. Normal
  rows use `action="status"`.

## 0.9.0 - 2026-06-28

- Added `target_path` to repo-grain `show --format pipe` records so downstream
  tools can consume the concrete repo checkout path without branching on
  `workspace.repo`. Empty workspace summary rows are tagged
  `workspace.summary` and omit `target_path`.

## 0.8.0 - 2026-06-27

- Changed prune safety so `sync --prune` no longer deletes clean orphan
  clones with commits or local tags not reachable from local
  remote-tracking refs. Unsafe orphans are skipped with `unsafe local
  state: ...`; corrupt/uninspectable or symlinked orphans are also
  skipped. `sync --prune` remains prompt-free and has no `--yes`.
- Changed `remove --prune` and `forget --prune` to refuse clones with
  stash entries or commits/local tags not reachable from local
  remote-tracking refs, not just dirty worktrees, before mutating
  manifests, registry state, or files.
- Fixed `forget --prune` to inspect immediate child git clones that are
  not declared in the manifest before deleting the workspace directory,
  while skipping symlinked child entries because workspace deletion only
  unlinks them.

## 0.7.0 - 2026-06-22

- Changed `sync --parallel` / `sync -j` to mean concurrent repo sync jobs for
  both single-workspace sync and `sync --all`. Previously, `sync --all -j`
  capped concurrent workspaces.
- Added repo-oriented sync progress and a stderr summary while keeping
  `SyncOutcome` output rows unchanged for `json`, `yaml`, `raw`, and `pipe`
  formats.
- Avoided redundant bare-cache fetches after a fresh bare clone and kept
  existing local clones from touching the bare cache during sync.
