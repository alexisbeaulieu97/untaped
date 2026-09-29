---
name: untaped-awx
description: Use the `untaped awx` command to operate Ansible Automation Platform (AAP) or AWX and to prove playbook changes with `awx test` suites. Use when the user mentions AAP, AWX, Tower, automation controller, job templates, workflow templates, surveys, launching, syncing or following jobs, job logs, inventories, projects, schedules, or testing a playbook, role or template change.
---

# untaped AWX/AAP

`untaped awx` reads and changes AAP/AWX resources by name, launches and
follows jobs, and runs declarative test suites that check every job. Prefer
it to raw API calls: it resolves names, previews writes, redacts secrets and
pipes typed records.

The details ship next to this page:

| File | Read it when |
|---|---|
| [references/test-suites.md](references/test-suites.md) | writing a test suite: every field, header variables, Jinja, merge rules, `!ref`, workflow suites, temporary test sets (`--source-ref`, `prune`) |
| [references/test-results.md](references/test-results.md) | reading `awx test run` results and deciding what to fix |
| [references/agent-profile.md](references/agent-profile.md) | setting up the AWX user and untaped profile an agent runs as |
| [references/resources.md](references/resources.md) | selecting, patching, editing, copying, renaming, deleting resources |
| [references/specs.md](references/specs.md) | the YAML document format of `export` and `apply`: workflow node graphs, `apply --source-ref` |
| [references/jobs.md](references/jobs.md) | launching, syncing, waiting, following, cancelling, inspecting jobs |
| [examples/](examples/) | starting points: `smoke.yml`, `variants.yml`, `negative.yml`, `idempotent.yml`, `workflow.yml` |

## Setup

- Settings live under `profiles.<name>.awx`: `base_url`, `token` or
  `token_command`, `api_prefix`, `default_organization`, `page_size`,
  `test_timeout` and `test_parallel`.
- AAP uses the default `api_prefix` `/api/controller/v2/`; upstream AWX
  usually needs `untaped config set awx.api_prefix /api/v2/`.
- Set the token with `untaped config set awx.token --prompt` (or `--stdin`),
  or point `awx.token_command` at an argv list that prints it (environment
  fallbacks: [references/agent-profile.md](references/agent-profile.md)).
  Never print or echo a token.
- `untaped awx ping` checks the controller and the token.

## Commands

| Task | Command |
|---|---|
| Find resources | `untaped awx job-templates list --filter name__icontains=deploy` |
| Read one in full | `untaped awx job-templates get Deploy --format yaml` |
| Change a field | `untaped awx job-templates patch Deploy --set verbosity=2 --dry-run` |
| Export / apply YAML | `untaped awx job-templates export Deploy --out deploy.yml`, then `untaped awx apply deploy.yml --dry-run` |
| Launch and follow | `untaped awx job-templates launch Deploy --host-pattern web --follow` |
| Sync a project | `untaped awx projects sync Playbooks --wait` |
| Inspect a job | `untaped awx jobs logs 101 --tail 50`, `untaped awx jobs events 101` |
| Where a template runs | `untaped awx job-templates usage Deploy --recursive` |
| Start a test suite | `untaped awx test init Deploy` |
| Check suites | `untaped awx test validate` |
| Run suites | `untaped awx test run --scm-branch HEAD --format json` |
| Run suites on the branch's specs | `untaped awx test run --source-ref HEAD --format json` |
| Delete leftover test copies | `untaped awx test prune --dry-run` |
| Suite format as JSON Schema | `untaped awx schema AwxTestSuite` |

Writable groups: `job-templates`, `workflow-templates`, `projects`,
`schedules`, `hosts`, `groups`, `inventories`, `inventory-sources`.
Credentials, credential types, organizations and unified templates are
read-only views. Run `untaped awx job-templates --help` (or any group) to
confirm options before acting.

## Test a change

Suites live in the playbook repository under `.untaped/awx/tests/`. After
changing a playbook, role or template variables:

1. Without a suite yet, `untaped awx test init "Deploy app"` (`--workflow`
   for a workflow) writes a starter suite from the template's survey and
   prompts; edit its cases, starting from the examples.
2. Once per task, save the base branch's results outside the checkout:
   `untaped awx test run --scm-branch main --format json > /tmp/baseline-PROJ-123.json`
   (exit 1 is expected when `main` already fails cases).
3. Commit and push the branch (`git push -u origin HEAD`): AWX runs what the
   remote has.
4. `untaped awx test validate` checks every case without launching.
5. `untaped awx test run --scm-branch HEAD --compare /tmp/baseline-PROJ-123.json --format json`
   runs every case on the pushed commit and gives each row a `change`
   (narrow it with `--case SUITE/CASE`).
6. Exit 0: no regression and no failing new case (`still_failing` rows are
   only reported); 4 or 5: the environment, not your change. Each failing
   row's `failure.system` says who must act and `failure.evidence` why
   ([references/test-results.md](references/test-results.md)); fix, push,
   rerun.

When the change also edits a template or workflow spec under
`.untaped/awx/`, use `--source-ref HEAD` instead of `--scm-branch HEAD`:
suites run temporary copies of the specs at the commit.
A provisioning failure launches nothing and is never a test result.

Run as the dedicated agent profile when one exists
(`untaped --profile agent awx test run`).

## Output and pipes

- Read results with `--format json`; never parse tables. stdout carries data
  only; everything else goes to stderr.
- `--format pipe` emits typed records that a `--stdin` consumer of the same
  kind reads directly:
  `untaped awx job-templates launch Deploy --format pipe | untaped awx jobs wait --stdin`.

## Safety

- `patch`, `edit`, `apply`, `delete`, `copy`, `rename`, `jobs cancel` and
  `jobs relaunch` preview once and ask (No by default). Run them with
  `--dry-run`, show the user the preview, then pass `--yes` only once the user
  approves. Without a terminal, a configuration write needs `--yes` or
  `--dry-run` (exit 2 otherwise).
- A single named `launch`/`sync` submits at once; several targets list
  them and ask once.
- Exit codes: 0 success, 1 failure or declined, 2 usage error, 3 drift
  (`apply --check`), 4 fix the environment, 5 retry later, 130
  interrupted. `--format json` makes stderr JSON Lines, and failed rows
  carry an `error` (see [references/resources.md](references/resources.md)).
- Keep `$encrypted$` placeholders as they are; they preserve stored secrets.

## Pitfalls

- A name that exists in several organizations is ambiguous: pass
  `--organization` or set `awx.default_organization`.
- AWX ignores a launch field whose `ask_*_on_launch` is false; untaped refuses
  it before launching, so enable the prompt on the template instead.
- `--scm-branch` needs `ask_scm_branch_on_launch`, which AWX allows only when
  the project allows branch override.
- `patch` and `edit` never create or rename; use `apply` and `rename`.
- `-f` always means `--format`; `--follow` has no short form.
