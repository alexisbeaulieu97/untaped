# Scripting

What a script can rely on: the records commands write, the exit codes they
return, and the environment variables they read. The
[Versioning section of the README](../README.md#versioning) says what stays
stable within a major release.

## Output and pipes

`--format pipe` writes records that another `untaped` command reads with
`--stdin`. Each record keeps all its fields and names its kind, so the
consumer never parses table text.

```bash
untaped github repos list --org acme --format pipe \
  | untaped workspace create acme --stdin
```

### Envelope format

`--format pipe` writes NDJSON: one JSON object per line.

```json
{"untaped": "1", "kind": "github.repo", "record": {"repo": "acme/api", "...": "..."}}
```

| Field | Meaning |
|---|---|
| `untaped` | Envelope version. Always `"1"`. A consumer rejects other versions. |
| `kind` | Record kind, `<capability>.<noun>` (root commands use `untaped.<noun>`). May be `null`. |
| `record` | The row, as a JSON object. Values are JSON types; timestamps are strings such as `2026-01-02T03:04:05Z`. |

Because every line stands alone, `head`, `grep` and `cat a b` keep a stream
valid.

A record that names a concrete filesystem target carries it as an absolute,
non-empty `record.target_path`. A consumer reads this field rather than
another capability's domain fields.

### How `--stdin` reads input

- The first non-blank line decides the mode. If it is an envelope, every line
  must be one. Otherwise every line is a bare value (a name, an ID, a path).
  Mixing the two is an error.
- A consumer lists the kinds it accepts. A record of another kind exits 2
  (`record kind 'awx.host' is not accepted here`). A record whose `kind` is
  `null` is accepted.
- Kinds ending in `.summary` (`<capability>.<noun>.summary`) are summary
  rows, not items. `recipe apply --stdin` skips them.
- Empty stdin is an error (`no identifiers received on stdin`).
- `--stdin` and positional arguments cannot be combined (exit 2).
- When stdin carries data, confirmation prompts read the terminal
  (`/dev/tty`). With no terminal, pass `--yes` (or `--dry-run`).

### Failed rows: the `error` field

A failed row of an outcome record (`*_outcome` kinds such as
`workspace.repo_outcome`, and per-repo records such as `workspace.status`)
carries an `error` object next to its human `detail`. Rows that did not fail
have no `error` key.

```json
{"name": "Deploy", "action": "failed", "detail": "HTTP 503 for https://aap/api/v2/job_templates/7/", "error": {"category": "unavailable", "system": "awx", "retryable": true, "message": "HTTP 503 for https://aap/api/v2/job_templates/7/", "hint": null}}
```

| Field | Meaning |
|---|---|
| `category` | What kind of failure it is; it selects the exit code. See [categories](#categories). |
| `system` | Who is responsible: `untaped`, `local`, `git`, or a service such as `awx`. |
| `retryable` | `true` only for `unavailable` failures. |
| `message` | The failure, as `detail` shows it. |
| `hint` | A follow-up such as ``run `untaped config set awx.token --prompt` ``, or `null`. |

Tables leave `error` out (the `detail` column says the same); ask for it with
`--columns error` or use `json`, `yaml` or `pipe`.

URL passwords are masked in every message, detail and record. `error` is a
reserved record field.

### stderr diagnostics

stdout carries data only. With `--format json`, `yaml` or `pipe`, stderr
carries JSON Lines: one object per error, per-item error, warning, hint or
note. Progress spinners are silent in this mode.

- The format counts whether it comes from the flag, `UNTAPED_FORMAT` or
  `ui.format`. A command's own default, such as `export`'s YAML, does not.
- `UNTAPED_DIAGNOSTICS=json` turns JSON Lines on for any format;
  `UNTAPED_DIAGNOSTICS=text` keeps text lines.
- A parse error (an unknown flag or command) and a warning about a
  quarantined provider come before `ui.format` is read, so only a `--format`
  on the command line or in `UNTAPED_FORMAT` switches them.

```json
{"level": "error", "message": "AWX rejected the token (HTTP 401)", "category": "auth", "system": "awx", "retryable": false, "hint": "run `untaped config set awx.token --prompt`", "exit_code": 4, "details": {"status": 401, "url": "https://aap/api/v2/me/", "attempts": 1}}
{"level": "error", "item": "Deploy", "message": "HTTP 503 for https://aap/api/v2/job_templates/7/", "category": "unavailable", "system": "awx", "retryable": true, "hint": null, "exit_code": 5, "details": {"status": 503, "url": "https://aap/api/v2/job_templates/7/", "attempts": 3}}
{"level": "warning", "message": "--parallel 64 clamped to 16 (2 * os.cpu_count())"}
{"level": "hint", "message": "run `untaped skills install`"}
{"level": "info", "message": "sync: 2 cloned, 1 failed"}
```

| Field | On | Meaning |
|---|---|---|
| `level` | every line | `error`, `warning`, `hint`, `info`, `success`, or `debug` (with `--verbose`) |
| `message` | every line | The text the line would show, without its `error:`/`warning:`/`hint:` prefix |
| `item` | per-item errors | The item that failed (a name, an ID) |
| `category`, `system`, `retryable`, `hint`, `exit_code`, `details` | errors | As in the `error` field above; `exit_code` is the code this failure alone selects, and `details` holds machine context such as `status`, `url` and `attempts` |

An `error` line that does not come from a raised failure (a plain
`error: …` message) carries only `level`, `message` and `hint`; the exit code
still follows the rules in [exit codes](#precedence).

## Output records

What each command writes, and which kinds each `--stdin` reads. Commands not
listed write no records. `--columns ?` on a command lists its record's fields.

### Root

| Command | Writes |
|---|---|
| `config list`, `config get` | `untaped.setting` |
| `config set`, `config unset` | `untaped.setting_outcome` (never the value) |
| `profile list` | `untaped.profile` |
| `profile create`, `profile delete`, `profile rename` | `untaped.profile_outcome` |
| `skills list` | `untaped.skill` |
| `skills status` | `untaped.installed_skill` |
| `skills update`, `skills remove` | `untaped.skill_outcome` |
| `doctor`, `setup` | `untaped.doctor_check` |
| `capabilities` | `untaped.capability` |
| `alias list` | `untaped.alias` |
| `alias set`, `alias remove` | `untaped.alias_outcome` |

`--stdin` on `skills install`, `status`, `update` and `remove` reads bare
skill names, one per line. With `--dry-run`, `config set/unset`,
`profile create/delete/rename` and `alias set/remove` validate, write nothing
and print their outcome with `action` `planned`.

### workspace

| Command | Writes |
|---|---|
| `workspace list` | `workspace.workspace` |
| `workspace create`, `workspace add` | `workspace.repo_outcome` |
| `workspace status` | `workspace.status` |
| `workspace archive` | `workspace.archive_outcome` |
| `workspace run` | `workspace.run_outcome` |

| Consumer | Reads | Field used |
|---|---|---|
| `workspace run --stdin` | `workspace.status`, `workspace.repo_outcome`, `workspace.run_outcome`; or lines, each a repo name or directory | `repo`, else `dir` |
| `workspace create --stdin`, `workspace add --stdin` | `github.repo`, `github.repo_hit`, `github.sweep_repo`; or lines, each any repo identifier (`owner/name`, a unique name, a URL) | `full_name`, else `repo`, resolved through the GitHub inventory; `clone_url`, else `url`, when there is no name or the inventory lacks it |

### github

| Command | Writes |
|---|---|
| `github whoami` | `github.user` |
| `github repos list` | `github.repo` |
| `github search repos` | `github.repo_hit` |
| `github search code` | `github.code` |
| `github search issues` | `github.issue` |
| `github search users` | `github.user_hit` |
| `github sweep` | `github.sweep_repo`; `github.sweep_file` with `--show files`; `github.sweep_match` with `--show matches` |
| `github cache status`, `cache delete`, `cache prune` | `github.corpus_repo` |
| `github cache sync` | `github.sync_outcome` |
| `github cache worktree` | `github.worktree` |

| Consumer | Reads | Field used |
|---|---|---|
| `github search repos/code/issues --stdin`, `github sweep --stdin`, `github cache sync --stdin` | `github.repo`, `github.repo_hit`, `github.sweep_repo`; or `owner/name` lines | `repo` (`sweep` and `cache sync` use a `github.repo` record as-is, without an API call) |

### jira

| Command | Writes |
|---|---|
| `jira whoami` | `jira.user` |
| `jira issues get`, `issues search`, `issues assigned` | `jira.issue` |
| `jira issues create`, `patch`, `comment`, `transition`, `links create` | `jira.issue_outcome` |
| `jira issues comments list` | `jira.comment` |
| `jira issues transitions` | `jira.transition` |
| `jira projects list`, `projects get` | `jira.project` |
| `jira boards list` | `jira.board` |
| `jira sprints list` | `jira.sprint` |

| Consumer | Reads | Field used |
|---|---|---|
| `jira issues get --stdin`, `jira issues transition --stdin` | `jira.issue`, `jira.issue_outcome`; or key lines | `key` |

### awx

Resource kinds are `awx.<snake_case kind>`: `awx.organization`,
`awx.credential_type`, `awx.credential`, `awx.project`, `awx.inventory`,
`awx.inventory_source`, `awx.host`, `awx.group`, `awx.job_template`,
`awx.workflow_job_template`, `awx.schedule`.

| Command | Writes |
|---|---|
| `awx <resource> list`, `awx <resource> get` | that resource's kind |
| `awx <resource> export --format json` or `--format pipe` | `awx.document` (YAML by default) |
| `awx apply`, `awx <resource> patch`, `edit` | `awx.apply_outcome` |
| `awx <resource> delete` | `awx.delete_outcome` |
| `awx job-templates copy`, `awx workflow-templates copy` | `awx.copy_outcome` (`id` names the new template) |
| `awx job-templates rename`, `awx workflow-templates rename` | `awx.rename_outcome` |
| `awx <resource> <members> add/remove` | `awx.membership_outcome` |
| `awx job-templates launch`, `awx workflow-templates launch` | `awx.launch_outcome` |
| `awx projects sync`, `inventories sync`, `inventory-sources sync` | `awx.sync_outcome` |
| `awx jobs list`, `jobs get`, `jobs wait` | `awx.job` |
| `awx jobs cancel` | `awx.cancel_outcome` |
| `awx jobs relaunch` | `awx.relaunch_outcome` (`id`/`kind` name the new execution) |
| `awx jobs events` | `awx.event`, with the job id as `job` |
| `awx jobs logs` | `awx.log` (`job`, `line`) |
| `awx unified-templates list/get` | `awx.unified_template` |
| `awx job-templates usage`, `awx workflow-templates usage` | `awx.template_usage` |
| `awx workflow-templates nodes` | `awx.workflow_node` |
| `awx test list` | `awx.test_case` |
| `awx test run` | `awx.test_result` |
| `awx test validate --source-ref`, `awx test run --dry-run --source-ref` | `awx.provision_outcome` |
| `awx test prune` | `awx.prune_outcome` |
| `awx ping` | `awx.status` |

| Consumer | Reads | Field used |
|---|---|---|
| `awx <resource> <verb> --stdin` (selection commands) | that resource's kind (templates also take `awx.copy_outcome` and `awx.rename_outcome` of their kind); or name lines (ID lines with `--by-id`) | `id` of a record; a line is a name, or an ID with `--by-id` |
| `awx <resource> <members> add/remove --stdin` | the member resource's kind; or name lines (ID lines with `--by-id`) | `id` of a record; a line is a name, or an ID with `--by-id` |
| `awx jobs get/events/logs/wait/cancel/relaunch --stdin` | `awx.job`, `awx.launch_outcome`, `awx.sync_outcome`, `awx.relaunch_outcome`; or ID lines | `id`; a record's own execution kind wins over `--kind` |
| `awx unified-templates get --stdin` | `awx.unified_template`; or ID lines | `id` |
| `awx job-templates usage --stdin`, `workflow-templates usage/nodes --stdin` | the template's kind; or name lines (ID lines with `--by-id`) | name field (`id` with `--by-id`) |

`awx jobs events` and `awx jobs logs` with several ids print one json or yaml
array covering every job; the `job` field says which job a row belongs to.
`pipe` and `raw` print one line per row, and `--follow --format json` streams
NDJSON.

### ansible

| Command | Writes |
|---|---|
| `ansible deps` | `ansible.dependency` (with the ROLE ref it was reached from, `root_ref`) |
| `ansible impact` | `ansible.dependent` (with `root_ref`) |
| `ansible find` | `ansible.dependency_match` (with the input record's `input_kind`, `input_id`, `input_name`) |
| `ansible source-alias list` | `ansible.source_alias` |
| `ansible source-alias set`, `source-alias remove` | `ansible.source_alias_outcome` |
| `ansible source list`, `source get` | `ansible.source` |
| `ansible source status` | `ansible.source_status` |
| `ansible source set`, `source patch`, `source remove` | `ansible.source_outcome` |

| Consumer | Reads | Field used |
|---|---|---|
| `ansible find --stdin` | any record kind; or `owner/repo@ref` lines | repository from `scm_url`, `repo_url`, `repo` or `full_name`; ref from `effective_scm_ref` or `ref`; identity from `id` and `name` |

`ansible graph` has its own formats (`tree`, `mermaid`, `json`) and no pipe
output.

### recipe

| Command | Writes |
|---|---|
| `recipe apply` | `recipe.apply_outcome` |
| `recipe list`, `recipe get` | `recipe.recipe` |
| `recipe packs list`, `recipe packs get` | `recipe.pack` |
| `recipe packs add` | `recipe.add_outcome` |
| `recipe packs sync` | `recipe.sync_outcome` |
| `recipe packs remove` | `recipe.remove_outcome` |
| `recipe hooks list`, `recipe hooks get` | `recipe.hook` |
| `recipe validate` | `recipe.check` |
| `recipe test` | `recipe.test` |
| `recipe hooks run` | `recipe.hook_run` |
| `recipe backups list/get` | `recipe.backup` |
| `recipe backups restore` | `recipe.restore_outcome` |
| `recipe backups prune` | `recipe.prune_outcome` |

`recipe packs sync --stdin` and `recipe packs remove --stdin` read pack
names, or `recipe.pack` records from `recipe packs list --format pipe`.

`recipe apply --stdin` reads target directories: path lines, or records of any
kind that carry an absolute `target_path` (else `path`), such as
`workspace.status` or `workspace.repo_outcome`.

```bash
untaped workspace status NAME --format pipe \
  | untaped recipe apply acme/ci-baseline --stdin --dry-run
```

## Exit codes

Every `untaped` command uses the same exit codes. Scripts can rely on them.

| Code | Meaning |
|---|---|
| 0 | Success. Per-item `skipped` rows are success too. |
| 1 | The thing you ran failed: a runtime failure, at least one failed item, a name that does not exist, input the remote or a local file rejected, or a declined confirmation (`cancelled; no changes made`). |
| 2 | Usage error, found before any side effect: an unknown flag, conflicting flags, a value out of range, no selection, or a prompt with no terminal and no `--yes`. |
| 3 | Predicate hit: the command worked, and the condition you asked it to check was true. |
| 4 | The environment needs fixing: missing or invalid settings or config file, a rejected token (HTTP 401), missing permission (HTTP 403), a missing tool such as `git` or `$EDITOR`. Retrying won't help, and neither will changing your code. |
| 5 | Temporary: the network is down, a request timed out, the service answered 5xx or 429, or another `untaped` process holds a lock. Retry later. |
| 130 | Interrupted with Ctrl-C, including at a prompt. |

### Categories

Every failure has a **category**, which selects its exit code, and a
**system**, which says who is responsible: `untaped` (a bug or the command
line), `local` (config, files, the environment), `git`, or a service such as
`awx`, `jira` or `github` (a capability may refine it, such as `awx.scm`).

| Category | Meaning | Exit |
|---|---|---|
| `usage` | Bad flags or arguments, found before any side effect | 2 |
| `config` | Local setup: missing or invalid settings, config file, CA bundle, missing tool | 4 |
| `auth` | Credentials rejected (401) | 4 |
| `permission` | Authenticated but not allowed (403) | 4 |
| `not_found` | A named thing does not exist | 1 |
| `invalid` | The remote rejected the input (400/422), or a local input file is invalid | 1 |
| `conflict` | A concurrent change, or a name already taken (409) | 1 |
| `unavailable` | Network down, timeout, 5xx, 429: retryable | 5 |
| `failed` | The operation ran and failed (a job, a test, a git command) | 1 |
| `interrupted` | Ctrl-C | 130 |

The category alone selects the exit code and retryability, so the two never
disagree. A failure keeps its attribution when code replaces it or turns it
into a row. `ConfigError` means local setup; an invalid input file or value is
`invalid` (exit 1).

Only `unavailable` is retryable. With `--format json`, `yaml` or `pipe` (or
`UNTAPED_DIAGNOSTICS=json`), stderr reports each failure as a JSON line with
its `category`, `system`, `retryable`, `hint` and `exit_code`; failed rows of
outcome records carry the same fields in their `error`. See
[stderr diagnostics](#stderr-diagnostics).

### Precedence

One run can see several failures. It exits with the most severe one:

```text
130 > 2 > 4 > 5 > 1 > 3 > 0
```

So a batch where one item hit a rejected token and another a missing name
exits 4: fix the environment before you look at anything else. A batch
command that fails on some items still prints a row for every item. When both
a failure and a predicate hit happen, the failure's code wins.

Output into a closed pipe exits 0 quietly: when the reader stops early, as in
`untaped awx jobs list --format raw | head -1` or `untaped --help | head`, the
command stops writing and exits 0 with no error. A failure unrelated to that
pipe keeps its own exit code.

### Commands that exit 3

| Command | Exits 3 when |
|---|---|
| `untaped github sweep --fail-on-match` | Any repository matched the query. |
| `untaped github sweep --strict` | Any repository could not be scanned. |
| `untaped awx apply --check` | Any document would change the controller. |
| `untaped recipe apply --check` | Any target would change. |
| `untaped skills status --check` | An installed skill is outdated or no longer shipped. |
| `untaped workspace status --check` | Any repo would block `workspace archive` (uncommitted changes, stashes on its branch, unpushed commits, initialised submodules, or a missing repo cache). A repo whose git state cannot be read exits 1 instead. |

Use these in CI to tell "the check found something" (3) apart from "the tool
failed" (1), "fix the setup" (4) and "try again later" (5):

```bash
untaped github sweep --org acme --grep 'log4j' --fail-on-match --format raw --columns repo
case $? in
  0) echo "clean" ;;
  3) echo "banned pattern found" ;;
  4) echo "fix the token or config" ;;
  5) echo "GitHub unavailable; retry later" ;;
  *) echo "sweep failed" ;;
esac
```

### Codes inside records

Some rows carry a code of their own. It never becomes the process exit code:

- `untaped workspace run` reports each repo's command status as `returncode`
  in its `workspace.run_outcome` row. Any failed repo makes the command exit
  1, whatever the `returncode`.
- `untaped awx test run` reports each case's result in its rows. A case that
  did not pass carries a `failure` with its own `category` and `system` (such
  as `awx.scm` or `awx.hosts`), and the command exits with the most severe:
  - 4 for a rejected token, a credential lookup or a failed inventory update;
  - 5 for an unavailable controller, a job stuck pending or unreachable hosts;
  - otherwise 1.

  Compared with a baseline (`--compare` or `--baseline`):
  - a case that fails as it did in the baseline (`still_failing`: same
    `system`) does not count;
  - a regression, a failure the baseline cannot vouch for (`unverified`) and
    a failing new case exit 1;
  - 4 and 5 still count for any case, the `--baseline` run's included.

  With `--source-ref`, temporary copies that cannot be provisioned stop the
  run before any case, with their own code: 1 for a spec or suite to fix, 4
  when AWX refused the agent's user, 5 when it was unavailable. A copy that
  teardown cannot delete is a warning and never changes the code.

## Environment variables

Every environment variable `untaped` reads or sets.

### untaped

| Variable | Effect |
|---|---|
| `UNTAPED_CONFIG` | Path of the config file. Default: `~/.untaped/config.yml`. |
| `UNTAPED_STATE` | Path of the state file. Default: `state.yml` next to `config.yml`, or `NAME.state.yml` next to any other config file `NAME.EXT`. It must not name the config file. |
| `UNTAPED_PROFILE` | Active profile for this process. It must name an existing profile. The root `--profile` option takes precedence over it. |
| `UNTAPED_FORMAT` | Default `--format` (`json`, `yaml`, `table`, `raw` or `pipe`) for commands whose default is `table`. Wins over the `ui.format` setting; an explicit `--format` wins over it. Any other value exits 2, except under `doctor` and `setup`, which ignore it. |
| `UNTAPED_DIAGNOSTICS` | `json` makes stderr diagnostics (errors, warnings, hints, notes) JSON Lines for every format; `text` keeps text lines even with `--format json`, `yaml` or `pipe` (from the flag, `UNTAPED_FORMAT` or `ui.format`), which otherwise switch to JSON. Other values are ignored. See [stderr diagnostics](#stderr-diagnostics). |
| `UNTAPED_CONFIG_LOCK_TIMEOUT` | Seconds to wait for the config or state file lock before a write fails. A non-negative number; default `5`. |
| `UNTAPED_<SECTION>__<FIELD>` | Overrides one profile setting for this process, for example `UNTAPED_GITHUB__TOKEN` or `UNTAPED_HTTP__VERIFY_SSL`. Nested fields add another `__`: `UNTAPED_GITHUB__SWEEP__MAX_AGE_SECONDS`. |
| `GH_TOKEN`, then `GITHUB_TOKEN` | GitHub token used when neither `github.token` nor `github.token_command` is set. See [Tokens](./configuration.md#tokens). |
| `JIRA_API_TOKEN` | Jira token used when neither `jira.token` nor `jira.token_command` is set. |
| `CONTROLLER_OAUTH_TOKEN`, then `TOWER_OAUTH_TOKEN`, then `AAP_TOKEN` | AWX/AAP token used when neither `awx.token` nor `awx.token_command` is set. |

A setting's value comes from the first of these that has it: its
`UNTAPED_*` variable, the active profile, `profiles.default`, the built-in
default. The [configuration reference](./reference/config.md) lists the variable for
every setting. `untaped doctor` names the variable when an override holds an
invalid value.

### Workspace run

`untaped workspace run` sets these in each repo's process.

| Variable | Value |
|---|---|
| `UNTAPED_WORKSPACE` | Workspace name. |
| `UNTAPED_REPO` | Repo display name. |
| `UNTAPED_BRANCH` | Task branch; empty for a read-only repo. |
| `UNTAPED_BASE` | Base branch. |
| `UNTAPED_READ_ONLY` | `1` for a read-only repo, else `0`. |

### Editors

| Variable | Used by |
|---|---|
| `VISUAL`, then `EDITOR` | `untaped config edit`, `untaped awx <resource> edit`, `untaped recipe edit` (and `recipe packs edit`, `recipe hooks edit`). |

The value is split like a shell command line but no shell runs it. Include your
GUI editor's wait flag, for example `VISUAL="code --wait"`. If neither is set,
the commands fail and ask you to set one.

### Terminal output

| Variable | Effect |
|---|---|
| `NO_COLOR` | Any non-empty value turns off color. It wins over `FORCE_COLOR`. |
| `FORCE_COLOR` | Any non-empty value turns on color even when output is not a terminal. |

Without either, color is used only when the stream is a terminal.
`COLUMNS` sets the table width; without it a terminal's width is used, and
output that does not go to a terminal is not wrapped.

### HTTP

When the `http.proxy` setting is unset, the HTTP client honors the standard
proxy variables: `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` and `NO_PROXY`. TLS
trust comes from the `http.*` settings (the OS trust store by default), not
from environment variables.

### Git

`untaped` runs `git` for workspaces, GitHub sweeps and Ansible source refresh.
For each `git` call it:

- sets `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never`, so a remote that
  needs credentials fails instead of waiting for input;
- sets `GIT_SSH_COMMAND="ssh -o BatchMode=yes"` unless you set
  `GIT_SSH_COMMAND`, `GIT_SSH` or the `core.sshCommand` Git setting yourself;
- removes `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
  `GIT_ALTERNATE_OBJECT_DIRECTORIES` and `GIT_COMMON_DIR`, so an outer
  repository cannot redirect the command;
- removes `GIT_TRACE*` and `GIT_CURL_VERBOSE` when it passes a token to Git,
  so the token is not logged.

If you set `GIT_SSH_COMMAND` yourself, add `-o BatchMode=yes` to keep the
fail-fast behavior.

### Recipe hooks

Recipe hooks, and the `uv` commands `untaped` runs on a pack, get a reduced
environment: only an allowlist of variables passes through (`PATH`, `HOME`,
locale, temp directories, `UV_*`, `XDG_*`, TLS and proxy settings, and
`SSH_AUTH_SOCK`/`GIT_SSH_COMMAND`). Tokens such as `GITHUB_TOKEN` and
`UNTAPED_*` credentials are not passed. Hook workers get `PYTHONPATH` set to
the pack's `src/` only. The full list is in the recipe skill's
[pack library reference](../packages/untaped-recipe/src/untaped_recipe/skills/untaped-recipe/references/library.md).

## See also

- [Command and output conventions](./conventions.md#output-records): record
  field rules.
- [Building a capability provider](./plugins.md#5-piping): emitting and
  reading records from capability code.
