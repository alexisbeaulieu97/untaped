**Breaking (users):** `untaped jira` is now built on a 12.0 plugin class.
`jira issues comment` reads its body only from `--body` or `--body-file`;
`--body -` reads stdin, and a piped body without it exits 2. `jira issues get`
prints an issue's links, when it has some, and with `--comments` its
comments, as tables of their own after the issues', each headed by its
issue's key when there are several. `jira issues transition` prints a list
even for one key, and a key that fails is a row with `action: failed` and its
`error`, so a pipe into `--stdin` carries it too. The guide to jira's writes
is in `jira issues --help`. Some usage messages are worded anew.
