# awx test regression tools: declarative checks; a baseline excuses only the same failure

Decision ID: `dec_01a0ee7aa1b0734c84536654eaf40cb0`

An agent proving a playbook change must know that it broke nothing, that a
negative case failed for the right reason, and that a second run changes
nothing. A pass/fail over the whole run cannot say that when the base branch
already fails some cases.

- New checks (`changed`, `hosts`, `failed_tasks`, `idempotent`) are
  declarative under `expect:` and inherit like `status` and `log`; no new
  concept. Host bounds merge per counter, and a named host's bound wins over
  `"*"`. A cut host list never hides a failure: past 500 hosts, the checks
  read the offending hosts with filtered queries. An idempotent rerun runs
  the commit the first job ran, and only a case expecting success can be
  idempotent.
- A baseline is either a saved run (`--compare FILE`) or a run made first on
  another ref (`--baseline REF`); both feed one comparison keyed by
  `(suite, case)`, so a saved file costs no AWX time per iteration.
- Only a failure the baseline already had is excused, and only when it fails
  the same way (same `system`). A failure the baseline cannot vouch for
  (it failed for the environment: `unverified`), a new case that fails, and a
  regression all fail the run. Exit codes that outrank 1 (4, 5, 2, 130)
  still win for any case, and for the `--baseline` run too, since the
  comparison is only as good as the run it compares with.
- The runner never notes failures one case at a time: the command counts the
  failures the compared outcome says decide the exit code, and a run that
  aborts counts the cases that finished before it propagates.

## Related decisions

- Builds on: [awx test failures name the responsible system](dec_01a0ee3c7ace70f6afb9ccbbac9d6d93-v4-awx-test-failures-name-the-responsible-system.md)
