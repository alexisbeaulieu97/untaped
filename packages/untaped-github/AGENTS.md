# AGENTS.md — `untaped-github`

GitHub bounded context. Today the package ships `whoami` plus a
`search` sub-app (`repos`, `code`, `issues`, `users`). This doc
captures the package's contract so future commands (releases, repo
metadata, gists, …) follow the same shape. For workspace-wide rules
and the cross-cutting helpers index, see the
[root `AGENTS.md`](../../AGENTS.md). For user-facing config reference,
see [`docs/configuration.md`](../../docs/configuration.md).

## Auth model

GitHub uses bearer-token auth. The token is a `SecretStr` read through
`get_config_section("github", GithubSettings)` (or
`UNTAPED_GITHUB__TOKEN` via the env-var shorthand). The CLI composition
root reads it once and passes the narrowed `GithubSettings` into
`GithubClient`. Adapters never read `Settings` (the cross-cutting
aggregate) directly — they take the narrowed sub-model — same rule that
applies to every other plugin package.

`GithubClient.__init__` fail-fasts with `ConfigError` if the token is
missing or whitespace-only. There is no anonymous-mode fallback —
unauthenticated GitHub is severely rate-limited and not worth
supporting inline.

## Base URL: GitHub vs GHE

`config.base_url` defaults to `https://api.github.com`. For GitHub
Enterprise Server, point it at `https://<host>/api/v3`. Trailing
slashes are stripped at client construction so URL joins are clean.
No auto-detection — the user configures it explicitly.

## Settings sub-model: package-local `GithubSettings`

`GithubClient.__init__` takes `GithubSettings` from
`untaped_github.settings` directly — no separate `GithubConfig` struct
in between. The two fields (`base_url`, `token`) have no extra adapter
invariants beyond what `GithubSettings` already declares, so a second
mirroring config class would be pure duplication. Symmetric with how
`GithubClient` consumes `HttpSettings` directly for TLS configuration.

Adding a new field is a two-place edit (`GithubSettings` registration
model + the `GithubClient` constructor or call site that needs it). The
settings-schema tests in core pin loading/env-override behaviour.

Contrast with `AwxConfig` (`packages/untaped-awx/src/untaped_awx/infrastructure/config.py`):
that package keeps a local struct because it adds invariants on top
of the schema (`gt=0` on `page_size`, `frozen=True` to lock the
struct after composition-root assembly). The principle is: keep
the package-local config only when the adapter needs to add
validation or invariants beyond the schema sub-model.

The CLI composition root lives in `cli/_client.py::open_client`,
which all top-level commands (`whoami`, `search`) use. Adding a
new top-level command is a one-line `with open_client() as client:`
away.

## HTTP wiring

`GithubClient` wraps `untaped.HttpClient` with three GitHub-specific
headers:

- `Accept: application/vnd.github+json`
- `X-GitHub-Api-Version: 2022-11-28`
- `Authorization: Bearer <token>`

TLS: `verify=resolve_verify(http)` per the workspace-wide rule (root
AGENTS.md Hard Rule 11). Don't invent your own verify resolution.

## Rate limiting

Authenticated GitHub gives 5000 req/hour overall and a separate 30
req/min budget for the `/search/*` endpoints. `whoami` is one call;
`search` paginates 100 rows per page and stops at `--limit` (default
`30`). The 30-default keeps a casual exploratory query to a single
round trip against the 30/min search budget; pass `--limit 1000` to
opt into GitHub's hard search ceiling. GitHub enforces that ceiling
on its side — the CLI accepts larger values, but the paginator stops
once GitHub stops returning a `next` link.
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

The three scoped subcommands (`repos`, `code`, `issues`) accept the
common scope flags `--user`, `--org` (repeatable), `--repo`
(repeatable), and `--team` (requires a single `--org`). `search users`
does not — GitHub's user-search endpoint ignores those qualifiers, so
exposing them on the CLI would mislead. All four subcommands share
`--limit` and the standard `--format/-f` + `--columns/-c` from
`untaped`.

Note: `search code` does not accept `--sort` — GitHub no longer
supports a sort parameter on code search (best-match is the only
order).

### `SearchLimitOption`

`cli/search_commands.py` defines a package-local
`SearchLimitOption = Annotated[int, typer.Option("--limit", min=1, help=...)]`
applied to all four subcommands. The default (`30`) is supplied at the
call site (`limit: SearchLimitOption = 30`) so future tweaks land in
one place. The alias lives here rather than in `untaped`
(root-AGENTS convention) because the help string names GitHub's
1000-result search cap — that's a GitHub-specific contract, not a
workspace-wide one. Future GitHub-only option aliases (e.g. a
`--page-size`) should land beside it for the same reason.

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
raises `ConfigError` (teams are scoped to an org). The use case bounds
iteration at `MAX_TEAM_REPO_QUALIFIERS + 1` via `itertools.islice` so a
5k-repo team doesn't drag every page over the wire just to be
truncated. If the cap is exceeded, we keep the first N and emit a
stderr warning via the injected `warn` callback. Raise the cap before
increasing it past 256 without measuring; the search API also rejects
queries with too many boolean operators.

### Pagination

`paginate_search` and `paginate_list` (in
`infrastructure/pagination.py`) follow GitHub's RFC 5988 `Link` header
(`<url>; rel="next"`) until exhausted or `--limit` is hit. Search
payloads nest results under `items`; list payloads (e.g. team repos)
return a raw JSON array. Two efficiency knobs:

- **First-page `per_page` shrinking**: when `--limit < per_page`, the
  first request asks for only `limit` rows so a `--limit 5` call
  doesn't fetch a 100-row page. Subsequent pages accept the
  server-echoed `per_page` on the `next` URL.
- **Cycle / max-page guard**: the paginator visits at most
  `_MAX_PAGES` (100) URLs and refuses to follow a `next` link that
  matches the current or any previously-visited URL — defensive
  against a malformed Link header.

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
- `infrastructure/`: `GithubClient` and `pagination.py`. Adapters
  satisfy application Protocols structurally — no import from
  `application/`. `GithubClient` exposes one method per endpoint and
  delegates list/search calls to the pagination helpers.
- `cli/`: composition root. The shared `cli/_client.open_client`
  helper reads `settings.github` and returns a context-managed
  `GithubClient`; every top-level command (`whoami`, `search`) uses
  it. The `search` sub-app lives in `cli/search_commands.py` and is
  mounted on the root app via `app.add_typer(...)`.

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
