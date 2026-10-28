**Breaking (plugin authors):** `untaped_lint.api` offers the `Findings`
bundle (`--select`, `--ignore`, `--strict`) a lint command takes,
`lint_report`, which runs the engine, and its `Report`; `lint` and the
`IgnoreOption`, `SelectOption` and `StrictOption` spellings are gone.
