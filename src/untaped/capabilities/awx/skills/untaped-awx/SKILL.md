---
name: untaped-awx
description: Use the `untaped awx` command to operate Ansible Automation Platform (AAP) or AWX and to prove playbook changes with `awx test` suites. Use when the user mentions AAP, AWX, Tower, automation controller, job templates, workflow templates, surveys, launching, syncing or following jobs, job logs, inventories, projects, schedules, or testing a playbook, role or template change.
---

# untaped AWX/AAP

`untaped awx` reads and changes AAP/AWX resources by name, launches and
follows jobs, and runs declarative test suites that launch a job template with
parameter variants and check every job. Prefer it to raw API calls: it
resolves names, previews writes, redacts secrets and pipes typed records.

This page is the map; the details ship next to it:

| File | Read it when |
|---|---|
| [references/test-suites.md](references/test-suites.md) | writing a test suite: every field, header variables, Jinja, merge rules, `!ref` |
| [references/test-results.md](references/test-results.md) | reading `awx test run` results and deciding what to fix |
| [references/agent-profile.md](references/agent-profile.md) | setting up the AWX user and untaped profile an agent runs as |
| [references/resources.md](references/resources.md) | selecting, patching, editing, copying, renaming, deleting resources |
| [references/specs.md](references/specs.md) | the YAML document format of `export` and `apply` |
| [references/jobs.md](references/jobs.md) | launching, syncing, waiting, following, cancelling, inspecting jobs |
| [examples/](examples/) | starting points: `smoke.yml`, `variants.yml`, `negative.yml`, `idempotent.yml` |

## Setup

- Settings live under `profiles.<name>.awx`: `base_url`, `token` or
  `token_command`, `api_prefix`, `default_organization`, `page_size`,
  `test_timeout` and `test_parallel`.
- AAP uses the default `api_prefix` `/api/controller/v2/`; upstream AWX
  usually needs `untaped config set awx.api_prefix /api/v2/`.
- Set the token with `untaped config set awx.token --prompt` (or `--stdin`),
  or point `awx.token_command` at an argv list that prints it. Without either,
  `CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN` or `AAP_TOKEN` is used. Never
  print or echo a token.
- `untaped awx ping` checks the controller and the token, and reports the
  authenticated `user`. Run it first when the profile may be stale.

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
| Suite format as JSON Schema | `untaped awx schema AwxTestSuite` |

Writable groups: `job-templates`, `workflow-templates`, `projects`,
`schedules`, `hosts`, `groups`, `inventories`, `inventory-sources`.
Credentials, credential types, organizations and unified templates are
read-only views. Confirm options with `--help` before acting.

## Test a change

Suites live in the playbook repository under `.untaped/awx/tests/`. After
changing a playbook, role or template variables:

1. Without a suite yet, `untaped awx test init "Deploy app"` writes a
   commented starter suite from the template's survey and launch prompts to
   `.untaped/awx/tests/deploy-app.yml`. Edit its cases;
   the examples show variants, `!ref`, negative and idempotent cases.
2. Once per task, save the base branch's results:
   `untaped awx test run --scm-branch main --format json > baseline.json`.
3. Commit and push the branch (`git push -u origin HEAD`): AWX runs what the
   remote has.
4. `untaped awx test validate` checks every case against its template
   without launching.
5. `untaped awx test run --scm-branch HEAD --compare baseline.json --format json`
   runs every case on the pushed commit (refused until HEAD is pushed) and
   gives each row a `change` (`regression`, `fixed`, `still_failing`, …).
   Narrow it with `--case SUITE/CASE` or suite paths.
6. Exit 0: nothing regressed; 1: a `regression`; 4 or 5: the environment,
   not your change. Each non-`pass` row's `failure.system` says who must act
   and `failure.evidence` why
   ([references/test-results.md](references/test-results.md)); fix, push,
   rerun.

Run as the dedicated agent profile when one exists
(`untaped --profile agent awx test run`); see
[references/agent-profile.md](references/agent-profile.md).

## Output and pipes

- Read results with `--format json` (or `yaml`); never parse tables. stdout
  carries data only; previews, prompts, progress and errors go to stderr.
- `--format pipe` emits typed records (`awx.job_template`, `awx.job`,
  `awx.launch_outcome`, …) and a `--stdin` consumer of the same kind uses
  their ids directly:
  `untaped awx job-templates launch Deploy --format pipe | untaped awx jobs wait --stdin`.

## Safety

- `patch`, `edit`, `apply`, `delete`, `copy`, `rename`, `jobs cancel` and
  `jobs relaunch` preview once and ask with No as the default. Run them with
  `--dry-run`, show the user the preview, then pass `--yes` only once the user
  approves. Without a terminal, a configuration write needs `--yes` or
  `--dry-run` (exit 2 otherwise).
- A single named `launch`/`sync` submits at once; several targets, `--all`,
  `--filter`, `--search` or `--stdin` list them and ask once.
- Exit codes: 0 success, 1 failure or declined, 2 usage error, 3 drift
  (`apply --check`), 4 fix the environment, 5 retry later, 130
  interrupted. `--format json` makes stderr JSON Lines with each error's
  `category`, `system` and `hint`; a failed row carries them in `error`
  (`failure` in `awx test run` rows).
- Keep `$encrypted$` placeholders as they are; they preserve stored secrets.

## Pitfalls

- A name that exists in several organizations is ambiguous: pass
  `--organization` or set `awx.default_organization`.
- AWX ignores a launch field whose `ask_*_on_launch` is false; untaped refuses
  it before launching, so enable the prompt on the template instead.
- `--scm-branch` needs `ask_scm_branch_on_launch`, which AWX allows only when
  the project allows branch override.
- `patch` and `edit` never create or rename; use `apply` and `rename`.
- A workflow template export carries its node graph (`spec.nodes`), and
  `apply --source-ref REF PATH...` applies documents at a git ref; see
  [references/specs.md](references/specs.md).
- `-f` always means `--format`; `--follow` has no short form.
