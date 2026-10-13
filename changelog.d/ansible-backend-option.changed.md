The graph commands' `--backend` applies to whichever source refresh the
command runs, not only to `--refresh`, and is an error only with `--cached`.
`--cached` and `--backend` without a selected source are usage errors, like
`--refresh`.
