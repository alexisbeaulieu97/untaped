# Pipes and record kinds

`--format pipe` writes records that another `untaped` command can read with
`--stdin`. Each record keeps its full fields and says what it is, so the
consumer does not have to parse table text.

```bash
untaped github repos list --org acme --format pipe \
  | untaped workspace add --stdin --workspace acme
```

## Envelope format

`--format pipe` writes NDJSON: one JSON object per line.

```json
{"untaped": "1", "kind": "github.repo", "record": {"full_name": "acme/api", "...": "..."}}
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
- Kinds ending in `.summary` (for example `workspace.repo.summary`) are summary
  rows, not items. `recipe apply --stdin` skips them.
- Empty stdin is an error (`no identifiers received on stdin`).
- `--stdin` and positional arguments cannot be combined (exit 2).
- When stdin carries data, confirmation prompts read the terminal
  (`/dev/tty`). With no terminal, pass `--yes` (or `--dry-run`).

## Producers and consumers

The tables list what each command writes and which kinds each `--stdin`
reads. Commands not listed write no records.

### Root

| Command | Writes |
|---|---|
| `config list`, `config get` | `untaped.setting` |
| `config set`, `config unset` | `untaped.setting_outcome` (`key`, `profile`, `action`; never the value) |
| `profile list` | `untaped.profile` |
| `profile create`, `profile delete`, `profile rename` | `untaped.profile_outcome` (`name`, `previous_name`, `copied_from`, `action`) |
| `skills list` | `untaped.skill` |
| `doctor` | `untaped.doctor_check` |
| `capabilities` | `untaped.capability` |

`skills install --stdin` reads bare skill names, one per line. With
`--dry-run`, `config set/unset` and `profile create/delete/rename` validate,
write nothing and print their outcome with `action` `planned`.

### workspace

| Command | Writes |
|---|---|
| `workspace list` | `workspace.workspace` |
| `workspace get` | `workspace.repo`; `workspace.repo.summary` for an empty manifest |
| `workspace init` | `workspace.init_outcome` |
| `workspace forget` | `workspace.forget_outcome` |
| `workspace add` | `workspace.add_outcome` (`workspace.sync_outcome` with `--sync`) |
| `workspace remove` | `workspace.remove_outcome` |
| `workspace branch set`, `workspace branch apply` | `workspace.branch_outcome` |
| `workspace branch unset` | `workspace.branch_unset_outcome` |
| `workspace sync` | `workspace.sync_outcome` |
| `workspace status` | `workspace.status` |
| `workspace foreach` | `workspace.foreach_outcome` |

| Consumer | Reads | Field used |
|---|---|---|
| `workspace add --stdin` | `github.repo`, `github.repo_hit`, `github.sweep_repo`, `workspace.repo`; or URL lines | `clone_url`, else `url` |
| `workspace remove --stdin` | `workspace.repo`, `workspace.sync_outcome`; or repo lines | `repo` |
| `workspace path --stdin` | `workspace.workspace`; or name lines | `name` |

### github

| Command | Writes |
|---|---|
| `github whoami` | `github.user` |
| `github repos list` | `github.repo` |
| `github search repos` | `github.repo_hit` |
| `github search code` | `github.code` |
| `github search issues` | `github.issue` |
| `github search users` | `github.user_hit` |
| `github sweep` | `github.sweep_repo`; `github.sweep_match` with `--show matches` |
| `github cache status`, `cache delete`, `cache prune` | `github.corpus_repo` |
| `github cache worktree` | `github.worktree` |

| Consumer | Reads | Field used |
|---|---|---|
| `github search repos/code/issues --stdin`, `github sweep --stdin` | `github.repo`, `github.repo_hit`, `github.sweep_repo`; or `owner/name` lines | `full_name` |

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
| `awx apply`, `awx <resource> apply`, `patch`, `edit` | `awx.apply_outcome` |
| `awx <resource> delete` | `awx.delete_outcome` |
| `awx <resource> <members> add/remove` | `awx.membership_outcome` |
| `awx job-templates launch`, `awx workflow-templates launch` | `awx.launch_outcome` |
| `awx projects sync`, `inventories sync`, `inventory-sources sync` | `awx.sync_outcome` |
| `awx jobs list`, `jobs get`, `jobs wait` | `awx.job` |
| `awx jobs events` | `awx.event`, with the job id as `job` |
| `awx jobs logs` | `awx.log` (`job`, `line`) |
| `awx unified-templates list/get` | `awx.unified_template` |
| `awx job-templates usage`, `awx workflow-templates usage` | `awx.template_usage` |
| `awx workflow-templates nodes` | `awx.workflow_node` |
| `awx test list`, `awx test validate` | `awx.test_case` |
| `awx test run` | `awx.test_result` |
| `awx ping` | `awx.status` |

| Consumer | Reads | Field used |
|---|---|---|
| `awx <resource> <verb> --stdin` (selection commands) | that resource's kind; or name lines (ID lines with `--by-id`) | name field, or `id` with `--by-id` |
| `awx <resource> <members> add/remove --stdin` | the member resource's kind | name field, or `id` |
| `awx jobs get/events/logs/wait --stdin` | `awx.job`, `awx.launch_outcome`, `awx.sync_outcome`; or ID lines | `id`; a record's own execution kind wins over `--kind` |
| `awx unified-templates get --stdin` | `awx.unified_template`; or ID lines | `id` |
| `awx job-templates usage --stdin`, `workflow-templates usage/nodes --stdin` | the template's kind; or name lines | name field |

`awx jobs events` and `awx jobs logs` with several ids print one json or yaml
array covering every job; the `job` field says which job a row belongs to.
`pipe` and `raw` print one line per row, and `--follow --format json` streams
NDJSON.

### ansible

| Command | Writes |
|---|---|
| `ansible alias list` | `ansible.alias` |
| `ansible alias set`, `alias remove` | `ansible.alias_outcome` |
| `ansible source list`, `source get` | `ansible.source` |
| `ansible source status` | `ansible.source_status` |
| `ansible source set`, `source patch`, `source remove` | `ansible.source_outcome` |

`ansible graph` has its own formats (`tree`, `mermaid`, `json`) and no pipe
output.

### recipe

| Command | Writes |
|---|---|
| `recipe apply` | `recipe.apply_outcome` |
| `recipe list`, `recipe get` | `recipe.recipe`, `recipe.hook` or `recipe.pack` |
| `recipe add` | `recipe.add_outcome` |
| `recipe remove` | `recipe.remove_outcome` |
| `recipe validate` | `recipe.check` |
| `recipe test` | `recipe.test` |
| `recipe hook run` | `recipe.hook_run` |
| `recipe backup list/get/restore/prune` | `recipe.backup` |

`recipe apply --stdin` reads target directories: path lines, or records of any
kind that carry an absolute `target_path` (else `path`), such as
`workspace.repo`, `workspace.status` or `workspace.sync_outcome`.

```bash
untaped workspace get --workspace prod --format pipe \
  | untaped recipe apply acme/ci-baseline --stdin --dry-run
```

## See also

- [Command and output conventions](../conventions.md#output-records): record
  field rules.
- [Exit codes](./exit-codes.md)
- [Building a capability provider](../plugins.md#5-piping): emitting and
  reading records from capability code.
