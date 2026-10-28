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
| `config migrate` | `untaped.config_migration_outcome` (`from`, `to`, `profile`, `detail`; `action` `renamed`, `dropped` or `deleted`, with an empty `to` and the old value in `detail`; `planned` under `--dry-run`) |
| `auth login`, `auth forget` | `untaped.credential_outcome` (never the secret; `plugin`, `name`, `key` described, `id` short, `store`, `action`) |
| `auth list` | `untaped.stored_credential` (`id`, `plugin`, `name`, `key`, `backend`, `where`: the store entry or the command's program alone) |
| `auth status` | `untaped.token_source` (one row per credential for this run: `key`, `step` `variable`, `stored` or `env`, `source`, `id`, or `refusal`) |
| `profile list` | `untaped.profile` (`name`, `selected`, `keys`) |
| `profile show` | `untaped.profile_view` (`name`, `selected`, `raw`, `data`: the settings, secrets `***` unless `--show-secrets`; `yaml` by default) |
| `profile current` | `untaped.profile_pick` (`name`: the pick comma-joined, else `default`; `profiles`, each `name` and `source` `flag` or `env`; `raw` by default) |
| `profile create`, `profile delete`, `profile rename` | `untaped.profile_outcome` |
| `project list` | `untaped.recorded_project` (`name`, `path`, `state` `present`, `unreadable`, `missing` or `unknown`, `current`, `id`, `holds` (items per `plugin.store`), `detail`; experimental) |
| `project init` | `untaped.project_outcome` (`name`, `path`, `id`, `action` `created`, `unchanged`, `recorded`, `repaired` or `new id`, `detail`; experimental) |
| `project move`, `remove`, `prune` | `untaped.hold_outcome` (`holder`, `plugin`, `item`, `action` `planned`, `refused`, `failed`, `released`, `forgotten`, `repaired`, `moved`, `removed` or `skipped`, `detail`, `hint`; experimental) |
| `skills list` | `untaped.skill` |
| `skills status` | `untaped.installed_skill` |
| `skills update`, `skills remove` | `untaped.skill_outcome` |
| `doctor`, `setup` | `untaped.doctor_check` |
| `doctor fix` | `untaped.fix_outcome` |
| `setup plan` | `untaped.setup_step` |
| `setup register` | `untaped.register_outcome` (`holder` (`kind`, `name`; left out on `gone`), `environment`, `version`, `targets` (each `plugin`, `store`, `layout`), `action` `registered`, `unchanged`, `removed` or `gone`) |
| `setup prune` | `untaped.prune_outcome` (`what` `reference` or `folder`, `target_path`, `bytes`, `detail`; `action` `planned` under `--dry-run`, `removed`, `kept` with why in `detail`, `skipped` when it changed since the preview, or `failed` with `error`) |
| `plugin list` | `untaped.installed_plugin` (`name`, `status`, `distribution`, `version`; `package` (a quarantined plugin's from its entry point); a ready plugin's `source_dir`, from a checkout `tests_dir`, `requires` (the distributions it requires, canonical, including an extra named after an owner it fills) and `fills` (those owners)) |
| `plugin contracts` | `untaped.contract_method` |
| `plugin rank` | `untaped.setting_outcome` (key `<owner>.extensions`) |
| `plugin new` | `untaped.scaffold_outcome` (one per file, its `target_path`) |

`--stdin` on `skills install`, `status`, `update` and `remove` reads bare
skill names, one per line. With `--dry-run`, `config set/unset/migrate`,
`plugin rank`, `plugin new`, `profile create/delete/rename`, `doctor fix` and
`setup prune` validate, write nothing and print their outcome with `action` `planned`; `doctor fix`'s manual
fixes stay `skipped` and its refused ones `failed`.

A `fix_outcome` row has `fix` (the argv run after `untaped`), `checks` (the
doctor checks it covers), `action` (`fixed`, `partial` when a check still
warns or fails afterwards, `failed`, `skipped` for a fix that needs you,
`planned`), `detail`, and `error` on a failed row.

A `doctor_check` row's `fix`, and a `setup_step` row's `run`, is the argv to
run after `untaped`, `--profile NAME` first; a `<NAME>` token is a value to
supply, and `fix` is null on a passing row. A `doctor_check` row's
`automatic` is true when its fix is safe to run unattended (no value, no
input, and nothing a person should decide, such as registering an install). A
`setup_step` row also has `step` (`install` for this install's registration,
`profile`, `<service>.<setting>` for each setting the plugin needs, such as
`awx.server` and `awx.token`, `<service>.settings` when the service's
settings are invalid, and `<service>.online.<check>` for each online check,
such as `awx.online.api`),
`state` (`done`, `todo`, `failed`, `skipped`), `detail` and `by`: `user` for
a step that asks for or reveals a secret, else `agent`.

## workspace

| Command | Writes |
|---|---|
| `workspace list` | `workspace.workspace` |
| `workspace create`, `workspace add` | `workspace.repo_outcome` |
| `workspace status` | `workspace.repo_status` |
| `workspace archive` | `workspace.archive_outcome` |
| `workspace remove` | `workspace.remove_outcome` |
| `workspace run` | `workspace.run_outcome` |
| `workspace repos resolve` | `workspace.resolve_outcome` |

| Consumer | Reads | Field used |
|---|---|---|
| `workspace run --stdin` | `workspace.repo_status`, `workspace.repo_outcome`, `workspace.run_outcome`; or lines, each a repo name or directory | `repo`, else `dir` |
| `workspace create --stdin`, `workspace add --stdin` | `workspace.repo`, and any kind a `RepoSource` provider reads (github: `github.repo`, `github.sweep_repo`, `github.corpus_repo`); or lines, each a repo name (`owner/name`, a unique name) or a clone URL | the whole record, turned into a repo by the plugin whose kind it is, without an API call; a name line is looked up in the providers' repos |

## github

| Command | Writes |
|---|---|
| `github whoami` | `github.user` |
| `github repos list`, `github search repos` | `github.repo` |
| `github search code` | `github.code` |
| `github search issues` | `github.issue` |
| `github search users` | `github.user_hit` |
| `github sweep`, `sweep files`, `sweep matches` | `github.sweep_repo`, `github.sweep_file`, `github.sweep_match` |
| `github cache status`, `cache delete`, `cache prune` | `github.corpus_repo` (`cache delete` and `cache prune`: `status` `removed` with the `disk_bytes` freed, or `released` with `kept` naming who still uses the repo) |
| `github cache sync` | `github.sync_outcome` |
| `github cache worktree` | `github.worktree` |

| Consumer | Reads | Field used |
|---|---|---|
| `github search repos/code/issues --stdin`, `github sweep --stdin` (and `sweep files`, `sweep matches`), `github cache sync --stdin` | `github.repo`, `github.sweep_repo`, `github.corpus_repo`; or `owner/name` lines | `full_name` (`sweep` and `cache sync` use the record as-is, without an API call) |

## jira

| Command | Writes |
|---|---|
| `jira whoami` | `jira.user` |
| `jira issues get`, `issues search`, `issues assigned` | `jira.issue` (`search` and `assigned` leave the detail fields `null`) |
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
`awx.inventory_source`, `awx.host`, `awx.group`, `awx.execution_environment`,
`awx.label`, `awx.instance_group`, `awx.system_job_template`, `awx.job_template`,
`awx.workflow_job_template`, `awx.schedule`. A resource row is AWX's answer, as
are `awx.job_record` and `awx.unified_template` rows: the fields untaped reads lead
(`id`, `name`, `type` and the resource's own; `id`, `name`, `status` on a job),
then every other field AWX sent, in its order. `untaped plugin schema
awx.job_template` lists the typed fields "plus any others" (`additionalProperties`).

| Command | Writes |
|---|---|
| `awx <resource> list`, `awx <resource> get` | that resource's kind |
| `awx <resource> export --format json` or `--format pipe` | `awx.document` (YAML by default) |
| `awx apply`, `awx <resource> patch`, `edit` | `awx.apply_outcome_row` |
| `awx <resource> delete` | `awx.delete_outcome` |
| `awx job-templates copy`, `awx workflow-templates copy` | `awx.copy_outcome` (`id` names the new template) |
| `awx job-templates rename`, `awx workflow-templates rename` | `awx.rename_outcome` |
| `awx <resource> <members> add/remove` | `awx.membership_outcome` |
| `awx job-templates launch`, `awx workflow-templates launch` | `awx.launch_outcome` |
| `awx projects sync`, `inventories sync`, `inventory-sources sync` | `awx.sync_outcome` |
| `awx jobs list`, `jobs get`, `jobs wait` | `awx.job_record` |
| `awx jobs cancel` | `awx.job_cancel_outcome` |
| `awx jobs relaunch` | `awx.job_relaunch_outcome` (`id`/`kind` name the new execution) |
| `awx jobs events` | `awx.job_event_row` (`counter`, `event`, then the job id as `job`) |
| `awx jobs logs` | `awx.job_log_line` (`line`, `job`) |
| `awx unified-templates list/get` | `awx.unified_template` |
| `awx job-templates usage`, `awx workflow-templates usage` | `awx.workflow_usage` (`contains` lists what each workflow runs, as `id`, `type` and `node_count`; `target` and `stopped`) |
| `awx workflow-templates nodes` | `awx.workflow_node` |
| `awx test list` | `awx.suite_case` |
| `awx test run` | `awx.case_result` |
| `awx test validate --source-ref` | `awx.provision_outcome` |
| `awx test prune` | `awx.prune_outcome` |
| `awx ping` | `awx.ping_status` |
| `awx lint` | `lint.finding` ([lint](#lint)) |

| Consumer | Reads | Field used |
|---|---|---|
| `awx <resource> <verb> --stdin` (selection commands) | that resource's kind (templates also take `awx.copy_outcome` and `awx.rename_outcome` of their kind); or name lines (ID lines with `--by-id`) | `id` of a record; a line is a name, or an ID with `--by-id` |
| `awx <resource> <members> add/remove --stdin` | the member resource's kind; or name lines (ID lines with `--by-id`) | `id` of a record; a line is a name, or an ID with `--by-id` |
| `awx jobs get/events/logs/wait/cancel/relaunch --stdin` | `awx.job_record`, `awx.launch_outcome`, `awx.sync_outcome`, `awx.job_relaunch_outcome`; or ID lines | `id`; a record's own execution kind wins over `--kind` |
| `awx unified-templates get --stdin` | `awx.unified_template`; or ID lines | `id` |
| `awx job-templates usage --stdin`, `workflow-templates usage/nodes --stdin` | the template's kind; or name lines (ID lines with `--by-id`) | `id` of a record; a line is a name, or an ID with `--by-id` |

`awx lint`'s rules read one `awx.resources` record (the documents or the
controller's resources it lints, with each reference and what it resolved
to), which the run builds and never prints; a repository's own rules read it
through `untaped_awx.api`.

`awx jobs events` and `awx jobs logs` stream, followed or not: json prints
one object per line and yaml one document per row, covering every job; the
`job` field says which job a row belongs to. `pipe` and `raw` print one line
per row (raw prints an event's `counter` and a log's `line`); with
`--columns '?'` they list the columns without reading the jobs.

## ansible

| Command | Writes |
|---|---|
| `ansible deps` | `ansible.dependency` (with the ROLE ref it was reached from, `root_ref`) |
| `ansible impact` | `ansible.dependent` (with `root_ref`) |
| `ansible impact uses` | `ansible.variable_use_hit` (with the `dependent` it was found in) |
| `ansible find` | `ansible.dependency_match` (with the input record's `input_kind`, `input_id`, `input_name`) |
| `ansible source-alias list` | `ansible.source_alias` |
| `ansible source-alias set`, `source-alias remove` | `ansible.source_alias_outcome` |
| `ansible source list`, `source get` | `ansible.source` (`ref_scan_default` `null` when the source leaves it to the `ansible.ref_scan_default` setting) |
| `ansible source status` | `ansible.source_status` |
| `ansible source set`, `source patch`, `source remove` | `ansible.source_outcome` |

| Consumer | Reads | Field used |
|---|---|---|
| `ansible find --stdin` | any installed plugin's record kind; or `owner/repo@ref` lines | repository from `scm_url`, `repo_url`, `repo` or `full_name`; ref from `effective_scm_ref` or `ref`; identity from `id` and `name` |

`ansible graph` writes one `ansible.graph_document`, the JSON graph; `tree`
(its default) and `mermaid` are its own formats.

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

A `list` command and its `get` write one kind with one set of fields: `get`
fills what only it reads (a recipe's `description`, `inputs`, `steps` and
`hooks`; a hook's `exports`; a backup's `inputs` and `files`), and `list`
writes those as null. A `recipe.pack` row's `recipes` and `hooks` are counts
from `packs list` and `{name, description}` and `{name, exports}` lists from
`packs get`. A `recipe.hook_run` row holds a transform's `file`, `content`
and `diff`, or a validate's `message`, with the other's fields null; a
`fail` verdict also carries an `error`. `hooks run` writes the row in every
format but its default `text`, which prints a transform's content (or diff)
or a validate's `STATUS: MESSAGE`.

`recipe packs sync --stdin` and `recipe packs remove --stdin` read pack
names, or `recipe.pack` records from `recipe packs list --format pipe`.

`recipe apply --stdin` reads target directories: path lines, or records of any
kind an installed plugin declares (or of a `null` kind) that carry an
absolute `target_path` (else `path`), such as `workspace.repo_status` or
`workspace.repo_outcome`.

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
| `dotfiles status` | `dotfiles.path_status` |
| `dotfiles summary` | `dotfiles.status_summary` |
| `dotfiles apply` | `dotfiles.apply_outcome` |
| `dotfiles sync` | `dotfiles.sync_outcome` |
| `dotfiles remove` | `dotfiles.remove_outcome` |

`dotfiles diff` prints a unified diff, not records. `dotfiles status` and
`dotfiles summary` (run without ITEMS or `--repo`) and `dotfiles sync` (and,
after their changes, `dotfiles apply` and `dotfiles remove`) write two files under
`dotfiles.state_dir`: `status.json`, the `dotfiles.status_summary` record,
and `attention`, one line holding its `attention` count (the rows that need
the user), for prompt segments.

## git

| Command | Writes |
|---|---|
| `git hosts` | `git.host` |
| `git store` | `git.store_report` (`-f json` and the other formats; `table` prints labelled lines: `used_by` repos per plugin, `exclusive_bytes` the size of repos only that plugin uses, `unowned` and `held_by_branches` as `{repos, size_bytes}`; `--columns` prints it as a table) |

`git credential` speaks Git's credential-helper protocol on stdout, not
records.

## lint

| Command | Writes |
|---|---|
| `lint` | `lint.finding` (`rule`, `severity` `error` or `warning`, `target`, `message`, `location`, `hint`, `fix`) |
| `lint rules` | `lint.rule` (`id`, `reads`, `severity`, `summary`) |
| `lint new` | `lint.scaffold_outcome` (`action` `created`, `updated` or `planned`, `target_path`) |

A finding's `location` is `{path, line, column}` in a file,
`{path, index}` for one document of a multi-document file, or `{id, url}`
for a resource with no file; the table shows `src/a.py:12`, `a.yml#2` or
the id. A rule that could not run is an `untaped.failure` on stderr, never
a finding. `lint --stdin` reads `--format pipe` records of any kind: each is
checked by the rules that read its kind (`untaped.installed_plugin` from
`plugin list`). A rule over a repository's files reads `lint.tree`
(`root`, `paths`), which the run builds and never prints.
