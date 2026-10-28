A lint rule is declared with `@rule(severity=...)` alone: its id is the
function's name in kebab case (`no_print` is `no-print`), its summary the
docstring's first paragraph, and what it reads the record model its first
parameter is annotated with. `Linter.findings` takes that record and
`TreeReader.read` returns records; `Rules` gives each provider its own copy.
Lint's layering check now flags `settings_of` outside `cli/`.
