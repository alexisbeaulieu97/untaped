---
description: "Read before launching a template or syncing a project or inventory: which launch values a template accepts, what a dry run shows and redacts, and how --wait, --follow, --timeout and --cancel end."
---

## Launch values

A later `--extra-vars` entry wins per key. YAML dates become ISO strings;
values JSON cannot carry (`.nan`, `!!binary`) are a usage error.
`--organization` scopes the template lookup and the `--launch-inventory` and
`--credential` names.

Before any POST, each template's launch settings are read:

- A flag whose `ask_*_on_launch` is false is a usage error, since AWX would
  silently ignore it. Enable the prompt on the template; `--scm-branch` also
  needs the project to allow branch override.
- It is accepted when the template already has that value: its own, its
  project's branch for a `--scm-branch` it does not set, or extra vars it
  saves with those values.
- With a survey but no `ask_variables_on_launch`, `--extra-vars` may carry
  only survey variables.
- A missing required survey variable is a usage error; an empty
  `--extra-vars` mapping is never refused.
- If AWX still reports `ignored_fields`, that row fails and keeps the job id.

## Dry runs

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

## Syncing

`inventories sync` updates every source of the inventory. Smart or
source-less inventories and manual or invalid sources fail the preflight
before any POST, so nothing starts. Results are `awx.sync_outcome` rows, as
launches give `awx.launch_outcome` rows; pipe either into `untaped awx jobs
wait --stdin` or `untaped awx jobs logs --stdin --follow`.

## Waiting and following

These apply to `launch` and `sync`:

- `--wait` fails the row (exit 1) on `failed`, `error` or `canceled`. The
  row gets the execution's `status`, `started_at`, `finished_at` (UTC) and
  `elapsed` (seconds).
- With several executions, `--follow` prefixes each log line with
  `[template]`. A sliced job template's launch is a workflow job, so it
  shows status changes, not a log.
- When AWX is still saving a job's events after it ends, a warning says the
  log may be cut short; `untaped awx jobs logs` has it all later.
- A row `--timeout` failed names the execution in an `untaped awx jobs wait`
  hint.
- A `--cancel` row's `detail` ends with `cancel requested`, `it ended
  (successful) before the cancel`, or `cancel failed: …`.
- Ctrl-C exits 130 and lists the executions not known to have finished, with
  an `untaped awx jobs wait …` command to resume; they keep running.
- With `--cancel`, Ctrl-C cancels them instead; a second Ctrl-C stops the
  cancel requests and names what may still run.
