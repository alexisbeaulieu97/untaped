# awx test regression tools: declarative checks; with a baseline, only a regression fails

Decision ID: `dec_01a0ee7aa1b0734c84536654eaf40cb0`

An agent proving a playbook change must know that it broke nothing, that a
negative case failed for the right reason, and that a second run changes
nothing. A pass/fail over the whole run cannot say that when the base branch
already fails some cases.

- New checks stay declarative under `expect:` and inherit from `defaults`
  like `status` and `log`: `changed` (a total over the host summaries),
  `hosts` (per-host upper bounds, `"*"` for every host; numbers only, no
  `">0"` until a need shows), `failed_tasks` (a task and message match over
  the failed tasks attribution already reads, rescued failures excluded) and
  `idempotent` (relaunch the same resolved payload once the case passed; the
  rerun must succeed with nothing changed). A failed check is
  `awx.expectation`; a rerun that fails is attributed like any job.
- Changed tasks come from one filtered events read
  (`event=runner_on_ok&changed=true`), only as evidence of a rerun that
  changed something.
- A baseline is either a saved run (`--compare FILE`, the JSON or pipe
  output) or a run made first on another ref (`--baseline REF`); both feed
  one comparison keyed by `(suite, case)`, so a saved file costs no AWX time
  per iteration. Baseline cases not run now are reported as `removed` rows
  without a job.
- With a baseline, only a regression exits 1: a failure the baseline already
  had, or a new case's, does not. Exit codes that outrank 1 (4, 5, 2, 130)
  still win for any case, and for the `--baseline` run too, since the
  comparison is only as good as the run it compares with.
- The runner never counts failures itself: the command counts the rows that
  fail the run after the comparison, so the ledger records exactly what
  decides the exit code.

## Related decisions

- Builds on: [awx test failures name the responsible system](dec_01a0ee3c7ace70f6afb9ccbbac9d6d93-v4-awx-test-failures-name-the-responsible-system.md)
