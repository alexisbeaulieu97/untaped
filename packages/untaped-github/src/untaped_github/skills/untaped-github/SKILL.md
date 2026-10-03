---
name: untaped-github
description: Queries GitHub or GitHub Enterprise through the `untaped github` command (org and team repository lists, repository, code, issue and user search, and grep-style sweeps over local clones of many repos). Use when the user asks which repos contain some code, file or pattern, searches code or issues across an org or team, or wants a codebase-wide sweep.
---

# untaped github

An answer covers only the repos it actually looked at: name the scope, then
read the stderr notice or footer that says what was cut off or left
unscanned before reporting "none" or "all".

## Setup

- Settings live under `profiles.<name>.github`: `base_url`, `token` or
  `token_command`, `default_org`, `corpus_path`, `sweep` freshness and
  concurrency, and `inventory` (the cached repo list the workspace picker
  searches).
- `base_url` defaults to `https://api.github.com`; GitHub Enterprise Server
  usually needs `untaped config set github.base_url https://HOST/api/v3`.
- The user stores the token by running `untaped auth set github` in their own
  terminal (it prompts and keeps the token out of `config.yml`), or points
  `github.token_command` at a command that prints it, such as
  `'["gh", "auth", "token"]'`. `GH_TOKEN`/`GITHUB_TOKEN` are the last
  fallback. Never ask for or print a token, never read
  `~/.untaped/config.yml`, and never pass `--show-secrets`; `untaped auth
  status` says where tokens come from.
- `untaped github whoami` checks the token. A rejected token exits 4.
- `sweep` and `cache` run `git`, which must be on `PATH`.

## Commands

| Task | Command | Use when |
|---|---|---|
| Complete inventory | `untaped github repos list 'svc-*' --org acme` | you need every repo of an org or team, or local glob/regex matching on names |
| Search repositories | `untaped github search repos --org acme --language python` | ranking or GitHub qualifiers matter more than completeness |
| Search indexed code | `untaped github search code "BaseModel" --org acme` | a quick look on default branches; no regex, no sort |
| Search issues and PRs | `untaped github search issues --org acme --state open --kind pr` | finding issues or pull requests |
| Sweep clones | `untaped github sweep --team acme/platform --grep old_api` | regexes, negation, path or file predicates, refs beyond the default branch, or repeated checks over many repos |
| Warm the corpus | `untaped github cache sync --org acme` | before a batch of sweeps, or on a schedule; `cache status` shows size and fetch age |
| Check out one cached ref | `untaped github cache worktree acme/api` | reading a repo's files once; use `untaped workspace` for checkouts you edit |
| Free disk | `untaped github cache delete acme/api --dry-run`, `untaped github cache prune --org acme --dry-run` | see the protocol below |

## Scopes

- `repos list`, `search`, `sweep` and `cache sync` take repeatable `--org`
  and `--team ORG/SLUG`. All but `repos list` also take `-r/--repo
  OWNER/NAME` and `--stdin`.
- With no scope flag, commands use `github.default_org`. Any scope flag
  replaces it, never adds to it.
- Without a default org, `repos list`, `sweep` and `cache sync` exit 2, and
  `search` falls back to the user's own repos (`user:@me`) with a stderr
  notice.
- Scopes add up: `--org acme --team backend` is the whole org plus that
  team. For one team, pass `--team acme/backend` alone.

## Workflows

### Scope a question and feed the result onward

1. Pick the tool: `repos list` for inventory, `search` for a quick indexed
   answer, `sweep` for anything exact or repeated.
2. Name the scope explicitly (`--org`, `--team` or `--repo`) rather than
   relying on the default org, so the answer states what it covered.
   Archived repos are left out unless you pass `--archived include` (or
   `--archived only`).
3. Run it with `--format json`. For `repos list` and `search`, check stderr
   for a `showing N of M` or `raise --limit` notice; without one you have
   every match.
4. For `sweep`, read the footer: matched and scanned counts, and any
   unscanned or `refresh failed` repos with their reasons. Report those
   repos as unknown, not as non-matching.
5. Feed the rows onward with `--format pipe`. `--stdin` reads `github.repo`,
   `github.repo_hit` and `github.sweep_repo` records, or bare `owner/name`
   lines:

```bash
untaped github repos list 'svc-*' --org acme --format pipe \
  | untaped github sweep --stdin --grep old_api --format pipe \
  | untaped github sweep --stdin --not-grep new_api
```

To check the matches out into a workspace, pipe them to
`untaped workspace create NAME --stdin`.

### Gate CI on a banned pattern

Run `sweep --fail-on-match`, adding `--strict` when an unscanned repo
should also fail the run; both exit 3.

## Safety

`untaped github` never changes GitHub. Its only writes, `cache delete` and
`cache prune`, remove local clones.

1. Preview: `untaped github cache delete acme/api --dry-run`, or
   `untaped github cache prune --org acme --dry-run` for repos that left the
   org or were archived.
2. Show the user the listed repos. `cache delete --all` selects the whole
   corpus; narrow it with `--org`.
3. After approval, rerun without `--dry-run` and with `--yes`.

A named repo that is not cached fails with exit 1 before anything is
deleted. A deleted repo is fetched again by the next sweep that covers it.

Exit codes: 0 success, 1 failure, 2 usage error, 3 `--fail-on-match`
matched or `--strict` left a repo unscanned, 4 fix the environment
(settings, token, permission), 5 retry later (search rate limits are
strict; exit 5 never means "no results"), 130 interrupted.

## Pitfalls

- Sweep patterns are POSIX extended regexes: `a|b` alternates, `\(` is a
  literal parenthesis, and `\d` does not work (use `[0-9]`).
- `--any` ORs the positive predicates only; `--not-grep` and `--lacks-file`
  still apply.
- A sweep scans each repo's default branch unless `--refs` or `--ref` says
  otherwise.
- `search repos` rows (`github.repo_hit`) lack `clone_url` and `pushed_at`.
  Feed a sweep from `repos list` instead: its rows let the sweep skip the
  per-repo lookup and the fetch of an unchanged repo.

## References

| File | Read it when |
|---|---|
| [references/sweep.md](references/sweep.md) | a sweep's footer, freshness, failures, predicates, refs or row fields need explaining, or you manage the corpus |
| [references/search.md](references/search.md) | matching repo names in `repos list`, or a search over a large team or repo scope may be incomplete |
