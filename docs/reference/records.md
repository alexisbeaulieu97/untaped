# Output records

What each command writes, and which kinds each `--stdin` reads. Commands not
listed write no records. `--columns '?'` on a command lists its record's fields; it acts after the
command runs and needs at least one row, so use it on a read command or add
`--dry-run` to a write.

## Root

| Command | Writes |
|---|---|
| `config list`, `config get` | `untaped.setting` (`stability`: `stable`, `experimental` or `deprecated`; `table` lists the last two apart) |
| `config set`, `config unset` | `untaped.setting_outcome` (never the value) |
| `config migrate` | `untaped.config_migration_outcome` (`from`, `to`, `profile`; `action` `renamed` or `dropped`) |
| `auth set`, `auth unset`, `auth migrate` | `untaped.auth_outcome` (never the token; `action` `gone` when `unset` found the entry already deleted; a token `migrate` could not move is `failed`, with `error`) |
| `auth status` | `untaped.token_source` |
| `profile list` | `untaped.profile` |
| `profile create`, `profile delete`, `profile rename` | `untaped.profile_outcome` |
| `skills list` | `untaped.skill` |
| `skills status` | `untaped.installed_skill` |
| `skills update`, `skills remove` | `untaped.skill_outcome` |
| `doctor`, `setup` | `untaped.doctor_check` |
| `doctor fix` | `untaped.fix_outcome` |
| `setup plan` | `untaped.setup_step` |
| `capabilities` | `untaped.capability` |
| `alias list` | `untaped.alias` |
| `alias set`, `alias remove` | `untaped.alias_outcome` |

`--stdin` on `skills install`, `status`, `update` and `remove` reads bare
skill names, one per line. With `--dry-run`, `config set/unset/migrate`,
`auth unset/migrate`, `profile create/delete/rename`, `alias set/remove`
and `doctor fix` validate, write nothing and print their outcome with
`action` `planned`; `doctor fix`'s manual fixes stay `skipped` and its
refused ones `failed`.

A `fix_outcome` row has `fix` (the argv run after `untaped`), `checks` (the
doctor checks it covers), `action` (`fixed`, `partial` when a check still
warns or fails afterwards, `failed`, `skipped` for a fix that needs you,
`planned`), `detail`, and `error` on a failed row.

A `doctor_check` row's `fix`, and a `setup_step` row's `run`, is the argv to
run after `untaped`, `--profile NAME` first; a `<NAME>` token is a value to
supply, and `fix` is null on a passing row. A `doctor_check` row's
`automatic` is true when its fix needs no value and no input. A
`setup_step` row also has `step` (`profile`, `<service>.base_url`,
`<service>.token`, `<service>.settings` when the service's settings are
invalid, and `<service>.online.<check>` for each online check, such as
`awx.online.api`),
`state` (`done`, `todo`, `failed`, `skipped`), `detail` and `by`: `user` for
a step that asks for or reveals a token, else `agent`.

## workspace

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

## github

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

## jira

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

## awx

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
| `awx test validate --source-ref` | `awx.provision_outcome` |
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

## ansible

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

## recipe

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

## dotfiles

| Command | Writes |
|---|---|
| `dotfiles subscribe`, `dotfiles items` | `dotfiles.item` |
| `dotfiles enable`, `dotfiles disable` | `dotfiles.item_outcome` |
| `dotfiles repos` | `dotfiles.repo` |
| `dotfiles unsubscribe` | `dotfiles.repo_outcome` |
| `dotfiles status` | `dotfiles.status` (`--summary`: `dotfiles.status.summary`) |
| `dotfiles apply` | `dotfiles.apply_outcome` |
| `dotfiles sync` | `dotfiles.sync_outcome` |
| `dotfiles remove` | `dotfiles.remove_outcome` |

`dotfiles diff` prints a unified diff, not records. `dotfiles status` and
`dotfiles sync` (and, after their changes, `dotfiles apply` and `dotfiles
remove`) write two files under `dotfiles.state_dir`: `status.json`, the
`dotfiles.status.summary` record, and `attention`, one line holding its
`attention` count (the rows that need the user), for prompt segments.
