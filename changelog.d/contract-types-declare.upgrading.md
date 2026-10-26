In a contract, remove `@bridge` and `@listing` (and their imports), and
replace `@cached(CACHE)` with `@cached(max_age=..., label=...)` taking the
cache's max age and label; delete the `Cache` and the plugin class attribute
that held it, and spell a command's `Refresh(CACHE)` as
`Refresh(Contract.method)`. A method that returned a list without `@listing`
now drops an invalid row instead of failing the whole answer.
