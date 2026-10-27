With `UNTAPED_HOME` set, move the library from
`~/.untaped/plugins/recipe/library` to `plugins/recipe/library/` under that
home, or set `recipe.library_dir` to where it is. A script that parsed
`hooks run` JSON for a validate reads the row's `status` and `message`, as
before, and a `fail` also has `error`; one that read the table of a
validate reads `STATUS: MESSAGE` lines, or passes `-f json`. Of the guides,
`trust` is now `guides hooks`, `writing-hooks` is `guides hook-contract`,
and `recipe-files` is `untaped docs recipe inputs recipe_file`.
