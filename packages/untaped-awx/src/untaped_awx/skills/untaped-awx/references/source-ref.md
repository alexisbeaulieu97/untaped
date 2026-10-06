# Temporary copies and `--source-ref`

`--source-ref REF` reads specs as they are at a git ref instead of the working
tree. `awx test` uses it to run suites against temporary copies of the
templates a change edits; `awx apply` uses it to apply files from a tag or
branch. Either way, messages name the files read as `REF:PATH`.

- [Test at a ref](#test-at-a-ref)
- [What a run with copies reports](#what-a-run-with-copies-reports)
- [Leftover copies](#leftover-copies)
- [Apply from a git ref](#apply-from-a-git-ref)

## Test at a ref

`--scm-branch` runs a branch's playbooks with the templates AWX holds. When
the branch also changes a template or workflow, keep its spec in the
repository ([specs.md](specs.md)) and run `untaped awx test run --source-ref
REF`: each suite runs against a temporary copy of its template as REF
describes it.

1. REF (a branch, tag or commit; `HEAD` once pushed) is pinned to its
   commit, which a remote must have. Suites and specs are read at that
   commit, never from the working tree.
2. A suite binds to a spec by name: a `kind: JobTemplate` (for
   `jobTemplate`) or `kind: WorkflowJobTemplate` document anywhere under
   `.untaped/awx/` with the template's name and organization.
   - A copied workflow's nodes that run a template with a spec run that
     template's copy.
   - A spec in another organization does not bind, with a warning.
3. Each copy is created as `apply` would create the spec, except:
   - its name is `NAME [untaped-test SHA RUN]` (7-digit commit and a random
     run id), so concurrent runs never collide;
   - its description carries the marker `untaped-test run=RUN ref=REF sha=SHA
     created=TIME` that `prune` finds leftovers by;
   - its `scm_branch` is the commit, so a job template's project must allow
     branch override; a workflow passes it to nodes that prompt for it;
   - it prompts on launch for every field a case sets in `launch`;
   - it has no webhook settings.

   Everything else a spec names must already exist: only the copies are
   created.
4. Every template the run launches without copying it must prompt for
   `scm_branch`: a suite's template without a spec, and each template a
   workflow of the run runs without a copy. Otherwise it would run its own
   branch, so the run is refused before anything is created: add the
   template's spec under `.untaped/awx/` or enable
   `ask_scm_branch_on_launch`.
5. The copies are created without a confirmation, the cases run, and the
   copies are deleted however the run ends (running jobs cancelled first);
   `--keep` keeps them.

- A case's `launch.scm_branch` is replaced by the commit.
- `--source-ref` cannot be combined with `--scm-branch` or `--baseline`
  (compare with `--compare`).
- `--no-cancel` needs `--keep`: AWX cannot delete a template while its job
  runs.
- `validate --source-ref REF` does everything but the writes. A case of a copied template is checked against
  the spec.
- The AWX user needs to create and delete templates
  ([agent-profile.md](agent-profile.md)).

## What a run with copies reports

The run creates its copies before any case launches. A case of a copied
template reports the copy's job, whose `scm_branch` is the commit. stderr
names each suite that runs a template AWX holds (`deploy: runs AWX's
JobTemplate 'Deploy' at 1a2b3c4 (no spec)`) and each copy created, with the
prompts it enables.

A copy that could not be provisioned is not a test result: the run stops
before any launch with one error and prints no row.

- The error starts `cannot provision the temporary test set of REF (nothing
  was created); nothing launched:` (or `(the copies created are torn
  down)`), or `cannot run REF; nothing launched:` when the run copies
  nothing.
- It lists each problem as `LABEL: message`, and carries the worst problem's
  `category`, `system` and `hint`.
- An invalid spec, or two specs matching one suite, fail alone as
  `awx.suite`, naming the files.

| `system` | When | `category` (exit) | What to do |
|---|---|---|---|
| `awx.suite` | A spec names something AWX does not have, a copy's name is taken, or two specs match one suite | `not_found`, `conflict`, `invalid` (1) | Fix the spec or suite (or create the missing resource in AWX), then `untaped awx test validate --source-ref REF`. |
| `awx.scm` | A copy's project does not allow branch override, or a template the run does not copy does not prompt for `scm_branch` | `invalid` (1) | Enable `allow_override` on the project; add the template's spec or enable `ask_scm_branch_on_launch`. |
| `awx.credentials` | AWX refused to create a copy (401, 403) | `auth`, `permission` (4) | Give the AWX user the roles in [agent-profile.md](agent-profile.md). |
| `awx.controller` | AWX was unavailable while creating a copy or reading a project | `unavailable` (5) | Retry later. |
| `awx` | AWX did not store a copy as the spec asks (`unverified …` fields) | `invalid` (1) | Rerun with `--keep` to inspect the copy, then fix the spec. |

After the results, each copy is deleted, workflows first, and named on
stderr; with `--keep` it is listed as `kept … (id N)`.

- A copy teardown could not delete in 30 seconds (a job of it still runs),
  or one a second Ctrl-C left, gets a `warning: teardown: … is left: …`
  line.
- The warning ends with the command that deletes that run's copies:
  `untaped awx test prune --run k3x9 --older-than 0`.
- The cases' results and the exit code stand.

`test validate --source-ref REF` prints one `awx.provision_outcome` row per copy
it would create (`planned`). `id` is `null` for a planned copy; `path` (`REF:PATH`
of its spec) and `prompts` are set on planned copies only. `created_at` is when the run
started, not when the copy was created.

## Leftover copies

`untaped awx test prune` deletes copies a killed run left behind: templates
named like a copy whose description carries the matching marker, created
more than `--older-than` ago.

- An age below the longest run deletes copies of runs still going, whose
  next launches then fail. Prune another run's copies only once it has ended;
  `--run RUN` limits it to one run, as the teardown warning's hint does.
- It lists the copies and asks once; preview with `--dry-run`.
- It prints one `awx.prune_outcome` row per leftover copy (`planned` with
  `--dry-run`, then `deleted` or `failed`).

Both `prune` and the `validate` preview print the fields of a `--format json`
row (`--columns '?'` lists them under `--dry-run`). A `failed` row's `error`
carries the attributed failure (`category`, `system`, `retryable`, `message`,
`hint`).

## Apply from a git ref

`--source-ref REF` reads the given files and directories as they are at `REF`
(a branch, tag or commit of the repository containing the current directory),
never from the working tree, then applies them as usual:

```bash
untaped awx apply --source-ref v1.4.0 .untaped/awx/templates .untaped/awx/workflows --dry-run
```

- Paths are relative to the current directory. Local edits and untracked
  files are never read.
- `HEAD` must be pushed to its upstream (as for `awx test run --scm-branch
  HEAD`); other refs need not be, since apply reads the files locally.
- A symbolic link at the ref is refused rather than followed. Stdin (`-`)
  cannot be combined with `--source-ref`.

A playbook repository keeps its specs beside its suites. Folder names are only
a convention, since each document's `kind` decides what it is:

```text
.untaped/awx/
├── templates/deploy.yml      # kind: JobTemplate
├── workflows/release.yml     # kind: WorkflowJobTemplate
└── tests/deploy-smoke.yml    # kind: AwxTestSuite
```
