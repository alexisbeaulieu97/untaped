**Breaking (users):** `untaped github` is now built on a 12.0 plugin class;
its record kinds keep their names. `github sweep --show files` and `--show
matches` are the commands `github sweep files` and `github sweep matches`,
with the same options. `repos list --fork` and `search repos --fork` take
`include` (the default), `exclude` or `only`: `--no-fork` is `--fork
exclude`, and a bare `--fork` is `--fork only`. The `untaped agents github
scopes` page is gone: the scope options' help says it. `fetched_at` on
`github.corpus_repo` and `github.sweep_repo` rows is a timestamp, shown in a
table as an age; `github.corpus_repo` gains `detail` (what a delete freed,
or what kept a repo) and its `disk_bytes` is the stored copy's size. The
tables show sizes, ages and per-predicate counts, and `cache status`
says "5 min ago" where it said "5 minutes ago".
`sweep -c` with an unknown column is refused (exit 2) before anything is
fetched, as other tables' unknown columns are; it only warned before. A
`search --limit` above GitHub's 1000 results is clamped to 1000 with a
warning. Retired settings `github.token`, `github.token_command`,
`github.base_url` and `github.sweep.max_age_seconds` are no longer
recognised.
