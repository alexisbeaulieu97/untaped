# Sweeps and the repo store

`untaped github sweep` answers grep-style questions over repos kept in the
git plugin's repo store (`git.store_dir`): one bare repository per remote,
shared with other plugins, holding full history but fetching file contents
only when a grep reads them. Use `untaped workspace` for checkouts you edit.

Contents: predicates, refs, freshness, the footer and failures, rows, piped
input, cache commands.

## Predicates

- Content predicates run `git grep -I --extended-regexp` whatever the
  user's `grep.patternType`; for the regex syntax see
  [Pitfalls](../SKILL.md#pitfalls). Binary files are skipped.
- `-i`, `-F` and `--word-regexp` apply to every `--grep` and `--not-grep`.
- `--has-file GLOB` and `--lacks-file GLOB` test that a file exists or not.
  `--path SPEC` limits content predicates to a Git pathspec.
- A content predicate first fetches the file contents it reads, once,
  limited by `--path`: a wildcard such as `src/*.py` fetches its directory
  (`src/`), and one with no directory before it, such as `*.py`, the whole
  tree. File predicates read names only and fetch nothing.
- All predicates must hold by default. `--any` ORs the positive ones;
  negative predicates stay ANDed.

Illustrations:

```bash
untaped github sweep --org acme --grep 'requests\.get\(' --path 'src/**' --has-file Jenkinsfile
untaped github sweep --team acme/platform --grep log4j --grep slf4j --any
untaped github sweep --org acme --ref 'release/*' --grep jenkins --show matches
```

## Refs

- Each repo's default branch is scanned unless `--refs
  default|branches|tags|all` or `--ref GLOB` says otherwise.
- Refs are reported by short name (`main`, `v1.2`). When a branch and a tag
  share a name, both are scanned and shown as `heads/NAME` and `tags/NAME`.
- Each fetch prunes refs deleted upstream. Named refs are listed against the
  remote first, so only new or moved ones are fetched, in batches, and an
  interrupted refresh resumes where it stopped; `--refs branches|tags|all`
  fetches with one glob per kind.
- Refs that share a tree are grepped once.

## Freshness

- By default a sweep fetches repos that are uncached, older than
  `github.sweep.max_age_seconds`, or cached with fewer refs than asked for.
- An old copy whose GitHub `pushed_at` and default branch have not changed
  since the last fetch counts as current and is not fetched.
- `--refresh` fetches every repo; `--cached` never fetches.
- Transient Git transport failures (dropped connections, `early EOF`, HTTP
  429/5xx) are retried with a short backoff.

## The footer and failures

The stderr footer reports matched and scanned counts, refreshed and cached
counts, the oldest fetch, and a warning per unscanned repo.

- A repo that cannot be scanned (an explicit name GitHub cannot resolve,
  corrupt metadata in its `untaped-github.json`, a host that refuses to send
  file contents by object id, a local filesystem or Git error) is listed with
  its reason; the rest of the sweep completes.
- A failed refresh falls back to a cached copy that covers the query, counted
  as cached, with `refresh failed for N repos; scanned cached copies` and a
  reason per repo. Without such a copy the repo is unscanned.
- Bad credentials (exit 4) and rate limits (exit 5) stop the whole sweep.
  So does a sweep in which every explicitly requested repo fails to resolve.
- `--strict` exits 3 when any repo went unscanned; `--fail-on-match` exits
  3 when any repo matched. Bad scopes, patterns or pathspecs exit 2.

## Rows

`--show` picks the kind: `repos` (default, `github.sweep_repo`), `files`
(`github.sweep_file`) or `matches` (`github.sweep_match`). `--columns '?'`
lists each kind's fields.

- `hits` counts per predicate on a repo row (matching lines for `grep`, 1 or
  0 for file and `not-grep` predicates), and matching lines per file on a file
  row.
- `matches` are deduped across refs: one row lists every ref (`refs`) that
  has the line.
- `owners` comes from CODEOWNERS; `--no-owners` skips that lookup.

## Piped input

- `sweep --stdin` and `cache sync --stdin` read bare `owner/name` lines or
  `github.repo`, `github.sweep_repo` and `github.corpus_repo` records (by
  `full_name`); other kinds exit 2.
- Records from `repos list` are used as they are, with no
  per-repo API call, and their `pushed_at` enables the unchanged-repo skip.
  Other records and bare names are looked up.
- Git fetches send the token only to the Git host of `github.base_url`; a
  piped `clone_url` on another host is fetched without it. `http.proxy`
  covers fetches from the GitHub host too, and `github.git_protocol: ssh`
  fetches its repos over SSH with the user's own keys. Two plugins serving
  the same host exit 4 until `untaped plugin rank` picks one.
- Sweeps can chain: a `--format pipe` sweep feeds the next sweep's
  `--stdin`, narrowing the set at each step.

## Cache commands

- `cache sync` fetches the repos in scope without a query. Each
  `github.sync_outcome` row says `synced`, `unchanged`, `skipped` or
  `failed` (with `error`); a failure exits 1, or 5 when a fetch timed out.
- `cache status` emits one `github.corpus_repo` row per repo github has
  stored and prints the count, total size and freshness spread. `path` is
  the store repo, which other plugins may share.
- `cache worktree OWNER/NAME` checks out a stored ref, detached, under
  `~/.untaped/plugins/github/worktrees/` and prints its path; `--format raw`
  prints just the path, for `$(…)`. It works only for refs already stored,
  and offline once their files have been fetched.
- `cache delete` takes `OWNER/NAME` arguments or `--all`, not both.
  Repeatable `--org` narrows the selection. There is no `--team`, because
  github does not record team membership.
- `cache delete` and `cache prune` release github's part of each repo: its
  refs, its `untaped-github.json` and its worktrees. A repo nothing else
  uses is `removed`, and `disk_bytes` says what that freed. One that a
  workspace, ansible or a worktree added by hand still uses stays as
  `released`, and `kept` says who kept it
  (`ansible, workspace (2 worktrees)`).
- `cache prune --org ORG` deletes cached repos that left the org or were
  archived.
- Concurrent sweeps are safe: the store locks each repo while it changes it.
