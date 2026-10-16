`untaped_lint.api.lint_config(start)` builds the configuration `lint()`
takes (the profile's `lint.ignore` and the repository lint file found from
`start`), so a plugin's own lint command applies the same ignores as
`untaped lint` without reading lint's settings. Importing `untaped_lint.api`
no longer loads lint's engine until `lint()` runs, and
`untaped_lint.testing.conformance` passes a `TreeReader` provider instead of
failing on its missing rule table.
