An unknown `--columns` name is now a usage error (exit 2) listing the valid
columns, as it already was for other record rows, on the root `auth`,
`config migrate`, `doctor`, `doctor fix`, `plugin`, `skills` and `setup`
commands, on `github cache`, and on `github sweep`'s repo rows and its
`--show files` and `--show matches` rows; it used to only warn. `doctor`'s,
`doctor fix`'s and `setup plan`'s tables, which show a command to type, and
the table of `github sweep`'s repo rows still only warn.
