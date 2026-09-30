# Changelog

## 9.0.0

A major release that makes untaped a harness for AI agents. Every failure
says what kind it is and which system is responsible, and selects the exit
code: **4** means fix the environment, **5** means retry later (capability
SDK 3.0; see the **Breaking** entries). `awx test` explains why a case failed
and gains regression checks, baseline comparison, workflow suites and
temporary test sets from a git ref (`--source-ref`). Every built-in skill is a
self-contained manual for the installed CLI.

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
  - **Behavior change:** an outcome record's `action` comes right after the
    field identifying the row (`repo`, or `id` and `name`) instead of last, in tables
    and in json/yaml key order. Fields are unchanged.
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
gone; import from `untaped.capability_api`). Read the **Breaking** entries
below before upgrading scripts or providers.

- Core
  - **Breaking:** `config set` and `config unset` lost `--target-profile`;
    they write to the active profile, and the root `--profile NAME` selects
    another one (`untaped --profile prod config set awx.token --prompt`).
  - **New:** the `ui.format` setting and the `UNTAPED_FORMAT` environment
    variable (which wins) replace the `table` default of the shared
    `--format` option; an explicit `--format` still wins. Its help reads
    `[default: table, or UNTAPED_FORMAT / ui.format]`.
  - **Behavior change:** table and styled output that does not go to a
    terminal is no longer wrapped at 80 columns; `COLUMNS` still bounds it.
  - **New:** `untaped setup`, an interactive wizard that configures a profile's
    service capabilities (base URL plus a typed token, a `token_command`, or
    the current one), creating the profile when new, then checks them online.
    Without a terminal it exits 2.
  - **New:** `untaped doctor --online` also runs online checks
    (`awx.api`, `github.api`, `jira.api` authenticate against the service),
    each with one attempt and a timeout capped at 10 seconds, and each failed
    row names the command that fixes it. Providers contribute
    them with `online_check(...)` or `DoctorCheck(..., online=True)`;
    `DoctorResult(..., fix=...)` appends the fix.
  - **New:** `untaped alias set NAME -- COMMAND ARGS…`, `alias list` and
    `alias remove NAME` manage per-profile command aliases in the new
    `shell.aliases` setting; `untaped NAME [ARGS…]` runs the stored argv with
    `ARGS` appended. Aliases never shadow built-in commands and never expand
    another alias.
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
  - `doctor`'s `unknown-keys` row also flags top-level `config.yml` keys other
    than `active` and `profiles`.
  - The `deprecated_alias` warning now says an old spelling will be removed
    "in the next major release" instead of naming 8.0. No built-in command
    has deprecated spellings left; the helper remains for providers.
  - **New:** `UiContext.styled(text, tail=...)` prints `tail` verbatim after
    the styled text (never wrapped or tab-expanded), for streamed log lines
    behind a styled label.
  - **Fix:** config and state writes go through a symlinked `config.yml` or
    `state.yml` instead of replacing the link with a regular file.
  - **Fix:** `config edit` edits a private copy, validates it, and only then
    saves it under the config lock, owner-only (`0600`), through a symlink and
    with its line endings. An invalid result, a config file changed meanwhile,
    or a failed save leaves `config.yml` unchanged (exit 1) and names the copy
    holding your edits; before, the invalid file was kept. Saving without
    changes writes nothing.
  - **Fix:** `atomic_write` (and `apply_file_changes`) now fsync the file and
    its directory, keep an existing file's permission bits (or apply `mode=`,
    which the file has from the moment it is created), and write through
    symlinks (never creating directories for a dangling link's target).
    Config writes, file transactions and the GitHub corpus metadata use this
    one helper.
  - **Fix:** with `skills.updates: auto`, the per-run skills check only warns
    after a failed command or a preview (`--dry-run`, `--check`); it no
    longer rewrites installed skills then.
  - **Fix:** git's `core.sshCommand` is looked up in the repository a git
    command runs in, not the current directory, before untaped defaults ssh
    to `BatchMode`.
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
  - **New:** `--dry-run` on every prune (it requires `--prune`).
    `forget --prune --dry-run` runs the same safety checks, lists the paths
    it would delete and prints a `planned` row; `sync --prune --dry-run`
    skips the sync and prints `planned` (safe), `skipped` (unsafe) and,
    under `--all`, `unavailable` rows. Neither writes anything.
  - **New:** `status --dirty` and `--behind` keep only repos with
    uncommitted changes or behind their upstream (either matches when both
    are given; repos that cannot be inspected stay visible);
    `status --check` exits 3 when any repo is dirty or behind, or 1 when a
    repo cannot be inspected.
  - **New:** `foreach --stdin` reads the repos to run in (names, or
    `workspace.repo` / `workspace.status` / `workspace.sync_outcome`
    records of that workspace) and `foreach --all` runs in every
    registered workspace, with `--repo` as a per-workspace filter.
    `--stdin` cannot be combined with `--repo` or `--all`.
  - **Fix:** `sync --prune` without a terminal and without `--yes` prints the
    sync summary and rows before exiting 2, as documented; they were lost.
  - **Fix:** declining the `sync --prune` confirmation exits 1 with
    `cancelled; no changes made`, per the conventions (it exited 0).
  - **Fix:** the bare-repo cache is locked across processes, so two
    concurrent syncs of the same URL no longer clone into, fetch, or delete
    each other's partial clone.
- AWX
  - **Breaking:** the per-kind `awx <kind> apply FILE` commands are removed;
    `awx apply` applies documents of every kind.
  - `awx apply -` reads the YAML documents from stdin, so
    `untaped --profile a awx export … | untaped --profile b awx apply -`
    promotes resources between profiles. Stdin with no documents is an
    error.
  - `awx apply --check` computes the plan and writes nothing; it exits 3 when
    any document would change the controller and 0 otherwise (1 when a
    document fails).
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
  - `--follow` and `jobs logs --follow` keep reading a finished job's log
    briefly until AWX has saved all its events, so the tail is not cut off,
    and warn when it may still be.
  - A name that is not found names its scope and suggests close names from
    it (`JobTemplate not found: 'deplyo' in organization 'Default'; did you
    mean 'deploy'?`, drawn from one page of names), and says when
    `awx.default_organization` chose the organization, for `launch
    --launch-inventory`/`--credential` names too. Other not-found messages
    keep their `(key=value)` form.
  - `launch --dry-run` rows carry the resolved `payload`: names resolved to
    ids, `extra_vars` merged into a mapping (compact JSON in `table`/`raw`),
    and survey password answers and secret-looking names (`vault_pass`,
    `dbPassword`, `db-password`, `ssh_key`, at any depth) shown as
    `<redacted>`.
  - **Breaking:** `awx test` cases declare what their job must produce in
    `expect:`: a `status` (default `successful`) and `log` checks
    (`contains`, `not_contains`, `matches`). The checks can be set in
    `defaults.expect` and overridden per case. The reserved `assert:` block
    is gone. A case may set `timeout:` (or `defaults.timeout` for the suite),
    and `launch:` may be omitted.
  - `awx.test_result` rows add `expectations` (each check's expected and
    actual value) and `job_url`. In `json`/`yaml`/`pipe` output, or with
    `--show-logs`, a case that did not pass also carries `log_tail`.
    `failure_reason` names the failed checks. The table shows the summary
    columns.
  - **Behavior change:** `awx test run`, `list` and `validate` without paths
    read every suite under `.untaped/awx/tests/` at the git checkout root.
    Directories are searched recursively for files with a
    `kind: AwxTestSuite` line; other YAML and hidden entries are skipped.
    Suite names must be unique.
    `--case` also takes `SUITE/CASE`. A suite may set `organization:` for its
    job template. `run` ends with a result summary on stderr.
  - **Breaking:** `awx test list` writes one `awx.test_case` row per case in
    every format (`suite`, `case`, `job_template`, `organization`, `path`,
    `variables`); the per-suite json/yaml rows with a `cases` list are gone.
  - The `untaped-awx` skill describes the "test your change" workflow, and
    [AWX agent profile](docs/awx/agent-profile.md) sets up a dedicated AWX
    user and token for agents.
  - `test run --scm-branch REF` runs every case's job on a branch, tag or
    commit; `--scm-branch HEAD` uses the current git branch once it is pushed.
    `awx.test_result` rows add `scm_branch` and `scm_revision`, and a case
    that did not pass carries `failed_tasks` (host, task, status, msg and
    stderr, from job events), which `--show-logs` prints too.
  - **Behavior change:** `test run` and `test validate` preflight every case
    against its template's launch prompts and survey. `run` launches nothing
    when a case would have fields ignored or miss survey variables.
  - `launch`, `sync` and `jobs wait` rows add the execution's `scm_branch`
    and `scm_revision`.
  - **Behavior change:** `test run` now waits at most 30 minutes per case by
    default (`awx.test_timeout`) and runs 4 cases at once by default
    (`awx.test_parallel`). A job the run stops watching (timeout, polling
    error, Ctrl-C) is cancelled; `--no-cancel` leaves it running. The row's
    `failure_reason` says whether the cancel was requested, and Ctrl-C lists
    cancelled jobs without a `jobs wait` hint. `--timeout` must be positive.
  - **Fix:** `jobs logs` and `test run --show-logs` download the full log
    (`format=txt_download`), so large jobs no longer print AWX's "too large
    to display" notice instead of their output.
  - **Behavior change:** job and workflow template exports now carry
    `labels` (by name), and apply reconciles them through the template's
    `labels/` endpoint, adding before removing. An unknown label fails the
    apply before any write; labels are never created implicitly. The new
    `job-templates labels add/remove` and `workflow-templates labels
    add/remove` commands manage them one by one.
  - **Fix:** template surveys now round-trip against AWX. They are read from
    and written to the `survey_spec/` endpoint (AWX ignores a `survey_spec`
    on the template record), `get` shows the survey, and `survey_spec: {}`
    removes it.
  - **Fix:** export keeps plain survey defaults; only `password` question
    defaults become `$encrypted$`. Applying an export as a new template drops
    those placeholders with a warning instead of refusing the create.
    `preserved_secrets` names the path `survey_spec.spec.*[type=password].default`.
  - **Fix:** schedule apply keeps the `$encrypted$` survey password answers
    AWX returns in `extra_data` instead of dropping them, so a PATCH no longer
    wipes them. A change to another `extra_data` key beside a placeholder,
    including removing another answer, is refused; `preserved_secrets` names
    each kept answer (for example `extra_data.db_password`), and `get` shows
    those answers as `<redacted>`. A real answer typed into `extra_data` is
    not recognized as a secret and appears in plain text in previews.
  - **Fix:** a template create or update whose survey write fails after the
    record write is reported `partial` with the record's `id`, not `failed`.
  - **Fix:** list pagination refuses a `next` URL whose scheme, host or port
    differs from `awx.base_url`, so the token is never sent to another host.
  - New `job-templates copy SOURCE --name NEW` and `workflow-templates copy`
    copy one template through AWX's `copy/` endpoint. They refuse a name
    already used in the source's scope, or `can_copy: false`, before any
    write; warn about what AWX will not copy; and emit `awx.copy_outcome`,
    which `--stdin` selection of the same kind (for example `patch --stdin`)
    accepts. `copy` joins the conventions' write verbs.
  - `job-templates list` and `get` accept `--with-scm`, adding `scm_url`,
    `effective_scm_ref` and `project_allow_override` from each template's
    project (read once per distinct project).
  - New `job-templates rename SOURCE NEW` and `workflow-templates rename`.
    They refuse a name already used in the same scope before any write,
    preview the old and new names, re-read the resource to verify the new
    name, and emit `awx.rename_outcome`, which `--stdin` selection of the same
    kind accepts. `patch` still rejects `name`; `rename` joins the
    conventions' write verbs. Other kinds opt in through their spec.
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
  - `issues get` now includes the issue's `links`: for each, the linked
    issue's `key`, `summary`, `status` and `url`, the link `type`, this
    issue's `direction` (`outward`/`inward`) and the `relation` phrase Jira
    shows for it. JSON and YAML carry a list (empty when there are none); the
    detail table shows one line per link. Linked issues are not fetched.
  - **Fix:** `issues patch --assignee/--unassign` now uses Jira's dedicated
    `PUT issue/KEY/assignee` endpoint instead of the issue edit, which failed
    when the assignee field was not on the edit screen. Combined with other
    field changes, the edit is sent first, then the assignment; the preview
    and `--dry-run` show both requests. The flags override a `fields.assignee`
    in `--fields-file`, and when only the assignment fails the error says the
    fields were already updated.
  - **Fix:** issue and project keys are validated before any request: an
    issue key must be `PROJECT-123` and a project key `PROJECT` (any case;
    sent uppercase), or a numeric id, else the command exits 2. The client
    also percent-encodes every key it puts in a request path.
- Ansible
  - New task-shaped commands answer the common graph questions as rows
    (`--format table|json|yaml|pipe|raw`, `--columns`), searching the full
    graph unless `--depth N` is given (the empty message then names the
    depth). `deps ROLE` lists what ROLE depends on (`ansible.dependency`) and
    `impact ROLE` what depends on it (`ansible.dependent`, cached source data
    only, no `--live`). Each row has `repo`, `ref`, `unresolved`, the
    verbatim `declared_ref`/`declared_in`, `depth`, the shortest `path` and
    the `root_ref` it was reached from (a ref-less ROLE is walked from each
    of its cached refs). `graph` stays for the whole picture (`tree`, `mermaid`,
    `json`, `--out`, both directions, default depth 3).
  - `find REPO...` reports the roots whose downstream graph contains a
    repository: one `ansible.dependency_match` row per match with the root,
    the matched repo, the ref exactly as declared, the dependency file and
    the shortest path. Roots come from repeatable `--root owner/repo[@ref]`
    or `--stdin` (`owner/repo@ref` lines, or pipe records carrying
    `scm_url`/`effective_scm_ref` such as `awx job-templates list --with-scm
    --format pipe`). REPO may be a source alias. Rows carry the input
    record's `input_kind`, `input_id` and `input_name`, and every input
    record gets its own rows even when records share a root, so a pipeline
    can join results back to its inputs. Live reads are shared across roots,
    so each repo and ref is read from GitHub once per command.
  - New `ansible.default_source` setting: the saved source `deps`, `impact`,
    `find` and `graph` use (and `--refresh` refreshes) when neither
    `--source` nor inline selectors are given. Setting it switches the
    downstream reads of `deps`, `find` and `graph` from live GitHub to that
    source's cache: roles outside the source get "not cached" warnings and no
    rows, and before the source's first refresh these commands fail with the
    refresh command to run. `--live` still reads GitHub.
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
  - **Fix:** an unpinned dependency now points at the dependency's
    default-branch node (the source's recorded default branch, or GitHub's
    for live reads), so downstream graphs, `find` and cycle detection
    continue past it instead of stopping at a ref-less node. With no known
    default branch the node stays ref-less; a tags-only source stops at the
    default-branch node with a "ref is not cached" warning. An unpinned and a
    default-branch-pinned declaration of one repo now give one `find` row.
  - **Fix:** cached ref snapshots record the dependency parser version, so a
    parser change re-parses refs on the next refresh instead of reusing stale
    results. The first refresh after upgrading re-parses every ref once, and
    a refresh interrupted before the upgrade starts over.
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
  - `packs sync` and `packs remove` accept `--stdin` (pack names, or
    `recipe.pack` records from `packs list --format pipe`), and `packs
    remove` takes several names.
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
  - "recipe not found" and "hook not found" errors quote the name
    (`recipe not found: 'x'`), like other not-found errors.
  - **Behavior change:** hook workers and `uv lock` runs on packs no longer
    inherit the whole environment. Only an allowlist passes through (`PATH`,
    `HOME`, locale, temp dirs, `UV_*`/`XDG_*`, TLS and proxy settings,
    `SSH_AUTH_SOCK`/`GIT_SSH_COMMAND`); `PYTHONPATH` is the pack's `src/`
    only. Tokens such as `GITHUB_TOKEN` or untaped's `UNTAPED_*` credentials
    are no longer in a hook's environment. `UV_*` (which may hold index
    credentials) still is, and hooks still run as you with full file access.
  - **Fix:** a hook that writes to stdout at the file-descriptor level (or
    spawns a process that does) no longer corrupts the worker protocol; the
    output becomes hook diagnostics, and hooks read an empty stdin.
  - **Behavior change:** `packs add` and `packs sync` refuse a pack
    containing symlinks (outside ignored dirs such as `.venv`) instead of
    copying their targets.
  - `packs add` and `packs sync` record the resolved `commit` of a git source
    next to the requested `rev` (also when the pack's files did not change);
    `packs list` and the `packs add`/`packs sync` rows show it. The `packs
    sync` confirmation (and `--dry-run`) shows each pack's commit move and the
    hook-code files that change (`src/`, root `*.py`, and the uv/Python
    project files).
  - **Fix:** backup bundles are created owner-only (dirs `0700`, files
    `0600`) and their `metadata.json` is written atomically.
  - `apply --dry-run`/`--check` help and docs now state that pack hooks still
    run to compute the plan.
- GitHub
  - **Fix (security):** `sweep` and `cache sync` send the GitHub token only to
    the Git host of `github.base_url` (`github.com`, or `HOST` for
    `https://HOST/api/v3`). A piped `clone_url` on another HTTPS host used to
    receive it; it is now fetched without credentials.
  - **Fix:** `repos list` rows now carry `pushed_at`, so `repos list --format
    pipe | sweep --stdin` (or `cache sync --stdin`) skips fetching unchanged
    repos. A source without `pushed_at` no longer erases the one stored from
    an earlier fetch.
  - **Behavior change:** the `repos list` table shows `full_name`,
    `default_branch`, `private`, `archived`, `fork` and `url`; `-c` and the
    structured formats still reach every field, including `pushed_at`.
  - **Fix:** `search repos` now caps a large team scope at 25 requests with a
    warning, like `search issues`, instead of tripping GitHub's per-minute
    search limit.
  - **Behavior change:** `cache delete OWNER/NAME` fails with `cached repo not
    found` and exit 1, before deleting anything, when a named repo is not
    cached or not in `--org`. It used to exit 0 silently.
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
  - `-r` is now short for `--repo` on `search repos|code|issues`, `sweep` and
    `cache sync`, matching the reserved short flag.
  - New `github.default_org` setting: `repos list`, `search repos|code|issues`,
    `sweep`, `cache sync` and `cache prune` use it when no scope flag is
    given. A search with no scope and no default org (or a `--team` with no
    repos) still searches `user:@me`, and now says so on stderr.
  - When `--limit` cuts results off, `repos list` and `search` print a notice
    on stderr (`showing 2 of 3 repositories; omit --limit to list all`).
    Search asks GitHub for one row past `--limit` to detect this, except at a
    multiple of 100 or at 1000 and up, where that row would cost an extra
    request; those limits print no notice.

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

Major cleanup release. Scripts should read **Upgrading** (under the UX
conventions notes below) first: exit codes, confirmations, record kinds and
some flags changed. Renamed commands and flags keep hidden, warning aliases
until 8.0.

Cleanup phase 6: performance and features. Items marked **behavior change**
alter output, exit codes, or defaults.

- Internal: simplification removed ~28,000 net lines (src −1,081, tests −26,991)
  of duplication and low-value tests. Only user-visible changes: `github sweep
  -f json|yaml` no longer requests table-only `grep:` columns (no "unknown
  column" warning or null keys), and Ctrl-C during `recipe apply` now cancels
  queued planning work. No other behavior change.
- Core
  - **Behavior change:** `github`, `jira` and `awx` accept
    `<section>.token_command` (an argv list, no shell). It runs at most once
    per process, only when a command first needs the token, and its stdout is
    the token. Precedence: `token` (config or `UNTAPED_*`) > `token_command` >
    `GH_TOKEN`, then `GITHUB_TOKEN` (github only). Errors name the program and
    exit status, never its arguments or output.
  - **Behavior change:** `doctor` warns when the config file is group/world
    accessible, on unknown profile keys and on installed skills that differ
    from the packaged copy. New rows: `github`/`jira`/`awx` `.connection`
    (base URL and token source; warns when only one is set) and
    `.git`/`recipe.uv` (warns when the program is not on PATH).
  - **Behavior change:** `--verbose` prints debug lines to stderr for each
    HTTP request (method, masked URL, status, time, retry waits) and git
    command (masked args, directory, exit status, time).
  - SDK: `capability_api` gains `TokenSources`, `TokenCommand`,
    `connection_check` and `executable_check`.
- github
  - **Behavior change:** an expired cached repo is re-fetched only when
    GitHub's `pushed_at` or default branch changed; an unchanged repo gets
    `fetched_at` bumped with no git call and counts as cached (300 repos:
    expired re-run 10.5s → 1.65s, zero fetches).
  - Refs sharing a tree are grepped once, each pattern runs one `git grep`
    across all trees, AND queries stop at the first failed predicate,
    `--not-grep` uses `git grep -q`, and CODEOWNERS is read in one
    `cat-file --batch` (cached AND query, 13k matched lines: 28.6s → 1.25s).
  - Each repo is scanned as soon as its fetch ends; progress reads
    "Sweeping 312/1400 repos (45 fetched, 3 failed)". `sweep --stdin` uses
    piped `github.repo` records as-is; other kinds are looked up 4 at a time.
  - Fixed: sync, touch and delete lock each cached repo, so two sweeps can run
    at once.
  - New `github cache sync` warms the corpus without a query (sweep's scope
    flags plus `--refs/--ref/--depth/-j/--refresh`), emits
    `github.sync_outcome` (`synced`/`unchanged`/`skipped`/`failed`) and exits
    1 on any failure.
  - New `sweep --show files` emits `github.sweep_file` (`full_name`, `path`,
    `refs`, `hits`).
  - `cache status` shows sizes like "1.2 MiB" and ages like "3 hours ago"
    (JSON keeps raw values).
  - **Behavior change:** `--show matches` merges refs showing the same line at
    the same path and line number into one row; `not-grep:` hit counts are
    0/1 and appear only on matched rows.
- jira
  - `issues get --comments` fetches every comment (listed after the issue in
    a table, nested under `comments` in JSON/YAML). **Behavior change:** the
    `issues get` table shows a short column set for several issues and a
    reordered detail view for one.
  - New `issues comments list KEY` (`jira.comment`), `issues patch
    --assignee USER|@me` / `--unassign`, and `issues transition --comment TEXT
    --resolution NAME`.
  - New `issues links create KEY TYPE OTHER` (action `linked`; previews print
    a `reads as:` direction line). **Behavior change:** `jira.issue_outcome`
    records gain `link_type` and `linked_key`.
- recipe
  - New `recipe sync PACK...|--all` re-fetches packs from their recorded
    source and rev, confirms before changing files (`--yes`, `--dry-run`),
    needs `--discard-edits` to overwrite local edits, and emits
    `recipe.sync_outcome` (`updated`/`unchanged`/`planned`). A pack recorded
    with a relative source path must be re-added first.
  - **Behavior change:** `recipe add` records a local source as an absolute
    path.
  - `recipe apply PACK` without `--recipe` picks the pack's only recipe; with
    several, the error lists them.
  - `copy`/`remove` steps, backups and restores are binary-safe;
    `--preview diff` prints `Binary file PATH differs`.
- awx
  - Fewer API requests: parent identities are memoized per run, organization
    names come from `summary_fields`, and inventory sync lists sources with
    `inventory__in`. Deleting 50 scoped hosts: 203 → 53 requests; scoped
    `patch --all`: 155 → 106; `inventories sync --all` (10 inventories):
    31 → 22.
  - **Behavior change:** `delete --yes` no longer re-reads its targets after
    selection (it still re-reads once after an interactive prompt).
  - New `jobs cancel ID...` (`awx.cancel_outcome`:
    `planned`/`skipped`/`cancel_requested`/`failed`) and `jobs relaunch ID...
    [--failed-hosts]` (`awx.relaunch_outcome`; `id`/`kind` name the new
    execution and `jobs * --stdin` accepts it), both with confirmation,
    `--yes` and `--dry-run`.
  - New `jobs list --template NAME|ID`, and `launch`/`sync --timeout SECONDS`
    (with `--wait` or `--track`): a still-running execution fails its row and
    a `jobs wait` hint names it.
  - **Behavior change:** `--track` and `jobs events --follow` print the
    failure reason (event stdout, ANSI stripped, at most 10 lines) under each
    failed or unreachable host.

Cleanup phase 5: docs and root fixes. Items marked **behavior change** alter
output, exit codes, or defaults.

- Core
  - Fixed: root options (`--profile`, `--verbose`/`-v`, `--quiet`/`-q`)
    placed between command names, as in `untaped github --profile work
    whoami`, failed with "Unknown command". They now work anywhere before a
    `--` separator, at any depth; `--profile=NAME` works too.
  - **Behavior change:** tokens after `--` are passed to the command and are
    never read as root options. `untaped workspace foreach -w ws -- echo
    --profile x` used to drop `--profile x` and switch profile; it is now a
    usage error (exit 2), and `-- "echo --profile x"` passes it through.
  - Fixed: `untaped --help | head` (help into a closed pipe) exited 1 through
    Rich's broken-pipe handler. It now exits 0 quietly, like data commands. A
    failure raised while handling a broken pipe to another process keeps its
    exit code.
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
  - Deprecated: the root `log_level` setting (and `UNTAPED_LOG_LEVEL`) never
    had an effect. `untaped doctor` now reports a `warn` row when it is set;
    it will be removed in 8.0.
  - `untaped.capability_api` exports `AbsolutePath`, the field type of
    `TargetRecord.target_path`, so records can re-declare it first.
- workspace
  - **Behavior change:** `workspace edit` with no `--editor`, `$VISUAL` or
    `$EDITOR` fails with `set $VISUAL or $EDITOR to use an external editor`
    (exit 1) instead of running `vi`, like every other `edit` command.
- recipe
  - `recipe edit` uses the shared editor launcher: bad quoting in
    `$VISUAL`/`$EDITOR` is a clean error instead of a Python error message.
  - **Behavior change:** `recipe.apply_outcome` records list `target_path`
    first in every format, so `recipe apply --format raw` prints target paths
    (it printed `files_changed` before).
  - The `recipe.library_root` default is stored as `~/.untaped/untaped-recipes`
    (expanded when used), like the other path defaults. Existing configs work
    unchanged.
- awx
  - **Behavior change:** `jobs events` and `jobs logs` with several ids print
    one json/yaml array instead of one document per job. Event and log rows
    carry the job id as `job` (first in the default json/yaml event columns;
    log rows are `{job, line}`, and `raw`/`table` still show the line).
    `--follow --format json` still streams NDJSON.
- Docs
  - New getting-started guide, per-capability guides for github, jira,
    ansible and recipe, a glossary and a skill template.
  - New reference pages: configuration (generated by
    `scripts/gen_config_reference.py`), pipes and record kinds, exit codes and
    environment variables. Tests check the pages for staleness, broken links
    and example commands or flags that do not exist.
  - The packaged awx, ansible and github skills no longer carry developer
    internals or notes about removed spellings.

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
  print one warning each and go away in 8.0.
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
- Docs: a getting-started page, guides for github, jira, ansible and recipe,
  and reference pages for settings (generated), pipes, exit codes and
  environment variables, indexed from `docs/README.md`.

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
