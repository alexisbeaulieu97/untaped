# Launching, syncing and inspecting jobs

- [Launch templates](#launch-templates)
- [Sync projects and inventories](#sync-projects-and-inventories)
- [Wait, follow, time out, cancel](#wait-follow-time-out-cancel)
- [Inspect jobs](#inspect-jobs)
- [Cancel and relaunch](#cancel-and-relaunch)

`--help` on each command lists its flags; this page covers what they do
together.

## Launch templates

```bash
untaped awx job-templates launch Deploy --organization Default \
  --extra-vars @vars.yml --extra-vars version=1.10.0 --host-pattern web --wait
untaped awx job-templates launch Deploy --launch-inventory Staging --dry-run -f yaml
untaped awx workflow-templates launch "Release train" --follow
```

- `--extra-vars` entries merge left to right into one mapping, a later entry
  winning per key. YAML dates become ISO strings; values JSON cannot carry
  (`.nan`, `!!binary`) are a usage error.
- `--organization` scopes the template lookup and the `--launch-inventory`
  and `--credential` names.
- A single named template launches at once; several targets or a query or
  `--stdin` selection are listed and confirmed once.
- Results are `awx.launch_outcome` rows; pipe them into
  `untaped awx jobs wait --stdin` or `untaped awx jobs logs --stdin --follow`.

Before any POST, each template's launch settings are read:

- A flag whose `ask_*_on_launch` is false is a usage error, since AWX would
  silently ignore it.
- It is accepted when the template already has that value: its own, its
  project's branch for a `--scm-branch` it does not set, or extra vars it
  saves with those values.
- With a survey but no `ask_variables_on_launch`, `--extra-vars` may carry
  only survey variables.
- A missing required survey variable is a usage error; an empty
  `--extra-vars` mapping is never refused.
- If AWX still reports `ignored_fields`, that row fails and keeps the job id.

`--dry-run` submits nothing and shows each target's resolved `payload`
(names resolved to ids, `extra_vars` merged). It shows as `<redacted>`
survey password answers and, at any depth, variables whose names look
secret:

- a word of the name (split at `_`, `-`, `.` and camelCase) is `pass`,
  `passwd`, `password`, `passphrase`, `pwd`, `secret` or `token`;
- or the name ends in `password`, `passphrase`, `secret` or `token`
  (`dbpassword`);
- or two adjacent words form `api_key`, `access_key`, `private_key`,
  `secret_key` or `ssh_key`.

## Sync projects and inventories

```bash
untaped awx projects sync Playbooks --wait
untaped awx inventory-sources sync Cloud --inventory Production --wait
untaped awx inventories sync Production --follow
```

`inventories sync` updates every source of the inventory. Smart or
source-less inventories and manual or invalid sources fail the preflight
before any POST; `--continue-on-error` covers only failures after it.
Results are `awx.sync_outcome` rows.

## Wait, follow, time out, cancel

These apply to `launch` and `sync`:

- `--wait` fails the row (exit 1) on `failed`, `error` or `canceled`. The
  row gets the execution's `status`, `started_at`, `finished_at` (UTC) and
  `elapsed` (seconds).
- `--follow` also streams each job's log to stderr, ending with its PLAY
  RECAP (prefixed `[template]` when several run).
- A workflow job, including a sliced job template's launch, has no log:
  `--follow` prints its status changes instead.
- When AWX is still saving a job's events after it ends, a warning says the
  log may be cut short; `untaped awx jobs logs` has it all later.
- `--timeout` fails a still-running execution's row; the execution keeps
  running and an `untaped awx jobs wait` hint names it.
- `--cancel` cancels each execution as soon as the command stops watching it
  (timeout, polling error, Ctrl-C). Its row's `detail` ends with `cancel
  requested`, `it ended (successful) before the cancel`, or `cancel failed:
  …`.
- Ctrl-C exits 130 and lists the executions not known to have finished, with
  an `untaped awx jobs wait …` command to resume; they keep running.
- With `--cancel`, Ctrl-C cancels them instead; a second Ctrl-C stops the
  cancel requests and names what may still run.

## Inspect jobs

```bash
untaped awx jobs list --status failed --limit 10
untaped awx jobs get 101 102 --format yaml
untaped awx jobs logs 101 --grep 'fatal:' -i -f json
untaped awx jobs events 101 --filter event=runner_on_failed
untaped awx jobs wait 101 --timeout 600
```

- `jobs list --template` takes digits as an id; match a numeric name with
  `--filter job_template__name=123`.
- `jobs list`, `get` and `wait` tables show a summary; `json` and `yaml`
  carry the whole record.
- `jobs logs` downloads the full stdout. With `--follow` it reads only new
  events on each poll (colours removed); `--tail N --follow` starts from the
  newest events.
- `jobs events` prints the structured per-task events.
- With several ids, `json`/`yaml` print one array whose rows name their
  `job`; `--follow --format json` streams one object per line.
- `--kind` selects the execution type. Project and inventory updates have
  logs and events; workflow jobs have neither (use `jobs wait`). Piped
  records carry their own kind.

## Cancel and relaunch

```bash
untaped awx jobs cancel 101 102 --dry-run
untaped awx jobs relaunch 101 --failed-hosts --yes --format pipe \
  | untaped awx jobs wait --stdin
```

Both read every id first (an unknown id rejects the batch), preview, and ask
once.

- `cancel` rows are `awx.cancel_outcome`: `cancel_requested` (AWX stops it
  asynchronously; `jobs wait` shows it reach `canceled`), `skipped` when
  already finished, `failed` when AWX refuses.
- A cancel row's `status` is the one read before the request.
- `relaunch` rows are `awx.relaunch_outcome`, whose `id` and `kind` name the
  new execution. `--failed-hosts` reruns only the failed hosts.
- Project and inventory updates cannot be relaunched; sync them instead.
