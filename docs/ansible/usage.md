# Ansible dependency graphs

`untaped ansible graph` shows how Ansible roles and projects depend on each
other through their requirements files:

- **downstream**: what a role or repo depends on;
- **upstream**: which repos depend on it, which is the impact of changing it.

Downstream works from live GitHub reads. Upstream needs a *source*: a saved
set of orgs, teams or repos whose dependency files `untaped` scans and caches.

## Set up

`untaped ansible` reads GitHub through the `github` settings, so set a GitHub
token first (see [GitHub](../github/usage.md#set-up)):

```bash
untaped config set github.token --prompt
```

Private repos also need Git access over HTTPS with that token, or over SSH
when `ansible.git_clone_protocol` is `ssh`.

## Show what a role depends on

```bash
untaped ansible graph acme/base-role --downstream
untaped ansible graph acme/base-role --downstream --ref v2.1.0 --depth unlimited
untaped ansible graph ./roles/web --target-repo acme/web --downstream
```

`TARGET` is `owner/repo`, a GitHub URL, an alias, or a local path. A local
path resolves its repo from the Git remote only at the top of a checkout; for
a subdirectory pass `--target-repo OWNER/NAME`.

Scanned files are, in each repo: `roles/requirements.yml`, `requirements.yml`,
`meta/requirements.yml` and `meta/main.yml` (`.yaml` too). Change the list
with `ansible.dependency_paths`. Collections in requirements files are not
followed; the command warns once and lists them.

## Find what depends on a role

Save a source, refresh it once, then graph upstream:

```bash
untaped ansible source set platform --org acme --team acme/platform
untaped ansible source refresh platform
untaped ansible graph acme/base-role --source platform --upstream
```

Graphs read the cached source data by default and never touch GitHub. Pass
`--refresh` (or run `source refresh`) when you want current data:

```bash
untaped ansible graph acme/base-role --source platform --upstream --refresh
```

For a one-off question, use inline selectors instead of a saved source. The
scan is cached, so repeating the same command reuses it:

```bash
untaped ansible graph acme/base-role --org acme --upstream --refresh
```

| Flag | Meaning |
|---|---|
| `--upstream`, `--downstream`, `--both` | Direction. Default `--both`; upstream needs a source. |
| `--source NAME` | Saved source to use. Repeat to combine. |
| `--org`, `--team`, `--repo`, `--path`, `--ref-kind`, `--ref-pattern`, `--ref-scan-default` | Inline source instead of a saved one. |
| `--refresh`, `--cached`, `--live` | Refresh the source first; read the cache only (the default); or read downstream live from GitHub even with a source. |
| `--ref REF` | Branch, tag or SHA of the target. |
| `--depth N\|unlimited` | Traversal depth. Default 3. |
| `--format tree\|mermaid\|json`, `--out FILE` | Output shape and destination. |
| `--contains OWNER/REPO`, `--stdin` | Report the roots whose downstream graph contains a repository (below). |

Conflicting flags (two directions, or two of `--refresh`/`--cached`/`--live`)
exit 2.

## Find which roots contain a repository

`--contains OWNER/REPO` (repeatable) turns `graph` into a search over many
roots: it walks each root's downstream graph and prints one row per node of a
wanted repository, and nothing for roots that never reach it. Give one root
as `TARGET`, or many through `--stdin`:

```bash
printf 'acme/site@main\nacme/app@v2.1.0\nacme/tools\n' \
  | untaped ansible graph --stdin --contains acme/base-role --source platform
untaped awx job-templates list --organization Default --with-scm --format pipe \
  | untaped ansible graph --stdin --contains acme/base-role --source platform
```

Stdin carries either bare `owner/repo@ref` lines (the ref is optional; a Git
URL or alias is also accepted, without a ref) or `--format pipe` records. A
record names its repository in `scm_url`, `repo_url`, `repo` or `full_name`
and its ref in `effective_scm_ref` or `ref`; an empty or missing ref means the
default branch, and every cached ref of that root is walked.

Each row (`ansible.dependency_match`) has `root_repo`, `root_ref`, the matched
`repo`, `declared_ref` (the ref string exactly as declared on the edge that
reaches it, or `null` when unpinned, never interpreted), `declared_in` (the
dependency file) and `path` (the shortest path from the root, as node
labels). A repository reached through two different declared refs gives two
rows. `--depth`, `--source`, inline selectors and `--refresh`/`--cached`/`--live`
apply as for a single graph; each root's graph warnings are printed on stderr
prefixed with the root.

This mode is downstream only (`--upstream` and `--both` are usage errors) and
prints `--format table` (default), `json` or `pipe`; `tree`, `mermaid` and
`--out` stay with the single-target graph. `--stdin` requires `--contains`,
and `--ref`/`--target-repo` apply only to `TARGET`.

## Manage sources

```bash
untaped ansible source list
untaped ansible source get platform
untaped ansible source status
untaped ansible source patch platform --add-repo acme/legacy-app --remove-team acme/platform
untaped ansible source remove platform --yes
```

- `source set NAME` creates or replaces a source. It needs at least one
  `--org`, `--team` or `--repo`.
- `source patch NAME` edits it with `--add-*`, `--remove-*` and `--clear-*`.
- `source status` shows `fresh`, `stale` (older than `ansible.stale_after`)
  or `not_refreshed`.
- `source refresh` saves each repo that succeeds. When some repos fail it
  lists them and exits 1; run it again to retry only those. It also stops and
  exits 1 with a resume hint when the GitHub GraphQL budget drops below
  `ansible.source_refresh_rate_limit_floor`.
- `--backend auto|graphql|git` picks how refs are probed. `auto` (the
  default, `ansible.source_refresh_backend`) falls back to `git ls-remote`
  when the GraphQL rate limit runs out.

## Aliases

When a requirements file names a role by Galaxy name rather than a Git URL,
map it to its repo:

```bash
untaped ansible alias set acme.base acme/ansible-role-base
untaped ansible alias list
untaped ansible alias remove acme.base --yes
```

Aliases apply when a source is refreshed; run `source refresh` afterwards.

## Read the output

- `tree` prints a shared subtree once and marks later occurrences
  `(see above)`.
- `json` has `nodes`, `edges` (with stable `id`s), `cycles` and `warnings`.
  A cycle is found only within the depth you asked for; raise `--depth` to
  look for longer loops.
- `mermaid` prints a Mermaid diagram.
- A dependency at `repo@v1` and one at `repo@main` are different nodes.
- A malformed or templated dependency file is skipped with a warning; it
  never fails the graph.

`graph` supports `--format pipe` only with `--contains`
(`ansible.dependency_match`). The `source` and `alias` commands
do (`ansible.source`, `ansible.source_status`, `ansible.alias`, and the
`ansible.source_outcome` and `ansible.alias_outcome` results).

## Troubleshooting

- **"no cached source data found"**: the source was never refreshed. Run the
  `source refresh` command the error prints, or add `--refresh`.
- **An older index is rebuilt**: after an upgrade the SQLite cache
  (`ansible.index_path`) may be rebuilt empty with a warning. Refresh each
  source again.
- **`ansible.freshness_ttl` warning**: the setting is ignored; remove it with
  `untaped config unset ansible.freshness_ttl`.

## See also

- [Configuration reference](../reference/config.md#ansible)
- [Pipes and record kinds](../reference/pipes.md)
- [GitHub](../github/usage.md)
