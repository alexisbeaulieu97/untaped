A plain `error:` line on stderr now counts toward the exit code and, under
`--format json`, carries `category` `failed`, `exit_code` and `details`.
