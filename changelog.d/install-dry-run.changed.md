`skills install` prints one row per skill it installs (`created`, `updated`
when `--force` replaced one, or `failed` with `error`), of the kind `skills
update` and `skills remove` print, instead of an `installed skill: NAME` line.
`skills install --dry-run` and `setup register --dry-run` (with `--remove`
too) print the `planned` row and write nothing.
