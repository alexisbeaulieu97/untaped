# AGENTS.md — `untaped-github`

GitHub bounded context. Today the package ships `whoami` plus a
`search` sub-app (`repos`, `code`, `issues`, `users`). This doc
captures the package's contract so future commands (releases, repo
metadata, gists, …) follow the same shape. For workspace-wide rules
and the cross-cutting helpers index, see the
[root `AGENTS.md`](../../AGENTS.md). For user-facing config reference,
see [`docs/configuration.md`](../../docs/configuration.md).

## Auth model

GitHub uses bearer-token auth. The token is a `SecretStr` read from
`Settings.github.token` (or `UNTAPED_GITHUB__TOKEN` via the env-var
shorthand). The CLI composition root reads it once, builds a
`GithubConfig`, and passes that into `GithubClient`. Adapters never
read `Settings` directly — same rule that applies to every other
domain package.

`GithubClient.__init__` fail-fasts with `ConfigError` if the token is
missing or whitespace-only. There is no anonymous-mode fallback —
unauthenticated GitHub is severely rate-limited and not worth
supporting inline.

## Base URL: GitHub vs GHE

`config.base_url` defaults to `https://api.github.com`. For GitHub
Enterprise Server, point it at `https://<host>/api/v3`. Trailing
slashes are stripped at client construction so URL joins are clean.
No auto-detection — the user configures it explicitly.

## `GithubConfig` (package-local config)

Lives in `infrastructure/config.py`. Mirrors the shape of
`untaped_core.settings.GithubSettings` so the CLI can build one from
settings in a single line, but is declared in this package so adapters
can depend on it without importing `untaped_core`. Adding a new field
means updating both: the `Settings.github` sub-model in core (for YAML
/ env-var loading) and `GithubConfig` here (for adapter consumption).

## HTTP wiring

`GithubClient` wraps `untaped_core.HttpClient` with three GitHub-specific
headers:

- `Accept: application/vnd.github+json`
- `X-GitHub-Api-Version: 2022-11-28`
- `Authorization: Bearer <token>`

TLS: `verify=resolve_verify(http)` per the workspace-wide rule (root
AGENTS.md Hard Rule 11). Don't invent your own verify resolution.

## Rate limiting

Authenticated GitHub gives 5000 req/hour overall and a separate 30
req/min budget for the `/search/*` endpoints. `whoami` is one call;
`search` paginates 100 rows per page and stops at the user-supplied
`--limit` (or GitHub's own 1000-result cap, whichever comes first).
Future high-volume features should honour the `X-RateLimit-Remaining`
/ `X-RateLimit-Reset` response headers and back off on `429 Too Many
Requests`.

## Search

`search` is a Typer sub-app mounted on the root `github` app, with one
subcommand per GitHub search endpoint:

| Subcommand            | Endpoint                  | Key filters                                              |
| --------------------- | ------------------------- | -------------------------------------------------------- |
| `search repos`        | `/search/repositories`    | `--name`, `--language`, `--archived/--no-archived`, `--fork/--no-fork`, `--visibility`, `--sort` |
| `search code`         | `/search/code`            | `--language`, `--filename`, `--path`, `--extension`      |
| `search issues`       | `/search/issues`          | `--state`, `--kind issue|pr`, `--author`, `--assignee`, `--label`, `--mentions` |
| `search users`        | `/search/users`           | `--kind user|org`, `--location`, `--language`            |

All four accept the common scope flags `--user`, `--org` (repeatable),
`--repo` (repeatable), `--team` (requires `--org`), the shared
`--limit` cap, and the standard `--format/-f` + `--columns/-c` from
`untaped_core`.

### Default-scope rule

`SearchRepos`, `SearchCode`, and `SearchIssues` inject `user:@me` into
the query whenever the user passes none of `--user`, `--org`, `--repo`,
or `--team`. This makes the bare command ("what's mine?") the safe
default. `SearchUsers` does not inject anything — GitHub's user-search
endpoint ignores `user:` / `repo:` / `org:` qualifiers, so global
results are the only sensible default.

### Team-to-repo resolution

There is no `team:` qualifier in GitHub search. When `--team` is passed,
the use case calls `GET /orgs/{org}/teams/{slug}/repos` and expands the
result into `repo:owner/name` qualifiers. `--team` without `--org`
raises `ConfigError` (teams are scoped to an org). If the team has more
than `MAX_TEAM_REPO_QUALIFIERS` (200) repos, we truncate and emit a
stderr warning via the injected `warn` callback — well short of
GitHub's 6000-char URL limit, but still wide enough for every team
we've seen in practice. Raise the cap before increasing it past 256
without measuring; the search API also rejects queries with too many
boolean operators.

### Pagination

`paginate_search` and `paginate_list` (in
`infrastructure/pagination.py`) follow GitHub's RFC 5988 `Link` header
(`<url>; rel="next"`) until exhausted or `--limit` is hit. Search
payloads nest results under `items`; list payloads (e.g. team repos)
return a raw JSON array. Each helper handles one shape.

## Layering

Standard 4-layer DDD per root AGENTS.md "Architecture: 4-Layer DDD":

- `domain/`: `GithubUser`, `RepoResult`, `CodeResult`, `IssueResult`,
  `UserResult` plus frozen filter value objects (`RepoSearchFilters`,
  `CodeSearchFilters`, `IssueSearchFilters`, `UserSearchFilters`) in
  `queries.py`. Each filter knows how to render itself into the `q=`
  string GitHub expects — pure functions, no I/O.
- `application/`: use cases (`WhoAmI`, `SearchRepos`, `SearchCode`,
  `SearchIssues`, `SearchUsers`) + the Protocols they consume,
  declared in `application/ports.py` (`GithubMeService`,
  `GithubSearchService`, `GithubTeamService`). Use cases take Protocols
  via constructor injection and call only the methods on them. Scope
  defaulting (`user:@me`) and team-to-repo resolution live in the
  search use cases.
- `infrastructure/`: `GithubClient`, `GithubConfig`,
  `pagination.py`. Adapters satisfy application Protocols structurally
  — no import from `application/`. `GithubClient` exposes one method
  per endpoint and delegates list/search calls to the pagination
  helpers.
- `cli/`: composition root. Reads `Settings.github`, builds
  `GithubConfig`, instantiates `GithubClient`, runs the use case,
  formats output. The `search` sub-app lives in `cli/search_commands.py`
  and is mounted on the root app via `app.add_typer(...)`.

## Recipe: add a new command

1. Add an HTTP method to `GithubClient` (e.g. `def list_repos(...)`).
2. Add a domain model in `domain/` if the response shape isn't already
   covered.
3. Add a use case in `application/` (e.g. `ListRepos`). Add its port
   `Protocol` to `application/ports.py` — alongside `GithubMeService`
   if it shares the contract, or as a sibling Protocol if the new
   command needs a different shape. The use case takes the Protocol via
   constructor injection and calls only its declared methods.
4. Wire the command in `cli/commands.py`. Mark `no_args_is_help=True`
   if it has required args. Pipe-friendly data output via
   `format_output` + `--format` / `--columns`.
5. Test the use case with a stub satisfying the new Protocol — same
   pattern as `tests/unit/test_whoami_use_case.py`.

## See also

- [Root AGENTS.md](../../AGENTS.md) — 4-Layer DDD, Hard Rules,
  cross-cutting helpers index.
- [`docs/configuration.md`](../../docs/configuration.md) — user-facing
  configuration reference.
