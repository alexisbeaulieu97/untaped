# Pipes and record kinds

`--format pipe` writes records that another `untaped` command reads with
`--stdin`. Each record keeps all its fields and names its kind, so the
consumer never parses table text.

```bash
untaped github repos list --org acme --format pipe \
  | untaped workspace create acme --stdin
```

## Envelope format

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

## How `--stdin` reads input

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

## Failed rows: the `error` field

A failed row of an outcome record (`*_outcome` kinds such as
`workspace.repo_outcome`, and per-repo records such as `workspace.status`)
carries an `error` object next to its human `detail`. Rows that did not fail
have no `error` key.

```json
{"name": "Deploy", "action": "failed", "detail": "HTTP 503 for https://aap/api/v2/job_templates/7/", "error": {"category": "unavailable", "system": "awx", "retryable": true, "message": "HTTP 503 for https://aap/api/v2/job_templates/7/", "hint": null}}
```

| Field | Meaning |
|---|---|
| `category` | What kind of failure it is; it selects the exit code. See [exit codes](./exit-codes.md#categories). |
| `system` | Who is responsible: `untaped`, `local`, `git`, or a service such as `awx`. |
| `retryable` | `true` only for `unavailable` failures. |
| `message` | The failure, as `detail` shows it. |
| `hint` | A follow-up such as ``run `untaped config set awx.token --prompt` ``, or `null`. |

Tables leave `error` out (the `detail` column says the same); ask for it with
`--columns error` or use `json`, `yaml` or `pipe`.

## stderr diagnostics

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
still follows the rules in [exit codes](./exit-codes.md#precedence).

## Producers and consumers

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

## See also

- [Command and output conventions](../conventions.md#output-records): record
  field rules.
- [Building a capability provider](../plugins.md#5-piping): emitting and
  reading records from capability code.
