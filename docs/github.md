# GitHub

The GitHub domain inspects the authenticated user and searches GitHub
for repositories, code, issues/PRs, and users/orgs. All commands
authenticate with the token from `github.token` and default their
scope to the authenticated user, so the bare commands answer
"what's mine?".

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

### `search`

`untaped github search` exposes one subcommand per GitHub search
endpoint. Every subcommand defaults its scope to `user:@me` when you
pass no `--user`, `--org`, `--repo`, or `--team`, so the bare command
always means "find this across my own stuff".

```bash
untaped github search repos --language python                    # my python repos
untaped github search repos --name client --language Go          # name matches "client"
untaped github search repos --org acme --visibility private      # private acme repos
untaped github search repos --org acme --team backend            # team's repos
untaped github search code "TODO" --language python              # TODOs in my code
untaped github search issues --state open --label bug --kind pr  # my open bug PRs
untaped github search users --kind org --location montreal       # MTL orgs
```

Common flags (`repos`, `code`, `issues`):

| Flag           | Effect                                                                 |
| -------------- | ---------------------------------------------------------------------- |
| `--user`       | `user:<login>` qualifier; pass `@me` to be explicit.                   |
| `--org`        | `org:<name>` qualifier; repeatable.                                    |
| `--repo`       | `repo:owner/name` qualifier; repeatable.                               |
| `--team SLUG`  | Resolves the team's repos into `repo:` qualifiers. Requires `--org`.   |
| `--limit N`    | Stop after N rows. Default `30` (one screen + one search request); pass `1000` for GitHub's hard maximum. |
| `--format`     | `table` (default), `json`, `yaml`, `raw`.                              |
| `--columns`    | Repeatable; dotted paths supported.                                    |

Repository-specific: `--name`, `--language`, `--archived/--no-archived`,
`--fork/--no-fork`, `--visibility public|private`, `--sort stars|forks|updated`.

Code-specific: `--language`, `--filename`, `--path`, `--extension`.

Issue-specific: `--state open|closed`, `--kind issue|pr`, `--author`,
`--assignee`, `--label` (repeatable), `--mentions`.

User-specific: `--kind user|org`, `--location`, `--language`,
`--sort followers|repositories|joined`. (User search ignores scope
flags — GitHub doesn't support them on that endpoint.)

A free-text query goes as the first positional argument and is passed
verbatim to GitHub's `q=` parameter:

```bash
untaped github search code "func init" --language go
untaped github search issues "memory leak" --state open
```

#### Pipe-friendly examples

```bash
# Repo names only, one per line
untaped github search repos --language python --format raw --columns full_name

# Feed the result into another command
untaped github search repos --org acme --format raw --columns full_name \
  | xargs -L1 gh repo view
```

## See also

- [`configuration.md`](./configuration.md) — token storage, profiles,
  TLS.
- [AGENTS.md](../AGENTS.md) — how to extend a domain (recipe: "Add a
  new command to an existing domain").
