---
description: "Read before naming the orgs, teams or repos a github command covers, piping rows between commands, or reporting that a search or sweep found none or all: how scopes add up and how to tell an answer is complete."
---

An answer covers only the repos it looked at. Name the scope with `--org`,
`--team` or `--repo` rather than relying on `github.default_org`, so the
answer states what it covered, and read the stderr notice or footer that
says what was cut off or left unscanned before reporting "none" or "all".

## Scopes

- `repos list`, `search repos|code|issues`, `sweep` and `cache sync` take
  repeatable `--org` and `--team ORG/SLUG`; all but `repos list` also take
  `-r/--repo OWNER/NAME` and `--stdin`.
- Any scope flag replaces `github.default_org`, never adds to it.
- Without a default org, `repos list`, `sweep` and `cache sync` exit 2;
  they never fall back to your own repos. A search falls back to them
  (`user:@me`) with a stderr notice, and so does a `--team` that resolves
  to no repos.
- Scopes add up: `--org acme --team backend` is the whole org plus that
  team. For one team, pass `--team acme/backend` alone.

## Knowing a result is complete

- When `--limit` cuts results off, stderr says so: `showing 50 of 312; omit
  --limit to list all` for `repos list`, `showing the
  first 30 results; more match, raise --limit to see them` for search.
- No notice means you have every match, with one exception: search can't
  tell at a `--limit` that is a multiple of 100, or 1000 and up, because
  checking would cost an extra request. Use an odd limit such as 150 when
  completeness matters.
- Search rate limits are strict: exit 5 means retry later, never "no
  results".
- A sweep's footer names the repos it couldn't scan
  (`untaped agents github sweep`).

## Large search scopes

A team or a list of repos becomes repo qualifiers, and GitHub limits how many
fit in one query, so a large scope is split into batches:

- Repeated repo scopes are ORed together; keep them as scopes rather than
  rewriting them as AND qualifiers.
- A batch holds at most five `AND`/`OR`/`NOT` operators. For `search
  repos`, your query text is also capped at 256 characters; generated
  qualifiers don't count, and quoted terms count as literal text.
- One run sends at most 9 code-search batches, or 25 issue or repo batches,
  to stay under GitHub's per-minute limits. Beyond that it warns that the
  results cover only the first N repositories: narrow the scope to search
  the rest.
- A rate limit after the first batch returns the merged partial results
  with a warning.
- Rows are deduped across batches (repos by `repo`, code by `url`, issues by
  `id`), and `--limit` applies across batches.

## Piped input

- `--stdin` reads bare `owner/name` lines or `github.repo`,
  `github.sweep_repo` and `github.corpus_repo` records, by `full_name`;
  other kinds exit 2. `untaped workspace create NAME --stdin` reads the
  same records, to check the repos out.
- `sweep` and `cache sync` use records from `repos list` as they are, with
  no API call per repo, and their `pushed_at` lets an unchanged repo skip
  its fetch. Other records and bare names are looked up.
- Git fetches send the token only to the Git host of `github.base_url`; a
  piped `clone_url` on another host is fetched without it.

A chain narrows at each step: each command reads the previous one's
`--format pipe` rows on its stdin.

```bash
untaped github repos list 'svc-*' --org acme --format pipe \
  | untaped github sweep --stdin --grep old_api --format pipe \
  | untaped github sweep --stdin --not-grep new_api
```
