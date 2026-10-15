A new `lint` plugin (`untaped[lint]`, experimental): `untaped lint` checks
the plugins a repository's `pyproject.toml` declares against untaped's
conventions and each contract they fill, or any records piped in with
`--stdin`, and reports findings with rule ids such as `lint.error-case`.
`untaped lint rules` lists every rule, `--ignore <rule>[:<glob>]` and the
`lint.ignore` setting leave findings out, and it exits 3 on an error finding.
A plugin adds rules by filling the `Linter` contract from `untaped_lint.api`,
and tests itself with `untaped_lint.testing.assert_clean(NAME)`. A
`# untaped: allow <rule>` marker may name the rule as `lint.<rule>` too.
