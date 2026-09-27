# Ansible dependency graphs

`untaped ansible` answers questions about how Ansible roles and projects
depend on each other through their requirements files:

| Command | Question | Output |
|---|---|---|
| `deps ROLE` | What does ROLE depend on (downstream)? | one `ansible.dependency` row per repo reached, per ROLE ref |
| `impact ROLE` | What depends on ROLE (upstream)? | one `ansible.dependent` row per repo reached, per ROLE ref |
| `find REPO --root ROOT` / `--stdin` | Which roots contain REPO downstream? | one `ansible.dependency_match` row per match |
| `graph TARGET` | The whole picture, both directions | a tree, Mermaid diagram or JSON graph |

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

Save a source, refresh it once, and make it the default so you can leave out
`--source`:

```bash
untaped ansible source set platform --org acme --team acme/platform
untaped ansible source refresh platform
untaped config set ansible.default_source platform
```

`ansible.default_source` is used by `deps`, `impact`, `find` and `graph`
whenever you give neither `--source` nor an inline selector. With a source
selected, every command reads the cached data and never touches GitHub; pass
`--live` to read downstream dependencies from GitHub anyway.

## Show what a role depends on

```bash
untaped ansible deps acme/base-role
untaped ansible deps acme/base-role --ref v2.1.0 --depth 2
untaped ansible deps ./roles/web --target-repo acme/web --format json
```

`ROLE` is `owner/repo`, a GitHub URL, a source alias, or a local path. A
local path resolves its repo from the Git remote only at the top of a
checkout; for a subdirectory pass `--target-repo OWNER/NAME`.

Without a source, `deps` reads GitHub live. Once a source is selected,
including through `ansible.default_source`, it reads that source's cache
instead: a role outside the source gets a "not cached" warning and no rows,
and before the source's first refresh the command fails with the refresh
command to run. Pass `--live` to read GitHub anyway. The same holds for
`find` and for `graph`'s downstream half.

Scanned files are, in each repo: `roles/requirements.yml`, `requirements.yml`,
`meta/requirements.yml` and `meta/main.yml` (`.yaml` too). Change the list
with `ansible.dependency_paths`. Collections in requirements files are not
followed; the command warns once and lists them.

## Find what depends on a role

```bash
untaped ansible impact acme/base-role
untaped ansible impact acme/base-role --source platform --refresh
untaped ansible impact acme/base-role --ref main --format pipe
```

`impact` reads cached source data only: from `--source NAME` (repeat to
combine), from inline selectors, or from `ansible.default_source`. Pass
`--refresh` (or run `source refresh`) when you want current data. For a
one-off question, use inline selectors instead of a saved source; the scan is
cached, so repeating the same command reuses it:

```bash
untaped ansible impact acme/base-role --org acme --refresh
```

`--ref R` also includes consumers that declare the role without a version
when `R` is the role's cached default branch.

## Find which roots contain a repository

`find REPO` walks the downstream graph of each root and prints one row per
node of REPO it reaches, and nothing for roots that never reach it. Give
roots with `--root` (repeatable) or through `--stdin`, and one or more repos
to look for:

```bash
untaped ansible find acme/base-role --root acme/site@main --root acme/app
printf 'acme/site@main\nacme/app@v2.1.0\nacme/tools\n' \
  | untaped ansible find acme/base-role --stdin
untaped awx job-templates list --organization Default --with-scm --format pipe \
  | untaped ansible find acme/base-role --stdin --format pipe
```

A root is `owner/repo@ref` (the ref is optional), a Git URL, a source alias,
or a local path. Stdin carries either such lines or `--format pipe` records.
A record names its repository in `scm_url`, `repo_url`, `repo` or
`full_name` and its ref in `effective_scm_ref` or `ref`; an empty or missing
ref means the default branch, and every cached ref of that root is walked.

Each row (`ansible.dependency_match`) has `root_repo`, `root_ref`, the
matched `repo`, `declared_ref` (the ref string exactly as declared on the
edge that reaches it, or `null` when unpinned, never interpreted),
`declared_in` (the dependency file), `path` (the shortest path from the
root, as node labels) and the input that named the root: `input_kind`,
`input_id` and `input_name` are the pipe record's kind, `id` and `name`
(`null` for `--root` and plain lines). Every input record gets its own rows,
even when several records name the same root, so a pipeline can join the
results back to its inputs; identical plain lines collapse.

A repository reached through two different declared refs gives two rows. An
unpinned declaration and one pinned to the default branch reach the same
node, so they give one row, whose `declared_ref` comes from the first edge
the search reached. Live reads are shared across roots, so each repo and ref
is read from GitHub once per command. Each root's graph warnings are printed
on stderr prefixed with the root.

## Draw the graph

```bash
untaped ansible graph acme/base-role
untaped ansible graph acme/base-role --downstream --format mermaid --out deps.mmd
untaped ansible graph acme/base-role --upstream --format json
```

`graph` shows both directions by default (`--upstream`, `--downstream` or
`--both` picks one), with a default `--depth` of 3. Without a source it shows
only downstream and warns that upstream was omitted.

## Flags

`deps`, `impact`, `find` and `graph` share these flags:

| Flag | Meaning |
|---|---|
| `--source NAME` | Saved source to use. Repeat to combine. Defaults to `ansible.default_source`. |
| `--org`, `--team`, `--repo`, `--path`, `--ref-kind`, `--ref-pattern`, `--ref-scan-default` | Inline source instead of a saved one. |
| `--refresh`, `--live` | Refresh the source first; or read downstream live from GitHub even with a source (`impact` has no `--live`). |
| `--parallel N`, `--backend auto\|graphql\|git` | Git fetch limit and ref probe backend for `--refresh`. |
| `--depth N\|unlimited` | Traversal depth. `deps`, `impact` and `find` default to `unlimited`, `graph` to 3. |

`deps`, `impact` and `graph` also take `--ref REF` (branch, tag or SHA of the
target) and `--target-repo OWNER/NAME`. `deps`, `impact` and `find` print
`--format table` (default), `json`, `yaml`, `pipe` or `raw`, with
`--columns`; `graph` prints `--format tree` (default), `mermaid` or `json`,
optionally to `--out FILE`, and keeps `--cached` (reading the cache is
already the default). Conflicting flags (two directions, or two of
`--refresh`/`--cached`/`--live`) exit 2.

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

## Source aliases

When a requirements file names a role by Galaxy name rather than a Git URL,
map it to its repo:

```bash
untaped ansible source-alias set acme.base acme/ansible-role-base
untaped ansible source-alias list
untaped ansible source-alias remove acme.base --yes
```

Source aliases apply when a source is refreshed; run `source refresh`
afterwards.

## Read the output

- `deps` rows (`ansible.dependency`) and `impact` rows (`ansible.dependent`)
  have `repo`, `ref`, `unresolved` (the declared name of a dependency that
  names no GitHub repo), `declared_ref` and `declared_in` (from the edge that
  reached the repo, verbatim), `depth`, `path` and `root_ref`. `path` reads
  in dependency order: from ROLE for `deps`, towards ROLE for `impact`. Each
  repo appears once per root, at its shortest path; a ref-less ROLE is walked
  from each of its refs, and `root_ref` says which ROLE ref a row was reached
  from.
- `tree` prints a shared subtree once and marks later occurrences
  `(see above)`.
- `json` has `nodes`, `edges` (with stable `id`s), `cycles` and `warnings`.
  A cycle is found only within the depth you asked for; raise `--depth` to
  look for longer loops.
- `mermaid` prints a Mermaid diagram.
- A dependency at `repo@v1` and one at `repo@main` are different nodes.
- An unpinned dependency points at the node for the dependency's default
  branch (`repo@main`), so the walk continues through it. Cached reads take
  the default branch the source recorded; live reads (`--live`, or no
  source) take it from GitHub. When no default branch is known (the repo is
  not in the source, or GitHub reports none), the node stays ref-less
  (`repo`). When a source scans only tags, the default-branch node is not
  cached, so the walk stops there with a "ref is not cached" warning.
- A malformed or templated dependency file is skipped with a warning; it
  never fails the command.

## Troubleshooting

- **"no cached source data found"**: the source was never refreshed. Run the
  `source refresh` command the error prints, or add `--refresh`.
- **An older index is rebuilt**: after an upgrade the SQLite cache
  (`ansible.index_path`) may be rebuilt empty with a warning. Refresh each
  source again. When the dependency parser changes, the next refresh
  re-parses every ref once instead of reusing cached results, and a refresh
  that was interrupted before the upgrade starts over.

## See also

- [Configuration reference](../reference/config.md#ansible)
- [Pipes and record kinds](../reference/pipes.md)
- [GitHub](../github/usage.md)
