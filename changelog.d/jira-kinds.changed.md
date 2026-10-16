`jira issues search` and `jira issues assigned` rows now carry the detail
fields of `jira.issue` (`reporter`, `labels`, `created_at`, `resolution`,
`description`, `links`, `comments`) as `null`, the shape `jira issues get`
writes, instead of leaving them out; `--columns` on them now accepts those
names.
