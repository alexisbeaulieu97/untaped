# AGENTS.md — `untaped-github`

GitHub bounded context. Today only `whoami` ships; this doc captures
the package's contract so future commands (issues, PRs, releases,
repo metadata) follow the same shape. For workspace-wide rules and
the cross-cutting helpers index, see the
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

Authenticated GitHub gives 5000 req/hour. Today `whoami` is one call;
future high-volume features (listing repos, paginating over issues)
should accept that 5000 is the budget, honour the `X-RateLimit-Remaining`
/ `X-RateLimit-Reset` response headers, and back off on `429 Too Many
Requests`.

## Layering

Standard 4-layer DDD per root AGENTS.md "Architecture: 4-Layer DDD":

- `domain/`: `GithubUser` and future entities. Pure pydantic models;
  no HTTP, no Settings.
- `application/`: use cases (`WhoAmI`) + the Protocols they declare
  (`GithubMeService`, etc.). Use cases take a Protocol via constructor
  injection and call only the methods on it.
- `infrastructure/`: `GithubClient`, `GithubConfig`. Adapters satisfy
  application Protocols structurally — no import from `application/`.
- `cli/`: composition root. Reads `Settings.github`, builds
  `GithubConfig`, instantiates `GithubClient`, runs the use case,
  formats output.

## Recipe: add a new command

1. Add an HTTP method to `GithubClient` (e.g. `def list_repos(...)`).
2. Add a domain model in `domain/` if the response shape isn't already
   covered.
3. Add a use case in `application/` (e.g. `ListRepos`). Declare its
   port `Protocol` inline for now; if a third use case lands and shares
   ports, consolidate into `application/ports.py` (matching the
   convention in `untaped-awx` and `untaped-workspace`).
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
