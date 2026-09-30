---
name: untaped-awx
description: Use the `untaped awx` command to operate Ansible Automation Platform (AAP) or AWX and to prove playbook changes with `awx test` suites. Use when the user mentions AAP, AWX, Tower, automation controller, job templates, workflow templates, surveys, launching, syncing or following jobs, job logs, inventories, projects, schedules, or testing a playbook, role or template change.
---

# untaped AWX/AAP

The controller is shared and runs only what the git remote holds. Change it
only after the user has seen a preview of an explicit selection, and call a
change proven only when a job ran your pushed commit and its row says why it
passed or failed.

Prefer `untaped awx` to raw API calls: it resolves names, previews writes
and redacts secrets. `--help` and `--columns ?` list options and columns.

## Setup

- Settings live under `profiles.<name>.awx`. Upstream AWX usually needs
  `untaped config set awx.api_prefix /api/v2/`; AAP uses the default.
- Set the token with `untaped config set awx.token --prompt` or
  `awx.token_command`. Never print or echo a token.
- `untaped awx ping` checks the controller and token and names the
  authenticated `user`; run it first when the profile may be stale.

## Commands

| When you need to | Use |
|---|---|
| find resources, or see what a selector matches before a write | `untaped awx job-templates list --filter name__icontains=deploy` |
| read one resource in full | `untaped awx job-templates get Deploy --format yaml` |
| set the same field on one or many resources | `untaped awx job-templates patch Deploy --set verbosity=2 --dry-run` |
| create resources, change many fields, keep config in git | `untaped awx apply deploy.yml --dry-run` on an `export` |
| run a template and see its outcome | `untaped awx job-templates launch Deploy --follow` (`projects sync` first for a new commit) |
| diagnose a job | `untaped awx jobs logs 101 --tail 50`, `untaped awx jobs events 101` |
| know what a template change affects | `untaped awx job-templates usage Deploy --recursive` |
| prove a playbook, role or template change | `untaped awx test` (below) |

Credentials, credential types, organizations and unified templates are
read-only.

## Workflows

### Change a template safely

1. Save a recovery point:
   `untaped awx job-templates export Deploy --out /tmp/deploy-before.yml`.
2. Preview: `patch … --dry-run`, or `untaped awx apply deploy.yml --dry-run`
   on an edited copy of the export. Show the user every planned row.
3. Once the user approves, rerun it with `--yes` instead of `--dry-run`.
4. Check: `untaped awx apply deploy.yml --check` exits 0, or `get --format
   yaml` shows the patched value.
5. To undo, apply the recovery point, preview first. It restores fields on the
   template that still exists; it cannot recreate secrets, access or schedules
   (read references/specs.md "Export" before relying on it for a delete).

### Test a change

Suites live in the playbook repository under `.untaped/awx/tests/`. `awx
test` is experimental: after upgrading untaped, revalidate and rebaseline.
Use the agent profile when one exists (`--profile agent`).

1. Without a suite, `untaped awx test init "Deploy app"` (`--workflow` for a
   workflow) writes a starter; edit its cases from the examples.
2. Once per task, save the base branch's results outside the checkout:
   `untaped awx test run --scm-branch main --format json > /tmp/baseline.json`
   (exit 1 is expected when `main` already fails some cases).
3. Commit and push: AWX runs what the remote has.
4. `untaped awx test validate` passes without launching anything.
5. `untaped awx test run --scm-branch HEAD --compare /tmp/baseline.json --format json`.
   When the change edits a spec under `.untaped/awx/`, use `--source-ref
   HEAD` instead of `--scm-branch HEAD` in steps 4 and 5.
6. Exit 0: no regression and no failing new case. Exit 1: each failing row's
   `failure.system` says who must act; fix, push, rerun. Exit 4 or 5: the
   environment, not the change.

## Destructive operations

Every write (`patch`, `edit`, `apply`, `delete`, `copy`, `rename`,
membership `add`/`remove`, `jobs cancel`, `jobs relaunch`, `test prune`)
previews once and asks once, No by default.

- **Select explicitly**: names (scoped by `--organization`, `--inventory`,
  `--parent`), `--by-id`, `--filter`/`--search` or `--stdin`. Use `--all`
  only when the user asked for everything in scope; `list` the same selector
  first.
- **Preview**: `--dry-run` never writes, even with `--yes`; `apply --check`
  exits 3 on drift. Show the user each planned row (resource, field, old →
  new).
- **Confirm**: pass `--yes` only after the user approves that preview.
  Without a terminal, a write needs `--yes` or `--dry-run` (else exit 2).
- **Read the exit**: 1 is a decline (`cancelled; no changes made`) or a
  failed row (read its `error`); 2 means nothing ran.
- **Recover**: there is no rollback, and a failed batch keeps what it wrote.
  Reapply a prior export; a deleted resource returns with a new id and
  without its secrets, access or history.
- A single named `launch` or `sync` submits at once; several targets or a
  query selection are listed and confirmed once.

## Pitfalls

- Read `--format json` or `yaml`, not tables. `--format pipe` feeds a
  `--stdin` consumer of the same kind.
- Keep `$encrypted$` placeholders as they are; they preserve stored secrets.
- A name in several organizations is ambiguous: pass `--organization` or set
  `awx.default_organization`.
- A launch field whose `ask_*_on_launch` is false is refused before
  launching; enable the prompt on the template. `--scm-branch` also needs the
  project to allow branch override.
- `patch` and `edit` never create or rename; use `apply` and `rename`.
- `edit` needs a real terminal even with `--yes`; without one use `patch`.

## References

Read, when you are:

- selecting, patching, editing, copying, renaming or piping resources:
  [references/resources.md](references/resources.md);
- writing or applying export documents or workflow node graphs:
  [references/specs.md](references/specs.md);
- launching, syncing, waiting on, cancelling or inspecting jobs:
  [references/jobs.md](references/jobs.md);
- writing a suite or testing with `--source-ref`:
  [references/test-suites.md](references/test-suites.md);
- reading a test run that exited non-zero, or a baseline comparison:
  [references/test-results.md](references/test-results.md);
- setting up the AWX user and profile an agent runs as:
  [references/agent-profile.md](references/agent-profile.md);
- starting a suite: [examples/](examples/) (smoke, variants, negative,
  idempotent, workflow).
