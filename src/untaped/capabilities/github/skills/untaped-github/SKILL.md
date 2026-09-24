---
name: untaped-github
description: Use the built-in `untaped github` capability for GitHub workflows.
---

# Untaped Github

Use this skill when the user wants an agent to operate the `untaped github` CLI for authenticated GitHub user, repository inventory, search, sweep, and local Git corpus cache workflows.

## Setup

- The command is `untaped github`. It ships with the unified `untaped` CLI (no separate install).
- Settings live under `profiles.<name>.github`: `base_url`, `token`, `corpus_path`, and `sweep` freshness/concurrency settings.
- `base_url` defaults to `https://api.github.com`; GitHub Enterprise Server usually uses `https://HOST/api/v3`.
- Set the token with `untaped config set github.token --prompt` or `--stdin`. A rejected token (HTTP 401) fails with a hint to run that command.
- Set the base URL with `untaped config set github.base_url https://HOST/api/v3`.

## Command Patterns

- `untaped github whoami` verifies the authenticated token and returns the current user — a single entity, so it renders as a vertical detail view under `--format table` and a bare JSON object (`{…}`) under `--format json`.
- `untaped github repos list [PATTERN] [--org ORG]... [--team ORG/SLUG|SLUG]... [--limit N]` lists complete org/team repository inventory from GitHub list APIs with at least one repeatable scope, emitting `github.repo` records (`full_name` plus `repo`, `html_url` plus `url`).
- `untaped github sweep --org ORG|--team ORG/SLUG|--repo OWNER/NAME --grep PATTERN` asks a question over the local Git corpus and emits matching `github.sweep_repo` rows by default.
- `untaped github sweep --org ORG --show matches --grep PATTERN` emits deduped `github.sweep_match` rows with `full_name`, `refs`, `path`, `line`, and `text`. `--show files` emits one `github.sweep_file` row per matching file (`full_name`, `path`, `refs`, `hits` = matching lines).
- `untaped github cache sync --org ORG|--team ORG/SLUG|--repo OWNER/NAME|--stdin [--refs ...] [--refresh]` warms the corpus without a query (nightly prewarm) and emits one `github.sync_outcome` per repo with `action` `synced`, `unchanged`, `skipped`, or `failed` (exit `1` on any failure).
- `untaped github cache status`, `cache delete OWNER/NAME...|--all`, `cache prune --org ORG`, and `cache worktree OWNER/NAME` inspect/delete/prune/materialize the managed corpus. `cache delete` and `cache prune` take `--yes|-y` and `--dry-run`.
- `untaped github search repos` searches repositories and emits `github.repo_hit` records (a different shape from the `github.repo` inventory rows).
- `untaped github search code` searches GitHub's indexed code search and does not support sort, regex, or exhaustive multi-ref sweeps.
- `untaped github search issues` searches issues and pull requests.
- `untaped github search users` searches users and organizations and emits `github.user_hit` records (`whoami` emits `github.user`).
- Search commands support scoped selectors such as `--user`, repeatable `--org`, repeatable `--repo`, and repeatable `--team ORG/SLUG` where applicable.
- Prefer `--team ORG/SLUG` for team-only operations. A bare `--team SLUG` is accepted only when exactly one `--org` is present and normalizes to `ORG/SLUG`.
- `repos list` requires explicit `--org` or `--team` scopes; it does not default to the authenticated user's repositories.
- `repos list` treats `--org` and `--team` as additive scopes: `--team acme/backend` is team-only, while `--org acme --team backend` includes the whole org plus that team.
- In `repos list`, `PATTERN` is a case-insensitive whole-target glob by default; `--regex` switches it to a case-insensitive, unanchored regex substring match. Patterns with `/` match `full_name`, otherwise they match repo `name`.
- Use `repos list --no-archived --no-fork --format raw --columns ssh_url` to produce cloneable inventory URL lines for `untaped workspace add --stdin`.
- Use `sweep` instead of GitHub `search code` for repeated team-wide code checks, regexes, path-scoped predicates, negation, and refs beyond the default branch.

## Agent Guidance

- Prefer `--format json` for structured search and sweep results.
- Prefer `repos list` over `search repos` when the user needs complete org/team inventory or local glob/regex matching.
- Prefer `sweep --team ORG/SLUG --grep PATTERN` for broad repeated code checks. It expands scopes from the org/team inventory and searches a local clone of each repo, not GitHub code search.
- Question-first sweep examples:
  - `untaped github sweep --org acme --grep 'requests\.get\(' --path 'src/**' --has-file Jenkinsfile`
  - `untaped github sweep --team acme/platform --grep log4j --grep slf4j --any`
  - `untaped github sweep --org acme --grep old_api --not-grep new_api`
  - `untaped github sweep --org acme --ref 'release/*' --grep jenkins --show matches`
- Use `--fail-on-match` as the CI gate for banned patterns: the sweep still reports rows, then exits `3` if any repo matched. Use `--strict` only when any unscanned repo should also fail the run (also exit `3`). Usage errors (bad scope, pattern, or pathspec) exit `2`.
- Per-repo problems never abort a sweep: an explicit `--repo`/`--stdin` name that GitHub cannot resolve (404, no access), corrupt corpus metadata, or a local filesystem/Git error becomes an unscanned repo with its reason in the footer, and the rest of the sweep completes (`--strict` still exits `3`). Bad credentials (401) and rate limits (429, rate-limited 403) abort the sweep instead, and a sweep whose explicitly requested repos all fail to resolve exits non-zero.
- Sweep freshness footer semantics: default online sweeps refresh uncached, stale, or under-profiled repos according to `github.sweep.max_age_seconds`, except that a stale copy whose GitHub `pushed_at` (and default branch) is unchanged since the last fetch is marked current without any Git network call (counted as cached); `--refresh` forces refresh; `--cached` scans only cached metadata. The footer reports matched/scanned counts, refreshed/cached counts, oldest fetch, and warnings for unscanned repos. A failed refresh scans a covering cached copy and counts it as cached, but the footer warns `refresh failed for N repos; scanned cached copies` and lists each stale repo with its failure reason; without a usable covering copy it becomes unscanned.
- Sweep refreshes retry transient Git transport failures (dropped TLS/TCP streams, `early EOF`, HTTP 429/5xx) with short backoff. Wide ref selections (`--refs branches|tags|all`, `--ref GLOB`) list remote refs first, fetch only new or moved refs in bounded batches, and prune refs deleted upstream, so an interrupted refresh resumes from the refs already fetched on the next run.
- Sweep content predicates use local `git grep -I --extended-regexp`: patterns are POSIX extended regexes regardless of the user's `grep.patternType` (`a|b` alternates, `\(` matches a literal parenthesis, Perl classes such as `\d` are unsupported — use `[0-9]`). Binary files are skipped. `-i`, `-F`, and `--word-regexp` apply to every `--grep` and `--not-grep` in the query. `--any` ORs positive predicates only; negative predicates remain ANDed.
- `github.sweep_repo` rows contain `full_name`, `clone_url`, `refs_matched`, `hits`, `owners`, and `synced_at`. `github.sweep_match` rows contain `full_name`, plural `refs`, `path`, `line`, and `text`. Refs are reported by short name (`main`, `v1.2`); when a branch and a tag share a name, both are scanned and shown as `heads/NAME` and `tags/NAME`.
- The sweep corpus lives under `github.corpus_path` (default `~/.untaped/github-corpus`) and is managed by `untaped github`. Use `cache worktree OWNER/NAME` for a one-off checkout path; it reads cached metadata locally and only materializes refs already present in the corpus. Use `untaped workspace` for human development workspaces.
- `cache status` emits `github.corpus_repo` rows (raw `disk_bytes`/`fetched_at`; the table shows a readable size and fetch age) and prints cache count, total size, and freshness spread. `cache delete` takes `OWNER/NAME` arguments or `--all` (not both); it prompts unless `--yes`/`-y` is passed, and `--dry-run` lists the selection without deleting. `cache prune --org ORG` removes cached repos in the org that departed or are now archived. With `cache delete`, `--org` (repeatable, case-insensitive) narrows the selection to repos owned by those orgs. There is no `--team` option because corpus metadata does not record team membership.
- Sweep and cache commands shell out to `git`; Git must be installed and available on `PATH`.
- Use `--format pipe` to chain a search into another untaped tool: each
  record is tagged (`github.repo_hit`/`github.code`/...), and `--stdin` reads a
  `--format pipe` stream of `github.repo`, `github.repo_hit`, or
  `github.sweep_repo` records back (mapping `full_name`; other kinds exit `2`)
  as well as bare `owner/name` lines — e.g. `untaped github search repos --org
  acme --format pipe | untaped github search code "BaseModel" --stdin`.
- `sweep --stdin` and `cache sync --stdin` use piped `github.repo` records (from `repos list`) as-is, with no per-repo API call; other records and bare names are looked up. Refs sharing a tree are grepped once, all-predicate queries stop evaluating a ref at its first failed predicate, and concurrent sweeps are safe (each cached repo is locked while it is written).
- Use `--format pipe` to chain sweep results into another sweep: `untaped github repos list 'svc-*' --org acme --format pipe | untaped github sweep --stdin --grep old_api --format pipe | untaped github sweep --stdin --not-grep new_api`.
- For `untaped workspace add --stdin`, use raw URL lines:
  `untaped github sweep --org acme --grep old_api --format raw --columns clone_url |
  untaped workspace add --stdin --workspace remediation`. `workspace add --stdin`
  also reads `github.repo`, `github.repo_hit` and `github.sweep_repo` pipe records
  (`untaped github search repos --org acme --format pipe | untaped workspace add --stdin`).
- `--profile <name>` works in any token position (e.g. `untaped github --profile work whoami`).
- Use `--limit` intentionally; GitHub search has stricter rate limits than normal REST reads.
- When no repo/org/user/team scope is passed to repo/code/issue search, the CLI defaults to the authenticated user.
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
  batch requests; beyond that it warns that results cover only the first N
  repositories — narrow the scope to search the rest. A rate limit after the
  first batch returns the partial merged results with a warning. Code results
  are deduped by `html_url` and issue results by `id`; `--limit` applies across
  batches, and a sorted multi-batch issue search (`--sort`, or a
  `sort:<field>[-asc|-desc]` qualifier in the raw query) queries every batch and
  merge-sorts locally before the limit; an unsupported `sort:` field warns and
  keeps batch order.
