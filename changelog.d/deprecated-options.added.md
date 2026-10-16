A plugin can deprecate a flag that has no new spelling: mark the option
`Annotated[bool, Parameter(group=DEPRECATED_OPTIONS), deprecated(removed_in=3,
replacement="--limit 1")]`. Any spelling of it (`--dry-run`, `--no-dry-run`,
`--dry-run=false`, a short flag) warns once per run, `--help` lists it under
Deprecated options only with `--deprecated`, its help being its `Deprecated:`
line, and its surface line carries the date. `untaped lint` reports a marked
option outside that panel or with `show=False`, an unmarked one inside it, or
a mark on a parameter that is not an option (`lint.deprecated-option-panel`),
and checks the option's replacement and nesting as a command's
([deprecation.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/deprecation.md)).
