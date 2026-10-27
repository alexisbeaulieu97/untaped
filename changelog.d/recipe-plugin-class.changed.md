**Breaking (users):** `untaped recipe` is now built on a 12.0 plugin class;
its record kinds keep their names. The library is `plugins/recipe/library/`
under the untaped home unless `recipe.library_dir` is set, so it follows
`UNTAPED_HOME`. `apply --stdin` reads a row of any kind an installed plugin
declares: its target is its absolute `target_path`, else its `path`, and a
`.summary` row is no longer skipped. A declined `apply` prints no
`cancelled` rows. `hooks run` prints a transform's content (or `--diff`) and
a validate's `STATUS: MESSAGE` in its default `text` format; other formats
print the `recipe.hook_run` row, which carries an `error` on a `fail`
verdict. `get` shows a recipe's inputs and steps, and `backups get` a
bundle's files, as tables under the record; list cells such as an input's
values read `a, b`. `backups restore` prints its outcome row above the
success line. The empty-library hint is a diagnostic in every format, and
`test -f json` with no cases prints `[]`. A missing `--vars-file` or
`--args-file`, and a negative `--hook-timeout`, are usage errors (exit 2).
The guides are at `untaped docs recipe guides hooks`, `guides inputs` and
`guides hook-contract`, and the recipe file reference at
`untaped docs recipe inputs recipe_file`.
