# GitHub

The GitHub domain is intentionally narrow: today it only inspects the
authenticated user. The `untaped-github` package exists as a stub to
host wider GitHub workflows when we need them — search, repos,
issues, PRs — but those aren't implemented yet.

## Setup

```bash
untaped config set github.token ghp_xxx        # personal access token
untaped github whoami                           # confirm it works
```

The token is stored as a secret: `untaped config list` shows `***`,
not the value. See [`configuration.md`](./configuration.md) for the
full schema.

You can also override the API base URL — for GitHub Enterprise,
typically:

```bash
untaped config set github.base_url https://github.example.com/api/v3
```

GHE URLs include the explicit `/api/v3` suffix because each GHE
instance hosts its API under that path; the public github.com default
(`https://api.github.com`) doesn't because the version sits behind a
versioned subdomain instead.

## Commands

### `whoami`

```bash
untaped github whoami                                  # tabular
untaped github whoami --format json                    # machine-readable
untaped github whoami --format raw --columns login     # bare login on stdout
```

Calls `GET /user` and prints the authenticated user's profile —
`login`, `name`, `email`, plan, etc. Pipe-friendly for shell prompts
and scripts:

```bash
echo "[gh:$(untaped github whoami --format raw --columns login)]"
```

## Roadmap

The package is wired up so adding more commands (search, repo
inspection, PR queries) is a matter of writing the use case + Typer
command. Until then, reach for `gh` directly when you need anything
beyond the authenticated user.

## See also

- [`configuration.md`](./configuration.md) — token storage, profiles,
  TLS.
- [AGENTS.md](../AGENTS.md) — how to extend a domain (recipe: "Add a
  new command to an existing domain").
