# GitHub

`untaped github` lists repository inventory, searches GitHub, and sweeps
many repositories for content with local `git grep`. Sweeps run over a local
Git corpus, so repeated questions over hundreds of repos avoid GitHub's search
limits.

## Set up

```bash
untaped config set github.token --prompt
untaped github whoami
```

For GitHub Enterprise Server, point the API at your host first:

```bash
untaped config set github.base_url https://github.example.com/api/v3
```

To keep the token out of `config.yml`, reuse the GitHub CLI's login instead
(`untaped config set github.token_command '["gh", "auth", "token"]'`), or
export `GH_TOKEN` or `GITHUB_TOKEN`. `github.token` wins over
`github.token_command`, which wins over the variables; see
[Tokens](../configuration.md#tokens).

A rejected token (HTTP 401) fails with a hint to run `config set github.token`.
`sweep` and `cache` run `git`, so Git must be on your `PATH`.

| Setting | Default | Purpose |
|---|---|---|
| `github.base_url` | `https://api.github.com` | API URL. |
| `github.token` | unset | Token for the API and for Git fetches. |
| `github.token_command` | unset | Command (argv list) that prints the token when `github.token` is unset. |
| `github.corpus_path` | `~/.untaped/github-corpus` | Where `sweep` keeps its Git copies. |
| `github.sweep.max_age_seconds` | `3600` | `sweep` refreshes cached copies older than this. |
| `github.sweep.sync_concurrency` | `12` | Default `sweep --parallel`. |

## List an org's or team's repos

`repos list` returns the complete inventory of the scopes you name. It needs
at least one `--org` or `--team`; scopes add up.

```bash
untaped github repos list --org acme
untaped github repos list --team acme/platform --no-archived --no-fork
untaped github repos list 'svc-*' --org acme
untaped github repos list 'api|web' --org acme --regex
```

- `PATTERN` is a case-insensitive glob. With `--regex` it is an unanchored,
  case-insensitive regex. A pattern with `/` matches `full_name`
  (`acme/svc-*`); otherwise it matches the repo name.
- `--team SLUG` without the org works when you pass exactly one `--org`;
  `--org acme --team backend` means all of `acme` plus that team.

Clone the result into a workspace:

```bash
untaped github repos list --team acme/platform --no-archived --format pipe \
  | untaped workspace add --stdin --workspace platform --sync
```

## Search GitHub

`search` calls GitHub's search API. With no `--user`, `--org`, `--team` or
`--repo`, it searches your own repositories (`@me`).

```bash
untaped github search repos --org acme --language python
untaped github search code "BaseModel" --org acme --extension py
untaped github search issues --org acme --state open --label bug --kind pr
untaped github search users --kind org --location Montreal
```

- `--limit` defaults to 30. GitHub never returns more than 1000 results, and
  search has stricter rate limits than other API calls.
- A long team or `--repo` scope is split into several requests and the
  results are merged. One command sends at most 9 code-search or 25
  issue-search requests; past that it warns that results cover only the first
  repositories.
- Code search cannot sort, use regexes, or look past the default branch. Use
  `sweep` for that.

Feed repos from one search into another:

```bash
untaped github search repos --org acme --format pipe \
  | untaped github search code "BaseModel" --stdin
```

## Sweep repositories for content

`sweep` answers a question such as "which repos still call the old API?"
across every repo in a scope. It fetches each repo into the local corpus, then
runs `git grep` on the refs you choose.

```bash
untaped github sweep --org acme --grep 'requests\.get\(' --path 'src/**'
untaped github sweep --team acme/platform --grep log4j --grep slf4j --any
untaped github sweep --org acme --grep old_api --not-grep new_api
untaped github sweep --org acme --has-file Jenkinsfile --lacks-file renovate.json
untaped github sweep --org acme --ref 'release/*' --grep jenkins --show matches
```

| Flag | Meaning |
|---|---|
| `--grep RE`, `--not-grep RE` | Content must (not) match. POSIX extended regex: `a\|b` alternates, `\(` is a literal parenthesis, use `[0-9]` not `\d`. Repeatable. |
| `--has-file GLOB`, `--lacks-file GLOB` | A file must (not) exist. |
| `--path SPEC` | Limit content predicates to a Git pathspec. |
| `--any` | A repo matches when any positive predicate holds. Without it, all must hold. Negative predicates are always ANDed. |
| `-i`, `-F`, `--word-regexp` | Case-insensitive, literal strings, whole words. They apply to every `--grep` and `--not-grep`. |
| `--refs default\|branches\|tags\|all`, `--ref GLOB` | Which refs to scan. Default: each repo's default branch. |
| `--refresh`, `--cached` | Fetch every repo, or scan only what is cached. Default: fetch copies older than `github.sweep.max_age_seconds`. |
| `--show repos\|matches` | One row per repo (`github.sweep_repo`), or one per matching line (`github.sweep_match`). |
| `--no-owners` | Skip the CODEOWNERS column. |
| `--depth N` | Git fetch depth; `0` is full history. |
| `-j N` | Parallel Git workers (at most 32). |

Binary files are skipped. A branch and a tag with the same name are both
scanned and shown as `heads/NAME` and `tags/NAME`.

### Sweep in CI

```bash
untaped github sweep --org acme --grep 'BEGIN RSA PRIVATE KEY' --fail-on-match
```

- `--fail-on-match` exits 3 when any repo matches.
- `--strict` exits 3 when any repo could not be scanned.
- A repo that cannot be fetched or read never stops the sweep: it is listed as
  unscanned in the footer on stderr. A bad token (401) or a rate limit does
  stop it.

When a refresh fails but an older copy is cached, the sweep scans that copy
and warns `refresh failed for N repos; scanned cached copies`.

### Chain sweeps

```bash
untaped github repos list 'svc-*' --org acme --format pipe \
  | untaped github sweep --stdin --grep old_api --format pipe \
  | untaped github sweep --stdin --not-grep new_api
```

## Manage the corpus

```bash
untaped github cache status
untaped github cache worktree acme/api --ref main
untaped github cache delete acme/old-service --dry-run
untaped github cache delete --all --org acme --yes
untaped github cache prune --org acme
```

- `cache status` lists cached repos with disk use and age.
- `cache worktree` checks out a cached ref and prints its path. It only uses
  refs already in the corpus.
- `cache delete` removes the repos you name, or `--all` (narrowed by `--org`).
- `cache prune --org ORG` removes cached repos that left the org or were
  archived.
- `delete` and `prune` preview and ask first; `--yes` skips the question and
  `--dry-run` only previews.

The corpus is for sweeps. For clones you work in, use
[workspaces](../workspace/usage.md).

## Output

| Command | Record kind |
|---|---|
| `whoami` | `github.user` |
| `repos list` | `github.repo` |
| `search repos` / `code` / `issues` / `users` | `github.repo_hit` / `github.code` / `github.issue` / `github.user_hit` |
| `sweep` | `github.sweep_repo`, or `github.sweep_match` with `--show matches` |
| `cache status` / `delete` / `prune` | `github.corpus_repo` |
| `cache worktree` | `github.worktree` |

`--stdin` on `search repos`, `search code`, `search issues` and `sweep` reads
`owner/name` lines or `github.repo`, `github.repo_hit` and `github.sweep_repo`
records.

## See also

- [Pipes and record kinds](../reference/pipes.md)
- [Exit codes](../reference/exit-codes.md)
- [Configuration reference](../reference/config.md#github)
- [Workspaces](../workspace/usage.md)
