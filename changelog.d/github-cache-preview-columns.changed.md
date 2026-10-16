`untaped github cache delete --dry-run` and `cache prune --dry-run` show the
cached repo table's usual columns plus `disk_bytes` (what each repo would
free) instead of every field; `--columns` and the JSON output are unchanged.
