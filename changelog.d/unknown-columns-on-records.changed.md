An unknown `--columns` name is now a usage error (exit 2) listing the valid
columns, as it already was for other record rows, on the root `auth`,
`config migrate`, `doctor`, `doctor fix`, `plugin`, `skills` and `setup`
commands and on `github cache` and `github sweep`'s repo rows; it used to
only warn. `doctor`'s, `doctor fix`'s and `setup plan`'s tables, which show a
command to type, and `github sweep`'s table still only warn.
