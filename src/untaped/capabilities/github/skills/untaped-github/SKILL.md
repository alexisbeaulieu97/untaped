---
name: untaped-github
description: Use the untaped-github CLI.
---

# Untaped Github

Use this skill when the user wants an agent to operate the `untaped github` CLI for authenticated GitHub user, repository inventory, search, sweep, and local Git corpus cache workflows.

## Setup

- The command is `untaped github`. It ships with the unified `untaped` CLI (no separate install).
- Settings live under `profiles.<name>.github`: `base_url`, `token`, `corpus_path`, and `sweep` freshness/concurrency settings.
- `base_url` defaults to `https://api.github.com`; GitHub Enterprise Server usually uses `https://HOST/api/v3`.
- Set the token with `untaped config set github.token --prompt` or `--stdin`.
- Set the base URL with `untaped config set github.base_url https://HOST/api/v3`.

## Command Patterns

- `untaped github whoami` verifies the authenticated token and returns the current user — a single entity, so it renders as a vertical detail view under `--format table` and a bare JSON object (`{…}`) under `--format json`.
- `untaped github repos list [PATTERN] [--org ORG]... [--team ORG/SLUG|SLUG]...` lists complete org/team repository inventory from GitHub list APIs with at least one repeatable scope.
- `untaped github sweep --org ORG|--team ORG/SLUG|--repo OWNER/NAME --grep PATTERN` asks a question over the local Git corpus and emits matching `github.sweep_repo` rows by default.
- `untaped github sweep --org ORG --show matches --grep PATTERN` emits deduped `github.sweep_match` rows with `full_name`, `refs`, `path`, `line`, and `text`.
- `untaped github cache status`, `cache clean --repo OWNER/NAME|--all|--prune [--yes|-y]`, and `cache worktree OWNER/NAME` inspect/prune/materialize the managed corpus.
- `untaped github search repos` searches repositories.
- `untaped github search code` searches GitHub's indexed code search and does not support sort, regex, or exhaustive multi-ref sweeps.
- `untaped github search issues` searches issues and pull requests.
- `untaped github search users` searches users and organizations.
- Search commands support scoped selectors such as `--user`, repeatable `--org`, repeatable `--repo`, and repeatable `--team ORG/SLUG` where applicable.
- Prefer `--team ORG/SLUG` for team-only operations. A bare `--team SLUG` is accepted only when exactly one `--org` is present and normalizes to `ORG/SLUG`.
- `repos list` requires explicit `--org` or `--team` scopes; it does not default to the authenticated user's repositories.
- `repos list` treats `--org` and `--team` as additive scopes: `--team acme/backend` is team-only, while `--org acme --team backend` includes the whole org plus that team.
- In `repos list`, `PATTERN` is a case-insensitive whole-target glob by default; `--regex` switches it to a case-insensitive, unanchored regex substring match. Patterns with `/` match `full_name`, otherwise they match repo `name`.
- Use `repos list --no-archived --no-fork --format raw --columns ssh_url` to produce cloneable inventory URL lines for `untaped workspace add --stdin`.
- Use `sweep` instead of GitHub `search code` for repeated team-wide code checks, regexes, path-scoped predicates, negation, and refs beyond the default branch.

## Client API Notes

- `untaped.capabilities.github` provides `GithubClient`, `GithubSettings`, `GithubGraphqlError`, the ref-probe result models, and the repository inventory helpers (`RepositoryInventoryScope`, `RepositoryInventoryItem`, `ResolveRepositoryInventory`, `TeamScope`, `normalize_team_scopes`). Capability code outside `github` must not import these private helpers; the narrow inter-capability API is defined per the Wave 2 import plan.
- `GithubClient.batch_repo_refs(...)` treats exact path-scoped GraphQL `NOT_FOUND`/`FORBIDDEN` errors (`path: ["rX"]`) as per-repo missing results. Nested paths raise `GithubGraphqlError`. Global `/graphql` access failures such as HTTP `401`/`403`/`429` or unscoped `RATE_LIMITED` raise `GithubGraphqlError`, which subclasses `UntapedError` and has a user-ready message. Retryable GraphQL HTTP 5xx and transport failures are retried, adaptively split after retry exhaustion, and surfaced as `BatchRepoRefsResult.failures` per repo instead of aborting successful subchunks; this also applies to all-ref pagination follow-up failures. `BatchRepoRefsResult.rate_limit_cost` sums GraphQL `rateLimit.cost` across every POST in the operation; `rate_limit_remaining` and `rate_limit_reset_at` come from the latest response.
- `GithubClient.batch_default_branch_refs(...)` probes only `defaultBranchRef { name target { oid } }` with no `refs(...)` connection and returns the same `BatchRepoRefsResult` shape with one synthesized `heads` ref per repo when a default branch exists. Both default-branch and all-ref probe modes share the same bounded retry and adaptive split machinery for transient GraphQL failures.
- Known limitation: a `200 OK` response containing per-alias `FORBIDDEN` for every repo is still reported as per-repo missing/inaccessible rather than inferred as a global SSO or token-scope failure.

## Agent Guidance

- Prefer `--format json` for structured search and sweep results.
- Prefer `repos list` over `search repos` when the user needs complete org/team inventory or local glob/regex matching.
- Prefer `sweep --team ORG/SLUG --grep PATTERN` for broad repeated code checks. It expands scopes with REST inventory but does not call GitHub Search APIs for code search.
- Question-first sweep examples:
  - `untaped github sweep --org acme --grep 'requests\.get\(' --path 'src/**' --has-file Jenkinsfile`
  - `untaped github sweep --team acme/platform --grep log4j --grep slf4j --any`
  - `untaped github sweep --org acme --grep old_api --not-grep new_api`
  - `untaped github sweep --org acme --ref 'release/*' --grep jenkins --show matches`
- Use `--fail-on-match` as the CI gate for banned patterns: the sweep still reports rows, then exits `1` if any repo matched. Use `--strict` only when any unscanned repo should also fail the run.
- Sweep freshness footer semantics: default online sweeps refresh uncached, stale, or under-profiled repos according to `github.sweep.max_age_seconds`; `--sync` forces refresh; `--no-sync` scans only cached metadata. The footer reports matched/scanned counts, refreshed/cached counts, oldest fetch, and warnings for unscanned repos. A failed refresh scans a covering cached copy and counts it as cached; without a usable covering copy it becomes unscanned.
- Sweep content predicates use local `git grep -I`, so binary files are skipped. `-i`, `-F`, and `-w` apply to every `--grep` and `--not-grep` in the query. `--any` ORs positive predicates only; negative predicates remain ANDed.
- `github.sweep_repo` rows contain `full_name`, `clone_url`, `refs_matched`, `hits`, `owners`, and `synced_at`. `github.sweep_match` rows contain `full_name`, plural `refs`, `path`, `line`, and `text`.
- The sweep corpus lives under `github.corpus_path` (default `~/.untaped/github-corpus`) and is managed by `untaped github`. Use `cache worktree OWNER/NAME` for a one-off checkout path; it reads cached metadata locally and only materializes refs already present in the corpus. Use `untaped workspace` for human development workspaces.
- `cache status` emits `github.corpus_repo` rows and prints cache count, disk bytes, and freshness spread. `cache clean` requires exactly one of `--repo OWNER/NAME`, `--all`, or `--prune`; destructive paths prompt unless `--yes`/`-y` is passed. `cache clean --prune --org ORG` removes cached repos in the org that departed or are now archived. `--prune --team` is rejected because corpus metadata does not record team membership.
- Sweep and cache commands shell out to `git`; Git must be installed and available on `PATH`.
- Use `--format pipe` to chain a search into another untaped tool: each
  record is tagged (`github.repo`/`github.code`/...), and `--repo-stdin` reads a
  `--format pipe` stream back (mapping `full_name`) as well as bare `owner/name`
  lines — e.g. `untaped github search repos --org acme --format pipe |
  untaped github search code "BaseModel" --repo-stdin`.
- Use `--format pipe` to chain sweep results into another sweep: `untaped github repos list 'svc-*' --org acme --format pipe | untaped github sweep --stdin --grep old_api --format pipe | untaped github sweep --stdin --not-grep new_api`.
- For `untaped workspace add --stdin`, use raw URL lines:
  `untaped github sweep --org acme --grep old_api --format raw --columns clone_url |
  untaped workspace add --stdin --workspace remediation`. `sweep --format pipe`
  emits typed `github.sweep_repo` records, but workspace add does not consume typed pipe records today.
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
