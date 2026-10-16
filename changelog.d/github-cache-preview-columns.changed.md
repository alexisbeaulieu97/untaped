`untaped github cache delete --dry-run` and `cache prune --dry-run` show
`full_name`, `ref`, `status`, `fetched_at`, `path` and `disk_bytes` instead of
every field; `--columns +name`/`-name` edit those, and JSON is unchanged.
