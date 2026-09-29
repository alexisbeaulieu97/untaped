# Listing and searching repositories

How `untaped github repos list` matches names, and how `untaped github search repos|code|issues|users` pick their scope, honour `--limit`, and split large scopes into several GitHub search requests.

- In `repos list`, `PATTERN` is a case-insensitive whole-target glob by default; `--regex` switches it to a case-insensitive, unanchored regex substring match. Patterns with `/` match `full_name`, otherwise they match repo `name`.
- Use `--limit` intentionally; GitHub search has stricter rate limits than normal REST reads. When `--limit` cuts results off, stderr says so (`showing 50 of 312 repositories; omit --limit to list all` for `repos list`; `showing the first 30 results; more match, raise --limit to see them` for search). No notice means you have every match, except that search cannot tell at a `--limit` that is a multiple of 100 or 1000 and up (checking would cost an extra request), so use an odd limit such as 150 when completeness matters.
- When no repo/org/user/team/stdin scope is passed to repo/code/issue search, the CLI searches `github.default_org`, or else the authenticated user (`user:@me`) and prints `no user, org or repository in scope; searching user:@me ...` on stderr (also when `--team` resolves to no repos).
- Repeated repo scopes are ORed together; do not rewrite them as separate AND qualifiers.
- `search repos` automatically batches large team-expanded repo scopes around
  GitHub's search validation limits: at most five `AND`/`OR`/`NOT` operators
  and 256 user query-text characters per request, excluding generated
  qualifiers/operators and unquoted supported raw qualifiers. Quoted terms
  count as literal query text and quoted boolean-looking tokens do not reduce
  the repo batch budget. Results are deduped by `full_name`; best-match and
  `help-wanted-issues` stop once `--limit` unique rows are available. Multi-batch
  `help-wanted-issues` emits a warning, while `stars`, `forks`, and
  `updated` query all batches and locally merge-sort before the final limit.
- `search code` and `search issues` batch team-expanded and `--repo`/`--stdin`
  scopes the same way (at most five boolean operators per request, counting
  unquoted `AND`/`OR`/`NOT` in the query). To stay under GitHub's per-minute
  search limits, one invocation sends at most 9 code-search or 25 issue-search
  batch requests (`search repos` is capped at 25 the same way); beyond that it warns that results cover only the first N
  repositories — narrow the scope to search the rest. A rate limit after the
  first batch returns the partial merged results with a warning. Code results
  are deduped by `html_url` and issue results by `id`; `--limit` applies across
  batches, and a sorted multi-batch issue search (`--sort`, or a
  `sort:<field>[-asc|-desc]` qualifier in the raw query) queries every batch and
  merge-sorts locally before the limit; an unsupported `sort:` field warns and
  keeps batch order.
