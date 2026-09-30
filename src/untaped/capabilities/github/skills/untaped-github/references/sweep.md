# Sweeps and the local corpus

`untaped github sweep` answers grep-style questions over local clones kept in
a managed corpus under `github.corpus_path` (default
`~/.untaped/github-corpus`). Use `untaped workspace` for clones you edit.

Contents: predicates, refs, freshness, the footer and failures, rows, piped
input, cache commands.

## Predicates

- Content predicates run `git grep -I --extended-regexp`: POSIX extended
  regexes whatever the user's `grep.patternType`. `a|b` alternates, `\(`
  is a literal parenthesis, and Perl classes such as `\d` are unsupported
  (use `[0-9]`). Binary files are skipped.
- `-i`, `-F` and `--word-regexp` apply to every `--grep` and `--not-grep`.
- `--has-file GLOB` and `--lacks-file GLOB` test that a file exists or not.
  `--path SPEC` limits content predicates to a Git pathspec.
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
- Wide selections list remote refs first and fetch only new or moved refs,
  in batches, pruning refs deleted upstream. An interrupted refresh resumes
  where it stopped.
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
  corrupt corpus metadata, a local filesystem or Git error) is listed with
  its reason; the rest of the sweep completes.
- A failed refresh falls back to a cached copy that covers the query, counted
  as cached, with `refresh failed for N repos; scanned cached copies` and a
  reason per repo. Without such a copy the repo is unscanned.
- Bad credentials (exit 4) and rate limits (exit 5) stop the whole sweep.
  So does a sweep in which every explicitly requested repo fails to resolve.
- `--strict` exits 3 when any repo went unscanned; `--fail-on-match` exits
  3 when any repo matched. Bad scopes, patterns or pathspecs exit 2.

## Rows

| `--show` | Kind | Fields |
|---|---|---|
| `repos` (default) | `github.sweep_repo` | `repo`, `clone_url`, `refs_matched`, `hits`, `owners`, `fetched_at` |
| `files` | `github.sweep_file` | `repo`, `path`, `refs`, `hits` (matching lines) |
| `matches` | `github.sweep_match` | `repo`, `refs`, `path`, `line`, `text`; deduped across refs |

`owners` comes from CODEOWNERS; `--no-owners` skips that lookup.

## Piped input

- `sweep --stdin` and `cache sync --stdin` read bare `owner/name` lines or
  `github.repo`, `github.repo_hit` and `github.sweep_repo` records; other
  kinds exit 2.
- `github.repo` records from `repos list` are used as they are, with no
  per-repo API call, and their `pushed_at` enables the unchanged-repo skip.
  Other records and bare names are looked up.
- Sweeps can chain: a `--format pipe` sweep feeds the next sweep's
  `--stdin`, narrowing the set at each step.

## Cache commands

- `cache sync` fetches the repos in scope without a query. Each
  `github.sync_outcome` row says `synced`, `unchanged`, `skipped` or
  `failed` (with `error`); a failure exits 1, or 5 when a fetch timed out.
- `cache status` emits one `github.corpus_repo` row per cached repo and
  prints the count, total size and freshness spread.
- `cache worktree OWNER/NAME` checks out a cached ref and prints its path. It
  works offline and only for refs already in the corpus.
- `cache delete` takes `OWNER/NAME` arguments or `--all`, not both.
  Repeatable `--org` narrows the selection. There is no `--team`, because the
  corpus does not record team membership.
- `cache prune --org ORG` deletes cached repos that left the org or were
  archived.
- Concurrent sweeps are safe: each cached repo is locked while it is written.
