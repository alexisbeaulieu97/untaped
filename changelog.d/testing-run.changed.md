In `untaped.sdk.testing`, `compose_with(...)` runs its block under a run of
that composition, as a command of it runs, and `make_project` and
`write_reference` open a run over the hermetic home when none is active.
