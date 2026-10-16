An unknown `--columns` name is now a usage error (exit 2) listing the valid
columns, as it already was wherever rows have a record model, on the root
`auth`, `config migrate`, `doctor`, `doctor fix`, `skills` and `setup`
commands and on `github cache` and `github sweep`; it used to only warn, as
`doctor`'s, `doctor fix`'s and `setup plan`'s `table` views still do.
