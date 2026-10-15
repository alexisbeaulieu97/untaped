Drop `--continue-on-error` from awx calls and scripts, since continuing is now
the default; passing it exits 2 as an unknown option. Add `--fail-fast` where
a script relied on awx stopping at the first failure.
