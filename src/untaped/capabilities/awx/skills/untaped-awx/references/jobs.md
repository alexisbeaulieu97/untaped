# Launching, syncing and inspecting jobs

## Launch templates

```bash
untaped awx job-templates launch Deploy --organization Default \
  --extra-vars @vars.yml --extra-vars version=1.10.0 --host-pattern web --wait
untaped awx job-templates launch Deploy --launch-inventory Staging --dry-run -f yaml
untaped awx workflow-templates launch "Release train" --follow
```

- `--extra-vars` is repeatable and merged left to right into one mapping (a
  later entry wins per key): `KEY=VAL` decodes only `true`/`false`/`null`,
  integers and JSON objects/arrays (`version=1.10` stays a string); `@FILE`
  reads a YAML/JSON mapping; a raw JSON or YAML mapping also works. YAML dates
  become ISO strings; values JSON cannot carry (`.nan`, `!!binary`) are a
  usage error.
- `--host-pattern` limits the hosts, `--launch-inventory NAME|ID` is the
  inventory to run against (digits mean an id), `--credential NAME` (repeatable)
  replaces credentials, and `--scm-branch`, `--job-tag`, `--skip-tag`,
  `--verbosity`, `--diff-mode` and `--job-type` override the template's own.
  `--organization` scopes the lookup of the template and of the
  `--launch-inventory` and `--credential` names.
- Before any POST, each template's launch settings are read. A flag whose
  `ask_*_on_launch` is false (AWX would silently ignore it) is a usage error,
  unless the template has that value already: its own, its project's branch
  for a `--scm-branch` it does not set, or extra vars it saves with those
  values (AWX treats these as no-ops). With a survey but no
  `ask_variables_on_launch`, `--extra-vars` may carry only survey variables.
  A missing required survey variable is a usage error too; an empty
  `--extra-vars` mapping is never refused as unprompted. If AWX still reports
  `ignored_fields`, that row fails and keeps the job id.
- `--dry-run` submits nothing and shows each target's resolved `payload`
  (names resolved to ids, `extra_vars` merged). Survey password answers and
  variables whose names look secret (`vault_pass`, `dbPassword`, `ssh_key`,
  `api_token`, at any depth) are shown as `<redacted>`. A name is split into
  words at `_`, `-`, `.` and camelCase; it looks secret when a word is
  `pass`, `passwd`, `password`, `passphrase`, `pwd`, `secret` or `token`,
  ends in `password`, `passphrase`, `secret` or `token` (`dbpassword`), or two
  adjacent words form `api_key`, `access_key`, `private_key`, `secret_key` or
  `ssh_key`.
- A single named template launches at once; several targets or a
  `--all`/`--filter`/`--search`/`--stdin` selection are listed and confirmed
  once (`--yes` skips, `--dry-run` previews).
- Results are `awx.launch_outcome` rows; pipe them into
  `untaped awx jobs wait --stdin` or `untaped awx jobs logs --stdin --follow`.

## Sync projects and inventories

```bash
untaped awx projects sync Playbooks --wait
untaped awx inventory-sources sync Cloud --inventory Production --wait
untaped awx inventories sync Production --follow
```

`inventories sync` updates every source of the inventory. Smart or
source-less inventories and manual or invalid sources fail the preflight
before any POST; `--continue-on-error` covers runtime failures after that
preflight, not an invalid selection. Results are `awx.sync_outcome` rows.

## Wait, follow, time out, cancel

These flags apply to `launch` and `sync`:

- `--wait` waits for each execution and fails the row (exit 1) on `failed`,
  `error` or `canceled`. The row then has the execution's `status`,
  `started_at` and `finished_at` (UTC, `2026-01-02T03:04:05Z`), as in the
  `awx.job` record `untaped awx jobs wait` prints.
- `--follow` does the same while streaming each job's log to stderr, ending
  with its PLAY RECAP (`[template]`-prefixed when several run), so failed
  hosts show as Ansible prints them (`fatal: [host]: FAILED! => …`). A
  workflow job, including the result of a sliced job template launch, has no
  log: its status changes are printed instead. When AWX is still saving the
  job's events after it ends, a warning says the log may be cut short
  (`untaped awx jobs logs` has it all later).
- `--timeout SECONDS` (with `--wait`/`--follow`) stops waiting: a still
  running execution fails its row, keeps running, and is named in an
  `untaped awx jobs wait` hint.
- `--cancel` (with `--wait`/`--follow`) cancels every execution the command
  stops watching (timeout, polling error, Ctrl-C) instead, as soon as it
  stops watching it, without waiting for the others; its row's `detail`
  ends with `cancel requested` (or `it ended (successful) before the
  cancel`, or `cancel failed: …`).
- Ctrl-C exits 130 and lists the executions not known to have finished, with
  an `untaped awx jobs wait …` command to resume; they keep running on the
  controller. With `--cancel`, it cancels them instead
  (`interrupted: <target>: job N cancel requested`, no hint), and a second
  Ctrl-C stops the cancel requests and names what may still run.

## Inspect jobs

```bash
untaped awx jobs list --status failed --limit 10
untaped awx jobs list --template Deploy
untaped awx jobs get 101 102 --format yaml
untaped awx jobs logs 101 --tail 50
untaped awx jobs logs 101 --grep 'fatal:' -i -f json
untaped awx jobs logs 101 --follow
untaped awx jobs events 101 --filter event=runner_on_failed
untaped awx jobs wait 101 --timeout 600
```

- `jobs list` shows the newest 20 (`--limit 0` for all); `--template NAME|ID`
  keeps one template's runs (the project for `--kind project_update`, the
  inventory source for `--kind inventory_update`; digits mean an id, so match
  a numeric name with `--filter job_template__name=123`). `jobs list`,
  `get` and `wait` tables show a summary; json and yaml carry the whole
  record.
- `jobs logs` prints a job's stdout, downloaded in full; with `--follow` it
  reads only new events on each poll (colours removed), and `--tail N
  --follow` starts from the newest events only. `jobs events` prints the
  structured per-task events. Both accept several ids or `--stdin` and
  `--follow` (no short form: `-f` is always `--format`). With several ids,
  json/yaml print one array whose rows name their `job`; `--follow --format
  json` streams one object per line.
- `--kind` selects the execution type: `job` (default), `workflow_job`,
  `project_update`, `inventory_update`, `ad_hoc_command`. Project and
  inventory updates have logs and events; workflow jobs, including sliced
  launch results, have neither (use `jobs wait`). Piped records carry their own kind.

```bash
untaped awx jobs wait 101 --kind project_update
untaped awx jobs logs 101 --kind project_update
```

## Cancel and relaunch

```bash
untaped awx jobs cancel 101 102 --dry-run
untaped awx jobs relaunch 101 --failed-hosts --yes --format pipe \
  | untaped awx jobs wait --stdin
```

Both read every id first (an unknown id rejects the batch), preview, and ask
once (`--yes`, `--dry-run`). `cancel` rows are `awx.cancel_outcome`
(`cancel_requested`: AWX stops it asynchronously, and `jobs wait` shows it
reach `canceled`; `skipped` when already finished; `failed` when AWX
refuses); `relaunch` rows are
`awx.relaunch_outcome`, whose `id`/`kind` name the new execution. A cancel
row's `status` is the one read before the request, so its table leaves it out.
`--failed-hosts` reruns only the failed hosts of a job; project and inventory
updates cannot be relaunched (sync them instead).
