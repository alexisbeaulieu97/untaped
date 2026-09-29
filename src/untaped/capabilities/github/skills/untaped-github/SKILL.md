---
name: untaped-github
description: Use the `untaped github` command to query GitHub or GitHub Enterprise (list org and team repositories, search repositories, code, issues and users, and sweep local clones of many repositories with grep-style questions). Use when the user mentions GitHub, GHE, repos, orgs, teams, code search, issue search, which repos use something, or a codebase-wide sweep.
---

# Untaped Github

Use this skill when the user wants an agent to operate the `untaped github` CLI for authenticated GitHub user, repository inventory, search, sweep, and local Git corpus cache workflows.

Details that do not fit here ship next to this file:

| File | Read it when |
|---|---|
| [references/sweep.md](references/sweep.md) | running sweeps: freshness, predicates, per-repo failures, row fields, `cache` commands |
| [references/search.md](references/search.md) | `repos list` patterns; searching: default scope, `--limit` notices, batching and rate limits |

## Setup

- The command is `untaped github`. It ships with the unified `untaped` CLI (no separate install).
- Settings live under `profiles.<name>.github`: `base_url`, `token`, `default_org`, `corpus_path`, and `sweep` freshness/concurrency settings.
- `untaped config set github.default_org ORG` gives `repos list`, `search repos|code|issues`, `sweep`, `cache sync` and `cache prune` an org scope when none of `--org`, `--team`, `--repo`, `--user` or `--stdin` is passed. Any explicit scope replaces it (never adds to it).
- `base_url` defaults to `https://api.github.com`; GitHub Enterprise Server usually uses `https://HOST/api/v3`.
- Git fetches (`sweep`, `cache sync`) send the token only to the Git host of `base_url` (`github.com`, or `HOST` for `https://HOST/api/v3`); a piped `clone_url` on another host is fetched without credentials.
- Set the token with `untaped config set github.token --prompt` or `--stdin`, or point `github.token_command` at a command that prints it (`'["gh", "auth", "token"]'`). `GH_TOKEN`/`GITHUB_TOKEN` are the last fallback. A rejected token (HTTP 401) fails with a hint to run that command and exits 4.
- Set the base URL with `untaped config set github.base_url https://HOST/api/v3`.

## Command Patterns

- `untaped github whoami` verifies the token and returns the current user (a detail view in `table`, one JSON object in `json`).
- `untaped github repos list [PATTERN] [--org ORG]... [--team ORG/SLUG|SLUG]... [--limit N]` lists complete org/team repository inventory from GitHub list APIs with at least one repeatable scope, emitting `github.repo` records (`full_name` plus `repo`, `html_url` plus `url`, `clone_url`, `ssh_url`, `pushed_at`, ...); the table shows only `full_name`, `default_branch`, `private`, `archived`, `fork` and `url`, so pass `-c` for other fields.
- `untaped github sweep --org ORG --grep PATTERN` (or a `--team ORG/SLUG` or `--repo OWNER/NAME` scope) asks a question over the local Git corpus and emits matching `github.sweep_repo` rows by default.
- `untaped github sweep --org ORG --show matches --grep PATTERN` emits deduped `github.sweep_match` rows with `full_name`, `refs`, `path`, `line`, and `text`. `--show files` emits one `github.sweep_file` row per matching file (`full_name`, `path`, `refs`, `hits` = matching lines).
- `untaped github cache sync --org ORG [--refs branches] [--refresh]` (or a `--team ORG/SLUG`, `--repo OWNER/NAME` or `--stdin` scope) warms the corpus without a query (nightly prewarm) and emits one `github.sync_outcome` per repo with `action` `synced`, `unchanged`, `skipped`, or `failed`; a failed row has `detail` and a structured `error`, and the command exits with the most severe failure (1, or 5 when a fetch timed out).
- `untaped github cache status`, `cache delete OWNER/NAME...|--all`, `cache prune --org ORG`, and `cache worktree OWNER/NAME` inspect/delete/prune/materialize the managed corpus. `cache delete` and `cache prune` take `--yes|-y` and `--dry-run`.
- `untaped github search repos` searches repositories and emits `github.repo_hit` records (a different shape from the `github.repo` inventory rows).
- `untaped github search code` searches GitHub's indexed code search and does not support sort, regex, or exhaustive multi-ref sweeps.
- `untaped github search issues` searches issues and pull requests.
- `untaped github search users` searches users and organizations and emits `github.user_hit` records (`whoami` emits `github.user`).
- Search commands support scoped selectors such as `--user`, repeatable `--org`, repeatable `-r/--repo`, and repeatable `--team ORG/SLUG` where applicable. `-r` always means `--repo` across `github` commands.
- `--archived include|exclude|only` on `repos list`, `search repos`, `sweep` and `cache sync` keeps, drops, or isolates archived repos; the default is `exclude` everywhere. There are no `--no-archived` or bare `--archived` spellings. An unquoted `archived:` qualifier in the `search repos` query wins over the flag.
- Prefer `--team ORG/SLUG` for team-only operations. A bare `--team SLUG` is accepted only when exactly one `--org` is present and normalizes to `ORG/SLUG`.
- `repos list` requires `--org` or `--team` scopes (or `github.default_org`); it does not default to the authenticated user's repositories.
- `repos list` treats `--org` and `--team` as additive scopes: `--team acme/backend` is team-only, while `--org acme --team backend` includes the whole org plus that team.
- Use `repos list --no-fork --format raw --columns ssh_url` to produce cloneable inventory URL lines for `untaped workspace repos add WS --stdin`.
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
- Use `--format pipe` to chain a search into another untaped tool: each
  record is tagged (`github.repo_hit`/`github.code`/...), and `--stdin` reads a
  `--format pipe` stream of `github.repo`, `github.repo_hit`, or
  `github.sweep_repo` records back (mapping `full_name`; other kinds exit `2`)
  as well as bare `owner/name` lines — e.g. `untaped github search repos --org
  acme --format pipe | untaped github search code "BaseModel" --stdin`.
- For `untaped workspace repos add WS --stdin`, use raw URL lines:
  `untaped github sweep --org acme --grep old_api --format raw --columns clone_url |
  untaped workspace repos add remediation --stdin`. `workspace repos add --stdin`
  also reads `github.repo`, `github.repo_hit` and `github.sweep_repo` pipe records
  (`untaped github search repos --org acme --format pipe | untaped workspace repos add acme --stdin`).
- `--profile <name>` works in any token position (e.g. `untaped github --profile work whoami`).
- Exit codes: 0 success, 1 failure, 2 usage error, 3 when `sweep --fail-on-match` matched or `--strict` left a repo unscanned, 4 fix the environment (`github.*` settings, a rejected token, missing permission), 5 temporary (network, timeout, rate limit; retry later), 130 interrupted. With `--format json` stderr is JSON Lines with each error's `category`, `system` (`github`, `git`) and `hint`.
