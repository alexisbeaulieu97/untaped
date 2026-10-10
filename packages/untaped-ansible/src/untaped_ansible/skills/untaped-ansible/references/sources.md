# Saved sources and refreshes

A source names the GitHub orgs, teams and repos whose Ansible dependency
files are indexed in a local SQLite cache. `impact` needs one; `deps`, `find`
and `graph` read it when one is selected.

Contents: selecting a source, managing sources and aliases, refreshing,
failed and paused refreshes, the cache.

## Selecting a source

- Commands read a source's cache and touch GitHub only with `--refresh` or
  `source refresh`. With no completed refresh yet, they fail with the exact
  refresh command rather than show partial upstream data.
- `ansible.default_source` applies when neither `--source` nor an inline
  selector is given. An unknown name fails naming the setting.
- Repeated `--source` and inline selectors (`--org`, `--team`, `--repo`,
  `--path`, `--ref-kind`, `--ref-pattern`, `--ref-scan-default`) add up.
  Repeating the same inline selectors reuses their cached scan.
- `--refresh` needs a boundary: `--source`, `--org`, `--team`, `--repo` or
  `ansible.default_source`. `--path` and the ref selectors do not count on
  their own.
- `--live` works before the source's first refresh; `graph --direction both`
  then omits upstream with a warning.
- `graph --cached` states the default explicitly.

## Managing sources and aliases

- `source set NAME` creates or replaces a source. `source patch NAME` edits
  it with `--add-*`, `--remove-*` and `--clear-*`.
- A changed `set` or `patch` drops the source's cached data at once:
  `deps`, `impact`, `find` and `graph` on it fail with "no cached source data
  found" until the next `source refresh NAME`.
- `source-alias set NAME OWNER/REPO` maps a Galaxy or role name in
  requirements files to its repo. Aliases apply at refresh, so run
  `source refresh NAME` after changing one.
- Changes print one outcome row: `ansible.source_outcome` (`action`,
  `name`, `changes`) or `ansible.source_alias_outcome` (`action`, `alias`,
  `repo`). `action` is `created`, `updated`, `unchanged`, `deleted` or
  `planned` (with `--dry-run`).
- `source remove` and `source-alias remove` confirm first; see
  [Safety](../SKILL.md#safety).
- `source remove` also releases the repos only that source used from the
  git plugin's repo store. Its `changes` name each repo: `removed` (with the
  space freed), `released` (another plugin kept it), or `kept` (another
  saved source still selects it); with `--dry-run`, `release`.
- `source status` reports `state` (`fresh`, `stale` or `not_refreshed`) and
  `scanned_at` in UTC.

## Refreshing

- `source refresh` expands orgs, teams and repos with the `github` settings.
  Progress goes to stderr; stdout stays machine-readable.
- Ref probing uses `ansible.source_refresh_backend` (default `auto`).
  `--backend auto|graphql|git` overrides it for one run, on `source refresh`
  or with `--refresh`; `--backend` alone is a usage error.
- The `git` backend replaces only the ref probe transport: private sources
  still need GitHub credentials.
- In `auto`, an exhausted primary GraphQL rate limit falls back to
  `git ls-remote` for the remaining repos, with a warning giving the count.
  That fallback is much slower: one network call per repo.
- Secondary rate limits, authentication errors, request-level forbidden
  errors and unknown GraphQL access failures abort the whole refresh. They
  are not per-repo failures; do not continue on the stale data.
- Tune large refreshes with `ansible.source_refresh_repo_batch_size`
  (default 100) and `ansible.source_refresh_rate_limit_floor` (default 500).

## Failed and paused refreshes

- Per-repo failures do not stop a refresh. Successes are saved and each
  failure prints `error: <repo>: <reason>` (a JSON error line with `item`,
  `category` and `system` under `--format json`).
- The run then exits 1, or 5 when any failure was transient (timeout,
  network error, rate limit), with `refresh completed with N repo failures;
  successes were saved`. Treat that as a partial success.
- If every repo failed, the previous index is kept: `refresh failed for all
  N repos; index left unchanged`, with the same exit codes.
- In `auto`, transient GraphQL failures for a repo retry over
  `git ls-remote` first; only unrecovered repos count as failures.
- `--refresh` on `deps`, `impact`, `find` and `graph` warns about failed
  repos instead, exits 0 and uses possibly stale data for them.
- When the GraphQL budget drops below the rate-limit floor with repos left,
  finished batches are kept, the source is not marked refreshed, and the run
  exits 5 with a resume hint. Rerun the same command: it skips the repos
  that succeeded and retries the rest.

## The cache

- The SQLite index is a cache. An index in an older format is rebuilt empty
  with a warning; refresh every saved source afterwards.
- An index written by a newer untaped is rejected with an error naming the
  file, so it is never destroyed by an older release.
