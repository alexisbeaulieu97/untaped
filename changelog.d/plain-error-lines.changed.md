A plain `error:` line on stderr now counts toward the exit code and, under
`--format json`, carries `category` `failed`, `exit_code` and `details`.
([#600](https://github.com/alexisbeaulieu97/untaped/pull/600))
