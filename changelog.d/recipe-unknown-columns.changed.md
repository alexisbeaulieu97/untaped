An unknown `--columns` name on `untaped recipe` commands is now a usage error
(exit 2) listing the valid columns; it used to only warn. The tables of
`apply`, `get` and `backups get` still only warn.
