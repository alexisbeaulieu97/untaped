**Breaking (github):** github no longer keeps a repository inventory file:
the repos it lists for workspace are cached by workspace for 6 hours, so
`github.inventory.path` and `github.inventory.max_age_seconds` are retired.
