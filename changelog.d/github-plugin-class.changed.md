**Breaking (users):** `untaped github` is now built on a 12.0 plugin class;
its record kinds keep their names. `github sweep --show files` and `--show
matches` are the commands `github sweep files` and `github sweep matches`,
with the same options. `repos list --fork` and `search repos --fork` take
`include` (the default), `exclude` or `only`: `--no-fork` is `--fork
exclude`, and a bare `--fork` is `--fork only`. The scopes guide is the
docstring of `Scope` now, served at `untaped docs github guides scope`.
`fetched_at` on `github.corpus_repo` and `github.sweep_repo` rows is a
timestamp, shown in a table as an age; `github.corpus_repo` gains `detail`
(what a delete freed, or what kept a repo) and its `disk_bytes` is the
stored copy's size. The tables show sizes, ages and per-predicate counts,
and `cache status` says "5 min ago" where it said "5 minutes ago".
`sweep -c` with an unknown column is refused (exit 2) before anything is
fetched, as other tables' unknown columns are; it only warned before. A
`search --limit` above GitHub's 1000 results is clamped to 1000 with a
warning. `github.token`, `github.token_command`, `github.base_url` and
`github.sweep.max_age_seconds` are retired: they are no longer read, and
`untaped doctor` names them for `untaped config migrate` to delete.
For plugin authors: `untaped_github.api.GithubClient(settings, http=)` takes
the run's `HttpFactory` (`self.ctx.http`) rather than `HttpSettings`, and
`GithubSettings` has no `base_url`, `token`, `token_command` or
`token_sources`.
