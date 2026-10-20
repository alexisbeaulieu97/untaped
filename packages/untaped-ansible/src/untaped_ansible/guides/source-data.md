---
description: "Read before trusting a deps, impact, find or graph answer: which data answered it (GitHub or a source's cache), when a source refreshes, and how a stale, partial or paused refresh shows."
---

A *source* is a saved set of GitHub orgs, teams and repos whose dependency
files are indexed in a local cache; the repos themselves live in the git
plugin's repo store. `impact` needs one; `deps`, `find` and `graph` read one
when it is selected.

## Where the answer comes from

| Situation | Downstream (`deps`, `find`, `graph` down) | Upstream (`impact`, `graph` up) |
|---|---|---|
| No source selected | GitHub, read live | none: `impact` fails, `graph` leaves it out with a warning |
| A source selected | the source's cache; a target outside it warns "not cached" and gives no rows | the source's cache |
| `--live` with a source | GitHub, read live | the source's cache |

A source is selected by `--source`, by the inline selectors (`--org`,
`--team`, `--repo`, `--path`, `--ref-kind`, `--ref-pattern`,
`--ref-scan-default`), or by `ansible.default_source` when neither is given (an unknown name there fails
naming the setting).
Repeated `--source` and inline selectors add up, and repeating the same
inline selectors reuses their cached scan. `--refresh`, `--cached` and
`--backend` need a boundary (`--source`, `--org`, `--team`, `--repo` or
`ansible.default_source`); `--path` and the ref selectors do not count on
their own.

## When a source refreshes

A command that reads a source refreshes it first when it was never
refreshed or is older than `ansible.caches.index.max_age_seconds`.
`--refresh` refreshes it now; `--cached` reads only what is cached, never
checks GitHub, and fails with "no cached source data" for a source never
refreshed. `deps --live` never reads or refreshes the source; `graph --live
--direction both` refreshes it for the upstream half.

A refresh that fails over cached data serves that data with one
`untaped.stale` line on stderr: `error` says why, and `retry_at` when the
next automatic check runs (a failure is not retried within five minutes).
With nothing cached, or when the cached data answers nothing, the command
fails with the refresh's error (exit 5 for a transient one).

## Partial and paused refreshes

After a refresh that failed for some repos, reads warn on every run with
an `untaped.stale` line whose `partial` is `true` and whose `error` names
the repos, and exit 0 unless the answer is empty, until `--refresh` or
`source refresh` completes a whole refresh.

After a refresh paused by GitHub's GraphQL budget
(`source refresh` says how it resumes), the source is not marked refreshed:
reads serve the cached data with a warning whose `retry_at` is GitHub's
reset time.

## Backends

`ansible.source_refresh_backend`, or `--backend`, picks how refs are probed.
`auto` uses GraphQL and, when its primary rate limit is exhausted, falls back
to `git ls-remote` for the remaining repos with a warning giving the count:
one network call per repo, so much slower. A transient GraphQL failure for
one repo also retries over `git ls-remote` first. The `git` backend replaces
only the probe: private sources still need GitHub credentials.

## The cache

The index is a cache. One in an older format is rebuilt empty with a
warning, and each source refreshes on its next read. One written by a newer
untaped is refused with an error naming the file, so an older release never
destroys it.
