# Listing and searching repositories

## Matching names in `repos list`

- `PATTERN` is a case-insensitive glob over the whole name by default.
  `--regex` makes it a case-insensitive, unanchored regex.
- A pattern containing `/` matches `repo` (`owner/name`); otherwise it
  matches the name alone.
- `repos list` never defaults to the user's own repos; it needs a scope or
  `github.default_org`.

## Knowing a result is complete

- When `--limit` cuts results off, stderr says so: `showing 50 of 312
  repositories; omit --limit to list all` for `repos list`, `showing the
  first 30 results; more match, raise --limit to see them` for search.
- No notice means you have every match, with one exception: search cannot
  tell at a `--limit` that is a multiple of 100, or 1000 and up (checking
  would cost an extra request). Use an odd limit such as 150 when
  completeness matters.
- Search `--limit` defaults to 30, and GitHub never returns more than 1000
  results.
- A search with no scope and no `github.default_org` searches `user:@me`
  and says so on stderr; so does a `--team` that resolves to no repos.

## Large scopes

A team or a list of repos becomes repo qualifiers, and GitHub limits how many
fit in one query, so large scopes are split into batches:

- Repeated repo scopes are ORed together; keep them as scopes rather than
  rewriting them as AND qualifiers.
- A batch holds at most five `AND`/`OR`/`NOT` operators. For `search
  repos`, the user's query text is also capped at 256 characters; generated
  qualifiers do not count, and quoted terms count as literal text.
- One invocation sends at most 9 code-search batches, or 25 issue or repo
  batches, to stay under GitHub's per-minute limits. Beyond that it warns
  that results cover only the first N repositories: narrow the scope to
  search the rest.
- A rate limit after the first batch returns the merged partial results with
  a warning.
- Results are deduped across batches: repos by `repo`, code by `url`,
  issues by `id`. `--limit` applies across batches.

## Sorting across batches

- `search repos` sorted by `stars`, `forks` or `updated` queries every batch
  and merge-sorts locally before the limit. Best match stops once `--limit`
  unique rows arrive; `help-wanted-issues` does too and warns, because the
  order is then only per batch.
- A sorted `search issues` (`--sort`, or a `sort:<field>[-asc|-desc]`
  qualifier in the query) also queries every batch and merge-sorts. An
  unsupported `sort:` field warns and keeps batch order.
- An unquoted `archived:` qualifier in a `search repos` query wins over
  `--archived`.
