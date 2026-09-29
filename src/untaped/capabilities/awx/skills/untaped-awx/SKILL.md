---
name: untaped-awx
description: Use the built-in `untaped awx` capability for AWX/AAP workflows.
---

# Untaped AWX/AAP

Use this skill when the user wants an agent to operate the `untaped awx` CLI for Ansible Automation Platform or AWX resources.

## Setup

- The command is `untaped awx`. It ships with the unified `untaped` CLI (no separate install).
- Settings live under `profiles.<name>.awx`: `base_url`, `token`, `api_prefix`, `default_organization`, and `page_size`.
- AAP uses the default `awx.api_prefix` of `/api/controller/v2/`; upstream AWX users usually set `/api/v2/`.
- Use `untaped config set awx.token --prompt` or `--stdin` for tokens, or set `awx.token_command` to an argv list that prints the token; without either, `CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN` or `AAP_TOKEN` is used.
- Run `untaped awx ping` before a workflow when the profile or controller may be stale; it also checks the token via `/me/` and reports the authenticated `user`.

## Resource and selection patterns

- The eight writable groups are `job-templates`, `workflow-templates`, `projects`, `schedules`, `hosts`, `groups`, `inventories`, and `inventory-sources`. Credentials, credential types, organizations, unified templates, and job records remain read-only or action-specific views.
- Selection modes are exclusive: positional names, names with `--by-id`, `--stdin`, `--filter`/`--search`, or `--all`. Organization, inventory, inventory-organization, and parent scopes constrain lookup and filters; each group offers only its own scopes (`--organization` for org-scoped kinds, `--inventory`/`--inventory-organization`/`--parent` for hosts, groups and inventory sources, `--parent` for schedules), and any other is an unknown option (exit 2). A name not found names its scope and suggests close names (`did you mean 'deploy'?`); a second line says when `awx.default_organization` chose the organization (pass `--organization` to search elsewhere).
- Prefer typed pipes for composition. `--format pipe` carries kind and ID, and a `--stdin` consumer uses those IDs directly:

  ```bash
  untaped awx job-templates list --filter name__icontains=deploy --format pipe \
    | untaped awx job-templates patch --stdin --set verbosity=2
  ```

- Mutation selection is complete before any write. Empty or invalid selections do not partially mutate a batch. Machine data is stdout; previews, prompts, and progress are stderr.

## Patch and edit

- Use `patch` for the same field on existing resources:

  ```bash
  untaped awx inventory-sources patch \
    --filter inventory__name=Production \
    --set update_cache_timeout=3600
  ```

- `--set` is repeatable and JSON-coerced, except that a field the record holds as a string stays a string unless the value is a JSON object/array (`scm_branch=1.10` stays `"1.10"`). Unknown field names that closely match a known field are rejected with a "did you mean" hint unless `--allow-unknown-fields`; other unknown names are sent with a warning; `--patch-file` accepts a YAML/JSON mapping, with `--set` taking precedence. Values replace top-level fields; omitted fields remain unchanged and nested maps are not merged. Foreign-key integers are IDs; strings are names in scope. To target a numeric-looking name, preserve the JSON string: `--set 'inventory="123"'`; unquoted `inventory=123` is ID 123.
- Inventory cache timeouts are seconds, and `0` is valid. Changing the timeout does not toggle `update_on_launch`. Maps replace exactly, lists preserve order, and known secrets are redacted.
- Patch and edit cannot create, rename, reparent, retarget, or change identity. Use `apply` for create/update and `delete` for removal.
- `edit` opens one YAML multi-document batch. `--field` limits editable fields; missing fields stay unchanged and removing a document deselects it. Set `VISUAL`/`EDITOR` to a waiting editor such as `code --wait`.
- Edit requires a real `/dev/tty`, even with piped stdin or `--yes`. Editor streams use that terminal. A failed session keeps the YAML file and prints its path. Invalid YAML can be reopened or cancelled, and a no-op does not prompt or write.

## Apply, export, sync, and following executions

- `untaped awx apply FILE_OR_DIRECTORY` is the declarative complete-document create/update path for every kind (there is no per-kind `apply`). `apply -` reads the documents from stdin (`untaped --profile a awx export ... | untaped --profile b awx apply - --yes` promotes between profiles). `apply --check` writes nothing and exits 3 when anything would change, 0 when nothing would. `export` writes a fixed selection as portable YAML; `$encrypted$` placeholders preserve controller secrets, including a schedule's survey password answers in `extra_data`. A template export carries credentials, labels (by name; unknown labels fail apply, never created), extra vars and the survey (plain defaults kept; password defaults become `$encrypted$` and are dropped with a warning when the file creates a new template), so export → rename `metadata.name` → apply copies a template's non-secret configuration. `survey_spec: {}` removes a survey. Workflow template exports do not round-trip node graphs.
- Inventory and source settings preserve organization and parent identity. Operation support is specific: inventory sync rejects smart/source-less inventories and invalid or manual sources during preflight, while apply accepts representable inventory documents and rejects only incompatible source/configuration combinations. Inventory settings changes do not rewrite source-managed hosts or groups.
- `job-templates copy SOURCE --name NEW` / `workflow-templates copy` copy one template server-side in the source's scope; they refuse a taken name or `can_copy: false` before writing, warn about parts AWX will not copy, preview/confirm (`--dry-run`, `--yes`), and emit `awx.copy_outcome` (`id` = the new template), which `patch --stdin` on the same kind accepts.
- `job-templates list|get --with-scm` adds `scm_url`, `effective_scm_ref` (template `scm_branch` if set and the project allows override, else the project's `scm_branch`; empty stays empty) and `project_allow_override`; one project read per distinct project; usable with `--columns` and every format.
- `job-templates rename SOURCE NEW` / `workflow-templates rename` rename one template (patch keeps rejecting `name`); they refuse a name taken in the same scope before writing, preview old → new and confirm (`--dry-run`, `--yes`), re-read to verify the new name (mismatch: `failed`, exit 1), and emit `awx.rename_outcome` (`id`, `name`, `old_name`, `kind`, `action`), which `patch --stdin` on the same kind accepts.
- `launch --extra-vars` is repeatable (a later entry wins per key): `KEY=VAL` (only true/false/null, integers, and JSON objects/arrays decoded; `1.10` stays a string), `@FILE` (YAML/JSON mapping), or a raw JSON/YAML mapping; entries merge into one JSON mapping; YAML dates become ISO strings. Launch checks the template's launch settings first: a flag whose `ask_*_on_launch` is false (unless its value equals the template's own), an extra var outside the survey when only the survey prompts, or a missing required survey variable, is a usage error before any POST; a response with `ignored_fields` fails that row. `launch --host-pattern` limits the hosts; `--launch-inventory NAME|ID` (digits mean an id) is the inventory to run against; `--organization` scopes the lookup of the template and of the `--launch-inventory` and `--credential` names. `launch --dry-run -f yaml` submits nothing and shows each target's resolved `payload` (ids, merged `extra_vars`, survey password answers and secret-looking names such as `vault_pass`, `dbPassword` or `ssh_key`, at any depth, as `<redacted>`). `jobs list` defaults to the newest 20 (`--limit 0` for all); `--template NAME|ID` (digits mean an id) keeps one template's runs. `get` defaults to a table; use `-f yaml`/`-f json` for full records. Ctrl-C during submission or `--wait`/`--follow` exits 130 and prints the executions not known to have finished with a `jobs wait` hint.
- Use `projects sync`, `inventory-sources sync`, and `inventories sync`. `launch`/`sync --wait` waits and fails on unsuccessful terminal states; `--follow` does the same while streaming each job's log to stderr (ending with its PLAY RECAP; `[template]`-prefixed when several run), so failed hosts show as Ansible prints them. `launch`/`sync --timeout SECONDS` (with `--wait`/`--follow`) stops waiting: a still-running execution fails its row, keeps running, and is named in a `jobs wait` hint. Add `--cancel` (with `--wait`/`--follow`) to cancel every execution the command stops watching (timeout, polling error, Ctrl-C) instead: its row `detail` ends with `cancel requested` and it gets no `jobs wait` hint; on Ctrl-C it prints `interrupted: <target>: job N cancel requested`, also without a hint. Known invalid sync selections produce zero POSTs.
- `jobs cancel ID...` and `jobs relaunch ID... [--failed-hosts]` read every target first, preview, and ask once (`--yes`, `--dry-run`). Cancel rows are `awx.cancel_outcome` (`cancel_requested`, or `skipped` when already finished); relaunch rows are `awx.relaunch_outcome` whose `id`/`kind` name the new execution, so they pipe into `jobs wait --stdin`. `--failed-hosts` applies to job executions only; project and inventory updates cannot be relaunched.
- `jobs events`/`jobs logs` with several ids print one json/yaml array (each row has `job`); `--follow --format json` streams NDJSON instead. `-f` is always `--format`; `--follow` has no short form.
- Ordinary jobs expose `job_events`; project and inventory updates expose `events`. Workflow jobs, including sliced launch results, have no events or stdout route: `--follow` prints their status changes instead. Use `--kind project_update` or `--kind inventory_update` for non-default `jobs` commands; use `jobs wait` for workflow jobs, not workflow `events` or `logs`.
- Writes are serial by default, `--parallel` is capped at ten, and runtime failure stops new scheduling unless `--continue-on-error` is supplied. Already-running requests finish; partial results retain IDs. There is no transaction or rollback. Async inventory deletion reports `deletion_requested`.

## Test your change with `awx test`

Suites in `.untaped/awx/tests/` launch job templates with parameter variants and check each job against its `expect:`. After changing a playbook, role, or template variables:

1. Commit and push the branch (`git push -u origin HEAD`); jobs run what the remote has.
2. `untaped awx test validate` preflights every case (template exists, prompts for every field a case sets, survey variables present) without launching.
3. `untaped awx test run --scm-branch HEAD --format json` runs every suite on the pushed branch (refused until HEAD is pushed). Narrow with `--case SUITE/CASE` (repeatable) or pass suite files or directories.
4. Exit 0 means every case passed. Exit 4 (token rejected or permission missing) or 5 (AWX unavailable: retry later) is the environment, not your change: fix it before touching code. Otherwise (exit 1) read each non-`pass` row: `failure_reason`, `expectations` (expected vs actual), `failed_tasks` (host, task, msg, stderr), `log_tail`, `job_url`, and `scm_revision` (the commit the job ran). Fix, push, and rerun the failing cases.

- A case is `expect: {status, log: {contains, not_contains, matches}}` over its `launch:` payload; `defaults` apply to every case, and a case's `status` or `log` list replaces the default's. `status: failed` tests an intended failure.
- Results are `pass`, `fail` (expectation not met), `error` (launch, polling or log problem), or `timeout` (job cancelled after `--timeout`, the case's `timeout:`, or `awx.test_timeout`). `--no-cancel` leaves timed-out jobs running.
- Nothing prompts without a terminal: supply suite variables with `--var KEY=VALUE` or `--vars-file` (`--var` wins over a vars file, a later file over an earlier one, and both over the variable's default).
- Run as the dedicated agent profile when one is configured (`--profile agent`); see `docs/awx/agent-profile.md` in the untaped repository.

## Confirmations and output

- `patch`, `edit`, `apply`, and `delete` show one redacted preview and default-No confirmation. `--yes` skips it; `--dry-run` never writes and wins over `--yes`. Declining exits 1 (`cancelled; no changes made`). Configuration writes without a controlling terminal require `--yes` or `--dry-run` (exit 2). A piped record of another kind exits 2; empty `--stdin` is an error. A single named `launch`/`sync` submits immediately; multiple targets or an `--all`/`--filter`/`--search`/`--stdin` selection lists the targets and asks once (`--yes` skips, `--dry-run` previews).
- Exit codes: 0 success; 1 the thing failed (a failed item, job or case, an invalid input file, a name not found, a declined prompt); 2 usage (bad or conflicting flags, no selection, missing `--var`); 3 `apply --check` drift; 4 fix the environment, not the code (AWX rejected the token: `untaped config set awx.token --prompt`; a missing permission; local setup such as settings, `git`, or an unpushed `--scm-branch HEAD`); 5 AWX unavailable (network, timeout, 5xx, 429): retry later; 130 Ctrl-C. A batch exits with its most severe failure (130 > 2 > 4 > 5 > 1 > 3).
- With `--format json`, `yaml` or `pipe` (or `UNTAPED_DIAGNOSTICS=json`), stderr is JSON Lines: `{"level": "error", "message", "category", "system", "retryable", "hint", "exit_code", "details"}` (`item` names a per-item failure; `details` has `status` and `url` for HTTP errors). A `failed`/`partial`/`conflict` row caused by an error carries `error: {category, system, retryable, message, hint}` beside its `detail`.
- Keep stdout data-only and prefer `--format json`, `yaml`, or `pipe` for automation. `list` default columns apply to `table`/`raw` only. Launch/sync rows are `awx.launch_outcome`/`awx.sync_outcome` (pipe them into `jobs wait --stdin`); previews use the action `planned`; `fields_changed` and `preserved_secrets` are lists. Never expose secrets; preserve `$encrypted$` placeholders.

For the full user guide, see `docs/awx/usage.md` in the untaped repository.
