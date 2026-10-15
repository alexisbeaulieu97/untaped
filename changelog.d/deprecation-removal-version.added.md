A deprecation names the major release that removes it: `deprecated()`,
`deprecated_alias()`, `Cache()` and the new `Renamed("new", removed_in=…)`
for `renamed_keys` take `removed_in=<major>`, and its warning, `--help` line,
`config list` note and `doctor` row say "removed in untaped-acme 3.0" (core's
own: the version alone). Under `--format json` each warning is an
`untaped.deprecation` line with its `id`, `removed_in`, `distribution` and
`replacement`, and `config list` rows carry `removed_in` and `distribution`.
`untaped.testing.assert_surface(..., version=)` fails an undated or overdue
deprecation, and `untaped lint` reports a hand-typed deprecation sentence as
`lint.hand-typed-deprecation`. Each kind is in
[deprecation.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/deprecation.md).
