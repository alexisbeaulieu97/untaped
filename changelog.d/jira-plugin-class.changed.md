**Breaking (users):** `untaped jira` is now built on a 12.0 plugin class.
`jira issues comment` reads its body only from `--body` or `--body-file`;
`--body -` reads stdin, and a piped body without it exits 2. `jira issues get
--comments` prints the issue's links and comments as tables of their own
after the issue's, and `jira issues transition` prints a list even for one
key. The guide to jira's writes is in `jira issues --help`. Some usage
messages are worded anew.
