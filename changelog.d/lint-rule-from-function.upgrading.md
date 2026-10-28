Write `@rule("no-print", reads=Tree, severity="error", summary="...")` as
`@rule(severity="error")` on `def no_print(tree: Tree)`, with the summary as
the docstring's first paragraph. A `Linter` takes `findings(self, record)`
and a `TreeReader` returns the records themselves; tests pass a record to
`run_rule` and `run` where they wrapped it in `subject(...)`.
