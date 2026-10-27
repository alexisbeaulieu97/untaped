**Breaking (users):** `untaped recipe` is now built on a 12.0 plugin class;
its record kinds keep their names. The library is `plugins/recipe/library/`
under the untaped home unless `recipe.library_dir` is set, so it follows
`UNTAPED_HOME`; a relative `recipe.library_dir` is refused (exit 4) instead
of read against the current folder, and every command creates the library
folder. `apply --stdin` reads a row of any kind an installed plugin
declares, or of a `null` kind: its target is its absolute `target_path`,
else its `path`, and a `.summary` row is no longer skipped. A row of a kind
no installed plugin declares is refused, as is a row its kind's model does
not validate (exit 1), and a row that names no target is reported as `line
N:`. An empty pipe says `no identifiers received on stdin`. A declined
`apply` prints no `cancelled` rows. `hooks run` prints a transform's content
(or `--diff`) and a validate's `STATUS: MESSAGE` in its default `text`
format; other formats print the `recipe.hook_run` row, which carries an
`error` on a `fail` verdict; `--columns` needs one of those formats (exit 2
under `text`). `hooks run --content -` with an empty pipe runs the transform
on empty content instead of exiting 1. `get` shows a recipe's inputs and
steps, and `backups get` a bundle's files, as tables under the record; list
cells such as an input's values read `a, b`. `backups restore` prints its
outcome row above the success line. The empty-library hint is a diagnostic
in every format, and `test -f json` with no cases prints `[]`. A missing
`--vars-file` or `--args-file`, and a negative `--hook-timeout`, are usage
errors (exit 2). The guides are at `untaped docs recipe guides hooks`,
`guides inputs` and `guides hook-contract`, and the recipe file reference at
`untaped docs recipe inputs recipe_file`.
