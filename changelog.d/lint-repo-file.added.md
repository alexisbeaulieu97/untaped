`untaped lint` lints the repository it runs in: `.untaped/lint.yml` at the
root names the repository's own rule modules (`.untaped/lint/*.py`, ids such
as `house.no-print`), which plugins read its files (`readers:`, through the
new `TreeReader` contract) and what to ignore, and every rule reading the
repository's files runs on what git tracks or would track. `untaped lint new
ID` writes a rule and its failing test, `--config PATH` lints the current
directory with another lint file, and `untaped_lint.testing` gains `tree`,
`run_rule` and `repo_rule` for a rule's tests.
