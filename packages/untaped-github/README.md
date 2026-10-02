# untaped-github

Install it as part of `untaped`: `uv tool install 'untaped[github]'` or `pip install 'untaped[github]'`.
To add it to an existing install: `uv tool install untaped --with untaped-github`.
(`uv tool install untaped-github` alone does not work: only `untaped` ships the command; see
[Getting started](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/getting-started.md).)

`untaped github` lists repository inventory, searches GitHub, and sweeps
many repositories for content with local `git grep`. Sweeps run over a local
Git corpus, so repeated questions over hundreds of repos avoid GitHub's search
limits.

The packaged skill is the full reference:
[listing and searching](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-github/src/untaped_github/skills/untaped-github/references/search.md)
and [sweeps and the corpus](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-github/src/untaped_github/skills/untaped-github/references/sweep.md).

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
[Tokens](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/configuration.md#tokens).

Git fetches send the token only to the Git host of `github.base_url`, so a
piped `clone_url` on another host is fetched without credentials. `sweep`
and `cache` run `git`, so Git must be on your `PATH`. The other settings
(default org, corpus location, sweep freshness and parallelism) are in the
[configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#github).

## Scopes and filters

`repos list`, `search repos|code|issues`, `sweep` and `cache sync` take the
same scope flags: repeatable `--org` and `--team ORG/SLUG`. All but
`repos list` also take repeatable `-r/--repo OWNER/NAME` and `--stdin`.

- With no scope flag, a command uses `github.default_org`:
  `untaped config set github.default_org acme` makes
  `untaped github sweep --grep old_api` sweep `acme`. Any scope flag replaces
  the default org; it never adds to it.
- Without `github.default_org`, `repos list`, `sweep` and `cache sync` fail
  with exit 2, and `search` searches your own repositories (`user:@me`).
- `--archived include|exclude|only` keeps archived repositories, drops them
  (the default), or keeps only them.
- `--limit N` caps the rows, and a notice on stderr says when it cut results
  off. No notice means the result is complete; for search, the
  [search reference](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-github/src/untaped_github/skills/untaped-github/references/search.md#knowing-a-result-is-complete)
  explains the one exception and how large team scopes are split into
  batches.

## List an org's or team's repos

`repos list` returns the complete inventory of the scopes you name; scopes
add up.

```bash
untaped github repos list --team acme/platform --no-fork
untaped github repos list 'svc-*' --org acme
untaped github repos list 'api|web' --org acme --regex
```

`PATTERN` is a case-insensitive glob, or a regex with `--regex`. The table
shows a few columns; use `-c` or `--format json` for the rest (`clone_url`,
`ssh_url`, ...).

Check the result out into a workspace:

```bash
untaped github repos list --team acme/platform --format pipe \
  | untaped workspace create platform --stdin
```

## Search GitHub

`search` calls GitHub's search API.

```bash
untaped github search repos --org acme --language python
untaped github search code "BaseModel" --org acme --extension py
untaped github search issues --org acme --state open --label bug --kind pr
untaped github search users --kind org --location Montreal
```

`--limit` defaults to 30; GitHub never returns more than 1000 results, and
search has stricter rate limits than other API calls. Code search cannot
sort, use regexes, or look past the default branch: use `sweep` for that.

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

Patterns are POSIX extended regexes (`a|b` alternates, `\(` is a literal
parenthesis, use `[0-9]` rather than `\d`). By default all predicates must
hold, on each repo's default branch; `--any`, `--refs` and `--ref` change
that, and `--show files|matches` prints files or lines instead of repos.

A repeated sweep is fast: a cached copy is fetched again only when it is
older than `github.sweep.max_age_seconds` and GitHub reports a push since.
`--refresh` always fetches, and `--cached` never does.

### Sweep in CI

```bash
untaped github sweep --org acme --grep 'BEGIN RSA PRIVATE KEY' --fail-on-match
```

`--fail-on-match` exits 3 when any repo matches, and `--strict` exits 3 when
any repo could not be scanned. A repo that cannot be fetched or read never
stops the sweep: it is listed as unscanned on stderr, so a sweep with no
matches proves nothing about those repos. A failed refresh scans a cached
copy when one covers the query, and the footer says so.

### Chain sweeps

```bash
untaped github repos list 'svc-*' --org acme --format pipe \
  | untaped github sweep --stdin --grep old_api --format pipe \
  | untaped github sweep --stdin --not-grep new_api
```

## Manage the corpus

```bash
untaped github cache sync --team acme/platform --refs all
untaped github cache worktree acme/api --ref main
untaped github cache prune --org acme --dry-run
untaped github cache delete --all --org acme --dry-run
```

- `cache sync` fetches every repo in scope without a query, so later sweeps
  start warm — for example from a nightly job.
- `cache worktree` checks out a cached ref and prints its path.
- `cache prune --org ORG` removes cached repos that left the org or were
  archived.
- `delete` and `prune` preview and ask first; `--yes` skips the question and
  `--dry-run` only previews.

The corpus is for sweeps. For clones you work in, use
[workspaces](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-workspace/README.md).

## Output

Records name the repository `owner/name` in `repo`; `--stdin` reads such
names or the repo records of another `github` command. See
[Pipes and record kinds](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#github) and
[Exit codes](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/scripting.md#exit-codes): a rejected token exits 4 and a
rate limit exits 5 (retry later).

## See also

- [Configuration reference](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/reference/config.md#github)
- [Workspaces](https://github.com/alexisbeaulieu97/untaped/blob/main/packages/untaped-workspace/README.md)
